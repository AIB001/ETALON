"""Publication failures are recoverable archival work, never scientific retries."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research import Artifact, ResearchJournal
from etalon.research.continuation import CHECKPOINT, STEP, read_content, record_content, resume_research
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.publication import (
    STAGES, discover_publications, inspect_publication, prepare_publication, resume_publication,
)


@pytest.fixture
def case(tmp_path):
    old = tmp_path / "frozen"
    old.mkdir()
    store = CampaignStore(old / "research.sqlite")
    store.configure(CampaignSpec("archive", 0, "wall_second", "fixture/1"),
        [Endpoint("archive", "fixture", "archive", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    evidence = old / "evidence.txt"
    evidence.write_text("fixture existing measurement; no scientific execution", encoding="utf-8")
    artifact = Artifact(str(evidence), **file_identity(evidence), source_kind="fixture", role="diagnostic", bindings={})
    journal.record(artifact)
    ref = make_ref("prior", artifact.id, artifact.as_dict())
    step = {"schema": STEP, "task_id": "fixture", "question": "Can the result be reused?",
        "branch_id": "existing", "operation": "decision", "reason": "Already measured under matching conditions",
        "input_refs": [ref], "output_refs": [], "method_refs": [], "accounting_refs": [],
        "source_watermarks": [], "supersedes": [], "detail": {"choice": "reuse", "alternatives": [],
            "basis_refs": [ref], "reconsider_when": "New conditions"}}
    rid = record_content(journal, step, old / "content", source_id="prior", source_kind="fixture")
    step_ref = make_ref("prior", rid, journal.get(rid))
    status = old / "PAUSED.md"
    status.write_text("Fixture paused. No science authorization.", encoding="utf-8")
    checkpoint = {"schema": CHECKPOINT, "task_id": "fixture", "question": step["question"],
        "step_refs": [step_ref], "claim_refs": [], "counter_refs": [], "method_refs": [],
        "branches": [{"branch_id": "existing", "state": "resolved", "basis_refs": [ref],
                      "next_read_refs": [], "reconsider_when": "New conditions"}], "pending_handles": [],
        "source_watermarks": [], "supersedes": [], "status_guards": [
            {"location": str(status), **file_identity(status), "state": "paused"}]}
    rid = record_content(journal, checkpoint, old / "content", source_id="prior", source_kind="fixture")
    cp_ref = make_ref("prior", rid, journal.get(rid))
    workspace = old / "workspace.json"
    workspace.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "fixture",
        "sources": [{"source_id": "prior", "kind": "journal", "location": str(store.path),
            "identity": {"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]}}],
        "status_refs": [str(status)], "location_map": []}), encoding="utf-8")
    parent = old / "entry.json"
    parent.write_text(json.dumps({"workspace": str(workspace), "checkpoint_ref": cp_ref}), encoding="utf-8")
    entry = tmp_path / "current" / "entry.json"
    result = tmp_path / "producer-result.json"
    result.write_text('{"fixture": true, "value": 1}', encoding="utf-8")
    request = {"entry": str(entry), "operation_id": "archive-result-1", "expected_entry_identity": None,
        "parent_entry": str(parent), "results": [{"name": "result", "path": str(result),
            "identity": file_identity(result), "source_kind": "fixture", "role": "diagnostic", "bindings": {}}],
        "steps": [{"name": "reuse", "content": {**step, "reason": "Explicitly archive a completed result",
            "output_refs": [{"publication_ref": "result"}]}}]}
    return {"request": request, "old": old, "old_identities": {str(p): file_identity(p) for p in old.rglob("*") if p.is_file()},
            "entry": entry, "parent": parent, "checkpoint": checkpoint, "result": result}


def assert_old_unchanged(case):
    assert {str(p): file_identity(p) for p in case["old"].rglob("*") if p.is_file()} == case["old_identities"]


def test_lossless_publication_and_idempotent_operation(case):
    prepared = prepare_publication(**case["request"])
    assert prepared["status"] == "prepared" and not case["entry"].exists()
    assert discover_publications(case["entry"])["incomplete_count"] == 1
    published = resume_publication(prepared["intent_path"])
    entry = json.loads(case["entry"].read_text())
    cp = read_content(entry["workspace"], entry["checkpoint_ref"])
    for key in ("branches", "pending_handles", "status_guards", "claim_refs", "question", "task_id"):
        assert cp[key] == case["checkpoint"][key]
    assert cp["step_refs"][:-1] == case["checkpoint"]["step_refs"]
    assert cp["supersedes"] == [json.loads(case["parent"].read_text())["checkpoint_ref"]]
    resumed = resume_research(entry["workspace"], entry["checkpoint_ref"])
    assert resumed["authority"]["state"] == "paused" and not resumed["automatic_retry"]
    assert not resumed["issues"] and not resumed["pending"]
    root = Path(prepared["intent_path"]).parent
    journal = CampaignStore(root / "research.sqlite", read_only=True)
    events = journal.events()
    identities = {str(p): file_identity(p) for p in root.rglob("*") if p.is_file()}
    assert prepare_publication(**case["request"])["status"] == "published"
    assert resume_publication(prepared["intent_path"]) == published
    assert journal.events() == events and not journal.actions()
    assert identities == {str(p): file_identity(p) for p in root.rglob("*") if p.is_file()}
    assert discover_publications(case["entry"])["incomplete_count"] == 0
    assert_old_unchanged(case)


@pytest.mark.parametrize("phase", STAGES)
def test_real_process_death_at_each_durable_boundary(case, phase):
    prepared = prepare_publication(**case["request"])
    script = """
