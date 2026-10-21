"""Read-only engineering fixtures; these do not measure independent research benefit."""
import base64
import json
import sqlite3
import subprocess
import sys

import pytest

from etalon.active.schema import canonical, digest
from etalon.research.evidence import EvidenceError, StaleCursorError, query_evidence, read_evidence
from etalon.research.evidence_refs import file_identity, make_ref


def publish(tmp_path, records, *, extra_sources=(), location_map=()):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": records}))
    ws = tmp_path / "workspace.json"
    ws.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "fixture",
        "sources": [{"source_id": "released", "kind": "artifact_manifest", "location": "manifest.json",
                     "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}}, *extra_sources],
        "location_map": list(location_map)}))
    return ws


def row(number, **body):
    return {"record_id": f"observation-{number:03}", "record_type": "observation", "visibility": "released",
            "record": {"task_id": "task", "role": "diagnostic", "cost": None, **body}}


def test_paging_filter_bounds_and_direct_read(tmp_path):
    ws = publish(tmp_path, [row(n, values={"effect": n}) for n in range(61)])
    first = query_evidence(ws, filters={"task_ids": ["task"]}, limit=50)
    second = query_evidence(ws, filters={"task_ids": ["task"]}, limit=50, cursor=first["next_cursor"])
    assert len(first["items"]) == 50 and len(second["items"]) == 11
    assert second["next_cursor"] is None
    assert len(canonical(first).encode()) <= 32768
    ref = first["items"][0]["ref"]
    assert read_evidence(ws, ref)["record"]["cost"] is None
    assert read_evidence(ws, ref)["verification"]["artifact"] == "not_checked"
    with pytest.raises(EvidenceError):
        query_evidence(ws, filters={"anything": []})
    with pytest.raises(EvidenceError):
        query_evidence(ws, limit=51)
    with pytest.raises(EvidenceError, match="digest"):
        read_evidence(ws, {**ref, "record_digest": "f" * 64})
    with pytest.raises(StaleCursorError):
        query_evidence(ws, query="changed", filters={"task_ids": ["task"]}, cursor=first["next_cursor"])


def test_missing_db_never_created_and_corruption_not_empty_success(tmp_path):
    missing = {"source_id": "missing", "kind": "journal", "location": "missing.sqlite",
               "identity": {"kind": "snapshot_sha256", "value": "0" * 64}}
    ws = publish(tmp_path, [row(1)], extra_sources=[missing])
    before = sorted(p.name for p in tmp_path.iterdir())
    result = query_evidence(ws, query="no-match")
    assert result["items"] == []
    assert [c["status"] for c in result["coverage"]] == ["complete", "unavailable"]
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    known_ref = make_ref("released", row(1)["record_id"], row(1)["record"])
    assert read_evidence(ws, known_ref)["record"] == row(1)["record"]
    (tmp_path / "manifest.json").write_text("corrupt")
    result = query_evidence(ws)
    assert all(c["status"] == "unavailable" for c in result["coverage"])


def test_live_journal_cursor_stale_after_append_and_reads_leave_bytes(tmp_path):
    path = tmp_path / "campaign.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE metadata(key TEXT PRIMARY KEY,body TEXT); CREATE TABLE events(sequence INTEGER PRIMARY KEY, body TEXT);")
        db.execute("INSERT INTO metadata VALUES (?,?)", ("resource:identity", canonical({"released_id": "fixture-1"})))
        for n in range(3):
            db.execute("INSERT INTO metadata VALUES (?,?)", (f"research:observation-{n}", canonical(row(n)["record"])))
        db.execute("INSERT INTO events VALUES (1,'{}')")
    source = {"source_id": "live", "kind": "journal", "location": path.name,
              "identity": {"kind": "journal_identity", "key": "resource:identity", "value": {"released_id": "fixture-1"}}}
    ws = publish(tmp_path, [], extra_sources=[source])
    before = file_identity(path)
    page = query_evidence(ws, limit=1)
    assert file_identity(path) == before
    assert page["coverage"][1]["read_watermark"] == 1
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO events VALUES (2,'{}')")
    with pytest.raises(StaleCursorError):
        query_evidence(ws, limit=1, cursor=page["next_cursor"])


