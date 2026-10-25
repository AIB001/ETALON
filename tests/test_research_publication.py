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
    STAGES, close_publication, discover_publications, inspect_publication, prepare_publication, resume_publication,
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


def transition_request(case, *, state="resolved", choice="reuse"):
    request = json.loads(json.dumps(case["request"]))
    decision = request["steps"][0]["content"]
    decision["detail"].update(choice=choice, basis_refs=[{"publication_ref": "result"}],
                              reconsider_when="New identity-bound evidence changes this condition")
    request["branch_updates"] = [{"branch_id": "existing", "expected_state": "resolved", "state": state,
        "basis_refs": decision["detail"]["basis_refs"], "next_read_refs": [{"publication_ref": "result"}],
        "reconsider_when": decision["detail"]["reconsider_when"], "decision_ref": {"publication_ref": "reuse"}}]
    return request


def test_explicit_branch_transition_keeps_scope_authority_and_counterevidence(case):
    request = transition_request(case, state="active", choice="reconsider")
    request["checkpoint_additions"] = {"counter_refs": case["checkpoint"]["branches"][0]["basis_refs"]}
    prepared = prepare_publication(**request)
    published = resume_publication(prepared["intent_path"])
    entry = json.loads(case["entry"].read_text())
    cp = read_content(entry["workspace"], entry["checkpoint_ref"])
    branch = cp["branches"][0]
    assert branch["state"] == "active" and len(branch["basis_refs"]) == 2
    assert branch["basis_refs"][-1] == cp["step_refs"][-1]
    assert branch["next_read_refs"] == branch["basis_refs"][:1]
    for key in ("task_id", "question", "pending_handles", "status_guards", "source_watermarks"):
        assert cp[key] == case["checkpoint"][key]
    assert cp["counter_refs"] == case["checkpoint"]["branches"][0]["basis_refs"]
    resumed = resume_research(entry["workspace"], entry["checkpoint_ref"])
    assert resumed["authority"]["state"] == "paused" and resumed["execution_permission"] == "not_granted"
    identities = {str(p): file_identity(p) for p in Path(prepared["intent_path"]).parent.rglob("*") if p.is_file()}
    assert resume_publication(prepared["intent_path"]) == published
    assert identities == {str(p): file_identity(p) for p in Path(prepared["intent_path"]).parent.rglob("*") if p.is_file()}
    assert_old_unchanged(case)


@pytest.mark.parametrize("problem", ["prior_state", "unknown_branch", "duplicate", "old_decision", "basis",
    "reconsider_when", "nondecision", "other_branch", "state_choice", "unbased_read", "authority", "empty"])
def test_invalid_branch_transition_rejected_before_archival_write(case, problem):
    request = transition_request(case)
    update = request["branch_updates"][0]
    if problem == "prior_state": update["expected_state"] = "waiting"
    elif problem == "unknown_branch": update["branch_id"] = "unregistered"
    elif problem == "duplicate": request["branch_updates"].append(dict(update))
    elif problem == "old_decision": update["decision_ref"] = case["checkpoint"]["step_refs"][0]
    elif problem == "basis": update["basis_refs"] = case["checkpoint"]["branches"][0]["basis_refs"]
    elif problem == "reconsider_when": update["reconsider_when"] = "A different condition"
    elif problem == "nondecision": update["decision_ref"] = {"publication_ref": "result"}
    elif problem == "other_branch": request["steps"][0]["content"]["branch_id"] = "elsewhere"
    elif problem == "state_choice": update["state"] = "active"
    elif problem == "unbased_read": update["next_read_refs"] = case["checkpoint"]["step_refs"]
    elif problem == "authority": update["status_guards"] = []
    elif problem == "empty": request["branch_updates"] = []
    with pytest.raises(EvidenceError):
        prepare_publication(**request)
    assert not case["entry"].exists()
    assert not list(case["entry"].parent.rglob("intent.json"))
    assert_old_unchanged(case)


