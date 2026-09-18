"""Reproducible equal-budget synthetic smoke benchmark and reliability ablation.

No success claim is inferred from this fixture. Benchmark metrics read the held-back objective
only AFTER selection; the learner receives only its query results and common charged warm start.
"""

from __future__ import annotations

import hashlib
import statistics
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any

from etalon.active.replay import from_manifest, synthetic_manifest
from etalon.active.schema import CampaignSpec, digest

POLICIES = ("random", "greedy", "ucb", "cost_only", "cost_aware")


def benchmark(workspace: str | Path, *, seeds: Sequence[int] = (0, 1, 2, 3, 4),
              budget: float = 120, size: int = 64, rounds: int = 40,
              policies: Sequence[str] = POLICIES) -> dict[str, Any]:
    if (not seeds or any(type(seed) is not int for seed in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ValueError("distinct integer seeds are required")
    if (not policies or any(not isinstance(policy, str) for policy in policies)
            or len(set(policies)) != len(policies)):
        raise ValueError("distinct supported policies are required")
    if type(rounds) is not int or rounds < 1:
        raise ValueError("rounds must be a positive integer")
    # Validate every arm before creating the first journal. In particular an invalid
    # later policy must not leave a partly executed comparison on disk.
    for policy in policies:
        spec = CampaignSpec("reference", budget, "synthetic_cost_units", "synthetic-3d/1", policy=policy)
        if policy in {"mf_kg", "decision_aware"} and size > spec.max_kg_candidates:
            raise ValueError("comparison pool exceeds max_kg_candidates for an exact-KG arm")
    names = ("model", "policy", "decision", "knowledge", "reliability", "recommendation",
             "runner", "schema", "store", "budget", "replay", "benchmark", "protocols", "proposer")
    implementation = {"modules": {
        name: hashlib.sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest()
        for name in names}, "environment": {name: version(name) for name in ("numpy", "scipy")},
        "admission_modules": {
            name: hashlib.sha256((Path(__file__).parent.parent / (name + ".py")).read_bytes()).hexdigest()
            for name in ("learn/admissible", "faults/attribution", "faults/taxonomy", "judgment/waiver")},
        "scope": "Offline controller, accounting and QC source identity; not a complete environment or container lock"}
    records = []
    for seed in seeds:
        for policy in policies:
            manifest = synthetic_manifest(seed=seed, size=size, budget=budget, policy=policy)
            database = Path(workspace) / f"seed-{seed}" / policy / "campaign.sqlite"
            campaign = from_manifest(manifest, database)
            campaign.store.bind_resource("benchmark_implementation", implementation, before_first_round=True)
            # Re-running a finished benchmark is idempotent; a partly completed one uses only
            # the remaining round quota, not another full quota on each restart.
            done = len(campaign.store.rounds())
            if rounds > done:
                campaign.run(max_rounds=rounds - done)
            observations = campaign.store.observations()
            oracle = {r["candidate_id"]: r["value"] for r in manifest["oracle"] if r["endpoint_id"] == "reference"}
            best_truth = min(oracle.values())
            top_ids = set(sorted(oracle, key=lambda key: (oracle[key], key))[:max(1, len(oracle) // 10)])
            acquired, best, spent, admitted, trace = set(), None, 0.0, 0, []
            for entry in observations:
                result = entry["result"]
                spent += result["cost"]
                admitted += int(entry["admitted"])
                if entry["admitted"] and result["endpoint_id"] == "reference":
                    acquired.add(result["candidate_id"])
                    best = result["value"] if best is None else min(best, result["value"])
                trace.append({"spent": spent, "admitted": admitted, "best_observed": best,
                              "simple_regret": None if best is None else best - best_truth,
                              "top_decile_recall": len(acquired & top_ids) / len(top_ids)})
            records.append({"seed": seed, "policy": policy, "database": str(database.resolve()),
                            "controls_hash": digest({key: manifest[key] for key in
                                                     ("candidates", "endpoints", "oracle", "initial")}),
                            "balance": campaign.store.balance(), "rounds": len(campaign.store.rounds()),
                            "queries_including_warmstart": len(observations), "admitted": admitted,
                            "final": trace[-1], "trace": trace})
    summary = {}
    for policy in policies:
        rows = [r for r in records if r["policy"] == policy]
        regrets = [r["final"]["simple_regret"] for r in rows]
        summary[policy] = {"seeds": len(rows), "mean_simple_regret": statistics.mean(regrets),
                           "sd_simple_regret": statistics.stdev(regrets) if len(regrets) > 1 else None,
                           "mean_spent": statistics.mean(r["balance"]["spent"] for r in rows)}
    return {"schema_version": 1, "dataset": "synthetic-v1", "cost_unit": "synthetic_cost_units",
            "implementation": implementation,
            "round_limit": rounds,
            "environment": {name: version(name) for name in ("numpy", "scipy")},
            "claim": "Engineering smoke benchmark only; neither CADD efficacy nor superiority over published methods",
            "controls": "Same oracle, charged warm start, features, QC and budget per seed. Objective-only baselines; cost_only ablates reliability weighting.",
            "summary": summary, "runs": records}
