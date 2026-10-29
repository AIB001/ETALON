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
from etalon.runtime.host_wait import inspect_host_process, inspect_wait

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


def test_shared_wait_handle_continues_once_without_polling_science(tmp_path, monkeypatch):
    from etalon.runtime.host_wait import register_wait, read_wait
    path, _, calls, fixture_host = setup(tmp_path, ["waiting", "complete"])
    marker = tmp_path / "completed"
    marker.write_text("fixture completion marker")
    handle = register_wait({"schema": "etalon-host-wait/1", "poll_seconds": .05,
        "max_checks": 10, "checks": [{"kind": "file", "path": str(marker)}]}, str(tmp_path))["handle"]
    deadline = time.monotonic() + 10
    while not read_wait(handle)["terminal"] and time.monotonic() < deadline:
        time.sleep(.05)
    monkeypatch.setattr(lifecycle, "inspect_wait", lambda _: pytest.fail("polled science instead of shared handle"))

    def host(body, folder, session):
        receipt = fixture_host(body, folder, session)
        if session is None:
            lifecycle.save(folder / "wait.json", handle)
        return receipt

    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert result["stop_reason"] == "host_complete" and calls == [None, "fixture-session"]
    assert lifecycle.read(tmp_path / "run/turn-001/wait-state.json")["source_check_count"] == 1
    lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    assert calls == [None, "fixture-session"]


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


def continuation():
    return {"objective": "Resolve the fixture scientific question",
        "completion_condition": "All fixture counterevidence has been interpreted",
        "authorized_actions": ["Read fixture outputs and continue this scoped interpretation"],
        "resume_after_host_exit": True}


def test_segment_completion_continues_lasting_objective_with_existing_authority(tmp_path):
    path, capsule, calls, fixture_host = setup(tmp_path, ["segment_complete", "complete"])
    capsule["continuation"] = continuation()
    lifecycle.save(path, capsule)
    prompt = lifecycle.prompt_for(capsule, tmp_path / "turn-001", None)
    assert "completion_condition describes this local segment" in prompt
    assert "without asking for\nthe same permission again" in prompt
    assert capsule["continuation"]["authorized_actions"][0] in prompt
    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=fixture_host)
    assert calls == [None, "fixture-session"]
    assert result["objective_complete"] is True
    assert result["completion_scope"] == "continuation_objective"
    assert result["stop_reason"] == "host_complete"
    assert lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=fixture_host)["objective_complete"]
    assert len(calls) == 2


def test_natural_goal_preserves_explicit_continuation_profile(case, tmp_path):
    _, profile, _, _ = setup(tmp_path, [])
    profile["continuation"] = continuation()
    body = lifecycle.capsule_from_goal("Finish this local segment", case["parent"], profile)
    assert body["continuation"] == profile["continuation"]
    assert body["completion_condition"] == profile["completion_condition"]
    profile["continuation"]["resume_after_host_exit"] = "yes"
    with pytest.raises(EvidenceError, match="continuation"):
        lifecycle.capsule_from_goal("goal", case["parent"], profile)


def abandoned_host(tmp_path, capsule):
    """Real owned subprocess, synthetic host artifacts; never a native Codex claim."""
    from etalon.runtime.native import process_identity
    folder = tmp_path / "run" / "turn-001"
    folder.mkdir(parents=True)
    process = subprocess.Popen([sys.executable, "-c", "import sys;sys.stdin.read()"], stdin=subprocess.PIPE)
    identity = process_identity(process.pid)
    assert identity and identity["alive"]
    started = lifecycle.stamp()
    lifecycle.save(folder / "process.json", {"pid": process.pid, "identity": identity, "started_at": started})
    lifecycle.save(folder / "session.json", {"session_id": "fixture-session"})
    # run_lifecycle creates its immutable capsule before inspecting this folder.
    lifecycle.save(folder.parent / "capsule.json", capsule)
    lifecycle.save(folder.parent / "started.json", {"started_at": started})
    lifecycle.save(folder / "intent.json", {"schema": "etalon-host-turn-intent/1", "started_at": started,
        "argv": lifecycle.native_argv(capsule, folder, None), "resumed_session_id": None,
        "capsule_identity": file_identity(folder.parent / "capsule.json")})
    (folder / "prompt.txt").write_text("synthetic fixture prompt")
    (folder / "events.jsonl").write_text(json.dumps({"type": "thread.started", "thread_id": "fixture-session"}) + "\n")
    lifecycle.save(capsule["entry"], {"fixture": "prior work remains available"})
    return folder, process