def test_sealed_and_derived_metadata_never_query_or_lineage(tmp_path):
    hidden = row(1, statement="SECRET"); hidden["visibility"] = "sealed"
    derivative = row(2, statement="SECRET derived", evidence=[hidden["record_id"]])
    clean = row(3)
    ws = publish(tmp_path, [hidden, derivative, clean])
    result = query_evidence(ws)
    assert len(result["items"]) == 1
    assert "SECRET" not in canonical(result)
    ref = make_ref("released", hidden["record_id"], hidden["record"])
    assert read_evidence(ws, ref)["availability"] == "unavailable"
    assert read_evidence(ws, result["items"][0]["ref"], view="lineage")["items"] == []


def test_artifact_whole_hash_verified_before_fragment_and_missing(tmp_path):
    data = b"released evidence\n" * 10000
    path = tmp_path / "released.bin"; path.write_bytes(data)
    body = {"path": "/historic/posix/released.bin", **file_identity(path), "source_kind": "existing_real_artifact", "role": "independent_validation"}
    record = {"record_id": "artifact-old", "record_type": "artifact", "visibility": "released", "record": body}
    mapping = {"source_id": "released", "original": body["path"], "location": path.name, **file_identity(path)}
    ws = publish(tmp_path, [record], location_map=[mapping])
    ref = query_evidence(ws)["items"][0]["ref"]
    result = read_evidence(ws, ref, view="artifact", offset=2, limit=32768)
    assert base64.b64decode(result["fragment"]["content"]) == data[2:32770]
    assert result["verification"]["artifact"] == "verified"
    assert len(canonical(result).encode()) < 65536
    path.write_bytes(data[:-1] + b"!")
    with pytest.raises(EvidenceError, match="mismatch"):
        read_evidence(ws, ref, view="artifact", limit=1)
    path.unlink()
    assert read_evidence(ws, ref, view="artifact")["availability"] == "unavailable"


def test_claim_cross_source_support_counter_and_lineage(tmp_path):
    original = row(1, effect="uncertain", role="independent_validation")
    ref = make_ref("released", original["record_id"], original["record"])
    claim = {"schema": "etalon-research-claim/1", "question": "effect?", "subjects": [], "conditions": {},
             "statement": "not established", "epistemic_status": "interpretation", "support_refs": [ref],
             "counter_refs": [ref], "limitations": ["previously seen"], "method_refs": [], "supersedes": []}
    ws = publish(tmp_path, [original, {"record_id": "claim-1", "record_type": "claim", "record": claim, "visibility": "released"}])
    result = query_evidence(ws, filters={"record_types": ["claim"]})
    assert len(result["items"]) == 1
    assert read_evidence(ws, result["items"][0]["ref"], view="lineage")["items"] == [ref, ref]