@pytest.mark.parametrize("phase", ["checkpoint_recorded", "entry_replaced"])
def test_branch_transition_recovers_without_repeating_native_work(case, phase):
    request = transition_request(case)
    prepared = prepare_publication(**request)
    script = """
import os,sys
from etalon.research import publication as p
stage = p._stage
def crash(root, phase, payload):
    result = stage(root, phase, payload)
    if phase == sys.argv[2]: os._exit(73)
    return result
p._stage = crash
p.resume_publication(sys.argv[1])
"""
    process = subprocess.run([sys.executable, "-c", script, prepared["intent_path"], phase], capture_output=True, text=True)
    assert process.returncode == 73, process.stderr
    case["result"].unlink()
    assert resume_publication(prepared["intent_path"])["status"] == "published"
    entry = json.loads(case["entry"].read_text())
    cp = read_content(entry["workspace"], entry["checkpoint_ref"])
    assert cp["branches"][0]["basis_refs"][-1] == cp["step_refs"][-1]
    assert not CampaignStore(Path(prepared["intent_path"]).parent / "research.sqlite", read_only=True).actions()
    assert_old_unchanged(case)


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
    with pytest.raises((EvidenceError, json.JSONDecodeError)):
        prepare_publication(**request)
    assert not case["entry"].exists()
    assert not Path(str(case["entry"]) + ".publications.json").exists()
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
    with pytest.raises(EvidenceError):
        prepare_publication(**case["request"])
    assert not case["entry"].exists()
    assert not Path(str(case["entry"]) + ".publications.json").exists()
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


@pytest.mark.parametrize("problem", ["operation", "choice", "missing_field", "extra_field", "unknown_alias",
    "forward_alias", "self_alias", "malformed_alias", "bad_ref", "bad_digest", "oversized_step", "result_type"])
def test_static_invalid_publication_is_rejected_before_any_archival_write(case, problem):
    request = case["request"]
    content = request["steps"][0]["content"]
    if problem == "operation":
        content["operation"] = "pretend_execute"
    elif problem == "choice":
        content["detail"]["choice"] = "approve_without_evidence"
    elif problem == "missing_field":
        del content["reason"]
    elif problem == "extra_field":
        content["execute_now"] = True
    elif problem == "unknown_alias":
        content["output_refs"] = [{"publication_ref": "missing"}]
    elif problem == "forward_alias":
        request["steps"].append({"name": "later", "content": json.loads(json.dumps(content))})
        content["output_refs"] = [{"publication_ref": "later"}]
    elif problem == "self_alias":
        content["output_refs"] = [{"publication_ref": "reuse"}]
    elif problem == "malformed_alias":
        content["output_refs"] = [{"publication_ref": "result", "extra": 1}]
    elif problem == "bad_ref":
        content["input_refs"] = [{"schema": "not-an-evidence-ref"}]
    elif problem == "bad_digest":
        content["input_refs"][0]["record_digest"] = "0" * 64
    elif problem == "oversized_step":
        content["reason"] = "x" * 16384
    elif problem == "result_type":
        content["operation"] = "result"
        content["detail"] = {"experiment_refs": content["input_refs"],
                             "observation_refs": content["input_refs"], "action_handles": []}
    with pytest.raises(EvidenceError):
        prepare_publication(**request)
    assert not case["entry"].exists()
    assert not (case["entry"].parent / ".etalon").exists()
    assert not Path(str(case["entry"]) + ".publications.json").exists()
    assert_old_unchanged(case)


def test_preflight_catches_checkpoint_overflow_not_only_individual_steps(case):
    from etalon.active.schema import canonical
    from etalon.research.continuation import MAX_CONTENT_BYTES
    old = case["old"]
    journal = ResearchJournal(CampaignStore(old / "research.sqlite"))
    content = json.loads(json.dumps(case["checkpoint"]))
    content["question"] += "x" * (MAX_CONTENT_BYTES - len(canonical(content).encode()) - 1)
    rid = record_content(journal, content, old / "content", source_id="prior", source_kind="fixture")
    parent = json.loads(case["parent"].read_text())
    parent["checkpoint_ref"] = make_ref("prior", rid, journal.get(rid))
    case["parent"].write_text(json.dumps(parent))
    workspace = Path(parent["workspace"])
    manifest = json.loads(workspace.read_text())
    manifest["sources"][0]["identity"]["value"] = file_identity(journal.store.path)["sha256"]
    workspace.write_text(json.dumps(manifest))
    case["old_identities"] = {str(p): file_identity(p) for p in old.rglob("*") if p.is_file()}
    with pytest.raises(EvidenceError, match="exceeds 16 KiB"):
        prepare_publication(**case["request"])
    assert not Path(str(case["entry"]) + ".publications.json").exists()
    assert_old_unchanged(case)