import os,sys
from etalon.research import publication as p
original = p._stage
def crash(root, phase, payload):
    result = original(root, phase, payload)
    if phase == sys.argv[2]: os._exit(73)
    return result
p._stage = crash
p.resume_publication(sys.argv[1])
"""
    process = subprocess.run([sys.executable, "-c", script, prepared["intent_path"], phase], capture_output=True, text=True)
    assert process.returncode == 73, process.stderr
    before = inspect_publication(prepared["intent_path"])
    assert before["phase"] == phase
    assert discover_publications(case["entry"])["operations"][0]["phase"] == phase
    # Recovery has saved bytes; producer output can disappear without a rerun.
    case["result"].unlink()
    published = resume_publication(prepared["intent_path"])
    assert published["status"] == "published" and published["phase"] == "complete"
    journal = CampaignStore(Path(prepared["intent_path"]).parent / "research.sqlite", read_only=True)
    events = journal.events()
    assert len([e for e in events if e["kind"] == "research_recorded"]) == 3
    assert not journal.actions()
    resume_publication(prepared["intent_path"])
    assert journal.events() == events
    assert_old_unchanged(case)


def test_same_operation_changed_payload_conflicts_even_after_publish(case):
    prepared = prepare_publication(**case["request"])
    resume_publication(prepared["intent_path"])
    changed = json.loads(json.dumps(case["request"]))
    changed["steps"][0]["content"]["reason"] = "Different meaning"
    with pytest.raises(EvidenceError, match="operation id"):
        prepare_publication(**changed)
    assert_old_unchanged(case)


def test_two_intents_cannot_silently_replace_same_parent(case):
    first = prepare_publication(**case["request"])
    second = prepare_publication(**{**case["request"], "operation_id": "competing"})
    resume_publication(first["intent_path"])
    identity = file_identity(case["entry"])
    with pytest.raises(EvidenceError, match="compare-and-swap"):
        resume_publication(second["intent_path"])
    assert inspect_publication(second["intent_path"])["status"] == "conflict"
    assert discover_publications(case["entry"])["incomplete_count"] == 1
    assert file_identity(case["entry"]) == identity
    assert_old_unchanged(case)


def test_successor_appends_new_source_without_mutating_first_snapshot(case):
    first = prepare_publication(**case["request"])
    resume_publication(first["intent_path"])
    old_root = Path(first["intent_path"]).parent
    saved = file_identity(old_root / "snapshot.sqlite")
    second_request = {**case["request"], "parent_entry": None, "operation_id": "later-result",
                      "expected_entry_identity": file_identity(case["entry"])}
    second = prepare_publication(**second_request)
    resume_publication(second["intent_path"])
    assert file_identity(old_root / "snapshot.sqlite") == saved
    assert inspect_publication(first["intent_path"])["status"] == "superseded"
    assert resume_publication(first["intent_path"])["status"] == "superseded"
    entry = json.loads(case["entry"].read_text())
    assert len(json.loads(Path(entry["workspace"]).read_text())["sources"]) == 3
    assert not resume_research(entry["workspace"], entry["checkpoint_ref"])["issues"]
    assert_old_unchanged(case)


def test_tampered_result_cannot_replace_saved_output(case):
    prepared = prepare_publication(**case["request"])
    case["result"].write_text("different bytes", encoding="utf-8")
    with pytest.raises(EvidenceError, match="producer result identity changed"):
        resume_publication(prepared["intent_path"])
    assert not case["entry"].exists()
    assert discover_publications(case["entry"])["incomplete_count"] == 1
    assert_old_unchanged(case)


def test_forbidden_checkpoint_edits_rejected_and_exact_parent_required(case):
    with pytest.raises(EvidenceError, match="cannot silently change"):
        prepare_publication(**{**case["request"], "checkpoint_additions": {"pending_handles": []}})
    first = prepare_publication(**case["request"])
    resume_publication(first["intent_path"])
    with pytest.raises(EvidenceError, match="compare-and-swap"):
        prepare_publication(**{**case["request"], "operation_id": "wrong-parent"})


def test_broken_indexed_intent_stays_visible_and_reads_do_not_write(case):
    prepared = prepare_publication(**case["request"])
    before = {str(p): file_identity(p) for p in case["entry"].parent.rglob("*") if p.is_file()}
    assert discover_publications(case["entry"])["incomplete_count"] == 1
    assert before == {str(p): file_identity(p) for p in case["entry"].parent.rglob("*") if p.is_file()}
    Path(prepared["intent_path"]).unlink()
    state = discover_publications(case["entry"])
    assert state["incomplete_count"] == 1 and state["operations"][0]["status"] == "unavailable"


def test_changed_frozen_source_fails_before_new_archival_writes(case):
    prepared = prepare_publication(**case["request"])
    with (case["old"] / "research.sqlite").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(EvidenceError, match="parent source changed"):
        resume_publication(prepared["intent_path"])
    assert not (Path(prepared["intent_path"]).parent / "research.sqlite").exists()


@pytest.mark.parametrize("boundary", ["journal-1", "journal-2", "journal-3", "entry-no-receipt"])
def test_crash_between_effect_and_receipt_is_not_repeated(case, boundary):
    prepared = prepare_publication(**case["request"])
    script = """
