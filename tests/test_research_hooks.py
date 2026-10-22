"""Lifecycle contract fixtures, not evidence of a native host/model session."""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from etalon import host, research_hooks as hooks


@pytest.fixture
def context(monkeypatch):
    value = {"schema": "fixture-context/1", "checkpoint_ref": {
        "source_id": "research", "record_id": "checkpoint", "record_digest": "a" * 64},
        "coverage": [{"source_id": "research", "status": "complete"}],
        "source_watermarks": [{"source_id": "research", "snapshot": "frozen-fixture"}],
        "authority": {"state": "paused"}, "execution_permission": "not_granted",
        "pending": [{"request_id": "already-running", "execution_state": "running"}],
        "branches": [{"branch_id": "R4b", "state": "paused"}],
        "policy": {"version": "fixture-policy/1"}}
    monkeypatch.setattr(hooks, "_build_context", lambda *args, **kwargs: value)
    return value


def event(name="SessionStart", **kwargs):
    return {"hook_event_name": name, "session_id": "session/../../A", "event_id": "event-1", **kwargs}


def invoke(tmp_path, value=None, **kwargs):
    return hooks.handle_event({"workspace": "fixture", "checkpoint_ref": {}}, value or event(),
                              host="codex", state_dir=tmp_path / "receipts", **kwargs)


