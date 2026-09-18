"""Equal-budget, real-feedback-frequency-matched decision-policy ablations.

This deliberately reuses the frozen synthetic-v1 oracle. Its arbitrary descriptors are not
molecular features, and no result from this benchmark establishes chemical efficacy. Hidden
objective values are used only by the post-hoc evaluator, after the campaign has selected its
actions and made its acquired-data-only recommendations.
"""

from __future__ import annotations

import hashlib
import statistics
from collections.abc import Mapping, Sequence
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any

from etalon.active.replay import from_manifest, synthetic_manifest
from etalon.active.schema import digest

GROUPS = ("random", "ucb", "cost_aware", "mf_kg", "decision_no_validity",
          "decision_no_guard", "decision_global", "decision_aware")

_OVERRIDES: dict[str, dict[str, Any]] = {
    "random": {"policy": "random"},
    "ucb": {"policy": "ucb"},
    "cost_aware": {"policy": "cost_aware"},
    "mf_kg": {"policy": "mf_kg", "validity_mode": "none", "confirmation_reserve": 0},
    "decision_no_validity": {"policy": "decision_aware", "validity_mode": "none"},
    "decision_no_guard": {"policy": "decision_aware", "confirmation_reserve": 0},
    "decision_global": {"policy": "decision_aware", "validity_mode": "global"},
    "decision_aware": {"policy": "decision_aware"},
}

_REGRETS = ("provisional_oracle_regret", "attainable_oracle_regret", "evidence_backed_oracle_regret",
            "legacy_best_observed_regret")


def _manifest(base: Mapping[str, Any], group: str) -> dict[str, Any]:
    manifest = deepcopy(dict(base))
    manifest["spec"].update({"batch_size": 1, "calibration_fraction": 0.15,
                             "confirmation_reserve": 1, "validity_mode": "local",
                             "max_kg_candidates": 512, **_OVERRIDES[group]})
    return manifest