def test_native_data_snapshot_explicit_members(tmp_path):
    from etalon.data.artifacts import seal
    data = tmp_path / "data"; data.mkdir()
    (data / "result.txt").write_text("fixture released")
    snapshot = seal(data, kind="fixture", result={}, infrastructure={})
    manifest = data / "snapshot.json"
    source = {"source_id": "data", "kind": "data_snapshot", "location": str(manifest),
              "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}}
    ws = publish(tmp_path, [], extra_sources=[source])
    result = query_evidence(ws)
    assert result["items"][0]["ref"]["record_id"] == snapshot["snapshot_id"]
    read = read_evidence(ws, result["items"][0]["ref"], view="artifact", member="result.txt")
    assert base64.b64decode(read["fragment"]["content"]) == b"fixture released"


def test_huge_record_summary_and_fragments_are_bounded(tmp_path):
    ws = publish(tmp_path, [row(1, statement="Ω" * 50000, conditions={"large": "x" * 50000})])
    page = query_evidence(ws)
    assert len(canonical(page).encode()) <= 32768
    assert page["items"][0]["truncated"]
    ref = page["items"][0]["ref"]
    read = read_evidence(ws, ref, limit=32768)
    assert len(canonical(read).encode()) <= 65536
    assert read["fragment"]["next_offset"] == 32768
    second = read_evidence(ws, ref, offset=32768, limit=32768)
    assert second["fragment"]["offset"] == 32768


def test_private_source_not_opened_and_cross_source_derived_hidden(tmp_path, monkeypatch):
    hidden = {"source_id": "private", "kind": "journal", "location": "private.sqlite", "visibility": "sealed",
              "identity": {"kind": "snapshot_sha256", "value": "0" * 64}}
    ref = make_ref("private", "observation-private", {"statement": "SECRET"})
    ws = publish(tmp_path, [row(1, evidence_refs=[ref], statement="SECRET derivative"), row(2)], extra_sources=[hidden])
    def never_connect(*args, **kwargs):
        raise AssertionError("sealed source was opened")
    monkeypatch.setattr(sqlite3, "connect", never_connect)
    page = query_evidence(ws)
    assert len(page["items"]) == 1 and "SECRET" not in canonical(page)
    assert page["coverage"][1]["reason"] == "source_not_published"


def test_identical_bytes_across_sources_preserve_source_and_cost_roles(tmp_path):
    original = row(1, role="independent_validation", cost=1.2)
    second = tmp_path / "second.json"
    second.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [original]}))
    source = {"source_id": "other", "kind": "artifact_manifest", "location": second.name,
              "identity": {"kind": "snapshot_sha256", "value": file_identity(second)["sha256"]}}
    ws = publish(tmp_path, [original], extra_sources=[source])
    items = query_evidence(ws)["items"]
    assert len(items) == 2
    assert items[0]["ref"]["record_digest"] == items[1]["ref"]["record_digest"]
    assert items[0]["ref"]["source_id"] != items[1]["ref"]["source_id"]
    assert all(read_evidence(ws, item["ref"])["record"]["role"] == "independent_validation" for item in items)


def test_existing_native_execution_receipt_is_read_without_recharge(tmp_path):
    path = tmp_path / "journal.sqlite"
    receipt = {"execution_id": "original", "cost": 2.5, "unit": "wall_second", "status": "settled"}
    key = "budget:execution:original"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE metadata(key TEXT PRIMARY KEY,body TEXT); CREATE TABLE events(sequence INTEGER PRIMARY KEY, body TEXT);")
        db.execute("INSERT INTO metadata VALUES (?,?)", (key, canonical(receipt)))
    identity = file_identity(path)
    ws = publish(tmp_path, [], extra_sources=[{"source_id": "journal", "kind": "journal", "location": path.name,
               "identity": {"kind": "snapshot_sha256", "value": identity["sha256"]}}])
    item = query_evidence(ws, filters={"record_types": ["execution_receipt"]})["items"][0]
    assert item["ref"]["record_id"] == key
    assert read_evidence(ws, item["ref"])["record"] == receipt
    assert file_identity(path) == identity


def test_method_filter_multi_empty_and_usage_method(tmp_path):
    rows = [row(1, selected_methods=[{"method_version_ref": "m1", "scope": "partial"},
                                    {"method_version_ref": "m2", "scope": "combine"}], usage_method_ref="u"),
            row(2, selected_methods=[], usage_method_ref=None)]
    ws = publish(tmp_path, rows)
    for method in ("m1", "m2", "u"):
        items = query_evidence(ws, filters={"method_refs": [method]})["items"]
        assert [i["ref"]["record_id"] for i in items] == [rows[0]["record_id"]]
    assert query_evidence(ws, filters={"method_refs": []})["items"] == []
    assert query_evidence(ws, filters={"method_refs": ["partial"]})["items"] == []
    assert len(query_evidence(ws)["items"]) == 2