def test_preflight_allows_ordered_aliases_without_changing_real_record_identity(case):
    import etalon.research.publication as p
    first = case["request"]["steps"][0]
    second = json.loads(json.dumps(first))
    second["name"] = "later"
    second["content"]["input_refs"] = [{"publication_ref": "reuse"}]
    second["content"]["reason"] = "Explicit later archival decision"
    case["request"]["steps"].append(second)
    prepared = prepare_publication(**case["request"])
    assert not (Path(prepared["intent_path"]).parent / "research.sqlite").exists()
    resumed = resume_publication(prepared["intent_path"])
    assert resumed["status"] == "published"
    refs = json.loads((Path(prepared["intent_path"]).parent / "stages" / "03-steps_recorded.json").read_text())["refs"]
    entry = json.loads(case["entry"].read_text())
    later = read_content(entry["workspace"], refs["later"])
    assert later["input_refs"] == [refs["reuse"]]


def test_withdraw_keeps_partial_data_and_is_exactly_idempotent(case, monkeypatch):
    import etalon.research.publication as p
    prepared = prepare_publication(**case["request"])
    original = p._stage
    def interrupt(root, phase, payload):
        result = original(root, phase, payload)
        if phase == "results_recorded":
            raise RuntimeError("fixture interruption")
        return result
    monkeypatch.setattr(p, "_stage", interrupt)
    with pytest.raises(RuntimeError, match="fixture interruption"):
        resume_publication(prepared["intent_path"])
    root = Path(prepared["intent_path"]).parent
    before = {str(path): file_identity(path) for path in root.rglob("*") if path.is_file()}
    arguments = {"disposition": "withdrawn", "reason": "Input protocol needs explicit correction",
                 "expected_intent_identity": prepared["intent_identity"]}
    closed = close_publication(prepared["intent_path"], **arguments)
    assert closed["status"] == "withdrawn" and closed["terminal"] and not closed["needs_recovery"]
    assert closed["phase"] == "results_recorded"
    assert discover_publications(case["entry"])["incomplete_count"] == 0
    assert all(file_identity(path) == identity for path, identity in before.items())
    identity = file_identity(root / "disposition.json")
    assert close_publication(prepared["intent_path"], **arguments) == closed
    assert file_identity(root / "disposition.json") == identity
    with pytest.raises(EvidenceError, match="terminal publication"):
        resume_publication(prepared["intent_path"])
    with pytest.raises(EvidenceError, match="immutable"):
        close_publication(prepared["intent_path"], **{**arguments, "reason": "A new rationale"})
    assert_old_unchanged(case)


def test_corrected_operation_explicitly_replaces_failed_intent(case):
    old = prepare_publication(**case["request"])
    correction = json.loads(json.dumps(case["request"]))
    correction.update(operation_id="corrected", corrects_intent=old["intent_path"],
                      correction_reason="Use the exact existing evidence reference")
    correction["steps"][0]["content"]["reason"] = "Reviewed corrected archival meaning"
    new = prepare_publication(**correction)
    arguments = {"disposition": "replaced", "reason": "Explicit correction ready for archival",
        "expected_intent_identity": old["intent_identity"], "replacement_intent": new["intent_path"]}
    closed = close_publication(old["intent_path"], **arguments)
    assert closed["status"] == "replaced" and not closed["needs_recovery"]
    assert closed["disposition"]["request"]["replacement"]["operation_id"] == "corrected"
    assert discover_publications(case["entry"])["incomplete_count"] == 1
    assert resume_publication(new["intent_path"])["status"] == "published"
    assert prepare_publication(**correction)["status"] == "published"
    assert close_publication(old["intent_path"], **arguments)["status"] == "replaced"
    assert discover_publications(case["entry"])["incomplete_count"] == 0
    assert_old_unchanged(case)


def test_unrelated_operation_cannot_be_claimed_as_replacement(case):
    old = prepare_publication(**case["request"])
    unrelated = prepare_publication(**{**case["request"], "operation_id": "unrelated"})
    with pytest.raises(EvidenceError, match="explicitly correct"):
        close_publication(old["intent_path"], disposition="replaced", reason="Not linked",
            expected_intent_identity=old["intent_identity"], replacement_intent=unrelated["intent_path"])
    assert not (Path(old["intent_path"]).parent / "disposition.json").exists()