def _posthoc(oracle_rows: Sequence[dict[str, Any]], objective: str,
             recommendations: Mapping[str, Any], observations: Sequence[dict[str, Any]],
             actions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Score finalized recommendations; never provide these truth values to a policy."""
    oracle = {r["candidate_id"]: r["value"] for r in oracle_rows if r["endpoint_id"] == objective}
    # synthetic-v1 has a minimizing, noise-free reference; do not generalize this evaluator
    # to physical experiments or noisy reference tables without defining the estimand first.
    best_truth = min(oracle.values())
    acquired = {r["result"]["candidate_id"] for r in observations
                if r["admitted"] and r["result"]["endpoint_id"] == objective}
    high_values = [r["result"]["value"] for r in observations
                   if r["admitted"] and r["result"]["endpoint_id"] == objective]
    best_observed = min(high_values) if high_values else None
    result: dict[str, Any] = {
        "legacy_best_observed_value": best_observed,
        "legacy_best_observed_regret": None if best_observed is None else best_observed - best_truth,
    }
    for name in ("provisional", "attainable", "evidence_backed"):
        recommendation = recommendations.get(name)
        key = recommendation["candidate_id"] if recommendation is not None else None
        if key is not None and key not in oracle:
            raise ValueError("recommendation does not identify a candidate in the frozen oracle")
        if name == "evidence_backed" and key is not None and key not in acquired:
            raise ValueError("evidence-backed recommendation lacks admitted objective evidence")
        result[f"{name}_candidate_id"] = key
        result[f"{name}_oracle_regret"] = None if key is None else oracle[key] - best_truth
    result["provisional_unconfirmed"] = (
        None if result["provisional_candidate_id"] is None
        else result["provisional_candidate_id"] not in acquired
    )
    result["invalid_spent"] = sum(r["result"]["cost"] for r in observations if not r["admitted"])
    result["objective_queries_including_warmstart"] = sum(
        a["endpoint_id"] == objective for a in actions)
    result["objective_queries_after_warmstart"] = sum(
        a["endpoint_id"] == objective and a["round_id"] != 0 for a in actions)
    result["calibration_spent"] = sum(a["cost"] for a in actions
                                       if a["decision"].get("reason") == "protocol-calibration")
    result["target_evidence_candidates"] = len(acquired)
    return result


def _summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for group in GROUPS:
        rows = [row for row in records if row["group"] == group]
        summary: dict[str, Any] = {"seeds": len(rows)}
        for metric in (*_REGRETS, "invalid_spent", "objective_queries_after_warmstart", "calibration_spent"):
            values = [row["final"][metric] for row in rows if row["final"][metric] is not None]
            summary[f"n_{metric}"] = len(values)
            summary[f"mean_{metric}"] = statistics.mean(values) if values else None
            summary[f"sd_{metric}"] = statistics.stdev(values) if len(values) > 1 else None
        flags = [r["final"]["provisional_unconfirmed"] for r in rows
                 if r["final"]["provisional_unconfirmed"] is not None]
        summary["unconfirmed_provisional_fraction"] = statistics.mean(flags) if flags else None
        summary["mean_spent"] = statistics.mean(row["balance"]["spent"] for row in rows)
        result[group] = summary
    return result


def _paired(records: Sequence[dict[str, Any]], seeds: Sequence[int]) -> dict[str, Any]:
    by_key = {(row["group"], row["seed"]): row for row in records}
    result = {}
    for group in GROUPS:
        if group == "decision_aware":
            continue
        metrics = {}
        for metric in _REGRETS:
            pairs = []
            for seed in seeds:
                ours = by_key[("decision_aware", seed)]["final"][metric]
                baseline = by_key[(group, seed)]["final"][metric]
                pairs.append({"seed": seed, "difference": None if ours is None or baseline is None
                              else ours - baseline})
            values = [p["difference"] for p in pairs if p["difference"] is not None]
            metrics[metric] = {"pairs": pairs, "n": len(values),
                               "mean_difference": statistics.mean(values) if values else None,
                               "sd_difference": statistics.stdev(values) if len(values) > 1 else None}
        result[group] = metrics
    return {"direction": "decision_aware minus comparator; negative regret differences favor decision_aware",
            "inference": "Descriptive paired differences only; no significance or superiority claim",
            "comparators": result}


def decision_benchmark(workspace: str | Path, *, seeds: Sequence[int] = (0, 1, 2, 3, 4),
                       budget: float = 120, size: int = 64, rounds: int = 200) -> dict[str, Any]:
    """Run eight fixed ablations; resume each journal up to the total round quota.

    Equal budget means equal caps and charged warm starts, not necessarily equal expenditure:
    a confirmation guard may deliberately leave unusable budget. Persisted round planning
    times and current-invocation wall times are reported separately from oracle cost units.
    Timing is nondeterministic metadata and must be excluded from deterministic result tests.
    """
    if (not seeds or len(set(seeds)) != len(seeds)
            or any(not isinstance(seed, int) or isinstance(seed, bool) for seed in seeds)):
        raise ValueError("distinct integer seeds are required")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
        raise ValueError("rounds must be a positive integer")
    if not isinstance(size, int) or isinstance(size, bool) or not 8 <= size <= 512:
        raise ValueError("the exact-KG smoke benchmark requires 8 to 512 candidates")
    # Refuse silently continuing an ablation journal under edited algorithm/metric code.
    # This is an implementation fingerprint, not a full environment/container lock.
    names = ("model", "policy", "decision", "knowledge", "reliability", "recommendation",
             "runner", "schema", "store", "budget", "replay", "decision_benchmark", "protocols", "proposer")
    implementation = {"modules": {
        name: hashlib.sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest()
        for name in names}, "environment": {name: version(name) for name in ("numpy", "scipy")},
        "admission_modules": {
            name: hashlib.sha256((Path(__file__).parent.parent / (name + ".py")).read_bytes()).hexdigest()
            for name in ("learn/admissible", "faults/attribution", "faults/taxonomy", "judgment/waiver")},
        "scope": "Offline controller, accounting and QC source identity; not a complete environment or container lock"}
    records = []
    for seed in seeds:
        base = synthetic_manifest(seed=seed, size=size, budget=budget, batch_size=1)
        controls_hash = digest({key: base[key] for key in ("candidates", "endpoints", "oracle", "initial")})
        for group in GROUPS:
            manifest = _manifest(base, group)
            database = Path(workspace) / f"seed-{seed}" / group / "campaign.sqlite"
            campaign = from_manifest(manifest, database)
            campaign.store.bind_resource("decision_benchmark_implementation", implementation,
                                         before_first_round=True)
            done = len(campaign.store.rounds())
            started = perf_counter()
            if rounds > done:
                campaign.run(max_rounds=rounds - done)
            run_wall_seconds = perf_counter() - started
            started = perf_counter()
            recommendations = campaign.recommend()
            recommendation_wall_seconds = perf_counter() - started
            # Planning is read-only. Recomputing the terminal reason makes a completed
            # report identical on restart even if the previous invocation hit its quota
            # immediately after the final affordable action.
            started = perf_counter()
            _, choices, terminal_reason = campaign.plan()
            terminal_plan_wall_seconds = perf_counter() - started
            stop_reason = "round_limit" if choices else terminal_reason
            observations = campaign.store.observations()
            actions = campaign.store.actions()
            completed = campaign.store.rounds()
            selected = [a for a in actions if a["round_id"] != 0]
            last = selected[-1] if selected else None
            final = _posthoc(manifest["oracle"], manifest["spec"]["objective"],
                             recommendations, observations, actions)
            records.append({
                "seed": seed, "group": group, "policy": manifest["spec"]["policy"],
                "spec": manifest["spec"], "controls_hash": controls_hash,
                "database": str(database.resolve()), "balance": campaign.store.balance(),
                "rounds": len(completed), "stop_reason": stop_reason,
                "queries_including_warmstart": len(observations),
                "admitted": sum(o["admitted"] for o in observations),
                "charged_warmstart_cost": sum(row["result"]["cost"] for row in manifest["initial"]),
                "recommendations": recommendations, "final": final,
                "last_action": None if last is None else {
                    **{key: last[key] for key in ("candidate_id", "endpoint_id", "replicate", "status", "cost")},
                    "reason": last["decision"].get("reason"),
                },
                "timing": {
                    "persisted_round_planning_seconds": sum(r.get("planning_seconds", 0.0) for r in completed),
                    "current_invocation_run_wall_seconds": run_wall_seconds,
                    "current_invocation_recommendation_wall_seconds": recommendation_wall_seconds,
                    "current_invocation_terminal_plan_wall_seconds": terminal_plan_wall_seconds,
                    "scope": "Run wall time includes planning; persisted planning sums executed rounds only. "
                             "Read-only terminal planning and recommendation fitting are additional. "
                             "Scheduling/training wall time is NOT charged as synthetic oracle cost.",
                },
            })
    return {
        "schema_version": 1, "dataset": "synthetic-v1", "cost_unit": "synthetic_cost_units",
        "benchmark": "decision-policy-ablation/1", "round_limit": rounds,
        "implementation": implementation,
        "environment": {name: version(name) for name in ("numpy", "scipy")},
        "claim": "Frozen synthetic engineering smoke benchmark only; neither chemical efficacy, "
                 "novelty, statistical significance, nor superiority over published methods",
        "controls": "Identical oracle, pool, endpoints, QC, charged warm start and budget per seed. "
                    "Every group uses batch_size=1 and receives real feedback after each action. "
                    "Only policy/declared ablation fields change; oracle values never enter acquisition.",
        "metric_definitions": {
            "provisional_oracle_regret": "Hidden reference value at the posterior-mean recommendation minus oracle optimum",
            "attainable_oracle_regret": "Hidden reference value at the evidence-attainable recommendation minus oracle optimum",
            "evidence_backed_oracle_regret": "Hidden reference value at the admitted-objective-evidence recommendation minus oracle optimum",
            "legacy_best_observed_regret": "Minimum acquired admitted objective value minus oracle optimum; NOT recommendation regret",
            "evidence_backed": "Admitted evidence at the declared synthetic protocol, not experimental confirmation",
        },
        "summary": _summary(records), "paired_differences": _paired(records, seeds), "runs": records,
    }