import os,sys
from etalon.research import publication as p
boundary=sys.argv[2]
original_record=p.ResearchJournal.record
count=0
def record(self,value):
    global count
    result=original_record(self,value)
    count+=1
    if boundary == 'journal-'+str(count): os._exit(73)
    return result
p.ResearchJournal.record=record
original_save=p._save
def save(path,value,**kwargs):
    result=original_save(path,value,**kwargs)
    if boundary == 'entry-no-receipt' and str(path)==sys.argv[3] and kwargs.get('replace'): os._exit(73)
    return result
p._save=save
p.resume_publication(sys.argv[1])
"""
    process = subprocess.run([sys.executable, "-c", script, prepared["intent_path"], boundary,
                              str(case["entry"])], capture_output=True, text=True)
    assert process.returncode == 73, process.stderr
    result = resume_publication(prepared["intent_path"])
    assert result["status"] == "published" and result["phase"] == "complete"
    journal = CampaignStore(Path(prepared["intent_path"]).parent / "research.sqlite", read_only=True)
    assert len([e for e in journal.events() if e["kind"] == "research_recorded"]) == 3
    assert not journal.actions()
    assert_old_unchanged(case)


def test_published_output_damage_does_not_return_idempotent_success(case):
    prepared = prepare_publication(**case["request"])
    resume_publication(prepared["intent_path"])
    root = Path(prepared["intent_path"]).parent
    result = next((root / "results").iterdir())
    result.write_text("damaged", encoding="utf-8")
    with pytest.raises(EvidenceError, match="result bytes changed"):
        resume_publication(prepared["intent_path"])
    assert_old_unchanged(case)


def test_external_artifact_cannot_masquerade_as_claim(case):
    request = case["request"]
    request["checkpoint_additions"] = {"claim_refs": request["steps"][0]["content"]["input_refs"]}
    prepared = prepare_publication(**request)
    with pytest.raises((EvidenceError, json.JSONDecodeError)):
        resume_publication(prepared["intent_path"])
    assert not case["entry"].exists()
    assert_old_unchanged(case)


def test_new_entry_fork_detects_parent_entry_changed_after_prepare(case):
    prepared = prepare_publication(**case["request"])
    with case["parent"].open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(EvidenceError, match="parent entry changed"):
        resume_publication(prepared["intent_path"])
    assert not case["entry"].exists()


def test_sealed_result_cannot_enter_published_step_context(case):
    case["request"]["results"][0]["role"] = "sealed_holdout"
    prepared = prepare_publication(**case["request"])
    with pytest.raises(EvidenceError):
        resume_publication(prepared["intent_path"])
    assert not case["entry"].exists()
    assert_old_unchanged(case)


@pytest.mark.parametrize("boundary", ["index", "intent"])
def test_prepare_crash_is_discoverable_before_any_results_are_archived(case, boundary, tmp_path):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(case["request"]), encoding="utf-8")
    script = """
