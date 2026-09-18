"""Global decision value, bounded protocol scouting, and a confirmation budget guard.

KG is established prior art, not an ETALON invention. The guard/scouting are explicit
heuristics, not a finite-budget optimality guarantee. One action is returned per round:
the next choice must see its real outcome, not an invented independent batch fantasy.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from etalon.active.budget import cost_quote, fits_budget
from etalon.active.knowledge import query_knowledge_gradient
from etalon.active.model import MultiEndpointGP
from etalon.active.policy import Choice
from etalon.active.reliability import admission_estimate
from etalon.active.schema import CampaignSpec, Candidate, Endpoint, finite

VERSION = "evidence-decision/3"


def choose_decision(spec: CampaignSpec, candidates: Mapping[str, Candidate],
                    endpoints: Mapping[str, Endpoint], model: MultiEndpointGP,
                    actions: Sequence[dict[str, Any]], observations: Sequence[dict[str, Any]],
                    *, remaining: float, slots: int, endpoint_limits: Mapping[str, int] | None = None,
                    endpoint_candidates: Mapping[str, set[str]] | None = None) -> list[Choice]:
    import numpy as np

    finite(remaining, "remaining budget")
    if isinstance(slots, bool) or not isinstance(slots, int) or slots < 0:
        raise ValueError("slots must be a nonnegative integer")
    if slots == 0:
        return []
    limits = endpoint_limits or {}
    allowed = endpoint_candidates or {}
    ids = sorted(candidates)
    if len(ids) > spec.max_kg_candidates:
        raise ValueError(f"exact global KG pilot limit is {spec.max_kg_candidates} candidates; "
                         "set max_kg_candidates explicitly or use a scalable acquisition backend")
    attempts = Counter((a["candidate_id"], a["endpoint_id"]) for a in actions)
    admitted = {(o["result"]["candidate_id"], o["result"]["endpoint_id"])
                for o in observations if o["admitted"]}
    high_seen = {key for key, task in admitted if task == spec.objective}
    costs = {task: cost_quote(endpoint, observations) for task, endpoint in endpoints.items()}
    available = {task: [key for key in ids
                       if attempts[(key, task)] < endpoint.max_replicates
                       and (task not in allowed or key in allowed[task])
                       and (not endpoint.requires_handoff or candidates[key].handoff)]
                 for task, endpoint in endpoints.items()
                 if fits_budget(costs[task], remaining) and limits.get(task, 1) > 0}
    objective = endpoints[spec.objective]
    high_cost = costs[spec.objective]
    guarded = spec.policy == "decision_aware" and spec.confirmation_reserve > 0
    if guarded and not available.get(spec.objective):
        # Buying more proxy labels cannot create an actionable final confirmation.
        return []
    # The evidence-aware terminal set excludes predictions that can neither be supported by
    # existing objective evidence nor receive another objective evaluation. Such molecules
    # remain valid *queries* if they inform attainable decisions elsewhere in feature space.
    decision_ids = ([key for key in ids if key in high_seen or key in available.get(spec.objective, [])]
                    if spec.policy == "decision_aware" else ids)
    if not decision_ids:
        return []
    reserve = min(spec.confirmation_reserve, len(available.get(spec.objective, []))) * high_cost if guarded else 0.0
    finite(reserve, "confirmation budget guard", minimum=0)
    mean, sd = model.predict(ids, spec.objective)
    sign = 1 if objective.direction == "maximize" else -1
    utility = sign * mean
    index = {key: i for i, key in enumerate(ids)}
    mode = spec.validity_mode if spec.policy == "decision_aware" else "none"
    probabilities, support = {}, {}
    for task in available:
        probabilities[task], support[task] = admission_estimate(model, ids, task, observations, mode=mode)
    calibration_spent = sum(a["cost"] for a in actions
                            if a.get("decision", {}).get("reason") == "protocol-calibration")
    calibration_cap = spec.budget * spec.calibration_fraction

    def leaves_confirmation(cost: float) -> bool:
        combined = cost + reserve
        return math.isfinite(combined) and fits_budget(combined, remaining)

    def choice(key: str, task: str, score: float, reason: str, kg: float | None = None) -> Choice:
        finite(float(score), "decision score")
        i = index[key]
        return Choice(key, task, float(score), {
            "policy": spec.policy, "policy_version": VERSION, "reason": reason,
            "objective_mean": float(mean[i]), "objective_sd": float(sd[i]),
            "knowledge_gradient": kg, "decision_pool_size": len(decision_ids),
            "excluded_unconfirmable_decisions": len(ids) - len(decision_ids),
            "validity_probability": float(probabilities[task][i]),
            "validity_mode": mode, "validity_support_mass": float(support[task][i]),
            "quoted_cost": costs[task], "confirmation_budget_guard": reserve,
            "calibration_spent": calibration_spent, "calibration_budget_cap": calibration_cap,
            "model_hash": model.fingerprint, "batch_mode": "sequential-real-feedback",
            "requested_batch_size": slots,
            "protocol_query_limit": limits.get(task),
        })

    # No claim of guaranteed successful confirmation: actual cost can overrun the quote and
    # scientific QC can reject the result. At the end buy the best actionable prediction.
    terminal_cost = reserve + high_cost
    if guarded and (not math.isfinite(terminal_cost) or fits_budget(remaining, terminal_cost)):
        key = max(available[spec.objective], key=lambda key: (utility[index[key]], sd[index[key]], key))
        return [choice(key, spec.objective, utility[index[key]], "terminal-confirmation")]

    if len(high_seen) < spec.bootstrap and available.get(spec.objective):
        unseen = [key for key in available[spec.objective] if key not in high_seen]
        if unseen:
            key = max(unseen, key=lambda key: (sd[index[key]], key))
            return [choice(key, spec.objective, sd[index[key]], "objective-bootstrap")]

    # Unknown task correlation is zero in this empirical-Bayes model. Its discovery therefore
    # requires a separately disclosed design budget, not a fabricated huge KG value.
    scouts = []
    for task, keys in sorted(available.items()):
        if (task == spec.objective or model.pair_counts[task] >= max(3, spec.bootstrap)
                or not fits_budget(calibration_spent + costs[task], calibration_cap)
                or not leaves_confirmation(costs[task])):
            continue
        for key in keys:
            if key in high_seen and (key, task) not in admitted:
                scouts.append(choice(key, task, probabilities[task][index[key]] / costs[task],
                                     "protocol-calibration"))
    if scouts:
        return [max(scouts, key=lambda c: (c.score, -model.pair_counts[c.endpoint_id], c.endpoint_id, c.candidate_id))]

    offers = []
    for task, keys in sorted(available.items()):
        if task != spec.objective and not leaves_confirmation(costs[task]):
            continue
        if not keys:
            continue
        values = query_knowledge_gradient(model, decision_ids, keys, task)
        if np.any(~np.isfinite(values)):
            raise ValueError("nonfinite global knowledge gradient")
        for key, kg in zip(keys, values, strict=True):
            p = float(probabilities[task][index[key]])
            offers.append(choice(key, task, p * float(kg) / costs[task], "global-knowledge-gradient", float(kg)))
    # This has physical utility/cost units. An absolute epsilon would change decisions merely
    # by expressing the same endpoint in a smaller unit. The KG kernel is already nonnegative.
    positive = [offer for offer in offers if offer.score > 0.0]
    if positive:
        return [max(positive, key=lambda c: (c.score, c.endpoint_id == spec.objective, c.endpoint_id, c.candidate_id))]
    if available.get(spec.objective):
        # Do not spend on an independent proxy just because all KG values tie at zero.
        key = max(available[spec.objective], key=lambda key: (utility[index[key]], sd[index[key]], key))
        return [choice(key, spec.objective, utility[index[key]], "objective-fallback", 0.0)]
    return []
