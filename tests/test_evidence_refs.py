"""Fixture tests for evidence identity and relocation, not scientific trials."""
import json

import pytest

from etalon.research.evidence_refs import (
    EvidenceError, EvidenceResolver, file_identity, load_workspace,
    make_ref, resolve_artifact, validate_claim, validate_ref,
)


def workspace(tmp_path, mappings=()):
    path = tmp_path / "workspace.json"
    path.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "fixture",
        "sources": [{"source_id": "released", "kind": "artifact_manifest", "location": "manifest.json",
                     "identity": {"kind": "snapshot_sha256", "value": "0" * 64}}],
        "location_map": list(mappings)}))
    return path


@pytest.mark.parametrize("original", ["/old/linux/result.json", r"Z:\old\result.json"])
def test_relocates_foreign_path_without_changing_record(tmp_path, monkeypatch, original):
    target = tmp_path / "result.json"
    target.write_bytes(b'{"cost":null,"role":"independent_validation"}')
    identity = file_identity(target)
    record = {"path": original, **identity}
    ref = make_ref("released", "artifact-old", record)
    ws = load_workspace(workspace(tmp_path, [{"source_id": "released", "original": original,
                       "location": "result.json", **identity}]))
    monkeypatch.chdir(tmp_path.parent)
    assert resolve_artifact(record, resolver=EvidenceResolver(ws, "released")) == target
    assert make_ref("released", "artifact-old", record) == ref
    assert record["path"] == original
    target.write_bytes(b"wrong")
    with pytest.raises(EvidenceError, match="mismatch"):
        resolve_artifact(record, resolver=EvidenceResolver(ws, "released"))


def test_boundaries_conflicting_mapping_and_missing_file(tmp_path):
    identity = {"sha256": "0" * 64, "bytes": 1}
    mapping = {"source_id": "released", "original": "/old", "location": "../escape", **identity}
    with pytest.raises(EvidenceError, match="escapes"):
        load_workspace(workspace(tmp_path, [mapping]))
    mapping["location"] = "missing"
    with pytest.raises(EvidenceError, match="conflicting"):
        load_workspace(workspace(tmp_path, [mapping, mapping]))
    resolver = EvidenceResolver(workspace(tmp_path, [mapping]), "released")
    with pytest.raises(FileNotFoundError):
        resolver.resolve("/old", **identity)
    assert not (tmp_path / "missing").exists()
    with pytest.raises(EvidenceError, match="conflicts"):
        resolver.resolve("/old", sha256="1" * 64, bytes=1)


def test_ref_and_claim_keep_provenance_and_epistemic_state():
    body = {"cost": None, "role": "independent_validation"}
    ref = make_ref("old-campaign", "observation-1", body)
    assert ref != make_ref("other-campaign", "observation-1", body)
    assert ref["record_digest"] == make_ref("other-campaign", "observation-1", body)["record_digest"]
    claim = {"schema": "etalon-research-claim/1", "question": "What changed?", "subjects": [],
             "conditions": {}, "statement": "Unknown effect", "epistemic_status": "interpretation",
             "support_refs": [ref], "counter_refs": [ref], "limitations": ["seen evidence"],
             "method_refs": [], "supersedes": []}
    assert validate_claim(claim) == claim
    with pytest.raises(EvidenceError):
        validate_ref({**ref, "record_digest": "bad"})
    with pytest.raises(EvidenceError):
        validate_claim({**claim, "epistemic_status": "proven"})


def test_no_raw_mapping_workspace_or_unknown_source(tmp_path):
    with pytest.raises(EvidenceError):
        load_workspace({"schema": "etalon-evidence-workspace/1"})
    with pytest.raises(EvidenceError):
        EvidenceResolver(workspace(tmp_path), "unknown")


def test_snapshot_resolver_uses_byte_hash_not_semantic_hash(tmp_path):
    target = tmp_path / "snapshot.json"
    target.write_text('{"manifest_sha256":"semantic"}')
    mapping = {"source_id": "released", "original": "/old/snapshot.json", "location": "snapshot.json", **file_identity(target)}
    resolver = EvidenceResolver(workspace(tmp_path, [mapping]), "released")
    assert resolver.resolve_snapshot({"manifest_path": mapping["original"], "manifest_sha256": "1" * 64}) == target