def test_proven_host_exit_resumes_judgment_once_without_repeating_science(tmp_path):
    path, capsule, calls, fixture_host = setup(tmp_path, ["complete"])
    capsule["continuation"] = continuation()
    lifecycle.save(path, capsule)
    folder, process = abandoned_host(tmp_path, capsule)
    try:
        running = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=fixture_host)
        assert running["stop_reason"] == "host_still_running" and not calls
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        assert inspect_host_process(lifecycle.read(folder / "process.json"))["state"] == "exited"

        def resume(body, turn, session):
            assert session == "fixture-session"
            prompt = lifecycle.prompt_for(body, turn, session)
            assert "scientific outcome is unresolved" in prompt
            assert "Do not resubmit" in prompt
            assert '"scientific_retry": false' in prompt
            assert lifecycle.read(capsule["entry"])["fixture"] == "prior work remains available"
            return fixture_host(body, turn, session)

        result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=resume)
        assert calls == ["fixture-session"] and result["stop_reason"] == "host_complete"
        recovered = lifecycle.read(folder / "receipt.json")
        assert recovered["checkpoint"] is None and recovered["exit_code"] is None
        assert recovered["wall_seconds"] is None and recovered["reconciliation_required"]
        assert result["host_wall_seconds"] is None and result["known_host_wall_seconds"] == 0.25
        assert result["unknown_host_wall_turns"] == 1
        assert result["usage"]["coverage"] == "partial"
        assert result["usage"]["unknown_usage_records_or_turns"] == 1
        assert lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=resume)["host_turns"] == 2
        assert calls == ["fixture-session"]
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.wait(timeout=10)


@pytest.mark.parametrize("change", ["unauthorized", "session", "intent", "entry"])
def test_dead_host_does_not_resume_without_authority_or_consistent_evidence(tmp_path, change):
    path, capsule, calls, fixture_host = setup(tmp_path, ["complete"])
    if change != "unauthorized":
        capsule["continuation"] = continuation()
    lifecycle.save(path, capsule)
    folder, process = abandoned_host(tmp_path, capsule)
    process.stdin.close()
    process.wait(timeout=10)
    if change == "session":
        lifecycle.save(folder / "session.json", {"session_id": "different-session"})
    elif change == "intent":
        intent = lifecycle.read(folder / "intent.json")
        intent["capsule_identity"]["sha256"] = "0" * 64
        lifecycle.save(folder / "intent.json", intent)
    elif change == "entry":
        Path(capsule["entry"]).unlink()
    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=fixture_host)
    assert result["stop_reason"] == "unknown_host_outcome" and not calls
    assert not (folder / "receipt.json").exists()
    assert not result["objective_complete"]


def test_unreadable_process_identity_is_not_evidence_of_exit(tmp_path, monkeypatch):
    from etalon.runtime import native
    _, capsule, _, _ = setup(tmp_path, [])
    folder, process = abandoned_host(tmp_path, capsule)
    try:
        monkeypatch.setattr(native, "process_identity", lambda pid: None)
        observed = inspect_host_process(lifecycle.read(folder / "process.json"))
        assert observed["state"] == "unknown"
    finally:
        process.stdin.close()
        process.wait(timeout=10)


