"""Separate a model recommendation from a candidate with acquired objective evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from etalon.active.budget import cost_quote, fits_budget
from etalon.active.model import MultiEndpointGP
from etalon.active.schema import CampaignSpec, Candidate, Endpoint, finite


def recommend(spec: CampaignSpec, candidates: Mapping[str, Candidate], endpoints: Mapping[str, Endpoint],
              model: MultiEndpointGP | None, actions: Sequence[dict[str, Any]],
              observations: Sequence[dict[str, Any]], *, remaining: float,
              endpoint_limits: Mapping[str, int] | None = None,
              endpoint_candidates: Mapping[str, set[str]] | None = None) -> dict[str, Any]:
    finite(remaining, "remaining budget")
    endpoint = endpoints[spec.objective]
    limits = endpoint_limits or {}
    allowed = endpoint_candidates or {}
    quote = cost_quote(endpoint, observations)
    attempts = Counter((a["candidate_id"], a["endpoint_id"]) for a in actions)
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        if row["admitted"] and row["result"]["endpoint_id"] == spec.objective:
            evidence[row["result"]["candidate_id"]].append(row)
    pending = [a["id"] for a in actions if a["status"] in {"reserved", "running"}]
    base = {"objective": endpoint.as_dict(), "model": model.snapshot() if model else None,
            "pending_actions": pending, "remaining_budget": remaining,
            "provisional": None, "attainable": None, "evidence_backed": None,
            "interpretation": "Evidence-backed means admitted observations at the declared objective protocol, "
                              "NOT experimentally validated activity, a noise-free optimum, or calibrated confidence. "
                              "All recommendations rank posterior means; raw minimum noisy labels are not the criterion."}
    if model is None:
        return base
    ids = sorted(candidates)
    mean, sd = model.predict(ids, spec.objective)
    sign = 1 if endpoint.direction == "maximize" else -1
    index = {key: i for i, key in enumerate(ids)}

    def row(key: str) -> dict[str, Any]:
        records = evidence[key]
        eligible = (endpoint.queryable and attempts[(key, spec.objective)] < endpoint.max_replicates
                    and limits.get(spec.objective, 1) > 0
                    and (spec.objective not in allowed or key in allowed[spec.objective])
                    and (not endpoint.requires_handoff or bool(candidates[key].handoff)))
        return {"candidate_id": key, "posterior_mean": float(mean[index[key]]),
                "posterior_sd": float(sd[index[key]]), "units": endpoint.units,
                "endpoint_id": endpoint.id, "protocol": endpoint.protocol,
                "has_objective_evidence": bool(records),
                "evidence_action_ids": [r["action_id"] for r in records],
                "observed_values": [r["result"]["value"] for r in records],
                "confirmation_eligible": eligible,
                "confirmation_affordable": bool(eligible and not pending and fits_budget(quote, remaining)),
                "confirmation_quoted_cost": quote}

    best = max(ids, key=lambda key: (sign * mean[index[key]], key))
    confirmed = [key for key in ids if evidence[key]]
    base["provisional"] = row(best)
    attainable = [key for key in ids if evidence[key] or row(key)["confirmation_affordable"]]
    if attainable:
        base["attainable"] = row(max(attainable, key=lambda key: (sign * mean[index[key]], key)))
    base["recommendation_scope"] = {
        "provisional": "unrestricted model prediction, including currently unconfirmable candidates",
        "attainable": "has objective evidence OR can afford and is eligible for objective confirmation; "
                      "preflight and scientific validity are not guaranteed",
        "evidence_backed": "already has admitted objective evidence",
    }
    if confirmed:
        base["evidence_backed"] = row(max(confirmed, key=lambda key: (sign * mean[index[key]], key)))
    base["training_action_ids"] = model.training_action_ids
    return base