def test_replay_is_idempotent_and_payload_conflict_rejected(tmp_path, context, monkeypatch):
    first = invoke(tmp_path)
    assert first["ok"] and not first["duplicate"]
    assert not first["receipt"]["automatic_retry"] and not first["receipt"]["rsi_triggered"]
    injection = first["receipt"]["injection"]
    text = first["host_output"]["hookSpecificOutput"]["additionalContext"]
    assert injection["text_bytes"] == len(text.encode())
    assert injection["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert "paused" in text and "already-running" in text
    assert "not instructions" in text
    monkeypatch.setattr(hooks, "_build_context", lambda *a, **k: pytest.fail("duplicate should not re-read"))
    again = invoke(tmp_path)
    assert again["duplicate"] and again["receipt"] == first["receipt"]
    assert len(list((tmp_path / "receipts").rglob("*.json"))) == 1
    with pytest.raises(ValueError, match="conflicts"):
        invoke(tmp_path, event(source="changed"))
    with pytest.raises(ValueError, match="conflicts"):
        invoke(tmp_path, policy={"new": "policy"})


def test_sessions_and_hosts_are_isolated(tmp_path, context):
    first = invoke(tmp_path)
    second = invoke(tmp_path, event(session_id="B"))
    third = hooks.handle_event({"workspace": "fixture", "checkpoint_ref": {}}, event(),
                              host="claude", state_dir=tmp_path / "receipts")
    assert len({r["receipt_path"] for r in (first, second, third)}) == 3
    assert all(Path(r["receipt_path"]).is_relative_to(tmp_path) for r in (first, second, third))


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH receipt regression")
def test_long_receipt_path_retains_full_identity_and_replays(tmp_path, context):
    root = tmp_path / ("long-export-" + "x" * 85) / "receipts"
    first = hooks.handle_event({}, event(), host="codex", state_dir=root)
    public_path = Path(first["receipt_path"])
    assert len(str(public_path)) > 260
    assert not str(public_path).startswith("\\\\?\\")
    assert len(public_path.stem) == 64 and len(public_path.parent.name) == 64
    again = hooks.handle_event({}, event(), host="codex", state_dir=root)
    assert first["ok"] and again["duplicate"]
    assert first["receipt"] == again["receipt"]


def test_native_payload_fallback_refreshes_context_after_compaction(tmp_path, context):
    native = event(source="compact")
    native.pop("event_id")
    first = invoke(tmp_path, native)
    assert first["receipt"]["identity_strength"] == "content_only"
    assert invoke(tmp_path, native)["duplicate"]
    context["pending"] = []
    second = invoke(tmp_path, native)
    assert second["receipt_path"] != first["receipt_path"] and not second["duplicate"]


def test_tool_event_records_do_not_admit_claims_or_preserve_raw_response(tmp_path, context):
    value = event("PostToolUse", tool_name="mcp__etalon__read_evidence", tool_use_id="tool-1",
                  tool_input={"request_id": "already-running"},
                  tool_response={"isError": True, "content": "PRIVATE_RAW_RESULT"})
    report = invoke(tmp_path, value)
    receipt = report["receipt"]
    assert report["host_output"] == {} and receipt["injection"] is None
    assert receipt["tool_observation"]["observation"] == "failed"
    assert receipt["tool_observation"]["scientific_admission"] == "not_assessed"
    assert "PRIVATE_RAW_RESULT" not in json.dumps(receipt)
    assert receipt["scientific_writes"] == 0


@pytest.mark.parametrize("name", ["PreCompact", "Stop"])
def test_checkpoint_ack_only_records_existing_pointer(tmp_path, context, name):
    report = invoke(tmp_path, event(name))
    assert report["host_output"] == {}
    ack = report["receipt"]["checkpoint_ack"]
    assert ack["checkpoint_ref"] == context["checkpoint_ref"]
    assert ack["status"] == "existing_entry_checked_no_new_scientific_checkpoint"
    assert report["receipt"]["scientific_writes"] == 0


def test_read_failure_is_durable_not_a_successful_injection(tmp_path, monkeypatch):
    def failure(*args, **kwargs):
        raise ValueError("source hash changed")
    monkeypatch.setattr(hooks, "_build_context", failure)
    report = invoke(tmp_path)
    assert not report["ok"] and report["host_output"] == {}
    assert report["receipt"]["injection"] is None
    assert report["receipt"]["error"]["message"] == "source hash changed"
    assert invoke(tmp_path)["duplicate"]


def test_ignore_unrelated_tools_and_own_hook_handler(tmp_path, context):
    for tool in ("Bash", "mcp__other__write", "mcp__etalon__research_hook"):
        report = invoke(tmp_path, event("PostToolUse", tool_name=tool))
        assert report["ignored"] and report["host_output"] == {}
    assert not (tmp_path / "receipts").exists()


def test_host_specific_failure_events(tmp_path):
    value = event("PostToolUseFailure", tool_name="mcp__etalon__query_evidence", error="offline")
    with pytest.raises(ValueError, match="unsupported"):
        invoke(tmp_path, value)
    report = hooks.handle_event({}, value, host="claude", state_dir=tmp_path)
    assert report["receipt"]["tool_observation"]["observation"] == "failed"


def test_cli_failure_has_no_success_stdout(tmp_path):
    command = [sys.executable, "-m", "etalon.research_hooks", "--host", "codex",
               "--entry", str(tmp_path / "missing.json"), "--state-dir", str(tmp_path / "receipts")]
    result = subprocess.run(command, input=json.dumps(event()), capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 1 and result.stdout == ""
    assert not json.loads(result.stderr)["ok"]


def test_cli_json_bytes_are_utf8_even_when_text_streams_are_ascii(tmp_path):
    # A transport-only fixture tests the process boundary, not scientific data.
    program = (
        "from etalon import research_hooks as h; "
        "h._build_context=lambda *a, **k: {'fixture_note': '\\u4e2d\\u6587 \\u03b1 \\U0001f9ea'}; "
        "raise SystemExit(h.main())"
    )
    command = [sys.executable, "-c", program, "--host", "codex", "--entry", str(tmp_path / "entry.json"),
               "--state-dir", str(tmp_path / "receipts")]
    env = {**os.environ, "PYTHONIOENCODING": "ascii:strict", "PYTHONUTF8": "0"}
    result = subprocess.run(command, input=json.dumps(event()).encode(), capture_output=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout.decode("utf-8"))
    assert "中文 α 🧪" in output["hookSpecificOutput"]["additionalContext"]

    # The diagnostic channel follows the same wire encoding on failure.
    bad = [sys.executable, "-m", "etalon.research_hooks", "--host", "codex",
           "--entry", str(tmp_path / "不存在.json"), "--state-dir", str(tmp_path / "failed")]
    failure = subprocess.run(bad, input=json.dumps(event()).encode(), capture_output=True, env=env, timeout=30)
    assert failure.returncode == 1 and not failure.stdout
    assert "不存在.json" in json.loads(failure.stderr.decode("utf-8"))["error"]["message"]


@pytest.mark.parametrize("adapter", ["codex", "claude"])
def test_project_export_is_opt_in_and_uses_current_host_protocol(tmp_path, adapter):
    entry = tmp_path / "research-entry.json"
    entry.write_text("{}")
    target = tmp_path / "a quoted ' project $name"
    report = host.export(target, adapter, research_entry=entry)
    assert host.verify(target)["export_matches_manifest"]
    config_path = ".codex/hooks.json" if adapter == "codex" else ".claude/settings.json"
    config = json.loads((target / config_path).read_text())
    handler = config["hooks"]["SessionStart"][0]["hooks"][0]
    assert all(h["timeout"] == 30 for groups in config["hooks"].values() for g in groups for h in g["hooks"])
    assert report["research_hooks"]["entry"] == str(entry.absolute())
    if adapter == "claude":
        assert handler["args"] == [str(target / ".etalon" / "research-hook.py")]
        assert "PostToolUseFailure" in config["hooks"]
    else:
        assert "PostToolUseFailure" not in config["hooks"]
        encoded = handler["commandWindows"].split()[-1]
        decoded = base64.b64decode(encoded).decode("utf-16-le")
        assert "quoted '' project $name" in decoded
    with pytest.raises(FileExistsError):
        host.export(target, adapter, research_entry=entry)


def test_existing_export_without_entry_has_no_hooks(tmp_path):
    report = host.export(tmp_path / "plain")
    assert "research_hooks" not in report
    assert ".codex/hooks.json" not in report["files"]
    assert host.verify(tmp_path / "plain")["matches_current_source"]


def test_existing_journal_is_unchanged_through_real_context_builder(tmp_path):
    from test_research_continuation import case, step, save, checkpoint, publish
    from etalon.research.evidence_refs import file_identity
    fixture = case.__wrapped__(tmp_path)
    journal, ref, _, _ = fixture
    step_ref = save(fixture, step(ref))
    cp = checkpoint(fixture, step_ref)
    cp["status_guards"][0]["state"] = "paused"
    cp_ref = save(fixture, cp)
    workspace = publish(fixture)
    entry = tmp_path / "entry.json"
    entry.write_text(json.dumps({"workspace": str(workspace), "checkpoint_ref": cp_ref}))
    before = file_identity(journal.store.path)
    report = hooks.handle_event(entry, event(), host="codex", state_dir=tmp_path / "host-trace")
    assert report["ok"], report["receipt"].get("error")
    text = report["host_output"]["hookSpecificOutput"]["additionalContext"]
    payload = json.loads(text.split("\n", 1)[1])["context"]
    assert payload["continuation"]["authority_state"] == "paused"
    assert payload["execution_permission"] == "not_granted"
    assert report["receipt"]["injection"]["policy"]["version"]
    assert file_identity(journal.store.path) == before
    assert not journal.store.actions()
