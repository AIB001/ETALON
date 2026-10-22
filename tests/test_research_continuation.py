"""Fixture counterexamples for optional append-only memory and read-only resume."""
import json
from pathlib import Path
import subprocess
import sys
from functools import partial

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical, digest
from etalon.active.store import StateError
from etalon.research import Artifact, ResearchJournal
from etalon.research.continuation import (
    CHECKPOINT, STEP, capture_watermarks, read_content, record_content, resume_research, validate_content,
)
from etalon.research.evidence import query_evidence, read_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref


@pytest.fixture(params=["fresh", "frozen_view"], autouse=True)
def resume_read_mode(request, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "resume_research", partial(resume_research, read_mode=request.param))


@pytest.fixture
def case(tmp_path):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("audit", 0, "wall_second", "continuation-fixture"),
                    [Endpoint("audit", "fixture", "unmeasured", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    evidence = tmp_path / "input.txt"
    evidence.write_text("fixture released evidence")
    artifact = Artifact(str(evidence), **file_identity(evidence), source_kind="fixture", role="diagnostic", bindings={})
    journal.record(artifact)
    ref = make_ref("research", artifact.id, journal.get(artifact.id))
    authority = tmp_path / "STATUS.md"
    authority.write_text("Active fixture task; no science authorization.")
    return journal, ref, authority, tmp_path


def step(ref, **changes):
    return {"schema": STEP, "task_id": "case", "question": "Can the available evidence be reused?",
            "branch_id": "existing", "operation": "decision", "reason": "Conditions match the released evidence",
            "input_refs": [ref], "output_refs": [], "method_refs": [], "accounting_refs": [],
            "source_watermarks": [], "supersedes": [],
            "detail": {"choice": "reuse", "alternatives": [{"branch_id": "repeat", "disposition": "skip",
                       "reason": "No new discrimination"}], "basis_refs": [ref], "reconsider_when": "Conditions change"}, **changes}


def save(case, content):
    journal, _, _, root = case
    rid = record_content(journal, content, root / "content", source_id="research", source_kind="fixture")
    return make_ref("research", rid, journal.get(rid))


def publish(case):
    journal, _, authority, root = case
    path = root / "workspace.json"
    path.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "continuation-fixture",
        "sources": [{"source_id": "research", "kind": "journal", "location": str(journal.store.path),
                     "identity": {"kind": "snapshot_sha256", "value": file_identity(journal.store.path)["sha256"]}}],
        "status_refs": [str(authority)], "location_map": []}))
    return path


def checkpoint(case, step_ref, **changes):
    _, ref, authority, _ = case
    return {"schema": CHECKPOINT, "task_id": "case", "question": "Can the available evidence be reused?",
            "step_refs": [step_ref], "claim_refs": [], "counter_refs": [], "method_refs": [],
            "branches": [{"branch_id": "existing", "state": "resolved", "basis_refs": [ref],
                          "next_read_refs": [ref], "reconsider_when": "New conditions or counterevidence"}],
            "pending_handles": [], "source_watermarks": [], "supersedes": [],
            "status_guards": [{"location": str(authority), **file_identity(authority), "state": "active"}], **changes}


def test_ordinary_artifacts_project_query_read_and_resume_idempotently(case):
    journal, ref, _, _ = case
    saved = save(case, step(ref))
    events = journal.store.events()
    assert save(case, step(ref)) == saved and journal.store.events() == events
    cp = save(case, checkpoint(case, saved))
    ws = publish(case)
    before = file_identity(journal.store.path)
    assert query_evidence(ws, query="Conditions match", filters={"task_ids": ["case"]})["items"][0]["ref"] == saved
    assert read_content(ws, saved) == step(ref)
    assert ref in read_evidence(ws, saved, view="lineage")["items"]
    result = resume_research(ws, cp)
    assert not result["review_required"]
    assert ref in result["checked_refs"]
    assert result["execution_permission"] == "not_granted" and not result["automatic_retry"]
    assert file_identity(journal.store.path) == before and not journal.store.actions()