@pytest.mark.parametrize("cross_source", [False, True])
def test_lineage_never_rebinds_exact_digest_and_keeps_member_identity(tmp_path, cross_source):
    artifact = {"path": "unused.txt", "sha256": "1" * 64, "bytes": 12,
                "role": "diagnostic", "source_kind": "fixture", "members": [
                    {"member": "report", "sha256": "2" * 64, "bytes": 4, "visibility": "released"}]}
    target = {"record_id": "target", "record_type": "artifact", "visibility": "released", "record": artifact}
    sid = "other" if cross_source else "released"
    exact = {**make_ref(sid, "target", artifact), "member": "report", "selector": "bytes", "sha256": "2" * 64, "bytes": 4}
    stale = {**exact, "record_digest": digest({"previous_record_at_same_id": True})}
    wrong_bytes = {**exact, "sha256": "3" * 64}
    origin = row(1, evidence_refs=[stale, exact, wrong_bytes])
    extra = []
    if cross_source:
        path = tmp_path / "other.json"
        path.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [target]}))
        extra = [{"source_id": "other", "kind": "artifact_manifest", "location": path.name,
                  "identity": {"kind": "snapshot_sha256", "value": file_identity(path)["sha256"]}}]
    ws = publish(tmp_path, [origin] if cross_source else [target, origin], extra_sources=extra)
    result = read_evidence(ws, make_ref("released", origin["record_id"], origin["record"]), view="lineage")
    assert result["items"] == [exact]
    assert result["missing"] == [{"ref": stale, "reason": "digest_mismatch"},
                                 {"ref": wrong_bytes, "reason": "artifact_identity_mismatch"}]
    assert all(r["reference_kind"] == "exact" for r in result["relationships"])


def test_legacy_relationship_is_marked_as_current_metadata(tmp_path):
    target, origin = row(1), row(2, evidence=["observation-001"])
    ws = publish(tmp_path, [target, origin])
    read = read_evidence(ws, make_ref("released", origin["record_id"], origin["record"]), view="lineage")
    assert read["relationships"][0]["reference_kind"] == "legacy_string"
    assert read["relationships"][0]["status"] == "resolved_current_metadata"


def test_normal_claim_artifact_publication_projection_and_tamper(tmp_path):
    from etalon.active import CampaignSpec, CampaignStore, Endpoint
    from etalon.research import Artifact, ResearchJournal
    store = CampaignStore(tmp_path / "campaign.sqlite")
    store.configure(CampaignSpec("x", 1, "fixture", "claim-publication"),
                    [Endpoint("x", "fixture", "unmeasured", "count", "fixture/1", 1, queryable=False)])
    journal = ResearchJournal(store)
    support_path = tmp_path / "support.txt"; support_path.write_text("fixture support")
    support = Artifact(str(support_path), **file_identity(support_path), source_kind="fixture", role="diagnostic", bindings={})
    support_id = journal.record(support)
    exact = make_ref("journal", support_id, journal.get(support_id))
    claim = {"schema": "etalon-research-claim/1", "question": "a queryable claim topic", "statement": "bounded interpretation",
             "subjects": [], "conditions": {}, "epistemic_status": "interpretation", "support_refs": [exact],
             "counter_refs": [exact], "limitations": ["fixture"], "method_refs": [], "supersedes": []}
    path = tmp_path / "claim.json"; path.write_text(json.dumps(claim))
    artifact = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic",
                        bindings={"schema": "etalon-research-claim/1"})
    identifier = journal.record(artifact)
    before = file_identity(store.path)
    ws = publish(tmp_path, [], extra_sources=[{"source_id": "journal", "kind": "journal", "location": str(store.path),
               "identity": {"kind": "snapshot_sha256", "value": before["sha256"]}}])
    page = query_evidence(ws, query="a queryable claim topic")
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["ref"] == make_ref("journal", identifier, journal.get(identifier))
    assert item["projection"]["status"] == "verified"
    assert read_evidence(ws, item["ref"], view="lineage")["items"] == [exact, exact]
    assert read_evidence(ws, item["ref"])["record"] == journal.get(identifier)
    child = subprocess.run([sys.executable, "-c",
        "import json,sys; from etalon.research.evidence import query_evidence,read_evidence; "
        "p=query_evidence(sys.argv[1],query=sys.argv[2]); r=p['items'][0]['ref']; "
        "print(json.dumps({'ref':r,'lineage':read_evidence(sys.argv[1],r,view='lineage')}))",
        str(ws), "a queryable claim topic"], capture_output=True, check=True, text=True)
    independent = json.loads(child.stdout)
    assert independent["ref"] == item["ref"] and independent["lineage"]["items"] == [exact, exact]
    path.write_text("tampered")
    broken = query_evidence(ws, query="a queryable claim topic")
    assert broken["items"] == [] and broken["coverage"][1]["status"] == "partial"
    assert broken["warnings"][0]["code"] == "content_projection_unavailable"
    assert read_evidence(ws, item["ref"], view="lineage")["missing"] == ["content_projection_unavailable"]
    path.unlink()
    assert query_evidence(ws)["coverage"][1]["projection_failures"][0]["reason"] == "FileNotFoundError"
    assert file_identity(store.path) == before


