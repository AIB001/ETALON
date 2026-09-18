"""Sealed-oracle offline replay. Only requested outcomes enter the learner's journal.

Replay verifies engineering behavior, not physical accuracy or chemical novelty. A real offline
study needs measured, protocol-matched oracle tables and an independent evaluation split. This
module never launches docking, MD or a language model and does not call replay cost GPU-hours.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from etalon.active.runner import ActiveCampaign
from etalon.active.schema import Action, CampaignSpec, Candidate, Endpoint, Evaluation, digest
from etalon.active.store import CampaignStore
from etalon.authority.grant import SpendAuthorization
from etalon.faults.attribution import Observation
from etalon.learn.admissible import Measurement, rule


class ReplayExecutor:
    def __init__(self, records: Sequence[dict[str, Any]]) -> None:
        self._answers: dict[tuple[str, str, int], Evaluation] = {}
        for record in records:
            body = dict(record)
            replicate = body.pop("replicate", 0)
            if type(replicate) is not int or replicate < 0:
                raise ValueError("oracle replicate must be a nonnegative integer")
            result = Evaluation.from_dict(body)
            key = result.candidate_id, result.endpoint_id, replicate
            if key in self._answers:
                raise ValueError(f"duplicate oracle result: {key}")
            self._answers[key] = result

    def __call__(self, action: Action, candidate: Candidate, endpoint: Endpoint,
                 grant: SpendAuthorization | None) -> Evaluation:
        if endpoint.requires_handoff or grant is not None:
            raise ValueError("offline replay must use explicitly unguarded offline endpoints")
        key = candidate.id, endpoint.id, action.replicate
        if key not in self._answers:
            raise ValueError(f"oracle has no result for {key}")
        result = self._answers[key]
        return replace(result, provenance={**result.provenance, "mode": "offline_replay"})


def from_manifest(manifest: Mapping[str, Any], database: str | Path) -> ActiveCampaign:
    """Validate the entire replay table before any action is reserved or queried."""
    if (not isinstance(manifest, Mapping) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1):
        raise ValueError("expected replay schema_version 1")
    spec = CampaignSpec(**manifest["spec"])
    endpoints = [Endpoint(**e) for e in manifest["endpoints"]]
    candidates = [Candidate.from_dict(c) for c in manifest["candidates"]]
    if len({c.id for c in candidates}) != len(candidates):
        raise ValueError("candidate ids must be unique in the replay pool")
    by_id = {e.id: e for e in endpoints}
    ids = {c.id for c in candidates}
    if len(by_id) != len(endpoints) or spec.objective not in by_id:
        raise ValueError("endpoint ids must be unique and include the objective")
    if len({e.target for e in endpoints}) != 1:
        raise ValueError("replay endpoints must measure one campaign target")
    if len({len(c.features) for c in candidates}) > 1:
        raise ValueError("replay candidate representation widths must match")
    if len(candidates) > spec.max_candidates:
        raise ValueError("replay candidate pool exceeds the declared controller limit")
    if spec.policy in {"mf_kg", "decision_aware"} and len(candidates) > spec.max_kg_candidates:
        raise ValueError("replay pool exceeds max_kg_candidates for the requested exact-KG policy")
    if any(e.requires_handoff for e in endpoints):
        raise ValueError("replay endpoints must explicitly set requires_handoff=false")
    executor = ReplayExecutor(manifest["oracle"])
    for (identifier, task, replicate), result in executor._answers.items():
        if identifier not in ids or task not in by_id or result.units != by_id[task].units:
            raise ValueError("oracle identity or units do not match the registered pool/endpoints")
        if replicate >= by_id[task].max_replicates:
            raise ValueError("oracle replicate lies outside the endpoint's declared repeat policy")
    for candidate in candidates:
        for endpoint in endpoints:
            for replicate in range(endpoint.max_replicates):
                if (candidate.id, endpoint.id, replicate) not in executor._answers:
                    raise ValueError(f"incomplete oracle table for {candidate.id}/{endpoint.id}/{replicate}")
    initial_rows = manifest.get("initial", [])
    if any(not isinstance(item.get("source_id"), str) or not item["source_id"].strip() for item in initial_rows):
        raise ValueError("every initial observation needs a stable source_id")
    initial = [Evaluation.from_dict(item["result"]) for item in initial_rows]
    keys = [(item["source_id"], result.candidate_id, result.endpoint_id)
            for item, result in zip(initial_rows, initial, strict=True)]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate initial source identities")
    for result in initial:
        if result.candidate_id not in ids or result.endpoint_id not in by_id:
            raise ValueError("initial label does not identify a registered molecule/endpoint")
        if result.units != by_id[result.endpoint_id].units:
            raise ValueError("initial label units differ from endpoint")
    # Validate QC vocabulary across the whole sealed table, without admitting or
    # exposing any unqueried label to the learner. Unknown checks would otherwise
    # fail only after a queried result, leaving a partly initialized campaign.
    for result in [*executor._answers.values(), *initial]:
        try:
            rule(Measurement(result.candidate_id, None, result.value, result.checks,
                             result.units, result.provenance), require_comparator=False)
        except KeyError as error:
            raise ValueError(f"replay has an unknown QC observation: {error}") from error
    # Validate the complete immutable identity before creating a database. A malformed
    # ancillary field must not leave a partially configured, unresumable journal.
    manifest_hash = digest(manifest)
    store = CampaignStore(database)
    store.configure(spec, endpoints)
    store.bind_resource("replay_manifest", {"sha256": manifest_hash})
    store.add_candidates(candidates)
    for item, result in zip(initial_rows, initial, strict=True):
        store.import_evaluation(result, source_id=item["source_id"])
    return ActiveCampaign(store, executor)


def synthetic_manifest(*, seed: int = 7, size: int = 64, budget: float = 120.0,
                       policy: str = "cost_aware", batch_size: int = 4) -> dict[str, Any]:
    """A deterministic two-fidelity toy, including flagged low-fidelity corruption.

    The descriptors/labels are synthetic, not calculated from the displayed SMILES. The same
    charged feature-only warm start is used by every policy. This is a smoke benchmark ONLY.
    """
    if type(size) is not int or size < 8:
        raise ValueError("synthetic pool needs an integer size of at least 8 candidates")
    spec = CampaignSpec("reference", budget, "synthetic_cost_units", "synthetic-3d/1",
                        batch_size=batch_size, seed=seed, policy=policy)
    if size > spec.max_candidates:
        raise ValueError("synthetic pool exceeds the declared controller limit")
    if policy in {"mf_kg", "decision_aware"} and size > spec.max_kg_candidates:
        raise ValueError("synthetic pool exceeds max_kg_candidates for the requested exact-KG policy")
    endpoints = [Endpoint("proxy", "toy", "synthetic_proxy", "arb", "synthetic-proxy/1", 1.0,
                          requires_handoff=False, noise=0.25, prior_scale=3.0),
                 Endpoint("reference", "toy", "synthetic_reference", "arb", "synthetic-reference/1", 8.0,
                          requires_handoff=False, noise=0.05, prior_scale=3.0)]
    rng = random.Random(seed)
    candidates, oracle = [], []
    for i in range(size):
        x = tuple(rng.uniform(-2, 2) for _ in range(3))
        candidate = Candidate(f"toy-{i:04d}", "C" * (1 + i % 6) + ("O" if i % 2 else "N"), x,
                              scaffold=f"synthetic-family-{i % 8}", source="synthetic; features unrelated to SMILES")
        candidates.append(candidate.as_dict())
        truth = -3 * math.exp(-sum((v - t)**2 for v, t in zip(x, (0.6, -0.4, 0.8), strict=True))) + 0.4 * sum(x)
        proxy = 1.8 * truth + 1.0 + 0.3 * math.sin(2 * x[0])
        # Every fifth cheap result is flagged as invalid (including a misleading attractive value).
        # Both policies with and without a reliability discount still obey this same QC gate.
        checks = (Observation("F_BUILD_INCOMPLETE", True, "synthetic quality failure"),) if i % 5 == 0 else ()
        oracle.extend([Evaluation(candidate.id, "proxy", -20.0 if checks else proxy, "arb", 1.0,
                                  checks=checks, provenance={"dataset": "synthetic-v1"}).as_dict(),
                       Evaluation(candidate.id, "reference", truth, "arb", 8.0,
                                  provenance={"dataset": "synthetic-v1"}).as_dict()])
    warm_ids = set(rng.sample([c["id"] for c in candidates], spec.bootstrap))
    initial = [{"source_id": f"synthetic-warmstart/{seed}/{r['candidate_id']}", "result": r}
               for r in oracle if r["candidate_id"] in warm_ids and r["endpoint_id"] == "reference"]
    if budget < spec.bootstrap * endpoints[1].cost:
        raise ValueError("budget cannot cover the common charged warm start")
    return {"schema_version": 1, "description": "Synthetic engineering smoke test; no physical CADD results",
            "spec": spec.as_dict(), "endpoints": [e.as_dict() for e in endpoints],
            "candidates": candidates, "oracle": oracle, "initial": initial}