@pytest.mark.parametrize("successful_state", ["published", "superseded", "before_receipt"])
def test_successful_publication_cannot_be_marked_failed(case, monkeypatch, successful_state):
    import etalon.research.publication as p
    old = prepare_publication(**case["request"])
    if successful_state == "before_receipt":
        original = p._stage
        def interrupt(root, phase, payload):
            if phase == "entry_replaced":
                raise RuntimeError("pointer committed")
            return original(root, phase, payload)
        monkeypatch.setattr(p, "_stage", interrupt)
        with pytest.raises(RuntimeError, match="pointer committed"):
            resume_publication(old["intent_path"])
    else:
        resume_publication(old["intent_path"])
    if successful_state == "superseded":
        with case["entry"].open("a") as handle:
            handle.write("\n")
    before = file_identity(case["entry"])
    with pytest.raises(EvidenceError, match="successful publication"):
        close_publication(old["intent_path"], disposition="withdrawn", reason="Cannot recast success",
                          expected_intent_identity=old["intent_identity"])
    assert file_identity(case["entry"]) == before
    assert not (Path(old["intent_path"]).parent / "disposition.json").exists()


def test_stale_intent_identity_cannot_close_or_hide_failed_progress(case):
    prepared = prepare_publication(**case["request"])
    with pytest.raises(EvidenceError, match="identity mismatch"):
        close_publication(prepared["intent_path"], disposition="withdrawn", reason="Review old request",
            expected_intent_identity={"sha256": "0" * 64, "bytes": prepared["intent_identity"]["bytes"]})
    assert discover_publications(case["entry"])["incomplete_count"] == 1


def test_terminal_disposition_process_death_is_replayable(case):
    prepared = prepare_publication(**case["request"])
    script = """
import os,sys
from pathlib import Path
from etalon.research import publication as p
original=p._save
def save(path,value,**kwargs):
    result=original(path,value,**kwargs)
    if Path(path).name=='disposition.json': os._exit(73)
    return result
p._save=save
state=p.inspect_publication(sys.argv[1])
p.close_publication(sys.argv[1],disposition='withdrawn',reason='Explicit archival withdrawal',expected_intent_identity=state['intent_identity'])
"""
    process = subprocess.run([sys.executable, "-c", script, prepared["intent_path"]], capture_output=True, text=True)
    assert process.returncode == 73, process.stderr
    closed = close_publication(prepared["intent_path"], disposition="withdrawn", reason="Explicit archival withdrawal",
                               expected_intent_identity=prepared["intent_identity"])
    assert closed["status"] == "withdrawn" and discover_publications(case["entry"])["incomplete_count"] == 0


def test_close_and_publisher_share_the_same_cross_process_lock(case):
    from etalon.host import _consumer_lock
    prepared = prepare_publication(**case["request"])
    script = """
import sys
from etalon.research.publication import close_publication,inspect_publication
state=inspect_publication(sys.argv[1])
try:
    close_publication(sys.argv[1],disposition='withdrawn',reason='Explicit withdrawal',expected_intent_identity=state['intent_identity'])
except OSError:
    sys.exit(75)
sys.exit(0)
"""
    with _consumer_lock(case["entry"].parent / (case["entry"].name + ".publication")):
        process = subprocess.run([sys.executable, "-c", script, prepared["intent_path"]], capture_output=True, text=True)
    assert process.returncode == 75, process.stderr
    assert inspect_publication(prepared["intent_path"])["status"] == "prepared"
    assert not (Path(prepared["intent_path"]).parent / "disposition.json").exists()
    assert close_publication(prepared["intent_path"], disposition="withdrawn", reason="Explicit withdrawal",
        expected_intent_identity=prepared["intent_identity"])["status"] == "withdrawn"


def test_correction_does_not_cross_destination_or_rewrite_closed_request(case, tmp_path):
    old = prepare_publication(**case["request"])
    with pytest.raises(EvidenceError, match="same destination"):
        prepare_publication(**{**case["request"], "entry": str(tmp_path / "different" / "entry.json"),
            "operation_id": "cross-entry", "corrects_intent": old["intent_path"], "correction_reason": "Not proven ancestry"})
    close_publication(old["intent_path"], disposition="withdrawn", reason="Explicit abandonment",
                      expected_intent_identity=old["intent_identity"])
    with pytest.raises(EvidenceError, match="unfinished publication"):
        prepare_publication(**{**case["request"], "operation_id": "too-late", "corrects_intent": old["intent_path"],
                               "correction_reason": "Do not rewrite a terminal operation"})
    assert prepare_publication(**case["request"])["status"] == "withdrawn"