def test_db_failure_leaves_complete_orphan_and_retry_reuses_it(case, monkeypatch):
    journal, ref, _, root = case
    original = journal.record
    monkeypatch.setattr(journal, "record", lambda _: (_ for _ in ()).throw(RuntimeError("injected DB failure")))
    with pytest.raises(RuntimeError, match="DB failure"):
        save(case, step(ref))
    files = list((root / "content").glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text()) == step(ref)
    identity = file_identity(files[0])
    monkeypatch.setattr(journal, "record", original)
    saved = save(case, step(ref))
    assert journal.get(saved["record_id"])["sha256"] == identity["sha256"]
    assert len(list((root / "content").iterdir())) == 1


def test_local_digest_and_original_file_checked_before_write(case):
    journal, ref, _, root = case
    with pytest.raises(EvidenceError, match="digest"):
        save(case, step({**ref, "record_digest": "0" * 64}))
    assert not (root / "content").exists()
    Path(journal.get(ref["record_id"])["path"]).write_text("tampered")
    with pytest.raises(StateError, match="hash"):
        save(case, step(ref))


def test_plan_cannot_contain_fake_execution_and_results_require_actual_records(case):
    _, ref, _, _ = case
    plan = step(ref, operation="plan", detail={"hypothesis_refs": [ref], "prediction": "Different readouts",
        "controls": ["frozen input"], "readouts": [{"name": "loss", "unit": "fraction"}],
        "protocol_refs": [ref], "decision_rule": "Compare", "stop_rule": "Evidence sufficient"})
    save(case, plan)
    plan["detail"]["action_ids"] = ["made-up"]
    with pytest.raises(EvidenceError):
        save(case, plan)
    with pytest.raises(EvidenceError, match="Experiment"):
        save(case, step(ref, operation="result", detail={"experiment_refs": [ref], "observation_refs": [ref], "action_handles": []}))


def test_failed_search_not_empty_complete_success(case):
    _, ref, _, _ = case
    content = step(ref, operation="search", detail={"query": "new evidence", "source_ids": ["a"],
        "conditions": "matching protocol", "coverage": [{"source_id": "a", "status": "complete", "truncated": False,
        "errors": ["source offline"]}], "selected_refs": [], "excluded": [], "refresh_when": "Source returns"})
    with pytest.raises(EvidenceError, match="complete coverage"):
        save(case, content)
    content["detail"]["coverage"][0]["status"] = "unavailable"
    save(case, content)


def test_changed_authority_and_paused_authority_override_old_frontier(case):
    _, ref, authority, _ = case
    saved = save(case, step(ref))
    cp = save(case, checkpoint(case, saved))
    ws = publish(case)
    authority.write_text("User paused this task. Do not resume.")
    changed = resume_research(ws, cp)
    assert changed["review_required"] and changed["authority"]["state"] == "review_required"
    assert "paused" in changed["authority"]["sources"][0]["current_excerpt"]
    content = checkpoint(case, saved)
    content["status_guards"][0]["state"] = "paused"
    content["branches"][0]["state"] = "active"
    paused_ref = save(case, content)
    paused = resume_research(publish(case), paused_ref)
    assert paused["authority"]["state"] == "paused" and paused["review_required"]
    assert paused["execution_permission"] == "not_granted"


def test_missing_or_new_status_authority_requires_review(case):
    _, ref, authority, root = case
    cp = save(case, checkpoint(case, save(case, step(ref))))
    ws = publish(case)
    config = json.loads(ws.read_text())
    new = root / "NEW_STATUS.md"; new.write_text("New project restriction")
    config["status_refs"].append(str(new)); ws.write_text(json.dumps(config))
    assert resume_research(ws, cp)["review_required"]
    authority.unlink()
    assert resume_research(ws, cp)["authority"]["state"] == "review_required"


def test_changed_source_watermark_and_damaged_evidence_require_review(case):
    _, ref, _, root = case
    stable = root / "stable.json"
    stable.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": []}))
    ws = publish(case)
    config = json.loads(ws.read_text())
    extra = {"source_id": "input", "kind": "artifact_manifest", "location": str(stable),
             "identity": {"kind": "snapshot_sha256", "value": file_identity(stable)["sha256"]}}
    config["sources"].append(extra); ws.write_text(json.dumps(config))
    marks = capture_watermarks(ws, source_ids=["input"])
    cp = save(case, checkpoint(case, save(case, step(ref)), source_watermarks=marks))
    ws = publish(case)
    config = json.loads(ws.read_text()); config["sources"].append(extra); ws.write_text(json.dumps(config))
    assert not resume_research(ws, cp)["review_required"]
    stable.write_text(stable.read_text() + "\n")
    extra["identity"]["value"] = file_identity(stable)["sha256"]
    config["sources"][-1] = extra; ws.write_text(json.dumps(config))
    result = resume_research(ws, cp)
    assert result["review_required"] and result["source_changes"][0]["status"] == "review_required"
    (root / "input.txt").unlink()
    result = resume_research(ws, cp)
    assert any(i["reason"] == "artifact_unavailable_or_changed" for i in result["issues"])


