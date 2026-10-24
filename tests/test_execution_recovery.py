"""Real subprocess handoff/recovery tests; native work is an explicit fixture."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from etalon.research import execution_recovery as recovery
from etalon.research.evidence_refs import EvidenceError, file_identity


def wait_for(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.025)
    raise AssertionError("native fixture did not reach expected state")


def native_fixture(root, duration=0.2):
    output = root / "native-output.json"
    count = root / "executions.txt"
    code = """
import json,sys,time
from pathlib import Path
root=Path(sys.argv[1])
with (root/'executions.txt').open('a',encoding='utf-8') as f: f.write('native fixture execution\\n')
(root/'native-ready').write_text('fixture ready',encoding='utf-8')
time.sleep(float(sys.argv[2]))
(root/'native-output.json').write_text(json.dumps({'fixture':True,'result':42}),encoding='utf-8')
"""
    return {"request_id": "fixture-request", "attempt_id": "trial-1", "science": "fixture"}, [
        sys.executable, "-c", code, str(root), str(duration)], {"result": str(output)}, count


def test_controller_is_actually_killed_native_survives_and_duplicate_entry_never_executes(tmp_path):
    request, command, outputs, count = native_fixture(tmp_path, 1.2)
    config = tmp_path / "controller-input.json"
    config.write_text(json.dumps({"request": request, "command": command, "outputs": outputs}), encoding="utf-8")
    code = """
