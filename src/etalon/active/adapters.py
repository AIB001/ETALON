"""Explicit seams to molecular representations and existing guarded CADD stages."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from etalon.active.schema import Action, Candidate, Endpoint, Evaluation, finite
from etalon.authority.grant import SpendAuthorization, require

MOLECULAR_REPRESENTATION = "molcascade:rdkit9+morgan-count-r2-1024/1"


def molecular_candidates(molecules: Mapping[str, str], *,
                         handoff: Mapping[str, Mapping[str, Any]] | None = None,
                         cheap: Mapping[str, float] | None = None,
                         source: str = "library") -> tuple[list[Candidate], list[str]]:
    """Featurize the full library, not only cascade survivors. Return every rejected id.

    Chemical states must have distinct stable identifiers. No experimental values are read here.
    Callers must use MOLECULAR_REPRESENTATION as the campaign's representation identifier.
    """
    from etalon.boundary.infra import load
    from etalon.learn.calibrate import scaffold_groups
    from etalon.learn.surrogate import featurize

    load("molcascade")
    ids = sorted(molecules)
    if not ids:
        return [], []
    features = featurize([molecules[key] for key in ids])
    aligned = features.align(ids)
    rejected = [ids[i] for i in features.unparsed]
    scaffolds = scaffold_groups([molecules[key] for key in aligned])
    candidates = [Candidate(key, molecules[key], tuple(float(v) for v in row), scaffold=str(scaffold),
                            source=source, handoff=dict((handoff or {}).get(key, {})),
                            cheap_value=(cheap or {}).get(key))
                  for key, row, scaffold in zip(aligned, features.matrix, scaffolds, strict=True)]
    return candidates, rejected


class StageExecutor:
    """Bridge an existing ExpensiveStage to the active loop, never inventing absent labels.

    Costs are charged at the declared quote (recorded as such), unless a caller supplies a real
    cost reader in the campaign's cost_unit. Wall time is NOT silently called GPU time. The legacy
    PrismStage reuses one run per molecule and is therefore forbidden for independent replicas;
    an action-scoped factory is required for replicas and must use action.id for its run directory.
    """

    def __init__(self, factories: Mapping[str, Callable[[Action], Any]], *,
                 actual_cost: Callable[[Action, Sequence[Any]], float] | None = None) -> None:
        self.factories = dict(factories)
        self.actual_cost = actual_cost

    def __call__(self, action: Action, candidate: Candidate, endpoint: Endpoint,
                 grant: SpendAuthorization | None) -> Evaluation:
        if (not isinstance(action.id, str) or not action.id.strip() or action.id in {".", ".."}
                or any(char in action.id for char in ("/", "\\", "\x00"))):
            raise ValueError("action id must identify one nonempty workspace path component")
        if (action.candidate_id != candidate.id or action.endpoint_id != endpoint.id
                or type(action.round_id) is not int or action.round_id < 1):
            raise ValueError("action candidate, endpoint and live round must match the requested execution")
        if (type(action.replicate) is not int
                or not 0 <= action.replicate < endpoint.max_replicates):
            raise ValueError("action replicate must be within the endpoint's declared replicate limit")
        if not endpoint.requires_handoff or grant is None:
            raise ValueError("StageExecutor requires a handoff endpoint and its authorization")
        if (candidate.handoff.get("parent_id") != candidate.id
                or candidate.handoff.get("parent_smiles") != candidate.smiles):
            raise ValueError("candidate identity and handoff disagree")
        require(candidate.handoff, {candidate.id: grant})

        from etalon.campaign.expensive import PrismStage

        stage = self.factories[endpoint.id](action)
        if isinstance(stage, PrismStage) and endpoint.max_replicates > 1:
            raise ValueError("legacy PrismStage cannot certify independent replicas; use an action-scoped executor")
        # Factory setup may take time or mutate the handoff; recheck at the actual dispatch.
        require(candidate.handoff, {candidate.id: grant})
        rows = list(stage([candidate.handoff], {candidate.id: candidate.cheap_value}
                          if candidate.cheap_value is not None else {}, {candidate.id: grant}))
        cost = self.actual_cost(action, rows) if self.actual_cost is not None else endpoint.cost
        finite(cost, "actual cost", minimum=0.0)
        cost = float(cost)
        if len(rows) != 1 or rows[0].parent_id != candidate.id:
            return Evaluation(candidate.id, endpoint.id, None, endpoint.units, cost, status="invalid",
                              provenance={"error": "stage returned missing, duplicate or mismatched labels"})
        result = rows[0]
        return Evaluation(candidate.id, endpoint.id, result.expensive_value, result.units, cost,
                          status="ok" if result.expensive_value is not None else "failed",
                          checks=result.observations, provenance={**result.provenance,
                              "cost_basis": "measured" if self.actual_cost is not None else "endpoint quote; not measured"})