import json,os,sys
from pathlib import Path
from etalon.research import publication as p
original=p._save
def save(path,value,**kwargs):
    result=original(path,value,**kwargs)
    if (sys.argv[2]=='index' and str(path).endswith('.publications.json')) or (sys.argv[2]=='intent' and Path(path).name=='intent.json'): os._exit(73)
    return result
p._save=save
p.prepare_publication(**json.loads(Path(sys.argv[1]).read_text()))
"""
    process = subprocess.run([sys.executable, "-c", script, str(request_path), boundary], capture_output=True, text=True)
    assert process.returncode == 73, process.stderr
    observed = discover_publications(case["entry"])
    assert observed["incomplete_count"] == 1
    assert observed["operations"][0]["status"] == ("unavailable" if boundary == "index" else "prepared")
    repaired = prepare_publication(**case["request"])
    assert resume_publication(repaired["intent_path"])["status"] == "published"
    assert_old_unchanged(case)


def test_operation_identity_is_per_entry_even_if_directory_changes(case, tmp_path):
    first = prepare_publication(**case["request"])
    repeated = prepare_publication(**case["request"], directory=tmp_path / "other")
    assert repeated["intent_path"] == first["intent_path"]
    changed = json.loads(json.dumps(case["request"]))
    changed["steps"][0]["content"]["reason"] = "Changed meaning"
    with pytest.raises(EvidenceError, match="different publication content"):
        prepare_publication(**changed, directory=tmp_path / "other")


def test_completed_fork_replay_allows_parent_pointer_to_advance(case):
    prepared = prepare_publication(**case["request"])
    resume_publication(prepared["intent_path"])
    identity = file_identity(case["entry"])
    with case["parent"].open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert resume_publication(prepared["intent_path"])["status"] == "published"
    assert file_identity(case["entry"]) == identity