@pytest.mark.parametrize("exit_code, write_checkpoint", [(7, True), (0, False)])
def test_recorded_interruption_keeps_original_receipt_and_resumes_exact_session(tmp_path, monkeypatch, exit_code, write_checkpoint):
    """Real local processes simulate Codex transport; no scientific job is retried."""
    path, capsule, _, _ = setup(tmp_path, [])
    capsule["native"]["usage_scope"] = "turn_delta"  # This fixture emits per-process deltas.
    capsule["continuation"] = continuation()
    lifecycle.save(path, capsule)
    lifecycle.save(capsule["entry"], {"result": "already computed"})
    code = ("import sys,pathlib,json;sys.stdin.read();"
        "print(json.dumps({'type':'thread.started','thread_id':'fixture-session'}));"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':9,'cached_input_tokens':2,'output_tokens':3}}));"
        "pathlib.Path(sys.argv[1]).write_text(sys.argv[2],encoding='utf-8') if sys.argv[3]=='yes' else None;"
        "sys.exit(int(sys.argv[4]))")

    def argv(body, folder, session):
        first = folder.name == "turn-001"
        checkpoint = {"schema": lifecycle.CHECKPOINT, "task_id": body["task_id"],
            "state": "continue" if first else "complete", "entry": body["entry"],
            "summary": "synthetic transport", "basis_refs": [], "wake_after_seconds": 0}
        return [sys.executable, "-c", code, str(folder / "checkpoint.json"), json.dumps(checkpoint),
                "yes" if not first or write_checkpoint else "no", str(exit_code if first else 0)]

    monkeypatch.setattr(lifecycle, "native_argv", argv)
    originals, calls = [], []

    def host(body, folder, session):
        calls.append(session)
        if folder.name == "turn-002":
            assert "scientific outcome is unresolved" in lifecycle.prompt_for(body, folder, session)
            assert lifecycle.read(capsule["entry"]) == {"result": "already computed"}
        receipt = lifecycle.host_turn(body, folder, session)
        originals.append(file_identity(folder / "receipt.json"))
        return receipt

    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)
    first = tmp_path / "run" / "turn-001"
    assert calls == [None, "fixture-session"] and result["stop_reason"] == "host_complete"
    assert file_identity(first / "receipt.json") == originals[0]
    assert lifecycle.read(first / "receipt.json")["exit_code"] == exit_code
    recovery = lifecycle.read(first / "recovery.json")
    assert recovery["receipt_identity"] == originals[0]
    assert not recovery["host_recovery"]["scientific_retry"]
    assert result["host_wall_seconds"] > 0 and result["unknown_host_wall_turns"] == 0
    assert result["usage"]["input_tokens"] == 18 and result["usage"]["coverage"] == "partial"
    assert result["usage"]["unknown_usage_records_or_turns"] == 1
    assert lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=host)["host_turns"] == 2
    assert calls == [None, "fixture-session"]


def test_recorded_failure_without_native_session_never_manufactures_resume(tmp_path, monkeypatch):
    path, capsule, _, _ = setup(tmp_path, [])
    capsule["continuation"] = continuation()
    lifecycle.save(path, capsule)
    lifecycle.save(capsule["entry"], {"result": "existing"})
    monkeypatch.setattr(lifecycle, "native_argv", lambda *args: [sys.executable, "-c", "import sys;sys.stdin.read();sys.exit(7)"])
    result = lifecycle.run_lifecycle(path, tmp_path / "run")
    assert result["host_turns"] == 1 and result["stop_reason"] == "host_error_or_invalid_checkpoint"
    assert not (tmp_path / "run" / "turn-001" / "recovery.json").exists()


def test_recovery_respects_original_host_admission_bound_even_with_complete_checkpoint(tmp_path):
    path, capsule, calls, fixture_host = setup(tmp_path, ["complete"])
    capsule["continuation"] = continuation()
    capsule["bounds"]["max_host_turns"] = 1
    lifecycle.save(path, capsule)
    folder, process = abandoned_host(tmp_path, capsule)
    lifecycle.save(folder / "checkpoint.json", {"schema": lifecycle.CHECKPOINT,
        "task_id": capsule["task_id"], "entry": capsule["entry"], "state": "complete",
        "summary": "unconfirmed terminal checkpoint", "basis_refs": [], "wake_after_seconds": 0})
    process.stdin.close()
    process.wait(timeout=10)
    result = lifecycle.run_lifecycle(path, tmp_path / "run", turn_runner=fixture_host)
    assert not calls and result["host_turns"] == 1 and result["stop_reason"] == "host_turn_bound"
    assert result["last_checkpoint"]["state"] == "complete" and not result["objective_complete"]
    assert result["host_recovery"]["scientific_outcome"] == "unresolved"


def usage_record(session, count, cached, output, *, resumed=None, scope="thread_cumulative", **extra):
    return {"session_id": session, "resumed_session_id": resumed, "usage_scope": scope,
            "usage": [{"input_tokens": count, "cached_input_tokens": cached, "output_tokens": output}], **extra}