def test_new_process_needs_only_workspace_and_checkpoint(case):
    journal, ref, _, _ = case
    cp = save(case, checkpoint(case, save(case, step(ref))))
    ws = publish(case)
    before = file_identity(journal.store.path)
    process = subprocess.run([sys.executable, "-c", "import json,sys; from etalon.research.continuation import resume_research; "
        "print(json.dumps(resume_research(sys.argv[1],json.loads(sys.argv[2]))))", str(ws), canonical(cp)],
        capture_output=True, text=True, check=True)
    result = json.loads(process.stdout)
    assert result["task_id"] == "case" and not result["review_required"]
    assert file_identity(journal.store.path) == before


def test_pending_handle_is_read_not_resubmitted_and_cost_unchanged(case, monkeypatch):
    from test_runtime_actions import fixture_action, submit
    from etalon.runtime import api
    journal, ref, _, root = case
    store, request, calls, _ = fixture_action(root / "runtime-fixture", monkeypatch, outcome="pending")
    handle = submit(request, root / "runtime-fixture")
    cp = save(case, checkpoint(case, save(case, step(ref)), pending_handles=[handle, handle]))
    ws = publish(case)
    before, events = store.export(), journal.store.events()
    monkeypatch.setattr(api, "submit_action", lambda *a, **kw: pytest.fail("resume dispatched"))
    first, second = resume_research(ws, cp), resume_research(ws, cp)
    assert len(first["pending"]) == len(second["pending"]) == 1
    assert first["pending"][0]["execution_state"] == "unresolved"
    assert len(calls) == 1 and store.export() == before and journal.store.events() == events


def test_newer_checkpoint_never_silently_replaces_selected_one(case):
    _, ref, _, _ = case
    s = save(case, step(ref))
    first = save(case, checkpoint(case, s))
    newer = checkpoint(case, s, supersedes=[first])
    newer["branches"][0]["state"] = "open"
    latest = save(case, newer)
    result = resume_research(publish(case), first)
    assert result["checkpoint_ref"] == first and result["review_required"]
    assert any(i["reason"] == "other_task_checkpoints_require_review" for i in result["issues"])
    assert not resume_research(publish(case), latest)["review_required"]


def test_correction_marks_dependent_checkpoint_for_review_without_reexecution(case):
    journal, ref, _, _ = case
    old = save(case, step(ref))
    cp = save(case, checkpoint(case, old))
    changed = step(ref, reason="New evidence invalidates the reuse condition", supersedes=[old])
    changed["detail"]["choice"] = "defer"
    save(case, changed)
    result = resume_research(publish(case), cp)
    assert result["review_required"]
    assert any(i["reason"] == "referenced_record_superseded_requires_review" for i in result["issues"])
    assert journal.store.actions() == []


def test_content_bound_and_unsupported_fields_do_not_silently_drop(case):
    _, ref, _, _ = case
    with pytest.raises(EvidenceError, match="fields"):
        validate_content({**step(ref), "automatic_execute": True})
    with pytest.raises(EvidenceError, match="16 KiB"):
        validate_content({**step(ref), "reason": "x" * 17000})


def test_checkpoint_rejects_nonstep_and_unrecorded_new_branch_is_reported(case):
    _, ref, _, _ = case
    with pytest.raises(EvidenceError, match="wrong schema"):
        save(case, checkpoint(case, ref))
    selected = save(case, step(ref))
    cp = save(case, checkpoint(case, selected))
    extra = step(ref, branch_id="new-hypothesis", reason="A new uncheckpointed branch")
    save(case, extra)
    result = resume_research(publish(case), cp)
    assert result["review_required"]
    assert any(i["reason"] == "uncheckpointed_task_step_requires_review" for i in result["issues"])
