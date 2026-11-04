"""Host-facing action facts and compact reads; synthetic data, no science calls."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

import pytest

from etalon.active.schema import canonical, digest
from etalon.active.store import CampaignStore, StateError
from etalon.runtime import api, native, operations, service
from etalon.runtime.store import RuntimeStore
from test_runtime_actions import fixture_action, submit


def method_request(tmp_path):
    program = tmp_path / "method.py"
    program.write_text("# Synthetic callable declaration\n", encoding="utf-8")
    return {"schema": "etalon-action/1", "request_id": "host-action", "operation": "method.run",
            "arguments": {"method_id": "fixture-reader", "command": [sys.executable, str(program)],
                          "files": [str(program)], "inputs": {}}, "limits": {"wall_second": 30}}


def recorded_action(tmp_path):
    request = method_request(tmp_path)
    planned = api.prepare_action(request)
    store = RuntimeStore(tmp_path / "runtime", create=True)
    store.create(request["request_id"], planned)
    store.set_state(request["request_id"], 0, "running")
    with store.connection(write=True) as db:
        db.execute("UPDATE nodes SET state='running',parameters=? WHERE job=?",
                   (canonical(request["arguments"]), request["request_id"]))
    return store, api._action_handle(store.get(request["request_id"])), request


def test_named_input_files_bind_arbitrary_input_keys_and_detect_changes(tmp_path):
    data = tmp_path / "training.csv"
    data.write_text("1\n", encoding="utf-8")
    request = method_request(tmp_path)
    request["arguments"]["inputs"] = {"training_csv": str(data), "literal": "not/a/file"}
    legacy = api.prepare_action(request)
    request["arguments"]["input_files"] = {"training": str(data)}
    bound = api.prepare_action(request)
    assert str(data) not in legacy["inputs"]
    assert str(data) in bound["inputs"]
    assert bound["request"]["arguments"]["inputs"] == request["arguments"]["inputs"]
    data.write_text("2\n", encoding="utf-8")
    assert api.prepare_action(request)["plan_id"] != bound["plan_id"]
    with pytest.raises(StateError, match="planned input changed"):
        operations.check_inputs(bound["inputs"])
    del request["arguments"]["input_files"]
    assert api.prepare_action(request)["plan_id"] == legacy["plan_id"]


def test_named_dependency_validation_keeps_unrelated_free_form_inputs(tmp_path):
    request = method_request(tmp_path)
    request["arguments"]["inputs"] = {"input_files": {"format": "uninterpreted text"}}
    api.prepare_action(request)
    for declaration in ([], {"x": "relative.csv"}, {"": str(tmp_path / "absent")}):
        bad = deepcopy(request)
        bad["arguments"]["input_files"] = declaration
        with pytest.raises((ValueError, FileNotFoundError)):
            api.prepare_action(bad)


def test_action_and_workflow_agree_when_controller_disappears(tmp_path):
    store, handle, _ = recorded_action(tmp_path)
    before = store.get(handle["job_id"])["sequence"]
    workflow = service.status(store.workspace, handle["job_id"])
    result = api.read_action_result(handle)
    assert workflow["worker_alive"] is result["native_status"]["worker_alive"] is False
    assert workflow["needs_reconciliation"] is result["recovery"]["required"] is True
    assert result["execution_state"] == workflow["nodes"][0]["execution_state"] == "unresolved"
    assert store.get(handle["job_id"])["sequence"] == before


def test_completed_receipt_survives_controller_exit_without_claiming_verification(tmp_path):
    store, handle, _ = recorded_action(tmp_path)
    store.receipt(handle["job_id"], 0, "action", "completed",
                  {"result": {"status": "completed", "summary": "Synthetic completed output"}})
    result = api.read_action_result(handle)
    workflow = service.status(store.workspace, handle["job_id"])
    assert result["execution_state"] == workflow["nodes"][0]["execution_state"] == "completed"
    assert result["verification"]["state"] == "pending"
    assert result["recovery"]["required"] is True
    assert workflow["needs_reconciliation"] is True


def test_live_native_child_and_finished_receipt_are_read_without_replay(tmp_path):
    store, handle, request = recorded_action(tmp_path)
    step = store.job_root(handle["job_id"]) / "steps" / "action"
    step.mkdir(parents=True)
    release, started, output = step / "release", step / "started", step / "output.json"
    command = [sys.executable, "-c", """import pathlib,sys,time,json
