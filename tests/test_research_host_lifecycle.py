"""Control-boundary tests; fixture hosts are not a scientific/live Codex claim."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from etalon.research.evidence_refs import EvidenceError
from test_research_publication import case
from test_research_notes import case as note_case
from etalon.research.notes import save_progress
from etalon.runtime.files import file_identity
from etalon.runtime.host_wait import inspect_wait

spec = importlib.util.spec_from_file_location("research_host_lifecycle", Path(__file__).parents[1] / "examples/research_host_lifecycle.py")
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


def setup(tmp_path, states):
    capsule = {"schema": lifecycle.CAPSULE, "task_id": "fixture", "entry": str(tmp_path / "entry.json"),
        "question": "fixture question", "scope": "fixture", "authority": "fixture only", "budget": {"native_runs": 0},
        "completion_condition": "host judges fixture complete", "host_instructions": "fixture host",
        "native": {"codex_executable": str(tmp_path / "codex"), "cwd": str(tmp_path), "model": "fixture", "reasoning_effort": "fixture"},
        "bounds": {"max_host_turns": 3, "max_wall_seconds": 60}}
    path = tmp_path / "capsule.json"
    lifecycle.save(path, capsule)
    calls = []

    def host(body, folder, session):
        folder.mkdir()
        calls.append(session)
        checkpoint = {"schema": lifecycle.CHECKPOINT, "task_id": body["task_id"], "state": states[len(calls) - 1],
            "entry": body["entry"], "summary": "fixture checkpoint", "basis_refs": [], "wake_after_seconds": 0}
        receipt = {"session_id": "fixture-session", "resumed_session_id": session, "session_continuity_verified": True,
            "exit_code": 0, "checkpoint": checkpoint, "finished_at": lifecycle.stamp(), "wall_seconds": 0.25,
            "usage": [{"input_tokens": 100, "cached_input_tokens": 70, "output_tokens": 20}]}
        lifecycle.save(folder / "receipt.json", receipt)
        return receipt
    return path, capsule, calls, host


def test_normal_exit_continues_saved_session_and_reentry_does_not_repeat(tmp_path):
    path, _, calls, host = setup(tmp_path, ["continue", "complete"])
    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert calls == [None, "fixture-session"]
    assert result["stop_reason"] == "host_complete"
    assert result["usage"]["uncached_input_tokens"] == 60
    assert result["host_wall_seconds"] == 0.5
    again = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert again["stop_reason"] == "host_complete" and len(calls) == 2


def test_bound_is_checkpoint_not_scientific_completion(tmp_path):
    path, _, calls, host = setup(tmp_path, ["continue"] * 3)
    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert result["stop_reason"] == "host_turn_bound"
    assert result["last_checkpoint"]["state"] == "continue" and len(calls) == 3
    assert result["external_compute_terminated"] is False


def test_unknown_process_outcome_never_relaunches(tmp_path):
    path, _, calls, host = setup(tmp_path, ["complete"])
    (tmp_path / "run" / "turn-001").mkdir(parents=True)
    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert result["stop_reason"] == "unknown_host_outcome" and not calls


def test_capsule_change_and_invalid_completion_rejected(tmp_path):
    path, capsule, _, host = setup(tmp_path, ["needs_user"])
    lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    capsule["authority"] = "changed"
    lifecycle.save(path, capsule)
    with pytest.raises(EvidenceError, match="immutable"):
        lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    with pytest.raises(EvidenceError, match="normal exit"):
        lifecycle.validate_checkpoint({"state": "complete"}, capsule)


def test_native_resume_targets_exact_session_and_stdin_only(tmp_path):
    _, capsule, _, _ = setup(tmp_path, [])
    argv = lifecycle.native_argv(capsule, tmp_path / "run" / "turn-002", "saved-session")
    assert argv[1:4] == ["exec", "resume", "saved-session"]
    assert "--last" not in argv and argv[-1] == "-"
    assert "--ephemeral" not in argv and "--output-schema" in argv


def test_natural_goal_builds_work_from_current_entry_and_profile(case, tmp_path):
    _, profile, _, _ = setup(tmp_path, [])
    body = lifecycle.capsule_from_goal("Answer this residual from saved evidence", case["parent"], profile)
    assert body["question"] == "Answer this residual from saved evidence"
    assert body["initial_work"]["branch_id"] == "existing"
    assert body["initial_work"]["status_guards"] == case["checkpoint"]["status_guards"]
    assert body["authority"] == profile["authority"]
    assert "No full parent conversation" in lifecycle.prompt_for(body, tmp_path, None)


def test_fresh_context_lifecycle_does_not_inject_saved_session_history(tmp_path):
    path, capsule, calls, host = setup(tmp_path, ["continue", "complete"])
    capsule["session_mode"] = "fresh"
    lifecycle.save(path, capsule)
    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert calls == [None, None]
    assert result["session_mode"] == "fresh" and result["stop_reason"] == "host_complete"


def test_note_goal_keeps_selected_branch_scope_and_recipe_and_reads_new_head(note_case, tmp_path):
    journal, note, _ = note_case
    note.update(status="waiting", pending_handles=["existing-job"],
                next_action="Interpret the pending result", completed_refs=["already-measured"],
                definitions={"metric:count": "a" * 64}, invalid_when=["different endpoint"],
                authorization={"scope": "fixture only"}, other_pauses={"R4b": "paused"})
    entry = tmp_path / "note-entry.json"
    save_progress(journal, entry, note)
    save_progress(journal, entry, {**note, "branch_id": "other", "summary": "other branch"}, activate=False)
    value = lifecycle.read(entry)
    value["read_recipe"] = {"view": "index", "limit": 5}
    lifecycle.save(entry, value)
    _, profile, _, _ = setup(tmp_path, [])
    capsule = lifecycle.capsule_from_goal("Interpret pending results", entry, profile, branch_id="anchor")
    current = capsule["initial_context"]
    assert current["branch_id"] == "anchor" and current["details"]["branch_id"] == "anchor"
    assert capsule["branch_catalog"]["entry"] == str(entry)
    assert capsule["branch_catalog"]["sdk"].endswith("build_resource_catalog")
    assert current["definitions"] == note["definitions"]
    assert current["knowledge"]["next_action"] == note["next_action"]
    assert current["knowledge"]["pending_handles"] == ["existing-job"]
    assert current["controls"]["other_pauses"] == {"R4b": "paused"}
    assert capsule["read_recipe"] == value["read_recipe"]
    assert capsule["authority"] == profile["authority"]
    save_progress(journal, entry, {**note, "summary": "Result became available"})
    prompt = lifecycle.prompt_for(capsule, tmp_path / "turn-001", None)
    assert "Result became available" in prompt and "current_context" in prompt
    assert "already-measured" in prompt or "/completed_refs" in prompt
    with pytest.raises(ValueError, match="absent"):
        lifecycle.capsule_from_goal("goal", entry, profile, branch_id="missing")


def test_explicit_note_context_budget_applies_to_initial_and_fresh_reads(note_case, tmp_path):
    journal, note, _ = note_case
    note.update(definitions={"metric:count": "a" * 64}, authorization={"scope": "fixture only"},
        other_pauses={"R4b": "paused"}, knowledge={str(i): "known result " * 100 for i in range(30)},
        next_action="Read the explicitly scoped counterevidence.")
    entry=tmp_path/"bounded-entry.json"
    save_progress(journal,entry,note)
    _,profile,_,_=setup(tmp_path,[])
    profile["context_max_bytes"]=4096
    capsule=lifecycle.capsule_from_goal("Continue this scoped result",entry,profile)
    assert capsule["context_max_bytes"]==4096
    prompt=lifecycle.prompt_for(capsule,tmp_path/"turn-001",None)
    current=json.loads(prompt.split("Task capsule:\n",1)[1])["current_context"]
    for context in (capsule["initial_context"],current):
        assert len(json.dumps(context,ensure_ascii=False,separators=(",",":")).encode())<=4096
        assert context["definitions"]==note["definitions"]
        assert context["controls"]["authorization"]==note["authorization"]
        assert context["controls"]["other_pauses"]=={"R4b":"paused"}
        assert context["coverage"]["counter_count"]==1
        assert "counterevidence" in context["followup"]
        assert context["coverage"]["knowledge"]=="partial"
    profile.pop("context_max_bytes")
    default=lifecycle.capsule_from_goal("Continue this scoped result",entry,profile)
    assert "context_max_bytes" not in default and lifecycle.context_budget(default)==16384


@pytest.mark.parametrize("value", [4095,65537,"4096",True])
def test_context_budget_rejects_unbounded_or_ambiguous_values(tmp_path,value):
    _,capsule,_,_=setup(tmp_path,[])
    capsule["context_max_bytes"]=value
    with pytest.raises(EvidenceError,match="4096..65536"):
        lifecycle.validate_capsule(capsule)


def test_fresh_prompt_includes_last_checkpoint_and_observed_wait_result(tmp_path):
    _, capsule, _, _ = setup(tmp_path, [])
    lifecycle.save(tmp_path / "receipt.json", {"task_id": capsule["task_id"],
        "last_checkpoint": {"summary": "finished earlier batch"},
        "wait_observation": {"state": "ready", "checks": []}})
    prompt = lifecycle.prompt_for(capsule, tmp_path / "turn-002", None)
    assert "finished earlier batch" in prompt and '"wait_observation"' in prompt


def test_native_launch_respects_existing_trust_profile(tmp_path):
    _, capsule, _, _ = setup(tmp_path, [])
    argv = lifecycle.native_argv(capsule, tmp_path / "turn-001", None)
    assert not any("approval_policy" in arg or "sandbox_mode" in arg for arg in argv)
    capsule["native"].update(approval_policy="never", sandbox_mode="read-only")
    argv = lifecycle.native_argv(capsule, tmp_path / "turn-001", None)
    assert 'sandbox_mode="read-only"' in argv


@pytest.mark.parametrize("session", [None, "saved-session"])
def test_explicit_git_check_skip_supports_narrow_cwd_without_changing_trust(tmp_path, session):
    _, capsule, _, _ = setup(tmp_path, [])
    narrow = tmp_path / "narrow-host-cwd"
    narrow.mkdir()
    capsule["native"]["cwd"] = str(narrow)
    before = lifecycle.native_argv(capsule, tmp_path / "turn-001", session)
    assert "--skip-git-repo-check" not in before
    capsule["native"]["skip_git_repo_check"] = True
    lifecycle.validate_capsule(capsule)
    argv = lifecycle.native_argv(capsule, tmp_path / "turn-001", session)
    assert argv.count("--skip-git-repo-check") == 1
    assert [arg for arg in argv if arg != "--skip-git-repo-check"] == before
    assert capsule["entry"] != str(narrow) and Path(capsule["entry"]).is_absolute()
    if session is None:
        assert argv[argv.index("-C") + 1] == str(narrow)
    else:
        assert argv[1:4] == ["exec", "resume", session]
    capsule["native"]["skip_git_repo_check"] = False
    assert lifecycle.native_argv(capsule, tmp_path / "turn-001", session) == before
    capsule["native"]["skip_git_repo_check"] = "true"
    with pytest.raises(EvidenceError, match="explicit boolean"):
        lifecycle.validate_capsule(capsule)


def test_exit_zero_does_not_prove_resumed_session_without_native_thread_event(tmp_path, monkeypatch):
    """A real Python subprocess simulates CLI transport, not a native Codex run."""
    _, capsule, _, _ = setup(tmp_path, [])
    folder = tmp_path / "run" / "turn-001"
    folder.parent.mkdir()
    lifecycle.save(folder.parent / "capsule.json", capsule)
    checkpoint = {"schema": lifecycle.CHECKPOINT, "task_id": capsule["task_id"], "state": "complete",
        "entry": capsule["entry"], "summary": "synthetic transport", "basis_refs": [], "wake_after_seconds": 0}
    code = ("import sys,pathlib,json;sys.stdin.read();"
            "pathlib.Path(sys.argv[1]).write_text(sys.argv[2],encoding='utf-8');"
            "print(json.dumps({'type':'turn.completed','usage':"
            "{'input_tokens':1,'cached_input_tokens':0,'output_tokens':1}}))")
    monkeypatch.setattr(lifecycle, "native_argv", lambda *args: [
        sys.executable, "-c", code, str(folder / "checkpoint.json"), json.dumps(checkpoint)])
    result = lifecycle.host_turn(capsule, folder, "expected-session")
    assert result["exit_code"] == 0 and result["checkpoint"]["state"] == "complete"
    assert result["session_id"] is None and not result["session_continuity_verified"]


@pytest.mark.parametrize("exit_code, wait_state", [(0, "ready"), (7, "failed")])
def test_real_child_finishes_after_fixture_host_exits_and_resumes_once(tmp_path, exit_code, wait_state):
    """Real detached local computation; host decisions are synthetic, not Codex."""
    from etalon.runtime.native import launch_detached_attempt, inspect_attempt
    path, _, calls, fixture_host = setup(tmp_path, ["waiting", "complete"])
    attempt, output = tmp_path / "attempt", tmp_path / "completed.json"
    command = [sys.executable, "-c", "import pathlib,sys,time;time.sleep(1);pathlib.Path(sys.argv[1]).write_text('42');sys.exit(int(sys.argv[2]))", str(output), str(exit_code)]
    request = {"request_id": "fixture-wait", "attempt_id": "one"}
    launch_detached_attempt(attempt, request, command, cwd=tmp_path, outputs={"result": str(output)})
    deadline = time.monotonic() + 10
    while inspect_attempt(attempt)["state"] == "unknown" and time.monotonic() < deadline:
        time.sleep(0.025)
    assert inspect_attempt(attempt)["state"] == "running"

    def host(body, folder, session):
        if session:
            assert output.read_text() == "42"
            assert inspect_attempt(attempt)["state"] == "finished-unpublished"
            assert ('"state": "' + wait_state + '"') in lifecycle.prompt_for(body, folder, session)
        receipt = fixture_host(body, folder, session)
        if session is None:
            lifecycle.save(folder / "wait.json", {"schema": "etalon-host-wait/1", "poll_seconds": 0.05,
                "checks": [{"kind": "native_attempt", "path": str(attempt), **request}]})
        return receipt

    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert calls == [None, "fixture-session"] and result["stop_reason"] == "host_complete"
    observed = lifecycle.read(tmp_path / "run/turn-001/wait-state.json")
    assert observed["state"] == wait_state and observed["check_count"] >= 2
    assert observed["read_wall_seconds"] > 0
    assert result["wait_checks"] == observed["check_count"]
    assert result["wait_check_wall_seconds"] == observed["read_wall_seconds"]
    assert not (attempt / "receipt.json").exists()  # Inspect did not publish or settle.
    assert lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)["host_turns"] == 2
    assert len(calls) == 2


@pytest.mark.parametrize("body, expected", [('{broken', "check_error"), ('{"status":"running"}', "running"),
    ('{"status":"failed"}', "failed"), ('{"status":"unrecognized"}', "unknown"), ('{"status":"done"}', "ready")])
def test_declarative_check_distinguishes_running_failure_unknown_and_read_errors(tmp_path, body, expected):
    state = tmp_path / "status.json"
    state.write_text(body)
    result = inspect_wait({"schema": "etalon-host-wait/1", "checks": [{"kind": "json_status",
        "path": str(state), "field": "status", "running": ["running"], "ready": ["done"], "failed": ["failed"]}]})
    assert result["state"] == expected


def test_bad_check_stops_without_another_llm_turn_and_can_be_reentered_after_repair(tmp_path):
    path, _, calls, fixture_host = setup(tmp_path, ["waiting", "complete"])
    status = tmp_path / "status.json"
    status.write_text('{invalid')

    def host(body, folder, session):
        receipt = fixture_host(body, folder, session)
        if session is None:
            lifecycle.save(folder / "wait.json", {"schema": "etalon-host-wait/1", "checks": [
                {"kind": "json_status", "path": str(status), "field": "state", "ready": ["done"]}]})
        return receipt

    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert result["stop_reason"] == "wait_check_error" and calls == [None]
    assert result["wait_observation"]["checks"][0]["state"] == "check_error"
    lifecycle.save(status, {"state": "done"})
    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert result["stop_reason"] == "host_complete" and calls == [None, "fixture-session"]
    assert result["wait_check_errors"] == 1


def test_wait_bound_is_visible_without_llm_polling_or_job_retry(tmp_path):
    path, _, calls, fixture_host = setup(tmp_path, ["waiting"])

    def host(body, folder, session):
        receipt = fixture_host(body, folder, session)
        lifecycle.save(folder / "wait.json", {"schema": "etalon-host-wait/1", "poll_seconds": 0.05,
            "max_checks": 2, "checks": [{"kind": "file", "path": str(tmp_path / "not-yet-finished")}]})
        return receipt

    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert result["stop_reason"] == "wait_check_bound" and calls == [None]
    assert result["wait_observation"]["state"] == "running" and result["wait_observation"]["check_count"] == 2


def test_unknown_usage_is_null_and_exact_child_receipts_deduplicate(tmp_path):
    assert lifecycle.usage_summary([{"usage": []}], tmp_path)["input_tokens"] is None
    folder = tmp_path / "turn-001"
    folder.mkdir()
    child = tmp_path / "child.json"
    lifecycle.save(child, {"session_id": "child-exact", "usage_id": "request-1",
        "usage": {"input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 4, "cost_usd": 0.02}})
    source = {"session_id": "child-exact", "usage_id": "request-1", "receipt": str(child), "identity": file_identity(child)}
    lifecycle.save(folder / "usage-sources.json", [source, source])
    result = lifecycle.usage_summary([{"usage": []}], tmp_path)
    assert result["input_tokens"] == 12 and result["measured_usage_records"] == 1
    assert result["known_cost_usd"] == 0.02 and result["cost_usd"] is None
    assert result["coverage"] == "partial"
    source["usage_id"] = "wrong-request"
    lifecycle.save(folder / "usage-sources.json", [source])
    result = lifecycle.usage_summary([{"usage": []}], tmp_path)
    assert result["input_tokens"] is None and result["issues"]


def test_conflicting_exact_usage_is_unknown_not_arbitrarily_selected(tmp_path):
    records = [{"session_id": "same", "usage": [{"usage_id": "one", "input_tokens": 12,
        "cached_input_tokens": 3, "output_tokens": 4}]},
        {"session_id": "same", "usage": [{"usage_id": "one", "input_tokens": 13,
        "cached_input_tokens": 3, "output_tokens": 4}]}]
    result = lifecycle.usage_summary(records, tmp_path)
    assert result["input_tokens"] is None and result["coverage"] == "partial"
