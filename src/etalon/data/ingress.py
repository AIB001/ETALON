"""Evidence admission and geometry attachment at explicit campaign boundaries."""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from etalon.data.artifacts import digest, read_snapshot

_MOLAR = {"M": "1", "mM": "0.001", "uM": "0.000001", "µM": "0.000001",
          "μM": "0.000001", "nM": "0.000000001", "pM": "0.000000000001"}


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} requires nonempty review text")
    return value


def review_template(library: Path, *, endpoint_id: str, protocol: str) -> dict[str, Any]:
    snapshot = read_snapshot(library)
    root = Path(snapshot["snapshot"])
    if snapshot["kind"] != "library" or "inventory.json" not in snapshot["files"]:
        raise ValueError("assay review requires a library derived from a collected target dossier")
    inventory = json.loads((root / "inventory.json").read_text())
    return {"schema": "etalon-assay-review/1", "library_snapshot_id": snapshot["snapshot_id"],
            "reviewer": "", "reviewed_at": "", "endpoint_id": endpoint_id, "protocol": protocol,
            "target": {"accession": "", "taxon": 9606, "construct": ""},
            "observations": [{"measurement_index": index, "decision": "withhold",
                              "experiment_id": "", "rationale": "Not reviewed; excluded from training."}
                             for index in range(len(inventory["measurements"]))]}