started,release,output=map(pathlib.Path,sys.argv[1:])
started.write_text('started')
deadline=time.monotonic()+15
while not release.exists() and time.monotonic()<deadline: time.sleep(.02)
output.write_text(json.dumps({'fixture': 7}))
""", str(started), str(release), str(output)]
    directory = step / "native"
    native.launch_detached_attempt(directory,
        {"request_id": handle["job_id"], "attempt_id": "action",
         "parameters_sha256": digest(request["arguments"])}, command, outputs={"result": str(output)})
    def until(check):
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if value := check():
                return value
            time.sleep(.03)
        pytest.fail("native fixture did not reach expected state")
    try:
        until(started.exists)
        running = api.read_action_result(handle)
        assert running["execution_state"] == "running"
        assert running["native_status"]["attempt"]["state"] == "running"
        assert running["recovery"]["required"] is True
        assert service.status(store.workspace, handle["job_id"])["nodes"][0]["execution_state"] == "running"
    finally:
        release.write_text("finish", encoding="utf-8")
        until(lambda: (directory / "finished.json").is_file())
    finished = api.read_action_result(handle)
    assert finished["execution_state"] == "completed"
    assert finished["verification"]["state"] == "pending"
    assert finished["recovery"]["required"] is True
    assert json.loads(output.read_text()) == {"fixture": 7}
    assert not (directory / "receipt.json").exists()  # Reads do not publish or replay work.


def test_result_reads_scan_costs_once_and_summary_preserves_decision_facts(tmp_path, monkeypatch):
    campaign, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    request.update(operation="active.run", arguments={"database": str(campaign.path), "max_rounds": 2},
                   limits={"campaign:fixture-cost": 6})
    handle = submit(request, tmp_path)
    assert len(calls) >= 2
    statements = []
    connection = CampaignStore.connection
    @contextmanager
    def traced(self, **kwargs):
        with connection(self, **kwargs) as db:
            db.set_trace_callback(statements.append)
            yield db
    monkeypatch.setattr(CampaignStore, "connection", traced)
    monkeypatch.setattr(CampaignStore, "snapshot", lambda self: pytest.fail("result needs no full planning snapshot"))
    full = api.read_action_result(handle)
    assert sum("LIKE 'execution-cost:%'" in sql for sql in statements) == 1
    statements.clear()
    summary = api.read_action_result(handle, view="summary")
    assert not any("LIKE 'execution-cost:%'" in sql for sql in statements)
    assert summary["execution_state"] == full["execution_state"] == "completed"
    assert summary["verification"] == full["verification"]
    assert summary["observation_summary"]["count"] == len(full["observations"])
    assert summary["observations"] == [] and "result" not in summary["outputs"][0]
    assert summary["outputs"][0]["sha256"] == full["outputs"][0]["sha256"]
    assert summary["accounting_refs"][0]["charged"] == sum(r["charged"] for r in full["accounting_refs"] if r["kind"] == "campaign")
    assert summary["outputs"][0]["read"]["tool"] == "etalon_workflow_artifact"
    assert api.read_action_result(handle) == full


def test_capabilities_distinguish_controller_limits_from_native_guarantees(tmp_path):
    available = api.prepare_action(method_request(tmp_path))["capabilities"]
    assert available["enforcement"]["resource_allowance"] == "dispatch_reservation"
    assert available["enforcement"]["native_hard_timeout"] is False
    assert available["enforcement"]["hard_cpu_limit"] is False
    assert available["enforcement"]["hard_gpu_limit"] is False
