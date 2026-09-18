"""Fixed synthetic audit-search stopping ablations, isolated from real protocol evidence.

The complete cost-to-skill grid is declared in advance. No result selects a favorable
conversion factor, seed or scenario. A zero outside option means ending this research
search, NOT the measured performance of a seed protocol or permission to deploy an arm.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from etalon.active.protocol_benchmark import SCENARIOS, _evaluate, _scenario
from etalon.active.protocol_score import rank_variants
from etalon.active.schema import digest

VERSION = "synthetic-protocol-stopping/1"
OPPORTUNITY_COSTS = (0.02, 0.1, 0.3)
GROUPS = ("linear_ucb", "audit_ei", "audit_ei_no_stop", "audit_ei_no_transfer", "no_search")
ECONOMIC_STOP = "nonpositive_one_step_net_value"


def _snapshot(features: Mapping[str, Sequence[float]], costs: Mapping[str, float],
              observed: Sequence[dict[str, Any]], eligible: Sequence[str]) -> dict[str, Any]:
    return {"features": {key: list(features[key]) for key in sorted(features)},
            "costs": dict(costs), "observed": [dict(row) for row in observed],
            "eligible": list(eligible)}


def _decision(features: Mapping[str, Sequence[float]], costs: Mapping[str, float],
              observed: Sequence[dict[str, Any]], eligible: Sequence[str], *,
              group: str, seed: int, opportunity_cost: float) -> dict[str, Any]:
    """No oracle argument. Fit on all acquired labels, then stop only on affordable arms."""
    if group not in GROUPS or group == "no_search":
        raise ValueError("a querying benchmark policy is required")
    if not eligible:
        raise ValueError("at least one eligible protocol is required")
    if {row["id"] for row in observed} & set(eligible):
        raise ValueError("a completed protocol cannot be queried again")
    ids = sorted(features)
    vectors = ({key: [float(position == index) for index in range(len(ids))]
                for position, key in enumerate(ids)} if group == "audit_ei_no_transfer" else features)
    parameters: dict[str, Any] = {"policy": "linear_ucb" if group == "linear_ucb" else "audit_ei",
                                  "beta": 1.0, "ridge": 1.0, "noise": 0.5, "seed": seed}
    if group != "linear_ucb":
        parameters["opportunity_cost"] = opportunity_cost
    # The feature vocabulary, dimensions and history do not change with affordability.
    # Ranker economics is diagnostic; the decision below filters BEFORE comparing to stop.
    result = rank_variants(vectors, observed, costs, **parameters)
    allowed = set(eligible)
    ranking = [dict(row) for row in result["ranking"] if row["id"] in allowed]
    if not ranking:
        raise ValueError("ranker returned no eligible protocol")
    should_stop = group in {"audit_ei", "audit_ei_no_transfer"} and ranking[0]["score"] <= 0
    return {"chosen": None if should_stop else ranking[0],
            "stop_reason": ECONOMIC_STOP if should_stop else None,
            "ranking": ranking, "training_hash": result["training_hash"],
            "training_size": result["training_size"], "parameters": parameters,
            "economics": result.get("economics"),
            "max_affordable_net_value": ranking[0]["score"] if group != "linear_ucb" else None,
            "snapshot": _snapshot(vectors, costs, observed, eligible)}


def _posthoc(rewards: Mapping[str, float], costs: Mapping[str, float],
             observed: Sequence[dict[str, Any]], *, budget: float, spent: float,
             opportunity_cost: float, stop_reason: str) -> dict[str, Any]:
    """The evaluator alone uses unqueried values to expose stopping failures, not hide them."""
    affordable_initial = [key for key in rewards if costs[key] <= budget]
    result = _evaluate(rewards, observed, affordable_initial)
    result["research_best_completed_variant"] = result.pop("recommendation")
    # Avoid equating the old benchmark's outside option with a real seed protocol score.
    result["outside_option_score"] = result.pop("no_edit_baseline_utility")
    best = result["best_revealed_positive_utility"]
    acquired = {row["id"] for row in observed}
    remaining = [key for key in rewards if key not in acquired and costs[key] <= budget - spent]
    raw_gains = {key: max(0.0, rewards[key] - best) for key in remaining}
    net_gains = {key: gain - opportunity_cost * costs[key] for key, gain in raw_gains.items()}
    positive_net = sorted(key for key, value in net_gains.items() if value > 0)
    return {**result, "net_utility": best - opportunity_cost * spent,
            "cost_adjusted_regret": result["simple_regret"] + opportunity_cost * spent,
            "opportunity_charge": opportunity_cost * spent,
            "budget_saved": budget - spent,
            "oracle_remaining_affordable_improvement": max((0.0, *raw_gains.values())),
            "oracle_remaining_affordable_net_improvement": max((0.0, *net_gains.values())),
            "oracle_remaining_affordable_beneficial_queries": len(positive_net),
            "model_stop_left_raw_improvement": stop_reason == ECONOMIC_STOP and any(v > 0 for v in raw_gains.values()),
            "economically_premature_stop": stop_reason == ECONOMIC_STOP and bool(positive_net)}


def _run(catalog: Mapping[str, Any], rewards: Mapping[str, float], *, seed: int,
         scenario: str, group: str, opportunity_cost: float, budget: float,
         max_trials: int) -> dict[str, Any]:
    features, costs = catalog["features"], catalog["costs"]
    observed: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    spent = 0.0
    while True:
        acquired = {row["id"] for row in observed}
        eligible = sorted(key for key in features if key not in acquired and costs[key] <= budget - spent)
        if group == "no_search":
            reason = "no_search"
        elif len(acquired) == len(features):
            reason = "catalog_exhausted"
        elif not eligible:
            reason = "budget_exhausted"
        elif len(trace) >= max_trials:
            reason = "trial_limit"
        else:
            reason = None
        if reason is not None:
            stop_snapshot = {"chosen": None, "stop_reason": reason,
                             "snapshot": _snapshot(features, costs, observed, eligible),
                             "remaining_budget": budget - spent}
            break
        decision = _decision(features, costs, observed, eligible, group=group, seed=seed,
                             opportunity_cost=opportunity_cost)
        if decision["chosen"] is None:
            reason = decision["stop_reason"]
            stop_snapshot = {**decision, "remaining_budget": budget - spent}
            break
        identifier = decision["chosen"]["id"]
        # A single black-box response; never pass the whole table or its digest to ranker.
        reward, cost = rewards[identifier], costs[identifier]
        label = {"id": identifier, "reward": reward,
                 "evidence_hash": digest({"scope": "synthetic-stopping-table-reply-only", "version": VERSION,
                                          "seed": seed, "scenario": scenario, "id": identifier,
                                          "reward": reward, "cost": cost})}
        spent += cost
        trace.append({"trial": len(trace) + 1, **decision, "observed_reward": reward, "cost": cost,
                      "spent": spent, "synthetic_reply_hash": label["evidence_hash"]})
        observed.append(label)
    return {"seed": seed, "scenario": scenario, "group": group, "opportunity_cost": opportunity_cost,
            "budget": budget, "spent": spent, "remaining": budget - spent,
            "queries": len(observed), "stop_reason": reason, "stop_snapshot": stop_snapshot,
            "controls_hash": digest({"catalog": catalog, "sealed_rewards": rewards}),
            "public_catalog_hash": digest(catalog), "observed": observed, "trace": trace,
            "final": _posthoc(rewards, costs, observed, budget=budget, spent=spent,
                              opportunity_cost=opportunity_cost, stop_reason=reason)}


def _paired_metrics(runs: Sequence[dict[str, Any]]) -> None:
    baselines = {(row["seed"], row["scenario"], row["opportunity_cost"]): row
                 for row in runs if row["group"] == "audit_ei_no_stop"}
    for row in runs:
        baseline = baselines[(row["seed"], row["scenario"], row["opportunity_cost"])]
        row["paired_no_stop"] = {"spent_saved": baseline["spent"] - row["spent"],
                                 "queries_saved": baseline["queries"] - row["queries"],
                                 "net_utility_difference": row["final"]["net_utility"] - baseline["final"]["net_utility"],
                                 "simple_regret_difference": row["final"]["simple_regret"] - baseline["final"]["simple_regret"],
                                 "cost_adjusted_regret_difference": row["final"]["cost_adjusted_regret"] - baseline["final"]["cost_adjusted_regret"]}


def _summary(runs: Sequence[dict[str, Any]], opportunity_costs: Sequence[float]) -> list[dict[str, Any]]:
    summary = []
    for opportunity_cost in opportunity_costs:
        for scenario in SCENARIOS:
            groups = {}
            for group in GROUPS:
                rows = [r for r in runs if r["scenario"] == scenario and r["group"] == group
                        and r["opportunity_cost"] == opportunity_cost]
                metrics: dict[str, Any] = {"n": len(rows)}
                for name in ("best_revealed_positive_utility", "simple_regret", "net_utility",
                             "cost_adjusted_regret", "budget_saved", "missed_positive_opportunities",
                             "opportunity_recall"):
                    values = [row["final"][name] for row in rows if row["final"][name] is not None]
                    metrics["mean_" + name] = statistics.mean(values) if values else None
                    metrics["sd_" + name] = statistics.stdev(values) if len(values) > 1 else None
                    metrics["n_" + name] = len(values)
                metrics["mean_spent"] = statistics.mean(row["spent"] for row in rows)
                metrics["mean_spent_saved_vs_no_stop"] = statistics.mean(row["paired_no_stop"]["spent_saved"] for row in rows)
                metrics["mean_net_utility_difference_vs_no_stop"] = statistics.mean(row["paired_no_stop"]["net_utility_difference"] for row in rows)
                metrics["model_stop_count"] = sum(row["stop_reason"] == ECONOMIC_STOP for row in rows)
                metrics["model_stop_left_raw_improvement_count"] = sum(row["final"]["model_stop_left_raw_improvement"] for row in rows)
                metrics["economically_premature_stop_count"] = sum(row["final"]["economically_premature_stop"] for row in rows)
                groups[group] = metrics
            summary.append({"opportunity_cost": opportunity_cost, "scenario": scenario, "groups": groups})
    return summary


def protocol_stopping_benchmark(*, seeds: Sequence[int] = (0, 1, 2, 3, 4), budget: float = 6.0,
                                max_trials: int = 16,
                                opportunity_costs: Sequence[float] = OPPORTUNITY_COSTS) -> dict[str, Any]:
    """Report ALL prespecified cost conversions, including unsuccessful stopping decisions.

    This reuses the unchanged synthetic catalog/table generator, not real panel_skill or a
    protocol registry. There is no deployment recommendation. Both gross and cost-adjusted
    research utility are retained so that doing nothing cannot win by concealing missed gain.
    """
    if (not isinstance(seeds, Sequence) or isinstance(seeds, (str, bytes)) or not seeds
            or any(isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32 for seed in seeds)
            or len(set(seeds)) != len(seeds)):
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
    if (not isinstance(opportunity_costs, Sequence) or isinstance(opportunity_costs, (str, bytes))
            or not opportunity_costs):
        raise ValueError("opportunity_costs must be a nonempty sequence of distinct positive finite numbers")
    rates = []
    for rate in opportunity_costs:
        if isinstance(rate, bool) or not isinstance(rate, Real):
            raise ValueError("opportunity_costs must contain distinct positive finite numbers")
        try:
            converted = float(rate)
        except (OverflowError, ValueError) as error:
            raise ValueError("opportunity_costs must contain distinct positive finite numbers") from error
        if not math.isfinite(converted) or converted <= 0 or converted in rates:
            raise ValueError("opportunity_costs must contain distinct positive finite numbers")
        # The frozen catalog has sixteen unit-cost cells. Fail before any queries if
        # even its bounded possible total charge cannot be represented in the report.
        if not math.isfinite(converted * min(amount, float(min(max_trials, 16)))):
            raise ValueError("opportunity_costs overflow the bounded total evaluation charge")
        rates.append(converted)
    runs = []
    for seed in seeds:
        for scenario in SCENARIOS:
            catalog, rewards = _scenario(seed, scenario)
            for opportunity_cost in rates:
                for group in GROUPS:
                    runs.append(_run(catalog, rewards, seed=seed, scenario=scenario, group=group,
                                     opportunity_cost=opportunity_cost, budget=amount, max_trials=max_trials))
    _paired_metrics(runs)
    return {"schema_version": 1, "benchmark": VERSION, "synthetic": True,
            "seeds": list(seeds), "budget": amount, "max_trials": max_trials,
            "opportunity_costs": rates, "groups": list(GROUPS), "scenarios": list(SCENARIOS),
            "cost_unit": "synthetic_query_units", "opportunity_cost_unit": "panel_skill_points_per_synthetic_query_unit",
            "claim": "Synthetic stopping ablation only; no real protocol execution, deployment recommendation, "
                     "calibrated scientific stopping guarantee, global optimality or agent superiority claim.",
            "controls": "Same frozen reward table, catalog, empty initial history, quotes and hard budget per seed/scenario. "
                        "Every explicitly prespecified opportunity cost is reported without tuning or selecting seeds. "
                        "Shared and one-hot features have equal marginal prior variance. Every attempted query is charged.",
            "limitations": ["One-step predictive stopping can miss valuable multi-query information and interaction effects.",
                            "A clipped Gaussian working model is not an independently validated panel response distribution.",
                            "Failure penalization and execution evidence are not simulated; negative table cells are not physical failures.",
                            "The zero outside option means stopping this research search, not the measured score of the seed protocol.",
                            "Premature-stop flags are post-hoc single-query diagnostics, not proofs about an optimal multi-step policy.",
                            "Controller computation time is not charged as synthetic query cost."],
            "accounting_scope": "Only new synthetic table queries are charged, once. Already acquired real "
                                "reference-panel evidence is not represented or made free by this benchmark; "
                                "its sunk acquisition cost remains in the real campaign ledger. no_search makes zero queries.",
            "metric_definitions": {
                "net_utility": "best_revealed_positive_utility - opportunity_cost * total_spent",
                "cost_adjusted_regret": "simple_regret + opportunity_cost * total_spent; oracle reference is initially affordable best reward including zero",
                "budget_saved": "hard budget minus total_spent, also report paired spent_saved vs the identical no-stop acquisition",
                "economically_premature_stop": "Model stopped despite an unqueried still-affordable arm whose realized improvement exceeds opportunity_cost * quote",
                "model_stop_left_raw_improvement": "Model stopped while an unqueried still-affordable arm has higher realized utility, even if its cost may outweigh that gain",
                "research_best_completed_variant": "Highest positive acquired table cell only; not a proposal or deployment recommendation",
                "stop_snapshot": "The exact acquired-data-only economic decision or external budget/trial/no-search guard that ended the run",
            }, "summary": _summary(runs, rates), "runs": runs}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", default="0,1,2,3,4")
    parser.add_argument("--budget", type=float, default=6.0)
    parser.add_argument("--max-trials", type=int, default=16)
    parser.add_argument("--opportunity-costs", default="0.02,0.1,0.3")
    arguments = parser.parse_args(argv)
    try:
        result = protocol_stopping_benchmark(seeds=tuple(int(value.strip()) for value in arguments.seeds.split(",")),
                                             budget=arguments.budget, max_trials=arguments.max_trials,
                                             opportunity_costs=tuple(float(value.strip()) for value in arguments.opportunity_costs.split(",")))
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
