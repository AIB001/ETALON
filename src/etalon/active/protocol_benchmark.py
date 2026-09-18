"""Bounded synthetic black-box protocol-arm search; no scientific execution is simulated.

Only a queried table cell becomes feedback. The controller has no oracle argument, while
the final evaluator can inspect the sealed table. These labels are NOT registry evidence,
MolCascade results, measured panel skills, chemical efficacies, or an external benchmark.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections.abc import Mapping, Sequence
from itertools import product
from numbers import Real
from typing import Any

from etalon.active.protocol_score import rank_variants
from etalon.active.schema import digest

GROUPS = ("fixed", "random", "linear_ucb", "linear_ucb_no_transfer", "linear_greedy")
SCENARIOS = ("shared_additive", "strong_interaction", "uninformative_features", "negative_utility")
VERSION = "synthetic-protocol-search/1"


def _scenario(seed: int, name: str) -> tuple[dict[str, Any], dict[str, float]]:
    """Construct a public catalog and a separate, query-invariant synthetic reward table."""
    if name not in SCENARIOS:
        raise ValueError("unknown synthetic protocol scenario")
    edits = list(product((-1.0, 1.0), repeat=4))
    # Public id assignment is independent of the scenario's hidden response function.
    random.Random(seed).shuffle(edits)
    # Match marginal prior uncertainty to the one-hot ablation; only cross-arm
    # covariance/transfer should differ, not a gratuitous feature-norm advantage.
    scale = math.sqrt(5)
    features = {f"variant-{index:02d}": [value / scale for value in (1.0, *bits)]
                for index, bits in enumerate(edits)}
    noise = random.Random(seed + 104729)
    rewards = {}
    for identifier, bits in zip(features, edits, strict=True):
        if name == "shared_additive":
            value = -0.05 + sum(w * bit for w, bit in zip((0.35, -0.25, 0.15, 0.1), bits, strict=True))
        elif name == "strong_interaction":
            # Fourth-order parity has no main-effect linear representation in this catalog.
            value = 0.75 * math.prod(bits) - 0.1
        elif name == "uninformative_features":
            # Draw ONCE when the table is frozen, never at query time or per policy.
            value = noise.uniform(-0.8, 0.8)
        else:
            value = -0.1 - 0.1 * sum(bit > 0 for bit in bits)
        rewards[identifier] = round(value, 12)
    return {"features": features, "costs": dict.fromkeys(features, 1.0)}, rewards


def _select(features: Mapping[str, Sequence[float]], costs: Mapping[str, float],
            observed: Sequence[dict[str, Any]], eligible: Sequence[str], *,
            group: str, seed: int) -> dict[str, Any]:
    """Controller boundary: public catalog + acquired labels only, never a sealed table."""
    if group not in GROUPS:
        raise ValueError("unknown protocol benchmark policy")
    if not eligible:
        raise ValueError("at least one eligible protocol is required")
    acquired = {row["id"] for row in observed}
    live_ids = acquired | set(eligible)
    if acquired & set(eligible):
        raise ValueError("a completed protocol cannot be queried again")
    # Preserve the complete, fixed one-hot vocabulary even when affordability shrinks.
    ids = sorted(features)
    vectors = ({key: [float(index == position) for position in range(len(ids))]
                for index, key in enumerate(ids)} if group == "linear_ucb_no_transfer" else features)
    active_features = {key: list(vectors[key]) for key in ids if key in live_ids}
    active_costs = {key: costs[key] for key in active_features}
    policy = group if group in {"fixed", "random"} else "linear_ucb"
    parameters = {"policy": policy, "beta": 0.0 if group == "linear_greedy" else 1.0,
                  "ridge": 1.0, "noise": 0.5, "seed": seed}
    result = rank_variants(active_features, observed, active_costs, **parameters)
    available = set(eligible)
    ranking = [row for row in result["ranking"] if row["id"] in available]
    if not ranking:
        raise ValueError("ranker returned no eligible protocol")
    return {"chosen": dict(ranking[0]), "training_hash": result["training_hash"],
            "training_size": result["training_size"], "parameters": parameters,
            "snapshot": {"features": active_features, "costs": active_costs,
                         "observed": [dict(row) for row in observed], "eligible": list(eligible)}}


def _evaluate(rewards: Mapping[str, float], observed: Sequence[dict[str, Any]],
              initial_affordable: Sequence[str]) -> dict[str, Any]:
    """Post-hoc synthetic truth access, separate from acquisition and training."""
    acquired = {row["id"] for row in observed}
    positive_ids = {key for key in initial_affordable if rewards[key] > 0}
    best_oracle = max((0.0, *(rewards[key] for key in initial_affordable)))
    best_revealed = max((0.0, *(row["reward"] for row in observed)))
    positive = sorted((row for row in observed if row["reward"] > 0),
                      key=lambda row: (-row["reward"], row["id"]))
    return {"best_revealed_positive_utility": best_revealed,
            "oracle_best_positive_utility": best_oracle,
            "simple_regret": max(0.0, best_oracle - best_revealed),
            "oracle_positive_opportunities": len(positive_ids),
            "revealed_positive_opportunities": len(acquired & positive_ids),
            "missed_positive_opportunities": len(positive_ids - acquired),
            "opportunity_recall": len(acquired & positive_ids) / len(positive_ids) if positive_ids else None,
            "recommendation": positive[0]["id"] if positive else None,
            "no_edit_baseline_utility": 0.0}


def _run(catalog: Mapping[str, Any], rewards: Mapping[str, float], *, seed: int,
         scenario: str, group: str, budget: float, max_trials: int) -> dict[str, Any]:
    features, costs = catalog["features"], catalog["costs"]
    observed: list[dict[str, Any]] = []
    trace = []
    spent = 0.0
    initial_affordable = sorted(key for key in features if costs[key] <= budget)
    stop_reason = "trial_limit"
    for _ in range(max_trials):
        acquired = {row["id"] for row in observed}
        eligible = sorted(key for key in features if key not in acquired and costs[key] <= budget - spent)
        if not eligible:
            stop_reason = "catalog_exhausted" if len(acquired) == len(features) else "budget_exhausted"
            break
        decision = _select(features, costs, observed, eligible, group=group, seed=seed)
        identifier = decision["chosen"]["id"]
        # This is the sole synthetic query. No future label or whole-table digest is used
        # to choose the arm or construct its training record.
        reward = rewards[identifier]
        cost = costs[identifier]
        label = {"id": identifier, "reward": reward,
                 "evidence_hash": digest({"scope": "synthetic-table-reply-only", "version": VERSION,
                                          "seed": seed, "scenario": scenario, "id": identifier,
                                          "reward": reward, "cost": cost})}
        spent += cost
        trace.append({"trial": len(trace) + 1, **decision, "observed_reward": reward,
                      "cost": cost, "spent": spent, "synthetic_reply_hash": label["evidence_hash"]})
        observed.append(label)
    # Classify exhaustion even when the last allowed trial also uses the last quote.
    if len(observed) == len(features):
        stop_reason = "catalog_exhausted"
    elif not any(key not in {row["id"] for row in observed} and costs[key] <= budget - spent
                 for key in features):
        stop_reason = "budget_exhausted"
    return {"seed": seed, "scenario": scenario, "group": group,
            "budget": budget, "spent": spent, "remaining": budget - spent,
            "queries": len(observed), "stop_reason": stop_reason,
            "controls_hash": digest({"catalog": catalog, "sealed_rewards": rewards}),
            "public_catalog_hash": digest(catalog), "observed": observed, "trace": trace,
            "final": _evaluate(rewards, observed, initial_affordable)}


def _summarize(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    summary = {}
    for scenario in SCENARIOS:
        groups = {}
        for group in GROUPS:
            rows = [row for row in runs if row["scenario"] == scenario and row["group"] == group]
            metrics = {"n": len(rows)}
            for name in ("best_revealed_positive_utility", "simple_regret", "opportunity_recall"):
                values = [row["final"][name] for row in rows if row["final"][name] is not None]
                metrics["mean_" + name] = statistics.mean(values) if values else None
                metrics["sd_" + name] = statistics.stdev(values) if len(values) > 1 else None
                metrics["n_" + name] = len(values)
            metrics["mean_spent"] = statistics.mean(row["spent"] for row in rows)
            groups[group] = metrics
        summary[scenario] = groups
    return summary


def protocol_benchmark(*, seeds: Sequence[int] = (0, 1, 2, 3, 4), budget: float = 6.0,
                       max_trials: int = 16) -> dict[str, Any]:
    """Run five fixed policy groups on four fixed, finite synthetic scenario families.

    Seeds permute public ids and freeze the uninformative table; no seed or hyperparameter
    is selected based on results. Every group starts without observations, receives one
    feedback per query and pays the identical one-unit quote, even for negative utility.
    A free no-edit option has utility zero. Query cost caps search; it is not subtracted
    again from table utility, and controller computation is not included in these units.
    """
    if (not isinstance(seeds, Sequence) or isinstance(seeds, (str, bytes)) or not seeds
            or any(isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32
                   for seed in seeds) or len(set(seeds)) != len(seeds)):
        raise ValueError("seeds must be distinct integers in [0, 2**32)")
    if isinstance(budget, bool) or not isinstance(budget, Real):
        raise ValueError("budget must be a positive finite number")
    try:
        amount = float(budget)
    except (OverflowError, ValueError) as error:
        raise ValueError("budget must be a positive finite number") from error
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("budget must be a positive finite number")
    if isinstance(max_trials, bool) or not isinstance(max_trials, int) or max_trials < 1:
        raise ValueError("max_trials must be a positive integer")
    runs = []
    for seed in seeds:
        for scenario in SCENARIOS:
            catalog, rewards = _scenario(seed, scenario)
            for group in GROUPS:
                runs.append(_run(catalog, rewards, seed=seed, scenario=scenario, group=group,
                                 budget=amount, max_trials=max_trials))
    return {"schema_version": 1, "benchmark": VERSION, "synthetic": True,
            "seeds": list(seeds), "budget": amount, "max_trials": max_trials,
            "groups": list(GROUPS), "scenarios": list(SCENARIOS), "cost_unit": "synthetic_query_units",
            "claim": "Synthetic black-box engineering benchmark only; no real protocol execution, "
                     "chemical validation, independent panel validation, novelty or agent superiority claim.",
            "controls": "Identical table, public catalog, empty initial history, quotes, budget and trial cap "
                        "within each scenario/seed. One-hot ablation changes representation only. "
                        "Only queried labels enter training. No seed filtering or benchmark tuning.",
            "scenario_definitions": {
                "shared_additive": "Utility is linear in the four public edit features plus an intercept.",
                "strong_interaction": "Fourth-order parity utility violates the main-effect linear model.",
                "uninformative_features": "Uniform random utilities are frozen once, independently of features.",
                "negative_utility": "Every queried variant is worse than the free zero-utility no-edit option.",
            },
            "metric_definitions": {
                "best_revealed_positive_utility": "max(0, acquired table rewards); no hidden-label recommendation",
                "simple_regret": "Best initially affordable oracle utility including no-edit, minus best revealed positive utility",
                "opportunity_recall": "Queried positive-utility arms / all initially affordable positive-utility arms; null if none",
                "cost": "One fixed synthetic unit per query, including zero/negative replies; no timing or physical cost claim",
                "evidence_hash": "Synthetic reply identity only; not MolCascade provenance or registry-admissible evidence",
            }, "summary": _summarize(runs), "runs": runs}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", default="0,1,2,3,4", help="comma-separated fixed integer seeds")
    parser.add_argument("--budget", type=float, default=6.0)
    parser.add_argument("--max-trials", type=int, default=16)
    arguments = parser.parse_args(argv)
    try:
        seeds = tuple(int(value.strip()) for value in arguments.seeds.split(","))
        result = protocol_benchmark(seeds=seeds, budget=arguments.budget, max_trials=arguments.max_trials)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