def test_explicit_native_cumulative_usage_counts_session_once_and_raw_values_stay_intact(tmp_path):
    records = [usage_record("one", 100, 70, 20), usage_record("one", 160, 100, 30, resumed="one")]
    raw = json.dumps(records)
    result = lifecycle.usage_summary(records, tmp_path)
    assert result["input_tokens"] == 160 and result["cached_input_tokens"] == 100
    assert result["uncached_input_tokens"] == 60 and result["output_tokens"] == 30
    assert result["coverage"] == "declared_records_complete"
    assert result["model_response_count"] is None
    assert json.dumps(records) == raw


def test_explicit_usage_scopes_separate_fresh_sessions_and_preserve_legacy_deltas(tmp_path):
    first = usage_record("one", 100, 70, 20)
    second = usage_record("two", 160, 100, 30)
    assert lifecycle.usage_summary([first, second], tmp_path)["input_tokens"] == 260
    legacy = [{k: v for k, v in r.items() if k != "usage_scope"} for r in (
        first, usage_record("one", 160, 100, 30, resumed="one"))]
    assert lifecycle.usage_summary(legacy, tmp_path)["input_tokens"] == 260
    assert lifecycle.usage_summary(legacy, tmp_path, native_usage_scope="thread_cumulative")["input_tokens"] == 160


def test_cumulative_counter_regression_is_unknown_not_negative_or_rebased(tmp_path):
    result = lifecycle.usage_summary([usage_record("one", 100, 70, 20),
        usage_record("one", 80, 50, 10, resumed="one"),
        usage_record("one", 140, 80, 25, resumed="one")], tmp_path)
    assert result["input_tokens"] == 100 and result["coverage"] == "partial"
    assert result["unknown_usage_records_or_turns"] == 2
    assert any("regressed" in item["error"] for item in result["issues"])


def test_resumed_cumulative_counter_requires_known_run_origin_and_preserves_interruption_unknown(tmp_path):
    resumed = usage_record("one", 160, 100, 30, resumed="one")
    unknown = lifecycle.usage_summary([resumed], tmp_path)
    assert unknown["input_tokens"] is None and unknown["coverage"] == "partial"
    missing = {"session_id": "one", "resumed_session_id": None, "usage_scope": "thread_cumulative",
               "usage": [], "usage_incomplete": True}
    later = lifecycle.usage_summary([missing, resumed], tmp_path)
    assert later["input_tokens"] == 160 and later["coverage"] == "partial"
    assert later["unknown_usage_records_or_turns"] == 1
    partial = lifecycle.usage_summary([usage_record("one", 100, 70, 20),
        {**missing, "resumed_session_id": "one"}], tmp_path)
    assert partial["input_tokens"] == 100 and partial["coverage"] == "partial"


def test_exact_usage_id_deduplicates_cumulative_snapshot_and_response_count_is_explicit(tmp_path):
    first = usage_record("one", 100, 70, 20)
    first["usage"][0]["usage_id"] = "turn-1"
    duplicate = {**first, "resumed_session_id": "one"}
    result = lifecycle.usage_summary([first, duplicate], tmp_path)
    assert result["input_tokens"] == 100 and result["measured_usage_records"] == 1
    response = usage_record("one", 100, 70, 20, scope="turn_delta")
    response["usage"][0].update(usage_id="response-1", response_id="response-1")
    result = lifecycle.usage_summary([response, response], tmp_path)
    assert result["model_response_count"] == 1 and result["input_tokens"] == 100


def test_mixed_scopes_for_same_session_do_not_double_count(tmp_path):
    result = lifecycle.usage_summary([usage_record("one", 100, 70, 20),
        usage_record("one", 60, 30, 10, scope="turn_delta")], tmp_path)
    assert result["input_tokens"] is None and result["coverage"] == "partial"
    assert any("mixed" in item["error"] for item in result["issues"])


def test_real_interruption_counter_gap_stays_unknown_despite_complete_resumed_turn(tmp_path):
    # Native acceptance observed one billed response missing from the resumed
    # CLI counter; exact response records totaled 208055 / 177024 / 2758.
    failed = {"session_id": "real-session", "resumed_session_id": None,
              "usage_scope": "thread_cumulative", "usage": [], "usage_incomplete": True}
    complete = usage_record("real-session", 185187, 154752, 2126, resumed="real-session")
    result = lifecycle.usage_summary([failed, complete], tmp_path)
    assert result["input_tokens"] == 185187 and result["uncached_input_tokens"] == 30435
    assert result["coverage"] == "partial" and result["unknown_usage_records_or_turns"] == 1
    assert result["model_response_count"] is None and result["cost_usd"] is None
