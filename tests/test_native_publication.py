"""Native bindings and result publication reuse real APIs without scientific jobs."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from etalon.active.schema import digest
from etalon.boundary import native_result, runtime_profile
from etalon.research import execution_recovery as recovery
from etalon.research.continuation import read_content, _handle
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.publication import prepare_result_publication, resume_publication, prepare_publication
from test_research_publication import case


def helper_request(case):
    step = case["request"]["steps"][0]["content"]
    return dict(entry=case["entry"], operation_id="compact-result", branch_id="existing",
        parent_entry=case["parent"], results=case["request"]["results"],
        decision={"choice": "reuse", "state": "resolved", "reason": "Reuse fixture result",
                  "basis_refs": [{"publication_ref": "result"}], "reconsider_when": "New evidence"},
        counter_refs=step["input_refs"])


def test_compact_result_resume_keeps_counters_and_exact_idempotency(case):
    request = helper_request(case)
    prepared = prepare_result_publication(**request)
    # Intent is the checkpoint across a genuine fresh Python host process.
    code = "from etalon.research.publication import resume_publication; import sys; resume_publication(sys.argv[1])"
    subprocess.run([sys.executable, "-c", code, prepared["intent_path"]], check=True)
    assert prepare_result_publication(**request)["status"] == "published"
    selected = json.loads(case["entry"].read_text())
    cp = read_content(selected["workspace"], selected["checkpoint_ref"])
    assert cp["counter_refs"] == request["counter_refs"]
    assert cp["status_guards"] == case["checkpoint"]["status_guards"]
    assert cp["branches"][0]["state"] == "resolved"
    request["decision"]["reason"] = "Changed decision must not reuse old operation"
    with pytest.raises(EvidenceError, match="different publication content"):
        prepare_result_publication(**request)


def test_compact_result_retains_original_check_and_rejects_unknown_typed_results(case):
    request = helper_request(case)
    prepared = prepare_result_publication(**request)
    case["result"].write_text("changed", encoding="utf-8")
    with pytest.raises(EvidenceError):
        resume_publication(prepared["intent_path"])
    assert not case["entry"].exists()


def descriptor(tmp_path):
    profile = {"schema": "etalon-runtime-profile/1", "spec": {
        "python": "/fixed/venv/bin/python", "pythonpath": ["/fixed/source"]}}
    profile["manifest_digest"] = digest(profile)
    return recovery.bind_native_handle(tmp_path, native={
        "schema": "prism-gromacs-job-handle/1", "python": "/fixed/venv/bin/python",
        "source": "/fixed/source", "request_id": "fixture-only"}, profile=profile,
        transport={"kind": "ssh", "host": "fixture-host"}, reader_files={
            name: {"path": "/fixed/" + name + ".py", **file_identity(module.__file__)}
            for name, module in (("native_result", native_result), ("runtime_profile", runtime_profile))})


def test_native_handle_is_small_cross_platform_and_descriptor_changes_fail(tmp_path, monkeypatch):
    handle = descriptor(tmp_path)
    _handle(handle)
    assert len(json.dumps(handle)) < 1024
    assert descriptor(tmp_path) == handle
    Path(handle["descriptor"]).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not execute changed descriptor"))
    with pytest.raises(EvidenceError, match="descriptor bytes changed"):
        recovery.read_native_result(handle)


def test_transport_failure_is_not_completion_or_retry(tmp_path, monkeypatch):
    handle = descriptor(tmp_path)
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=255, stderr="fixture offline", stdout="")
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(EvidenceError, match="fixture offline"):
        recovery.read_native_result(handle)
    assert len(calls) == 1


def test_native_result_reentry_reads_same_binding_without_execution(tmp_path, monkeypatch):
    handle = descriptor(tmp_path)
    calls = []
    def read(argv, **kwargs):
        frozen = json.loads(kwargs["input"])
        calls.append((argv, frozen))
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "schema": "etalon-native-action-result/1", "request_id": handle["request_id"],
            "profile_digest": frozen["profile"]["manifest_digest"], "execution_state": "completed"}))
    monkeypatch.setattr(subprocess, "run", read)
    from etalon.runtime.api import read_action_result
    first, second = read_action_result(handle), read_action_result(handle)
    assert first["execution_state"] == second["execution_state"] == "completed"
    assert calls[0] == calls[1]
    assert calls[0][1]["native"]["request_id"] == "fixture-only"
    assert first["read_cost"]["wall_seconds"] >= 0


def test_declared_profile_cannot_silently_change_interpreter(tmp_path):
    handle = descriptor(tmp_path)
    desc = json.loads(Path(handle["descriptor"]).read_text())
    desc["profile"]["spec"]["python"] = "/another/python"
    with pytest.raises(EvidenceError, match="manifest digest"):
        recovery.bind_native_handle(tmp_path / "changed", **{k: desc[k] for k in (
            "native", "profile", "transport", "reader_files")})


def test_pending_set_cannot_be_cleared_without_result_lineage(case, tmp_path, monkeypatch):
    from etalon.research.publication import _check_handle_update
    handle = descriptor(tmp_path)
    parent = {**case["checkpoint"], "pending_handles": [handle]}
    request = {**case["request"], "handle_update": {"expected": [handle], "replacement": []}}
    with pytest.raises(EvidenceError, match="result step"):
        _check_handle_update(request, parent)
    request["steps"] = [{"content": {"operation": "result", "detail": {"action_handles": [handle]}}}]
    monkeypatch.setattr("etalon.runtime.api.read_action_result", lambda h: {"execution_state": "unresolved"})
    with pytest.raises(EvidenceError, match="nonterminal"):
        _check_handle_update(request, parent)
    monkeypatch.setattr("etalon.runtime.api.read_action_result", lambda h: {
        "execution_state": "completed", "verification": {"state": "passed"}})
    _check_handle_update(request, parent)
    request["handle_update"]["expected"] = []
    with pytest.raises(EvidenceError, match="exact prior"):
        _check_handle_update(request, parent)


def test_boundary_does_not_import_prism_after_runtime_drift():
    native = {"schema": "prism-gromacs-job-handle/1", "python": "/p"}
    runtime = SimpleNamespace(verify=lambda *a: {"verified": False})
    with pytest.raises(ValueError, match="runtime changed"):
        native_result.read_prism_result(native, {"spec": {"python": "/p"}}, runtime)


@pytest.mark.skipif(os.name != "nt", reason="Windows CommonAppData/OpenSSH contract")
def test_minimal_mcp_environment_retains_windows_ssh_runtime(monkeypatch):
    # Reproduces the actual MCP child environment; asks only ssh -V, no network.
    from mcp.client.stdio import get_default_environment
    import shutil
    ssh = shutil.which("ssh")
    if ssh is None:
        pytest.skip("OpenSSH is not installed")
    minimal = get_default_environment()
    monkeypatch.setattr(os, "environ", minimal)
    environment = recovery._transport_environment()
    assert Path(environment["PROGRAMDATA"]).is_dir()
    assert "PROGRAMDATA" not in minimal
    child = subprocess.run([ssh, "-V"], capture_output=True, env=environment)
    assert child.returncode == 0
