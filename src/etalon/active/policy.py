"""Auditable molecule × endpoint × replicate decisions under a shared budget.

The cost-aware rule values local objective uncertainty reduction and expected improvement,
discounted by an endpoint's observed admission rate. Pairing, exploration and scaffold diversity
are explicit design choices, not hidden claims of acquisition optimality.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from etalon.active.budget import cost_quote, fits_budget
from etalon.active.knowledge import _weighted_normal_hinge
from etalon.active.model import MultiEndpointGP
from etalon.active.schema import CampaignSpec, Candidate, Endpoint, finite


@dataclass(frozen=True)
class Choice:
    candidate_id: str
    endpoint_id: str
    score: float
    evidence: dict[str, Any]


def choose(spec: CampaignSpec, candidates: Mapping[str, Candidate], endpoints: Mapping[str, Endpoint],
           model: MultiEndpointGP, actions: Sequence[dict[str, Any]], observations: Sequence[dict[str, Any]],
           *, remaining: float, slots: int, endpoint_limits: Mapping[str, int] | None = None,
           endpoint_candidates: Mapping[str, set[str]] | None = None) -> list[Choice]:
    finite(remaining, "remaining budget")
    if isinstance(slots, bool) or not isinstance(slots, int) or slots < 0:
        raise ValueError("slots must be a nonnegative integer")
    if slots == 0:
        return []
    limits = endpoint_limits or {}
    allowed = endpoint_candidates or {}
    if spec.policy in {"mf_kg", "decision_aware"}:
        from etalon.active.decision import choose_decision

        return choose_decision(spec, candidates, endpoints, model, actions, observations,
                               remaining=remaining, slots=slots, endpoint_limits=limits, endpoint_candidates=allowed)
    attempts = Counter((a["candidate_id"], a["endpoint_id"]) for a in actions)
    admitted = {(o["result"]["candidate_id"], o["result"]["endpoint_id"]) for o in observations if o["admitted"]}
    high_seen = {key for key, task in admitted if task == spec.objective}
    ids = sorted(candidates)
    mean, sd = model.predict(ids, spec.objective)
    sign = 1 if endpoints[spec.objective].direction == "maximize" else -1
    utility = sign * mean
    best = max((sign * o["result"]["value"] for o in observations
                if o["admitted"] and o["result"]["endpoint_id"] == spec.objective), default=None)
    improvement = sd.copy()
    if best is not None:
        for i in range(len(ids)):
            delta, sigma = float(utility[i] - best), float(sd[i])
            finite(delta, "expected-improvement distance")
            # A physical-unit epsilon changes decisions under unit conversion. The
            # GP already has a standardized positive variance floor. Hinge evaluation
            # also avoids cancelling two nearly equal tail terms.
            improvement[i] = max(delta, 0.0) + _weighted_normal_hinge(abs(delta) / sigma, sigma)
    # RNG depends on recorded attempts, so restarting the process does not restart exploration.
    rng = random.Random(spec.seed + 104729 * len(actions))
    random_ties = {(key, task): rng.random() for key in ids for task in sorted(endpoints)}
    offers: list[Choice] = []
    costs = {task: cost_quote(endpoint, observations) for task, endpoint in endpoints.items()}
    for task, endpoint in sorted(endpoints.items()):
        if limits.get(task, slots) <= 0:
            continue
        cost = costs[task]
        if not fits_budget(cost, remaining):
            continue
        if spec.policy not in {"cost_aware", "cost_only"} and task != spec.objective:
            continue  # Objective-only baselines, same labels, features, QC and total budget.
        seen = [o for o in observations if o["result"]["endpoint_id"] == task
                and o["result"]["status"] != "blocked"]
        validity = (1 + sum(o["admitted"] for o in seen)) / (2 + len(seen))
        if spec.policy == "cost_only":
            validity = 1.0  # Reliability ablation; the QC admission gate is still mandatory.
        reduction = model.objective_reduction(ids, task)
        for i, identifier in enumerate(ids):
            if task in allowed and identifier not in allowed[task]:
                continue
            if attempts[(identifier, task)] >= endpoint.max_replicates:
                continue
            if endpoint.requires_handoff and not candidates[identifier].handoff:
                continue
            explore = rng.random() < spec.explore_fraction
            reason = "cost-aware-information"
            if spec.policy == "random":
                score, reason = random_ties[(identifier, task)], "random-objective"
            elif spec.policy == "greedy":
                score, reason = utility[i], "greedy-objective"
            elif spec.policy == "ucb":
                score, reason = utility[i] + spec.beta * sd[i], "ucb-objective"
            else:
                reduction_sd = math.sqrt(max(float(reduction[i]), 0.0))
                fraction = min(reduction_sd / float(sd[i]), 1.0) ** 2
                score = validity * (improvement[i] * fraction + spec.beta * reduction_sd) / cost
                if explore:
                    score = validity * math.sqrt(max(reduction[i], 0)) / cost
                    reason = "uncertainty-exploration"
                # Before cross-fidelity calibration, deliberately buy paired observations.
                # Bootstrap choice is feature-only; no hidden objective values are inspected.
                if len(high_seen) < spec.bootstrap and task == spec.objective and identifier not in high_seen:
                    score, reason = 1e12 + float(sd[i]), "objective-bootstrap"
                elif (task != spec.objective and identifier in high_seen
                      and (identifier, task) not in admitted and model.pair_counts[task] < spec.bootstrap):
                    score, reason = 1e9 / cost, "paired-calibration"
            if spec.policy in {"greedy", "ucb"} and len(high_seen) < spec.bootstrap:
                if identifier in high_seen:
                    continue
                score, reason = float(sd[i]), "objective-bootstrap"
            finite(float(score), "acquisition score")
            offers.append(Choice(identifier, task, float(score), {
                "policy": spec.policy, "reason": reason, "objective_mean": float(mean[i]),
                "objective_sd": float(sd[i]), "objective_variance_reduction": float(reduction[i]),
                "validity_probability": validity, "quoted_cost": cost,
                "model_hash": model.fingerprint, "tie_break": random_ties[(identifier, task)],
            }))
    chosen: list[Choice] = []
    selected_ids: set[str] = set()
    scaffolds: Counter[str] = Counter()
    by_endpoint: Counter[str] = Counter()
    # Budgeted greedy construction, with a transparent scaffold penalty, one action/molecule/batch.
    while offers and len(chosen) < slots:
        eligible = [o for o in offers if o.candidate_id not in selected_ids
                    and fits_budget(costs[o.endpoint_id], remaining)
                    and by_endpoint[o.endpoint_id] < limits.get(o.endpoint_id, slots)]
        if not eligible:
            break
        def adjusted(o: Choice) -> tuple[float, float]:
            scaffold = candidates[o.candidate_id].scaffold or o.candidate_id
            # Subtraction works even for a negative-valued greedy baseline.
            penalty = scaffolds[scaffold] * max(abs(o.score), 1.0) * 0.25
            return o.score - penalty, o.evidence["tie_break"]
        winner = max(eligible, key=adjusted)
        chosen.append(Choice(winner.candidate_id, winner.endpoint_id, winner.score,
                             {**winner.evidence, "batch_scaffold_penalty": winner.score - adjusted(winner)[0]}))
        selected_ids.add(winner.candidate_id)
        by_endpoint[winner.endpoint_id] += 1
        scaffolds[candidates[winner.candidate_id].scaffold or winner.candidate_id] += 1
        remaining -= costs[winner.endpoint_id]
    return chosen
