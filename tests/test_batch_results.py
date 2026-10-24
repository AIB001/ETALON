"""Real filesystem/process failures; fixture science is explicitly labelled."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.store import StateError
from etalon.research import ResearchJournal
from etalon.research.schema import Coupling, Experiment
from etalon.research import batch_results as batch
from etalon.research.continuation import CHECKPOINT, read_content, record_content, resume_research
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.evaluation_contract import build_evaluation_contract, evaluate_contract
from etalon.research.publication import prepare_publication, resume_publication
from etalon.research.evidence import query_evidence, read_evidence


@pytest.fixture
def case(tmp_path):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("archive", 0, "wall_second", "fixture/1"),
        [Endpoint("archive", "fixture", "archive", "count", "fixture/1", 0, queryable=False)])
    return ResearchJournal(store), tmp_path


def manifest(root, count=4):
    expected, rows = [], []
    for i in range(count):
        rid = "request-" + str(i)
        receipt = root / (rid + ".json")
        receipt.write_text(json.dumps({"fixture": True, "request_id": rid, "value": i}), encoding="utf-8")
        counter = root / (rid + "-counter.txt")
        counter.write_text("Fixture counterexample retained: " + str(i), encoding="utf-8")
        expected.append({"request_id": rid, "attempt_id": "trial-1",
            "required_originals": [], "required_counterevidence": ["counter"]})
        rows.append({"request_id": rid, "attempt_id": "trial-1", "status": "completed",
            "execution_mode": "new_execution", "receipt": {"path": str(receipt), **file_identity(receipt)},
            "originals": [], "counterevidence": [{"name": "counter", "path": str(counter), **file_identity(counter)}],
            "actual_consumption": {"wall_second": i + 0.5}, "identities": {"protocol": "fixture/1"}})
    return {"schema": batch.INPUT, "batch_id": "block-1", "task_id": "fixture", "question": "Fixture archival reliability?",
        "branch_id": "test", "expected_attempts": expected, "rows": rows,
        "scope_limits": ["Fixture results; no molecular computation or scientific generalization"]}


def ingest(case, data):
    journal, root = case
    return batch.ingest_batch_results(journal, data, root / "batches", source_id="batch", source_kind="fixture")


def test_count_denominators_unknown_cost_and_no_new_charge(case):
    journal, root = case
    data = manifest(root, 6)
    data["rows"][0]["status"] = "failed"
    data["rows"][1].update(status="not_applicable", execution_mode="not_executed", actual_consumption={"wall_second": None})
    data["rows"][2].update(status="unresolved", receipt=None, actual_consumption={"wall_second": None})
    data["rows"][3]["execution_mode"] = "historical_replay"
    data["rows"][4]["execution_mode"] = "cache_hit"
    data["rows"].pop()
    result = ingest(case, data)
    audit = batch.verify_batch_results(journal, result)
    summary = audit["summary"]
    assert (summary["expected"], summary["reported"], summary["missing"]) == (6, 5, 1)
    assert summary["status_counts"] == {"completed": 2, "failed": 1, "not_applicable": 1, "censored": 0, "unresolved": 1}
    assert summary["coverage"] == "incomplete" and summary["missing_required_counterevidence"] == 1
    costs = summary["actual_consumption_by_mode"]
    assert costs["new_execution"]["wall_second"] == {"known_sum": 0.5, "known_rows": 1, "unknown_rows": 1, "total": None}
    assert costs["not_executed"]["wall_second"]["total"] is None
    assert costs["historical_replay"]["wall_second"]["known_sum"] == 3.5
    assert summary["missing_attempt_consumption"] is None
    assert result["new_scientific_charge"] == 0 and result["ingestion_compute_cost"] is None
    assert not journal.store.actions() and not result["automatic_retry"]
    events = journal.store.events()
    assert ingest(case, data) == result and journal.store.events() == events
    changed = deepcopy(data)
    changed["rows"][0]["status"] = "completed"
    with pytest.raises(EvidenceError, match="conflict"):
        ingest(case, changed)
    assert journal.store.events() == events


def test_large_block_navigation_retains_late_counterexample_and_raw_identity(case):
    journal, root = case
    data = manifest(root, 97)
    result = ingest(case, data)
    assert batch.verify_batch_results(journal, result)["expected_verified"] == 97
    index = batch._load(journal, result["index_ref"])
    assert index["chunk_count"] >= 4
    assert all(Path(journal.get(chunk["ref"]["record_id"])["path"]).stat().st_size <= batch.MAX_CHUNK_BYTES
        for page in index["pages"] for chunk in batch._load(journal, page["ref"])["chunks"])
    observed = []
    offset = 0
    while True:
        page = batch.read_batch_results(journal, result, offset=offset, limit=17)
        observed.extend(row["expected"]["request_id"] for row in page["attempts"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert observed == [row["request_id"] for row in data["expected_attempts"]]
    last = batch.read_batch_results(journal, result, offset=96, limit=1)["attempts"][0]
    counter = next(row for row in last["archives"] if row["kind"] == "counterevidence")
    Path(counter["artifact"]["path"]).write_text("Changed hidden counterexample", encoding="utf-8")
    with pytest.raises(StateError, match="hash/size"):
        batch.verify_batch_results(journal, result)
    with pytest.raises(StateError, match="hash/size"):
        ingest(case, data)


def test_missing_counter_or_original_remains_incomplete_instead_of_omitting_denominator(case):
    _, root = case
    data = manifest(root, 3)
    data["rows"][2]["counterevidence"] = []
    data["expected_attempts"][2]["required_originals"] = ["pose_verifier"]
    result = ingest(case, data)
    summary = batch.verify_batch_results(case[0], result)["summary"]
    assert summary["reported"] == summary["expected"] == 3
    assert summary["coverage"] == "incomplete"
    assert summary["missing_required_originals"] == summary["missing_required_counterevidence"] == 1


def test_source_hash_failure_is_retained_and_recoverable_without_duplicate_records(case):
    journal, root = case
    data = manifest(root)
    original = Path(data["rows"][2]["receipt"]["path"])
    correct = original.read_bytes()
    original.write_bytes(b"wrong producer bytes")
    with pytest.raises(EvidenceError, match="identity changed"):
        ingest(case, data)
    failures = list((root / "batches").glob("*/failures/*.json"))
    assert len(failures) == 1
    before_ids = {event["body"]["id"] for event in journal.store.events() if event["kind"] == "research_recorded"}
    original.write_bytes(correct)
    result = ingest(case, data)
    assert batch.verify_batch_results(journal, result)["status"] == "verified"
    events = [event for event in journal.store.events() if event["kind"] == "research_recorded"]
    assert len(events) == len({event["body"]["id"] for event in events})
    assert before_ids <= {event["body"]["id"] for event in events}
    assert failures[0].exists() and not journal.store.actions()


def test_real_process_death_after_originals_saved_recovers_without_producer_or_rerun(case):
    journal, root = case
    data = manifest(root, 5)
    input_file = root / "input.json"
    input_file.write_text(json.dumps(data), encoding="utf-8")
    code = """
