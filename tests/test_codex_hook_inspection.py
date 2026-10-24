"""Native protocol fixtures: no models, trust writes or live hooks."""
import io
import json
import subprocess
import threading

import pytest

from etalon import codex_hooks

REAL_PROBE = codex_hooks._probe_transport


@pytest.fixture(autouse=True)
def supported_transport(monkeypatch):
    monkeypatch.setattr(codex_hooks, "_probe_transport", lambda *args: {
        "arguments": ["--listen", "stdio://"], "selection": "listen_stdio", "probe": "fixture"})


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
    assert launches[0][0] == ["codex", "app-server", "--listen", "stdio://"]
    assert result["verification_stages"]["login"] == "not_checked"
    assert result["verification_stages"]["mcp_reachability"] == "not_checked"
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


@pytest.mark.parametrize("help_text, arguments, selection", [
    ("Usage: codex app-server [OPTIONS]\n  --listen <URL>\n    stdio:// (default)", ["--listen", "stdio://"], "listen_stdio"),
    ("  --listen <URL>\n    stdio:// (default)\n  --stdio\n", ["--listen", "stdio://"], "listen_stdio"),
    ("  --stdio\n    Use standard input/output", ["--stdio"], "stdio_flag"),
    ("Usage: codex app-server\nTransport defaults to stdio", [], "default_stdio"),
])
def test_transport_selection_uses_advertised_capabilities(help_text, arguments, selection):
    assert codex_hooks._select_transport(help_text) == (arguments, selection)


@pytest.mark.parametrize("help_text", ["  --listen <URL>\nOnly ws:// endpoints", "unrecognized --stdio argument"])
def test_unadvertised_transport_is_not_guessed(help_text):
    with pytest.raises(codex_hooks.InspectionError, match="stdio_transport_not_advertised"):
        codex_hooks._select_transport(help_text)


def test_help_probe_is_bounded_and_does_not_echo_output(tmp_path, monkeypatch):
    import time
    process = Process([])
    process.stdin = None
    process.stdout = io.StringIO("PRIVATE_SENTINEL" * codex_hooks.MAX_HELP_BYTES)
    launches = []
    monkeypatch.setattr(codex_hooks.subprocess, "Popen", lambda argv, **kw: launches.append(argv) or process)
    with pytest.raises(codex_hooks.InspectionError, match="help_output_exceeds_bound") as error:
        REAL_PROBE("codex", tmp_path, time.monotonic() + 2)
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert launches == [["codex", "app-server", "--help"]]
    assert process.wait_calls


def test_unsupported_hooks_preserves_transport_config_and_login_diagnostics(tmp_path, monkeypatch):
    values = replies(tmp_path, blocked=False)
    values[-1] = {"id": 3, "error": {"code": -32601, "message": "PRIVATE_RPC_DATA"}}
    values.append({"id": 4, "result": {"account": None, "requiresOpenaiAuth": True}})
    process = Process(values)
    monkeypatch.setattr(codex_hooks.subprocess, "Popen", lambda *a, **k: process)
    result = codex_hooks.inspect(tmp_path, check_login=True)
    assert result["verification_stages"]["native_discovery"] == "unsupported"
    assert result["project_layer_status"] == "loaded"
    assert result["login"]["status"] == "missing"
    assert not result["hook_execution_verified"]
    assert "PRIVATE_RPC_DATA" not in json.dumps(result)
    sent = [json.loads(line) for line in process.stdin.saved.splitlines()]
    assert sent[-1] == {"id": 4, "method": "account/read", "params": {"refreshToken": False}}


def test_account_presence_is_not_login_validation_or_model_access(tmp_path, monkeypatch):
    values = replies(tmp_path)
    values.append({"id": 4, "result": {"account": {
        "type": "chatgpt", "email": "PRIVATE_EMAIL", "token": "PRIVATE_TOKEN"}, "requiresOpenaiAuth": True}})
    process = Process(values)
    monkeypatch.setattr(codex_hooks.subprocess, "Popen", lambda *a, **k: process)
    result = codex_hooks.inspect(tmp_path, check_login=True)
    assert result["login"]["status"] == "credential_present_unvalidated"
    assert not result["login"]["model_access_verified"]
    assert all(value not in json.dumps(result) for value in ("PRIVATE_EMAIL", "PRIVATE_TOKEN"))