def test_optional_projection_only_opens_explicit_published_known_records(tmp_path):
    claim = {"schema": "etalon-research-claim/1", "question": "explicit historical topic", "statement": "claim",
             "subjects": [], "conditions": {}, "epistemic_status": "hypothesis", "support_refs": [],
             "counter_refs": [], "limitations": [], "method_refs": [], "supersedes": []}
    path = tmp_path / "claim.json"; path.write_text(json.dumps(claim))
    body = {"path": str(path), **file_identity(path), "source_kind": "fixture", "role": "diagnostic", "bindings": {}}
    artifact = {"record_id": "artifact-old", "record_type": "artifact", "visibility": "released", "record": body}
    ws = publish(tmp_path, [artifact])
    assert query_evidence(ws, query="explicit historical topic")["items"] == []
    config = json.loads(ws.read_text())
    config["sources"][0]["content_projections"] = [{"record_id": "artifact-old", "schema": claim["schema"]}]
    ws.write_text(json.dumps(config))
    item = query_evidence(ws, query="explicit historical topic")["items"][0]
    assert item["ref"] == make_ref("released", "artifact-old", body)
    config["sources"][0]["record_ids"] = []
    ws.write_text(json.dumps(config)); path.unlink()
    page = query_evidence(ws)
    assert page["items"] == []
    assert page["coverage"][0]["projection_failures"] == [{"reason": "explicit_projection_record_not_published"}]
    config["sources"][0]["content_projections"][0]["schema"] = "unknown/1"
    ws.write_text(json.dumps(config))
    with pytest.raises(EvidenceError, match="unsupported content schema"):
        query_evidence(ws)


def test_lineage_large_exact_refs_remain_bounded_and_continue(tmp_path):
    target = row(1)
    ref = {**make_ref("released", target["record_id"], target["record"]), "note": "x" * 3000}
    origin = row(2, evidence_refs=[ref] * 50)
    ws = publish(tmp_path, [target, origin])
    original = make_ref("released", origin["record_id"], origin["record"])
    result = read_evidence(ws, original, view="lineage", limit=50)
    assert len(canonical(result).encode()) < 65536
    assert result["next_offset"] == len(result["items"]) > 0
    assert all(item == ref for item in result["items"])
    offset = result["next_offset"]
    second = read_evidence(ws, original, view="lineage", offset=offset, limit=50)
    assert second["items"][0] == ref and second["next_offset"] > offset
