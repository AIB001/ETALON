"""Lossless PRISM design provenance import. No PRISM imports or scientific labels.

The producer owns chemistry and structural verification. This consumer validates its
pinned schema, identity derivations, evidence references and admission consistency.
Pool features reuse the existing molecular representation; index_only retains references
without inventing features in campaigns using another representation.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath

from etalon.active.schema import canonical, digest
from etalon.active.store import CampaignStore, StateError
from etalon.boundary.prism_v1 import file_identity

SCHEMA_VERSION = "prism-design-candidate-bundle/1"
SCHEMA_SHA256 = "4a96481779b1110f6ad9b3b4c259d7d893c089031f3b6e8fd60ea06042de5de0"
SCHEMA_PATH = Path(__file__).parent / "contracts/design-candidate-bundle-v1.schema.json"
POLICY = "rdkit-exact-observed-graph/1"
PREFIX = "design:"


def _json(path: Path):
    def unique(pairs):
        value = {}
        for k, v in pairs:
            if k in value:
                raise ValueError(f"duplicate JSON key: {k}")
            value[k] = v
        return value
    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    canonical(result)
    return result


def _references(value):
    if isinstance(value, dict):
        if set(value) == {"path", "sha256", "bytes"}:
            yield value
        else:
            for item in value.values():
                yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


def _artifact(root: Path, ref: dict) -> Path:
    name = ref["path"]
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or any(p in {".", ".."} for p in name.split("/"))
            or relative.as_posix() != name or "\\" in name or ":" in name or "\x00" in name):
        raise ValueError("bundle reference must stay inside its root")
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("bundle reference escaped root or is missing")
    if file_identity(path) != {k: ref[k] for k in ("sha256", "bytes")}:
        raise ValueError("bundle artifact hash or byte count differs: " + name)
    return path


def _expect_id(actual, prefix, preimage):
    if actual != prefix + digest(preimage):
        raise ValueError("bundle identity derivation mismatch: " + prefix)


def read_bundle(path: str | Path) -> dict:
    """Strict schema and content validation without mutation, chemistry reruns or dispatch."""
    from jsonschema import Draft202012Validator

    path = Path(path).resolve()
    schema_bytes = SCHEMA_PATH.read_bytes()
    if hashlib.sha256(schema_bytes).hexdigest() != SCHEMA_SHA256:
        raise ValueError("pinned producer schema changed")
    bundle = _json(path)
    errors = list(Draft202012Validator(json.loads(schema_bytes)).iter_errors(bundle))
    if errors:
        raise ValueError("design bundle schema: " + errors[0].message)
    refs = {}
    for ref in _references(bundle):
        _artifact(path.parent, ref)
        previous = refs.setdefault(ref["path"], ref)
        if previous != ref:
            raise ValueError("one artifact path has conflicting identities")
    _expect_id(bundle["bundle_id"], "bundle-", {k: v for k, v in bundle.items() if k != "bundle_id"})
    source, receptor = bundle["source"], bundle["receptor"]
    design = {"annotation_sha256": source["annotation"]["sha256"]}
    action = {"compiled_plan_sha256": source["compiled_plan"]["sha256"],
              "results_sha256": source["results"]["sha256"], "slot_ledger_sha256": source["slot_ledger"]["sha256"],
              "seed": source["seed"], "backend_record_sha256s": sorted(r["sha256"] for r in source["backend_records"])}
    if source["identity_derivation"]["design_preimage"] != design or source["identity_derivation"]["action_preimage"] != action:
        raise ValueError("unbound design/action derivation")
    _expect_id(bundle["design_id"], "design-", design)
    _expect_id(bundle["generation_action_id"], "generation-", action)
    _expect_id(receptor["receptor_state_id"], "receptor-", {
        "receptor_sha256": receptor["receptor"]["sha256"], "pocket_sha256": receptor["pocket"]["sha256"],
        "coordinate_frame": receptor["coordinate_frame"], "protonation_status": receptor["protonation_status"]})
    rows = bundle["candidates"]
    ids, slots = set(), set()
    for row in rows:
        if row["candidate_id"] in ids or row["slot"] in slots:
            raise ValueError("candidate or slot identity duplicated within bundle")
        ids.add(row["candidate_id"]); slots.add(row["slot"])
        _expect_id(row["candidate_id"], "candidate-", {"generation_action_id": bundle["generation_action_id"], "slot": row["slot"]})
        identity, raw, validation = row["identity"], row["raw"], row["validation"]
        if identity is not None:
            if raw is None or row["raw_status"] != "DECODED" or identity["identity_policy"] != POLICY:
                raise ValueError("chemical identity lacks a decoded raw artifact or policy")
            _expect_id(identity["molecule_id"], "molecule-", {"policy": POLICY,
                "rdkit_version": bundle["identity_policy"]["rdkit_version"],
                "canonical_isomeric_smiles": identity["canonical_isomeric_smiles"]})
            _expect_id(identity["chemical_state_id"], "state-", {"molecule_id": identity["molecule_id"],
                "formal_charge": identity["formal_charge"], "protonation_status": identity["protonation_status"]})
            _expect_id(identity["pose_id"], "pose-", {"chemical_state_id": identity["chemical_state_id"],
                "receptor_state_id": receptor["receptor_state_id"], "raw_sha256": raw["sha256"],
                "coordinate_frame": receptor["coordinate_frame"]})
        for transformation in row["transformations"]:
            if (transformation["source"] != raw or transformation["target"] != row["processed"]
                    or transformation["provenance_record"] != validation["source_record"]
                    or transformation["verification"] != "pass"):
                raise ValueError("raw/processed transformation references disagree")
        if row["processed"] is not None and not row["transformations"]:
            raise ValueError("processed artifact has no provenance relationship")
        if row["readiness"]["pool_admission"] == "eligible":
            if (identity is None or validation["revalidation_record"] is None
                    or any(validation[k] != "pass" for k in ("status", "hard_status", "selection_status"))
                    or validation["qc_status"] != "PASS" or not validation["checks"]
                    or any(c["status"] != "pass" for c in validation["checks"])):
                raise ValueError("eligible candidate has failed/unknown/unevaluated required evidence")
    if any(r["duplicate_of"] is not None and r["duplicate_of"] not in ids for r in rows):
        raise ValueError("duplicate candidate points outside bundle")
    counts = bundle["counts"]
    statuses = Counter(r["raw_status"] for r in rows)
    expected = {"requested": len(rows), "raw_slots": len(rows) - statuses["NOT_ATTEMPTED"],
                "decoded": statuses["DECODED"], "decode_failed": statuses["DECODE_FAILED"],
                "serialize_failed": statuses["SERIALIZE_FAILED"], "not_attempted": statuses["NOT_ATTEMPTED"],
                "unique_selected": sum(r["selected_by_source"] for r in rows),
                "duplicate_selected_chemistry": sum(r["duplicate_of"] is not None for r in rows)}
    if any(counts[k] != v for k, v in expected.items()) or [r["slot"] for r in rows] != list(range(1, len(rows) + 1)):
        raise ValueError("slot ledger denominator mismatch")
    ledger = _json(_artifact(path.parent, source["slot_ledger"]))
    results = _json(_artifact(path.parent, source["results"]))
    if (not isinstance(ledger, list) or len(ledger) != len(rows)
            or results.get("summary", {}).get("counts") != counts or results.get("slots") != ledger):
        raise ValueError("source results/ledger disagree with bundle counts")
    summary = results["summary"]
    if any(source[k] != summary[v] for k, v in (("seed", "seed"), ("task", "task"), ("run_status", "status"))):
        raise ValueError("source task/seed/status does not match original results")
    earlier = set()
    for row, original in zip(rows, ledger, strict=True):
        if (row["source_slot_id"] != original["candidate_id"] or row["raw_status"] != original["status"]
                or row["selected_by_source"] != original["selected"]):
            raise ValueError("slot source ledger mismatch")
        if row["duplicate_of"] is not None and row["duplicate_of"] not in earlier:
            raise ValueError("duplicate must refer to an earlier candidate")
        if row["readiness"]["pool_admission"] == "eligible":
            original_validation = original.get("validation") or {}
            if (original_validation.get("design_hard_pass") is not True
                    or original_validation.get("selection_pass") is not True
                    or (original.get("qc") or {}).get("status") != "PASS"):
                raise ValueError("historical unknown/failed validation cannot become eligible")
        earlier.add(row["candidate_id"])
    return {"bundle": bundle, "path": str(path), "root": str(path.parent), "references": refs,
            "schema_sha256": SCHEMA_SHA256, "bundle_file_sha256": file_identity(path)["sha256"]}


def _put(db, key: str, value: dict) -> bool:
    body = canonical(value)
    old = db.execute("SELECT body FROM metadata WHERE key=?", (PREFIX + key,)).fetchone()
    if old:
        if old[0] != body:
            raise StateError("design identity changed: " + key)
        return False
    db.execute("INSERT INTO metadata VALUES (?,?)", (PREFIX + key, body))
    return True


def import_bundle(store: CampaignStore, bundle_path: str | Path, *, mode: str = "pool") -> dict:
    """Index every source/state/pose; pool only eligible molecules. Never create labels or spend."""
    from etalon.active.adapters import MOLECULAR_REPRESENTATION, molecular_candidates

    if mode not in {"pool", "index_only"}:
        raise ValueError("design import mode must be pool or index_only")
    checked = read_bundle(bundle_path)
    bundle = checked["bundle"]
    eligible = {r["identity"]["molecule_id"]: r["identity"]["canonical_isomeric_smiles"]
                for r in bundle["candidates"] if r["readiness"]["pool_admission"] == "eligible"}
    spec, _ = store.configuration()
    candidates = []
    if mode == "pool" and eligible:
        if spec.representation != MOLECULAR_REPRESENTATION:
            raise ValueError("pool import requires existing molecular representation; use explicit index_only otherwise")
        candidates, rejected = molecular_candidates(eligible, source=SCHEMA_VERSION)
        if rejected:
            raise ValueError("eligible molecule could not be featurized; use explicit index_only to retain diagnostics")
    with store.connection(write=True) as db:
        # Recheck immutable bytes after feature computation, before any journal mutation.
        if read_bundle(bundle_path)["bundle_file_sha256"] != checked["bundle_file_sha256"]:
            raise StateError("bundle changed during import")
        import_key = "import:" + bundle["bundle_id"] + ":" + mode
        old = db.execute("SELECT body FROM metadata WHERE key=?", (PREFIX + import_key,)).fetchone()
        if old:
            return {**json.loads(old[0]), "added_candidates": 0, "added_records": 0, "imported": False}
        if db.execute("SELECT 1 FROM actions WHERE status IN ('reserved','running')").fetchone() or db.execute(
                "SELECT 1 FROM rounds WHERE status='running'").fetchone():
            raise StateError("resolve active work before importing design sources")
        records = int(_put(db, "bundle:" + bundle["bundle_id"], bundle))
        records += _put(db, "generation:" + bundle["generation_action_id"], {
            "generation_action_id": bundle["generation_action_id"], "design_id": bundle["design_id"],
            "derivation": bundle["source"]["identity_derivation"]})
        records += _put(db, "receptor:" + bundle["receptor"]["receptor_state_id"], {
            "receptor_state_id": bundle["receptor"]["receptor_state_id"],
            "receptor_sha256": bundle["receptor"]["receptor"]["sha256"],
            "pocket_sha256": bundle["receptor"]["pocket"]["sha256"],
            "coordinate_frame": bundle["receptor"]["coordinate_frame"], "length_unit": "angstrom"})
        for row in bundle["candidates"]:
            identity = row["identity"]
            if identity:
                records += _put(db, "molecule:" + identity["molecule_id"], {
                    "molecule_id": identity["molecule_id"], "canonical_isomeric_smiles": identity["canonical_isomeric_smiles"],
                    "identity_policy": bundle["identity_policy"]})
                records += _put(db, "state:" + identity["chemical_state_id"], {k: identity[k] for k in (
                    "molecule_id", "chemical_state_id", "formal_charge", "protonation_status", "identity_policy")})
                records += _put(db, "pose:" + identity["pose_id"], {"pose_id": identity["pose_id"],
                    "chemical_state_id": identity["chemical_state_id"], "raw_sha256": row["raw"]["sha256"],
                    "receptor_state_id": bundle["receptor"]["receptor_state_id"]})
            records += _put(db, "occurrence:" + digest([bundle["bundle_id"], row["candidate_id"]]), {
                "bundle_id": bundle["bundle_id"], "generation_action_id": bundle["generation_action_id"],
                "design_id": bundle["design_id"], "candidate": row,
                "eligible_molecule_id": identity["molecule_id"] if identity and identity["molecule_id"] in eligible else None,
                "diagnostic": {"readiness": row["readiness"], "validation": row["validation"], "raw_status": row["raw_status"]}})
        added = store._add_candidates(db, candidates)
        result = {"schema_version": SCHEMA_VERSION, "schema_sha256": SCHEMA_SHA256,
                  "bundle_id": bundle["bundle_id"], "generation_action_id": bundle["generation_action_id"],
                  "source_kind": bundle["source"]["kind"], "mode": mode,
                  "indexed_slots": len(bundle["candidates"]), "eligible_molecules": len(eligible),
                  "pool_candidate_ids": sorted(eligible) if mode == "pool" else [],
                  "added_candidates": added, "added_records": records, "imported": True,
                  "observations_created": 0, "md_capability": "unsupported"}
        _put(db, "location:" + digest([bundle["bundle_id"], checked["root"]]), {
            "bundle_id": bundle["bundle_id"], "root": checked["root"], "bundle_file_sha256": checked["bundle_file_sha256"]})
        _put(db, import_key, result)
        store._event(db, "design_bundle_imported", result)
    return result


def design_index(store: CampaignStore) -> dict:
    """Return provenance and diagnostics independently of the scalar training table."""
    with store.connection() as db:
        records = {r[0][len(PREFIX):]: json.loads(r[1]) for r in db.execute(
            "SELECT key,body FROM metadata WHERE key LIKE ? ORDER BY key", (PREFIX + "%",))}
    return {"schema_version": SCHEMA_VERSION, "records": records,
            "counts": dict(Counter(k.split(":", 1)[0] for k in records))}


def verify_import(store: CampaignStore, bundle_path: str | Path, *, mode: str = "pool") -> dict:
    checked = read_bundle(bundle_path)
    key = "import:" + checked["bundle"]["bundle_id"] + ":" + mode
    index = design_index(store)["records"]
    if index.get("bundle:" + checked["bundle"]["bundle_id"]) != checked["bundle"] or key not in index:
        raise StateError("design bundle is absent or differs from imported evidence")
    for identifier in index[key]["pool_candidate_ids"]:
        if identifier not in store.candidates():
            raise StateError("imported pool candidate is absent")
    for row in checked["bundle"]["candidates"]:
        occurrence = index.get("occurrence:" + digest([checked["bundle"]["bundle_id"], row["candidate_id"]]))
        if not occurrence or occurrence["candidate"] != row:
            raise StateError("imported candidate provenance/diagnostics changed")
        if row["identity"]:
            identity = row["identity"]
            for kind, field in (("molecule", "molecule_id"), ("state", "chemical_state_id"), ("pose", "pose_id")):
                if index.get(kind + ":" + identity[field], {}).get(field) != identity[field]:
                    raise StateError("imported molecule/state/pose index is missing")
    return index[key]
