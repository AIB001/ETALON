"""Native protocol fixtures: no models, trust writes or live hooks."""
import io
import json
import subprocess
import threading

import pytest

from etalon import codex_hooks


class Input(io.StringIO):
    def close(self):
        self.saved = self.getvalue()
        super().close()


class Process:
    def __init__(self, replies):
        self.stdin = Input()
        self.stdout = io.StringIO("".join(json.dumps(r) + "\n" for r in replies))
        self.wait_calls = []
        self.terminated = False
        self.killed = False

    def wait(self, timeout):
        self.wait_calls.append(timeout)
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def replies(target, *, hooks=(), blocked=True):
    return [
        {"id": 1, "result": {}},
        {"id": 2, "result": {
            "config": {"features": {"hooks": True}, "token": "PRIVATE_SENTINEL", "env": {"PRIVATE": "SECRET"}},
            "layers": [{"name": {"type": "project", "dotCodexFolder": str(target / ".codex")},
                        "disabledReason": "Project review needed" if blocked else None,
                        "config": {"command": "MUST_NOT_LEAK"}}]}},
        {"id": 3, "result": {"data": [{"cwd": str(target), "hooks": list(hooks), "errors": [], "warnings": ["PRIVATE_WARNING"]}]}},
    ]


def test_preflight_only_reads_whitelisted_metadata_and_reports_blocked(tmp_path, monkeypatch):
    process = Process(replies(tmp_path))
    launches = []
    def launch(argv, **kwargs):
        launches.append((argv, kwargs))
        return process
    monkeypatch.setattr(codex_hooks.subprocess, "Popen", launch)
    result = codex_hooks.inspect(tmp_path)
    assert result["project_layer_status"] == "blocked"
    assert result["discovered_hook_count"] == 0 and not result["hook_execution_verified"]
    assert result["features_hooks_explicit_value"] is True
    encoded = json.dumps(result)
    assert all(word not in encoded for word in ("PRIVATE_SENTINEL", "SECRET", "MUST_NOT_LEAK", "PRIVATE_WARNING"))
    assert launches[0][0] == ["codex", "app-server", "--stdio"]
    sent = [json.loads(line) for line in process.stdin.saved.splitlines()]
    assert [row["method"] for row in sent] == ["initialize", "initialized", "config/read", "hooks/list"]
    assert not result["trust_modified"] and result["model_turns_started"] == 0


def test_discovered_hook_hash_needs_review_and_does_not_mean_executed(tmp_path, monkeypatch):
    hook = {"eventName": "sessionStart", "source": "project", "sourcePath": str(tmp_path / ".codex/hooks.json"),
            "key": "fixture-key", "currentHash": "a" * 64, "trustStatus": "untrusted", "enabled": True,
            "isManaged": False, "command": "PRIVATE_COMMAND", "env": {"token": "PRIVATE_TOKEN"}}
    process = Process(replies(tmp_path, hooks=[hook], blocked=False))
    monkeypatch.setattr(codex_hooks.subprocess, "Popen", lambda *a, **k: process)
    result = codex_hooks.inspect(tmp_path)
    assert result["project_layer_status"] == "loaded" and result["review_required_count"] == 1
    assert result["entries"][0]["hooks"][0]["currentHash"] == "a" * 64
    assert "PRIVATE_COMMAND" not in json.dumps(result) and "PRIVATE_TOKEN" not in json.dumps(result)
    assert not result["model_consumption_verified"]


def test_missing_project_layer_is_unknown_not_trusted(tmp_path, monkeypatch):
    values = replies(tmp_path)
    values[1]["result"]["layers"] = []
    process = Process(values)
    monkeypatch.setattr(codex_hooks.subprocess, "Popen", lambda *a, **k: process)
    assert codex_hooks.inspect(tmp_path)["project_layer_status"] == "absent_or_not_reported"


def test_rpc_error_does_not_echo_config(tmp_path, monkeypatch):
    process = Process([{"id": 1, "error": {"code": -32601, "message": "PRIVATE_CONFIGURATION"}}])
    monkeypatch.setattr(codex_hooks.subprocess, "Popen", lambda *a, **k: process)
    with pytest.raises(RuntimeError, match="-32601") as error:
        codex_hooks.inspect(tmp_path)
    assert "PRIVATE_CONFIGURATION" not in str(error.value)
    assert process.wait_calls


def test_overall_timeout_closes_only_own_subprocess(tmp_path, monkeypatch):
    finished = threading.Event()
    class Stalled:
        def readline(self, limit):
            finished.wait(2)
            return ""
    process = Process([])
    process.stdout = Stalled()
    def wait(timeout):
        process.wait_calls.append(timeout)
        if not process.terminated:
            raise subprocess.TimeoutExpired("fixture inspection", timeout)
        return 0
    def terminate():
        process.terminated = True
        finished.set()
    process.wait, process.terminate = wait, terminate
    monkeypatch.setattr(codex_hooks.subprocess, "Popen", lambda *a, **k: process)
    with pytest.raises(TimeoutError):
        codex_hooks.inspect(tmp_path, timeout=.01)
    assert process.terminated and not process.killed


def test_invalid_timeout_rejected_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_hooks.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    for timeout in (0, -1, 121, True):
        with pytest.raises(ValueError):
            codex_hooks.inspect(tmp_path, timeout=timeout)