import json,os,sys
from etalon.active import CampaignStore
from etalon.research import ResearchJournal
from etalon.research import batch_results as b
original=b._archive
count=0
def crash(*args,**kwargs):
    global count
    value=original(*args,**kwargs)
    count+=1
    if count==4: os._exit(73)
    return value
b._archive=crash
b.ingest_batch_results(ResearchJournal(CampaignStore(sys.argv[1])),json.load(open(sys.argv[2],encoding='utf-8')),sys.argv[3],source_id='batch',source_kind='fixture')
"""
    process = subprocess.run([sys.executable, "-c", code, str(journal.store.path), str(input_file), str(root / "batches")],
        capture_output=True, text=True)
    assert process.returncode == 73, process.stderr
    assert not list((root / "batches").glob("*/receipt.json"))
    # First two attempts have both originals durably copied and recorded.
    for row in data["rows"][:2]:
        Path(row["receipt"]["path"]).unlink()
        Path(row["counterevidence"][0]["path"]).unlink()
    result = ingest(case, data)
    assert batch.verify_batch_results(journal, result)["originals_verified"] == 10
    events = journal.store.events()
    assert ingest(case, data) == result and journal.store.events() == events
    assert len([e for e in events if e["kind"] == "research_recorded"]) == len({e["body"]["id"] for e in events if e["kind"] == "research_recorded"})


@pytest.mark.parametrize("mutation", [
    lambda m: m["rows"].append(deepcopy(m["rows"][0])),
    lambda m: m["rows"][0].update(attempt_id="unknown"),
    lambda m: m["rows"][0].update(receipt=None),
    lambda m: m["rows"][0].update(execution_mode="not_executed"),
    lambda m: m["rows"][0].update(actual_consumption={"wall_second": float("inf")}),
    lambda m: m["rows"][0].update(actual_consumption={"wall_second": True}),
])
def test_malformed_inventory_cannot_create_journal_records(case, mutation):
    journal, root = case
    data = manifest(root)
    mutation(data)
    before = journal.store.events()
    with pytest.raises(ValueError):
        ingest(case, data)
    assert journal.store.events() == before


def test_archived_block_connects_existing_checkpoint_publication_evaluation_and_new_reader(case):
    journal, root = case
    data = manifest(root, 36)
    data["rows"][-1]["status"] = "failed"
    result = ingest(case, data)
    authority = root / "STATUS.txt"
    authority.write_text("Fixture active; archival only.", encoding="utf-8")
    checkpoint = {"schema": CHECKPOINT, "task_id": "fixture", "question": data["question"],
        "step_refs": [result["step_ref"]], "claim_refs": [], "counter_refs": [result["index_ref"]], "method_refs": [],
        "branches": [{"branch_id": "test", "state": "waiting", "basis_refs": [result["summary_ref"]],
            "next_read_refs": [result["index_ref"]], "reconsider_when": "Host checks scientific conclusions"}],
        "pending_handles": [], "source_watermarks": [], "supersedes": [],
        "status_guards": [{"location": str(authority), **file_identity(authority), "state": "active"}]}
    checkpoint_id = record_content(journal, checkpoint, root / "checkpoint", source_id="batch", source_kind="fixture")
    checkpoint_ref = make_ref("batch", checkpoint_id, journal.get(checkpoint_id))
    snapshot = root / "snapshot.sqlite"
    with journal.store.connection() as db, sqlite3.connect(snapshot) as target:
        db.backup(target)
    workspace = root / "workspace.json"
    workspace.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "batch-fixture",
        "sources": [{"source_id": "batch", "kind": "journal", "location": str(snapshot),
            "identity": {"kind": "snapshot_sha256", "value": file_identity(snapshot)["sha256"]}}],
        "status_refs": [str(authority)], "location_map": []}), encoding="utf-8")
    parent = root / "entry.json"
    parent.write_text(json.dumps({"workspace": str(workspace), "checkpoint_ref": checkpoint_ref}), encoding="utf-8")
    # Publication uses its own tested stage receipts and CAS; no new publication engine.
    published_entry = root / "published" / "entry.json"
    step = read_content(workspace, result["step_ref"])
    prepared = prepare_publication(published_entry, operation_id="publish-batch", expected_entry_identity=None,
        parent_entry=parent, steps=[{"name": "batch", "content": step}])
    published = resume_publication(prepared["intent_path"])
    assert published["status"] == "published"
    entry = json.loads(published_entry.read_text(encoding="utf-8"))
    resumed = resume_research(entry["workspace"], entry["checkpoint_ref"])
    assert not resumed["review_required"] and not resumed["automatic_retry"]
    assert resumed["branches"][0]["next_read_refs"] == [result["index_ref"]]
    select = lambda pointer: {"input": "summary", "pointer": pointer}
    contract = build_evaluation_contract(task_id="fixture", scope="Fixture inventory only", conditions={"protocol": "fixture/1"},
        protocol_ref=result["manifest_ref"], initial_conditions_ref=result["manifest_ref"],
        inputs=[{"name": "summary", "kind": "result", "ref": result["summary_ref"]}],
        comparability=[{"check_id": "batch", "selector": select("/batch_id"), "expected": "block-1"}],
        required_checks=[{"check_id": "coverage", "selector": select("/coverage"), "expected": "complete"},
                         {"check_id": "counters", "selector": select("/missing_required_counterevidence"), "expected": 0}],
        metrics=[{"name": "failures", "family": "regression", "measure": {"operation": "value", "left": select("/status_counts/failed"), "right": None},
            "operator": "eq", "threshold": 0, "unit": "attempt", "denominator": "Every expected fixture attempt",
            "denominator_selector": select("/expected"), "missing_policy": "insufficient"}],
        provenance={"mode": "retrospective", "note": "Fixture rule replay", "basis_refs": []})
    report = evaluate_contract(entry=published_entry, contract=contract)
    assert report["status"] == "criteria_failed"
    assert report["adoption"] == "not_performed"
    assert batch.verify_batch_results(journal, result)["expected_verified"] == 36


@pytest.mark.parametrize("role", ["independent_validation", "sealed_holdout"])
def test_confirmation_role_survives_ingestion_and_seals_derived_counts_from_development(case, role):
    journal, root = case
    data = manifest(root, 1)
    data["rows"][0]["counterevidence"][0]["role"] = role
    result = ingest(case, data)
    assert batch.verify_batch_results(journal, result)["status"] == "verified"
    page = batch.read_batch_results(journal, result, limit=1)
    original = next(a for a in page["attempts"][0]["archives"] if a["kind"] == "counterevidence")
    assert journal.get(original["record_id"])["role"] == role
    for key in ("manifest_ref", "index_ref", "summary_ref", "step_ref"):
        assert journal.get(result[key]["record_id"])["role"] == role
    assert journal.get(result["index_ref"]["record_id"])["bindings"]["visibility"] == "sealed"
    for record_id in (original["record_id"], result["summary_ref"]["record_id"]):
        with pytest.raises(StateError, match="independent/holdout"):
            journal.record(Experiment("development", "file_audit", {}, (record_id,),
                "fixture", "trial", ("score",), Coupling()))
    workspace = root / "development-workspace.json"
    workspace.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "development",
        "sources": [{"source_id": "batch", "kind": "journal", "location": str(journal.store.path),
            "identity": {"kind": "snapshot_sha256", "value": file_identity(journal.store.path)["sha256"]}}],
        "location_map": []}), encoding="utf-8")
    visible = query_evidence(workspace, query="", limit=50)
    hidden_ids = {result[key]["record_id"] for key in ("manifest_ref", "index_ref", "summary_ref", "step_ref")}
    assert not hidden_ids & {item["ref"]["record_id"] for item in visible["items"]}
    assert read_evidence(workspace, result["summary_ref"], view="artifact")["availability"] == "unavailable"


def test_range_reader_opens_only_selected_page_and_chunk_and_checks_current_counter(case, monkeypatch):
    journal, root = case
    monkeypatch.setattr(batch, "CHUNK_ATTEMPTS", 4)
    monkeypatch.setattr(batch, "INDEX_PAGE_CHUNKS", 2)
    data = manifest(root, 17)
    result = ingest(case, data)
    index = batch._load(journal, result["index_ref"])
    assert len(index["pages"]) == 3
    last_page = batch._load(journal, index["pages"][-1]["ref"])
    original_load, loaded = batch._load, []

    def tracked(j, ref):
        loaded.append(ref["record_id"])
        return original_load(j, ref)

    monkeypatch.setattr(batch, "_load", tracked)
    page = batch.read_batch_results(journal, result, offset=16, limit=1)
    assert page["attempts"][0]["expected"]["request_id"] == "request-16"
    assert loaded == [result["index_ref"]["record_id"], index["pages"][-1]["ref"]["record_id"],
                      last_page["chunks"][0]["ref"]["record_id"]]
    assert page["next_cursor"] is None
    # A same-size edit, including restored mtime, must fail a selected current
    # counterexample check. It cannot be replaced by the old ingest audit.
    counter = next(a for a in page["attempts"][0]["archives"] if a["kind"] == "counterevidence")
    path = Path(counter["artifact"]["path"])
    stamp = path.stat()
    path.write_bytes(b"X" * stamp.st_size)
    import os
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert batch.read_batch_results(journal, result, offset=0, limit=1)["attempts"]
    with pytest.raises(StateError, match="hash/size"):
        batch.read_batch_results(journal, result, offset=16, limit=1)
    with pytest.raises(StateError, match="hash/size"):
        batch.verify_batch_results(journal, result)


def test_cursor_is_version_bound_and_cross_chunk_range_is_complete(case, monkeypatch):
    journal, root = case
    monkeypatch.setattr(batch, "CHUNK_ATTEMPTS", 4)
    monkeypatch.setattr(batch, "INDEX_PAGE_CHUNKS", 2)
    data = manifest(root, 10)
    result = ingest(case, data)
    page = batch.read_batch_results(journal, result, offset=3, limit=4)
    assert [r["expected"]["request_id"] for r in page["attempts"]] == ["request-3", "request-4", "request-5", "request-6"]
    following = batch.read_batch_results(journal, result, cursor=page["next_cursor"], limit=4)
    assert [r["expected"]["request_id"] for r in following["attempts"]] == ["request-7", "request-8", "request-9"]
    # Same batch ID with a different immutable index is also a different cursor
    # version. Receipt/intent checks still run before version acceptance.
    changed = deepcopy(result)
    changed["index_ref"] = result["summary_ref"]
    with pytest.raises(EvidenceError, match="receipt|cursor version"):
        batch.read_batch_results(journal, changed, cursor=page["next_cursor"], limit=4)
    another = manifest(root, 2)
    another["batch_id"] = "block-2"
    another_receipt = ingest(case, another)
    with pytest.raises(EvidenceError, match="cursor version"):
        batch.read_batch_results(journal, another_receipt, cursor=page["next_cursor"], limit=4)
    with pytest.raises(EvidenceError, match="cursor version"):
        batch.read_batch_results(journal, result, cursor={**page["next_cursor"], "offset": -1}, limit=4)
    assert batch.read_batch_results(journal, result, offset=100, limit=4)["attempts"] == []


@pytest.mark.parametrize("attack", ["directory_gap", "directory_count", "page_offsets", "archive_counter_removed"])
def test_range_reader_rejects_directory_or_selected_inventory_attacks(case, monkeypatch, attack):
    journal, root = case
    monkeypatch.setattr(batch, "CHUNK_ATTEMPTS", 2)
    monkeypatch.setattr(batch, "INDEX_PAGE_CHUNKS", 2)
    result = ingest(case, manifest(root, 5))
    original_load = batch._load

    def attacked(j, ref):
        value = deepcopy(original_load(j, ref))
        if attack == "directory_gap" and value["schema"] == batch.RANGE_INDEX:
            value["pages"][-1]["attempt_offset"] += 1
        if attack == "directory_count" and value["schema"] == batch.RANGE_INDEX:
            value["chunk_count"] += 1
        if attack == "page_offsets" and value["schema"] == batch.INDEX_PAGE:
            value["chunks"][0]["offset"] += 1
        if attack == "archive_counter_removed" and value["schema"] == batch.CHUNK:
            value["attempts"][0]["archives"].pop()
        return value

    monkeypatch.setattr(batch, "_load", attacked)
    with pytest.raises(EvidenceError, match="range|count|order|inventory|omits"):
        batch.read_batch_results(journal, result, offset=4, limit=1)


def test_legacy_index_keeps_bounded_chunk_loading_and_full_audit(case, monkeypatch):
    journal, root = case
    monkeypatch.setattr(batch, "CHUNK_ATTEMPTS", 2)
    monkeypatch.setattr(batch, "INDEX_PAGE_CHUNKS", 2)
    original_json = batch._json

    def legacy_json(j, content, *args, **kwargs):
        if content["schema"] == batch.RANGE_INDEX:
            content = deepcopy(content)
            content["schema"] = batch.INDEX
            for page in content["pages"]:
                del page["attempt_offset"], page["attempt_count"]
        return original_json(j, content, *args, **kwargs)

    monkeypatch.setattr(batch, "_json", legacy_json)
    result = ingest(case, manifest(root, 7))
    assert batch._load(journal, result["index_ref"])["schema"] == batch.INDEX
    assert batch.verify_batch_results(journal, result)["expected_verified"] == 7
    result.pop("default_role")  # Compatibility with earlier receipt payloads.
    batch._save(result["receipt_path"], result, replace=True)
    original_load, schemas = batch._load, []

    def tracked(j, ref):
        value = original_load(j, ref)
        schemas.append(value["schema"])
        return value

    monkeypatch.setattr(batch, "_load", tracked)
    assert batch.read_batch_results(journal, result, offset=6, limit=1)["attempts"][0]["expected"]["request_id"] == "request-6"
    assert schemas.count(batch.INDEX_PAGE) == 2 and schemas.count(batch.CHUNK) == 1


def test_bounded_read_defers_full_intent_bytes_to_explicit_audit(case):
    journal, root = case
    result = ingest(case, manifest(root, 2))
    intent = Path(result["receipt_path"]).parent / "intent.json"
    intent.write_bytes(intent.read_bytes() + b" ")
    page = batch.read_batch_results(journal, result, limit=1)
    assert page["attempts"][0]["expected"]["request_id"] == "request-0"
    assert "full intent/manifest" in page["verification_scope"]
    with pytest.raises(EvidenceError, match="intent identity changed"):
        batch.verify_batch_results(journal, result)


def test_range_retains_frozen_required_counterevidence_when_row_omits_it(case):
    journal, root = case
    data = manifest(root, 3)
    data["rows"][-1]["counterevidence"] = []
    data["expected_attempts"][-1]["required_originals"] = ["independent_verifier"]
    result = ingest(case, data)
    page = batch.read_batch_results(journal, result, offset=2, limit=1)
    assert page["attempts"][0]["expected"]["required_counterevidence"] == ["counter"]
    assert page["range_coverage"] == {"expected": 1, "reported": 1, "missing": 0,
        "required_originals": 1, "required_counterevidence": 1,
        "missing_required_originals": 1, "missing_required_counterevidence": 1, "coverage": "incomplete"}