import json,sys,time
from pathlib import Path
from etalon.research.execution_recovery import launch_detached_attempt
root=Path(sys.argv[1]);config=json.loads((root/'controller-input.json').read_text('utf-8'))
launch_detached_attempt(root/'attempt',config['request'],config['command'],cwd=root,outputs=config['outputs'])
(root/'controller-ready').write_text('controller handed off',encoding='utf-8')
time.sleep(30)
"""
    controller = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_for(lambda: (tmp_path / "controller-ready").exists() and (tmp_path / "native-ready").exists())
        root = tmp_path / "attempt"
        native = json.loads((root / "native.json").read_text("utf-8"))["process"]
        controller.kill()
        controller.wait(timeout=5)
        assert controller.returncode != 0
        assert recovery._same_process(native)
        assert recovery.recover_attempt(root)["state"] == "running"
        # A fresh controller may enter the same API while native work is alive.
        assert recovery.launch_detached_attempt(root, request, command, cwd=tmp_path, outputs=outputs)["state"] == "running"
        wait_for(lambda: (root / "finished.json").exists())
        assert recovery.inspect_attempt(root)["state"] == "finished-unpublished"
        result = recovery.recover_attempt(root)
        assert result["state"] == "published" and result["receipt"]["execution"] == "completed"
        nonce = json.loads((root / "intent.json").read_text("utf-8"))["launch_nonce"]
        assert len(nonce) == 32 and result["receipt"]["launch_nonce"] == nonce
        assert result["receipt"]["finished"]["launch_nonce"] == nonce
        receipt_identity = file_identity(root / "receipt.json")
        assert recovery.recover_attempt(root) == result
        assert recovery.launch_detached_attempt(root, request, command, cwd=tmp_path, outputs=outputs) == result
        assert file_identity(root / "receipt.json") == receipt_identity
        assert count.read_text("utf-8").splitlines() == ["native fixture execution"]
        # The archival receipt remains usable after the producer output vanishes.
        Path(outputs["result"]).unlink()
        assert recovery.inspect_attempt(root) == result
    finally:
        if controller.poll() is None:
            controller.kill()
            controller.wait(timeout=5)


def test_unknown_does_not_relaunch_and_immutable_request_conflicts(tmp_path):
    request, command, outputs, count = native_fixture(tmp_path)
    root = tmp_path / "attempt"
    root.mkdir()
    # A durable intent without worker proof models the launch/crash ambiguity.
    original = recovery.subprocess.Popen
    recovery.subprocess.Popen = lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture launch interruption"))
    try:
        with pytest.raises(OSError, match="interruption"):
            recovery.launch_detached_attempt(root, request, command, cwd=tmp_path, outputs=outputs)
    finally:
        recovery.subprocess.Popen = original
    assert recovery.inspect_attempt(root)["state"] == "unknown"
    assert recovery.recover_attempt(root)["state"] == "unknown"
    assert recovery.launch_detached_attempt(root, request, command, cwd=tmp_path, outputs=outputs)["state"] == "unknown"
    changed = deepcopy(request)
    changed["attempt_id"] = "trial-2"
    with pytest.raises(EvidenceError, match="conflicts"):
        recovery.launch_detached_attempt(root, changed, command, cwd=tmp_path, outputs=outputs)
    assert not count.exists()


def test_current_output_and_archived_counterexample_hashes_are_checked(tmp_path):
    request, command, outputs, _ = native_fixture(tmp_path)
    root = tmp_path / "attempt"
    recovery.launch_detached_attempt(root, request, command, cwd=tmp_path, outputs=outputs)
    wait_for(lambda: (root / "finished.json").exists())
    original = Path(outputs["result"]).read_bytes()
    Path(outputs["result"]).write_bytes(b"changed output")
    with pytest.raises(EvidenceError, match="bytes changed"):
        recovery.recover_attempt(root)
    assert not (root / "receipt.json").exists()
    Path(outputs["result"]).write_bytes(original)
    result = recovery.recover_attempt(root)
    archive = Path(result["receipt"]["archives"]["result"]["path"])
    archive.write_bytes(b"changed archived original")
    with pytest.raises(EvidenceError, match="archived original changed"):
        recovery.inspect_attempt(root)


def test_pid_reuse_or_changed_creation_identity_is_unknown(tmp_path, monkeypatch):
    request, command, outputs, _ = native_fixture(tmp_path)
    root = tmp_path / "attempt"
    original = recovery.subprocess.Popen
    monkeypatch.setattr(recovery.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(OSError):
        recovery.launch_detached_attempt(root, request, command, cwd=tmp_path, outputs=outputs)
    monkeypatch.setattr(recovery.subprocess, "Popen", original)
    saved = recovery.process_identity(os.getpid())
    saved["pid"] = 1_000_000
    intent = json.loads((root / "intent.json").read_text("utf-8"))
    recovery._save(root / "native.json", {"intent_digest": recovery.digest(intent),
                                         "launch_nonce": intent["launch_nonce"], "process": saved})
    key = "start_ticks" if saved["platform"] == "linux" else "creation_filetime"
    monkeypatch.setattr(recovery, "process_identity", lambda pid: {**saved, "pid": pid, "alive": True, key: saved[key] + 1})
    assert recovery.inspect_attempt(root)["state"] == "unknown"


def test_exec_chain_changes_command_observation_without_changing_creation(monkeypatch):
    saved = {"pid": 123, "platform": "linux", "boot_id": "fixture-boot", "start_ticks": 50,
             "cmdline_sha256": "env-wrapper", "alive": True}
    monkeypatch.setattr(recovery, "process_identity", lambda pid: {**saved, "cmdline_sha256": "actual-science"})
    assert recovery._same_process(saved)
    monkeypatch.setattr(recovery, "process_identity", lambda pid: {**saved, "start_ticks": 51})
    assert not recovery._same_process(saved)


def test_native_failure_and_missing_output_publish_as_failure_without_retry(tmp_path):
    root = tmp_path / "attempt"
    recovery.launch_detached_attempt(root, {"request_id": "fixture", "attempt_id": "1"},
        [sys.executable, "-c", "raise SystemExit(7)"], outputs={"missing_verifier": str(tmp_path / "absent")})
    wait_for(lambda: (root / "finished.json").exists())
    result = recovery.recover_attempt(root)
    assert result["receipt"]["execution"] == "failed"
    assert result["receipt"]["finished"]["returncode"] == 7
    assert result["receipt"]["finished"]["missing_outputs"] == ["missing_verifier"]
    assert result["receipt"]["automatic_retry"] is False


@pytest.mark.parametrize("field", ["launch_nonce", "intent_digest"])
def test_completion_from_another_request_or_launch_cannot_publish(tmp_path, field):
    request, command, outputs, _ = native_fixture(tmp_path)
    root = tmp_path / "attempt"
    recovery.launch_detached_attempt(root, request, command, cwd=tmp_path, outputs=outputs)
    wait_for(lambda: (root / "finished.json").exists())
    finished = json.loads((root / "finished.json").read_text("utf-8"))
    finished[field] = "another-launch-or-request"
    recovery._save(root / "finished.json", finished, replace=True)
    with pytest.raises(EvidenceError, match="immutable attempt"):
        recovery.recover_attempt(root)
    assert not (root / "receipt.json").exists()
