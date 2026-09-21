"""Register frozen database structures with the same identity policy as MolCascade."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from etalon.boundary.infra import load
from etalon.data.artifacts import digest, new_run, read_snapshot, seal, write_json


def _field(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _inventory_records(dossier: dict[str, Any], inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """Recover each source structure before upstream full-InChIKey inventory coalescing.

    Standard InChI may group distinct tautomers (or omit enhanced stereo layers). Keep
    measurements tied to their own source SMILES, then let MolCascade's declared policy
    decide which states coincide. The upstream inventory remains intact apart from an
    explicit per-measurement join key added for ETALON.
    """
    from rdkit import Chem
    from rdkit.Chem import RegistrationHash

    molecules = {row["molecule_chembl_id"]: row for row in dossier["chembl_details"]["molecule"]}
    compounds = {row["compound_id"]: row for row in inventory["compounds"]}
    records: dict[str, dict[str, Any]] = {}
    params = Chem.SmilesWriteParams()
    params.canonical = params.doIsomericSmiles = True

    def state(smiles: str) -> str | None:
        molecule = Chem.MolFromSmiles(smiles)
        return Chem.MolToCXSmiles(molecule, params, RegistrationHash.DEFAULT_CXFLAG) if molecule else None

    for index, measurement in enumerate(inventory["measurements"]):
        source_id = str(measurement["source_compound_id"])
        raw = measurement["raw"]
        raw_smiles = raw.get("canonical_smiles") if measurement["source"] == "chembl" else raw.get("smile")
        detail_smiles = ((molecules.get(source_id, {}).get("molecule_structures") or {}).get("canonical_smiles")
                         if measurement["source"] == "chembl" else None)
        smiles = detail_smiles or raw_smiles
        conflict = bool(raw_smiles and detail_smiles and state(raw_smiles) != state(detail_smiles))
        origin = measurement["source"] + ":" + source_id
        key = origin + ":structure:" + digest([smiles, raw_smiles, conflict])[:24]
        measurement["etalon_source_record_id"] = key
        record = records.setdefault(key, {
            "source_record_id": key, "source_ids": [origin], "smiles": smiles,
            "molquarry_compound_id": measurement["compound_id"],
            "source": measurement["source"], "source_compound_id": source_id,
            "activity_smiles": raw_smiles, "detail_smiles": detail_smiles,
            "source_structure_conflict": conflict,
            "chemical_status": "identity_conflict" if conflict else compounds[measurement["compound_id"]]["chemical_status"],
            "measurement_indices": [],
        })
        record["measurement_indices"].append(index)
    return list(records.values())


def prepare_library(snapshot: Path, workspace: Path, *, run_id: str,
                    id_field: str | None = None, smiles_field: str | None = None,
                    identity_policy: dict[str, Any] | None = None,
                    allow_partial: bool = False) -> dict[str, Any]:
    """Keep every source row and transformation; export one row per registered parent.

    No potency, commercial availability or coordinate suitability is inferred here.
    Query records need explicit dotted field mappings; target dossiers use MolQuarry's
    evidence inventory, retaining its measurements for a separate review/import step.
    """
    if type(allow_partial) is not bool:
        raise ValueError("allow_partial must be an explicit boolean")
    original = read_snapshot(snapshot)
    if original["kind"] not in {"query", "collect", "import_catalog", "search_catalog"}:
        raise ValueError("this snapshot does not contain a candidate library")
    coverage = original["result"].get("status")
    if coverage not in {"complete", "collected_requires_review"} and not allow_partial:
        raise ValueError("source coverage is incomplete; explicitly allow_partial or acquire the missing evidence")
    source = Path(original["snapshot"])
    inventory = None
    targets = None
    if original["kind"] == "collect":
        load("molquarry")
        from molquarry.workflows.ligands import ligand_inventory

        dossier = json.loads((source / "payload/dossier.json").read_text())
        inventory = ligand_inventory(dossier)
        targets = dossier["targets"]
        records = _inventory_records(dossier, inventory)
        id_field, smiles_field = "source_record_id", "smiles"
    else:
        records = json.loads((source / "payload/records.json").read_text())
        if not id_field or not smiles_field:
            raise ValueError("query/catalog libraries require explicit id_field and smiles_field mappings")
    infra = load("molcascade")
    from molcascade.chemistry.identity import (
        StandardizationFailure,
        rdkit_identity_metadata,
        standardize_parent,
    )
    from molcascade.chemistry.policies import IdentityPolicy
    from rdkit import Chem
    from rdkit.Chem import RegistrationHash

    if identity_policy is not None and not isinstance(identity_policy, dict):
        raise ValueError("identity_policy must be a mapping")
    policy = IdentityPolicy.model_validate({"schema_version": 1, "tautomer_policy": "preserve",
                                            **(identity_policy or {})})
    registered: dict[str, str] = {}
    mappings = []
    smiles_parameters = Chem.SmilesWriteParams()
    smiles_parameters.canonical = True
    smiles_parameters.doIsomericSmiles = True
    for index, record in enumerate(records):
        source_id = _field(record, id_field)
        smiles = _field(record, smiles_field)
        row = {"record_index": index, "source_record_id": source_id,
               "source_ids": record.get("source_ids", []), "raw_smiles": smiles}
        mappings.append(row)
        if (not isinstance(source_id, (str, int)) or isinstance(source_id, bool)
                or str(source_id).strip() == "" or not isinstance(smiles, str) or not smiles):
            row.update(status="rejected", reason="missing_identity_or_smiles")
            continue
        if record.get("chemical_status") == "identity_conflict":
            row.update(status="rejected", reason="source_identity_conflict")
            continue
        try:
            parent = standardize_parent(raw_smiles=smiles, policy=policy)
        except StandardizationFailure as error:
            row.update(status="rejected", reason=error.reason_code, detail=error.detail)
            continue
        raw = Chem.MolFromSmiles(smiles)
        state = Chem.MolFromSmiles(parent.parent_smiles)
        raw_key = Chem.MolToInchiKey(raw) if raw else ""
        parent_key = Chem.MolToInchiKey(state) if state else ""
        # Standard InChI collapses some tautomers; full-key equality cannot certify unchanged state.
        raw_state = Chem.MolToCXSmiles(raw, smiles_parameters, RegistrationHash.DEFAULT_CXFLAG) if raw else ""
        parent_state = Chem.MolToCXSmiles(state, smiles_parameters, RegistrationHash.DEFAULT_CXFLAG) if state else ""
        registered[parent.parent_id] = parent.parent_smiles
        row.update(status="registered", parent_id=parent.parent_id,
                   parent_smiles=parent.parent_smiles, source_inchikey=raw_key,
                   parent_inchikey=parent_key,
                   source_canonical_smiles=raw_state,
                   chemical_state_changed=not raw_state or raw_state != parent_state,
                   notices=[{"code": n.code, "detail": n.detail} for n in parent.notices])
    root = new_run(workspace, run_id, {"kind": "library", "source_snapshot": original["snapshot_id"],
                   "id_field": id_field, "smiles_field": smiles_field, "allow_partial": allow_partial,
                   "identity_policy": policy.model_dump(mode="json")})
    write_json(root / "source-records.json", records)
    write_json(root / "identity-map.json", mappings)
    if inventory is not None:
        write_json(root / "inventory.json", inventory)
        write_json(root / "targets.json", targets)
    with (root / "library.csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "smiles"])
        writer.writeheader()
        writer.writerows({"id": identifier, "smiles": smiles}
                         for identifier, smiles in sorted(registered.items()))
    return seal(root, kind="library", result={"status": "prepared", "source_coverage": coverage,
                "source_records": len(records), "candidates": len(registered),
                "rejected_records": sum(row["status"] == "rejected" for row in mappings),
                "changed_states": sum(bool(row.get("chemical_state_changed")) for row in mappings)},
                infrastructure={"molcascade": infra.provenance(),
                                "molquarry": original["infrastructure"]},
                source_snapshot={"snapshot_id": original["snapshot_id"], "path": str(source)},
                identity_policy=policy.model_dump(mode="json"),
                identity_implementation=rdkit_identity_metadata(policy))


def import_candidates(store: Any, library: Path, *, candidate_ids: list[str] | None = None) -> dict[str, Any]:
    """Register molecular features and retain the frozen source identity in the journal."""
    from etalon.active.adapters import MOLECULAR_REPRESENTATION, molecular_candidates

    snapshot = read_snapshot(library)
    if snapshot["kind"] != "library":
        raise ValueError("candidate import requires a registered library snapshot")
    spec, _ = store.configuration()
    if spec.representation != MOLECULAR_REPRESENTATION:
        raise ValueError("campaign representation does not match the MolCascade molecular features")
    infra = load("molcascade")
    from molcascade.chemistry.identity import rdkit_identity_metadata
    from molcascade.chemistry.policies import IdentityPolicy

    identity = rdkit_identity_metadata(IdentityPolicy.model_validate(snapshot["identity_policy"]))
    recorded = snapshot["infrastructure"]["molcascade"]
    if (identity != snapshot["identity_implementation"]
            or any(recorded[key] != infra.provenance()[key] for key in ("source_commit", "tree_sha256"))):
        raise ValueError("library identity implementation differs from the current pinned chemistry environment")
    with (Path(snapshot["snapshot"]) / "library.csv").open(newline="", encoding="utf-8") as handle:
        molecules = {row["id"]: row["smiles"] for row in csv.DictReader(handle)}
    if candidate_ids is not None:
        if (not isinstance(candidate_ids, list) or not candidate_ids
                or any(not isinstance(key, str) for key in candidate_ids)
                or len(set(candidate_ids)) != len(candidate_ids)
                or set(candidate_ids) - set(molecules)):
            raise ValueError("candidate selection must contain unique registered library parent ids")
        molecules = {key: molecules[key] for key in candidate_ids}
    known = store.candidates()
    if len(set(known) | set(molecules)) > spec.max_candidates:
        raise ValueError("candidate import exceeds the active pool limit; select an explicit subset or use bulk screening")
    candidates, rejected = molecular_candidates(molecules, source="snapshot:" + snapshot["snapshot_id"])
    if rejected:
        raise ValueError("registered library cannot be featurized without losing candidate identities")
    for candidate in candidates:
        old = known.get(candidate.id)
        if old and (old.smiles != candidate.smiles or old.features != candidate.features):
            raise ValueError("existing candidate identity or features differ from this library")
    store.bind_resource("data-ingress-representation", {
        "representation": MOLECULAR_REPRESENTATION, "identity_implementation": identity,
        "identity_policy": snapshot["identity_policy"], "molcascade_commit": infra.source_commit,
        "molcascade_tree": infra.tree_sha256})
    store.bind_resource("library:" + snapshot["snapshot_id"], {
        "snapshot_id": snapshot["snapshot_id"], "path": snapshot["snapshot"],
        "identity_implementation": snapshot["identity_implementation"]})
    added = store.add_candidates([candidate for candidate in candidates if candidate.id not in known])
    return {"added": added, "candidates": len(candidates), "snapshot_id": snapshot["snapshot_id"],
            "selection": "explicit_subset" if candidate_ids is not None else "full_library"}
