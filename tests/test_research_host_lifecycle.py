"""Control-boundary tests; fixture hosts are not a scientific/live Codex claim."""
import importlib.util
import json
from pathlib import Path

import pytest

from etalon.research.evidence_refs import EvidenceError
from test_research_publication import case

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