def import_assays(store: Any, library: Path, review: dict[str, Any]) -> dict[str, Any]:
    """Import only explicitly reviewed, uncensored scalar assays at a compatible endpoint.

    Raw inventory rows remain available, including all withheld bounds and conflicts.
    Review text is an accountable assertion, not machine proof of assay comparability.
    No historical observation can qualify a protocol's runtime audit panel.
    """
    from etalon.active.schema import Evaluation

    snapshot = read_snapshot(library)
    root = Path(snapshot["snapshot"])
    if snapshot["kind"] != "library" or "inventory.json" not in snapshot["files"]:
        raise ValueError("assays require the sealed MolQuarry target inventory")
    allowed = {"schema", "library_snapshot_id", "reviewer", "reviewed_at", "endpoint_id",
               "protocol", "target", "observations"}
    if not isinstance(review, dict) or set(review) != allowed or review["schema"] != "etalon-assay-review/1":
        raise ValueError("assay review does not match etalon-assay-review/1")
    if review["library_snapshot_id"] != snapshot["snapshot_id"]:
        raise ValueError("review refers to a different library snapshot")
    for key in ("reviewer", "reviewed_at", "endpoint_id", "protocol"):
        _text(review[key], key)
    target = review["target"]
    if not isinstance(target, dict) or set(target) != {"accession", "taxon", "construct"}:
        raise ValueError("target review needs accession, taxon and construct")
    _text(target["accession"], "target accession")
    _text(target["construct"], "target construct")
    if type(target["taxon"]) is not int or target["taxon"] < 1:
        raise ValueError("taxon must be a positive integer")
    _, endpoints = store.configuration()
    endpoint = endpoints.get(review["endpoint_id"])
    if (endpoint is None or endpoint.queryable or endpoint.requires_handoff
            or endpoint.target != target["accession"] or endpoint.protocol != review["protocol"]):
        raise ValueError("review requires the exact target/protocol of a historical-only, non-handoff endpoint")
    targets = {row["input"]: row for row in json.loads((root / "targets.json").read_text())}
    inventory = json.loads((root / "inventory.json").read_text())
    identities = json.loads((root / "identity-map.json").read_text())
    by_compound = {row["source_record_id"]: row for row in identities if row["status"] == "registered"}
    candidates = store.candidates()
    accepted, withheld, seen = [], [], set()
    if not isinstance(review["observations"], list):
        raise ValueError("review observations must be a list")
    for decision in review["observations"]:
        fields = {"measurement_index", "decision", "experiment_id", "rationale",
                  "state_equivalence_review", "duplicate_review", "validity_review"}
        if not isinstance(decision, dict) or set(decision) - fields:
            raise ValueError("unknown assay decision fields")
        index = decision.get("measurement_index")
        if type(index) is not int or not 0 <= index < len(inventory["measurements"]) or index in seen:
            raise ValueError("measurement indices must exist and occur only once per review")
        seen.add(index)
        _text(decision.get("rationale"), "assay rationale")
        if decision.get("decision") == "withhold":
            withheld.append(index)
            continue
        if decision.get("decision") != "accept":
            raise ValueError("assay decision must be accept or withhold")
        experiment = _text(decision.get("experiment_id"), "canonical experiment id")
        row = inventory["measurements"][index]
        resolved = targets.get(row.get("target"), {})
        if resolved.get("accession") != target["accession"] or resolved.get("taxon") != target["taxon"]:
            raise ValueError("assay does not map to the reviewed accession and taxon")
        identity = by_compound.get(row.get("etalon_source_record_id", row["compound_id"]))
        if not identity or identity["parent_id"] not in candidates:
            raise ValueError("assay compound has no registered campaign chemical state")
        candidate = candidates[identity["parent_id"]]
        if candidate.smiles != identity["parent_smiles"]:
            raise ValueError("assay identity differs from the campaign chemical state")
        if identity["chemical_state_changed"]:
            _text(decision.get("state_equivalence_review"), "changed chemical state")
        if row.get("possible_cross_source_overlap") or row.get("potential_duplicate") in (True, 1, "1"):
            _text(decision.get("duplicate_review"), "possible duplicate experiment")
        if row.get("data_validity_comment"):
            _text(decision.get("validity_review"), "source data validity flag")
        if row.get("relation") != "=":
            raise ValueError("censored or approximate assays cannot become exact GP labels")
        unit = row.get("unit")
        if unit not in _MOLAR or row.get("endpoint") not in {"Kd", "Ki", "IC50", "EC50"}:
            raise ValueError("assay quantity or concentration unit is unsupported")
        try:
            molar = Decimal(str(row["value"])) * Decimal(_MOLAR[unit])
            if not molar.is_finite() or molar <= 0:
                raise ValueError("assay concentration must be finite and positive")
            if endpoint.quantity == row["endpoint"] and endpoint.units in _MOLAR:
                value = float(molar / Decimal(_MOLAR[endpoint.units]))
                if value <= 0:
                    raise ValueError("converted assay concentration underflows the model's positive numeric range")
                conversion = f"{unit} to {endpoint.units}"
            elif endpoint.quantity == "p" + row["endpoint"] and endpoint.units == "-log10(M)":
                value = float(-molar.log10())
                conversion = f"-log10({row['endpoint']} in M)"
            else:
                raise ValueError("endpoint quantity/units do not match this assay; IC50, Ki and Kd are distinct")
        except InvalidOperation as error:
            raise ValueError("invalid numeric assay value") from error
        if not math.isfinite(value):
            raise ValueError("converted assay exceeds finite model range")
        result = Evaluation(candidate.id, endpoint.id, value, endpoint.units, 0.0, provenance={
            "mode": "reviewed_external_assay", "library_snapshot_id": snapshot["snapshot_id"],
            "review_sha256": digest(review), "reviewer": review["reviewer"],
            "reviewed_at": review["reviewed_at"], "target": target, "decision": decision,
            "measurement_index": index, "measurement": row, "identity": identity,
            "conversion": conversion, "cost_basis": "historical evidence; acquisition HTTP usage is separate"})
        accepted.append((experiment, result))
    provenance = {"sha256": digest(review), "library_snapshot_id": snapshot["snapshot_id"],
                  "review": review, "target": target}
    added = store.import_reviewed_evaluations(accepted, review=provenance)
    return {"added": added, "accepted": len(accepted), "explicitly_withheld": withheld,
            "unreviewed": sorted(set(range(len(inventory["measurements"]))) - seen),
            "review_sha256": provenance["sha256"]}


def attach_handoffs(store: Any, workspace: Path, artifact_id: str, *, rationale: str,
                    candidate_ids: list[str] | None = None) -> dict[str, Any]:
    """Attach a verified MolCascade contract, preserving all subsequent PRISM preflight checks."""
    from etalon.boundary.screen import Screen

    screen = Screen(workspace)
    rows = screen.read(artifact_id, contract_id="md_system_input/v1")
    if candidate_ids is not None:
        selected = set(candidate_ids)
        rows = [row for row in rows if row["parent_id"] in selected]
        if {row["parent_id"] for row in rows} != selected:
            raise ValueError("requested candidates are missing from this handoff artifact")
    if not rows:
        raise ValueError("handoff artifact has no selected rows")
    added = store.bind_handoffs(rows, source={"workspace": str(Path(workspace).resolve()),
                               "artifact_id": artifact_id, "contract": "md_system_input/v1",
                               "infrastructure": screen.infra.provenance()}, rationale=rationale)
    return {"bound": added, "records": len(rows), "artifact_id": artifact_id,
            "authorization": "not granted; receptor-bound preflight remains mandatory at execution"}
