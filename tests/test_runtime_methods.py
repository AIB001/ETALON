"""Optional strategy changes do not put a model on the numerical execution path."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import time

import pytest

from etalon.active.store import StateError
from etalon.runtime import operations, service
from etalon.runtime.revisions import apply_pending
from etalon.runtime.store import RuntimeStore


def method_node(tmp_path, name="first", inputs=None):
    script = tmp_path / "method.py"
    if not script.exists():
        script.write_text('''import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--result');a=p.parse_args()
r=json.loads(Path(a.request).read_text()); inputs=r['inputs']
Path(a.result).with_suffix('.started').write_text('started')
if inputs.get('hold'):
    until=time.monotonic()+30
    while not Path(a.result).with_suffix('.release').exists() and time.monotonic()<until: time.sleep(.05)
value=sum(inputs.get('values',[1,2,3]))*inputs.get('factor',1)
out={'schema':'etalon-method-result/1','summary':'Numerical integration fixture, not scientific efficacy',
     'outputs':{'value':value},'feedback':{'factor':inputs.get('factor',1)}}
if inputs.get('malformed'):out['schema']='bad'
Path(a.result).write_text(json.dumps(out))
''', encoding="utf-8")
    return {"id": name, "operation": "method.run", "arguments": {
        "method_id": "fixture-sum-v1", "command": [sys.executable, str(script)],
        "files": [str(script)], "inputs": inputs or {}}, "resources": {"wall_second": 40}}


def spec(*nodes):
    return {"schema": "etalon-workflow/1", "objective": "Continuous methods with optional host advice",
            "nodes": list(nodes), "limits": {"wall_second": 180}, "max_seconds": 120}


def queue_store(tmp_path, *nodes):
    store = RuntimeStore(tmp_path / "runtime", create=True)
    original = service.plan(spec(*nodes))
    store.create("job", original)
    return store, original


def revision(plan_id, *nodes, removed=(), revision_id="r1"):
    return dict(kind="revise", revision_id=revision_id, expected_plan_id=plan_id,
                nodes=list(nodes), remove_nodes=list(removed), reason="Use observed feedback for future work")


def test_revision_add_replace_remove_preserves_original_submission(tmp_path):
    first, second = method_node(tmp_path), method_node(tmp_path, "second")
    store, original = queue_store(tmp_path, first, second)
    changed = method_node(tmp_path, inputs={"factor": 2})
    added = method_node(tmp_path, "third")
    proposal = revision(original["plan_id"], changed, added, removed=["second"])
    accepted = service.advance(store.workspace, "job", proposal)
    assert accepted["pending_revision"] == "r1"
    assert "prepared" not in accepted["events"][-1]["body"]
    assert store.get("job")["nodes"][0]["definition"]["arguments"]["inputs"] == {}
    assert apply_pending(store, "job", 0)
    job = store.get("job")
    assert {n["id"] for n in job["nodes"]} == {"first", "third"}
    assert job["plan"]["plan_id"] != original["plan_id"]
    assert job["plan_id"] == original["plan_id"]
    assert not store.create("job", original)
    service.advance(store.workspace, "job", proposal)
    assert not apply_pending(store, "job", 0)
    assert len([e for e in store.get("job")["events"] if e["kind"] == "revision_applied"]) == 1
    assert job["llm_calls"] == 0


@pytest.mark.parametrize("late", [False, True])
def test_active_node_cannot_change_and_late_advice_does_not_stall(tmp_path, late):
    first = method_node(tmp_path)
    store, original = queue_store(tmp_path, first)
    proposal = revision(original["plan_id"], method_node(tmp_path, inputs={"factor": 3}))
    if late:
        service.advance(store.workspace, "job", proposal)
    with store.connection(write=True) as db:
        db.execute("UPDATE nodes SET state='running' WHERE job='job'")
    if late:
        assert not apply_pending(store, "job", 0)
        assert store.get("job")["events"][-1]["kind"] == "revision_not_applied"
        assert store.get("job")["proposal"] is None
    else:
        with pytest.raises(StateError, match="already started"):
            service.advance(store.workspace, "job", proposal)


def test_revision_cannot_drop_dependencies_or_expand_budget(tmp_path):
    first, second = method_node(tmp_path), method_node(tmp_path, "second")
    second["arguments"]["inputs"] = {"values": [{"$ref": "first.outputs.value"}]}
    store, original = queue_store(tmp_path, first, second)
    with pytest.raises(ValueError, match="dependency"):
        service.advance(store.workspace, "job", revision(original["plan_id"], removed=["first"]))
    too_much = copy.deepcopy(second)
    too_much["resources"]["wall_second"] = 181
    with pytest.raises(ValueError, match="limit"):
        service.advance(store.workspace, "job", revision(original["plan_id"], too_much))
    assert store.get("job")["proposal"] is None


def test_method_source_change_after_queue_retains_original_plan(tmp_path):
    node = method_node(tmp_path)
    store, original = queue_store(tmp_path, node)
    other = tmp_path / "alternative.py"
    other.write_text("pass", encoding="utf-8")
    changed = copy.deepcopy(node)
    changed["arguments"].update(command=[sys.executable, str(other)], files=[str(other)])
    service.advance(store.workspace, "job", revision(original["plan_id"], changed))
    other.write_text("raise Exception()", encoding="utf-8")
    assert not apply_pending(store, "job", 0)
    assert store.get("job")["plan"]["plan_id"] == original["plan_id"]


def test_terminal_commit_cannot_lose_queued_strategy(tmp_path):
    store, original = queue_store(tmp_path, method_node(tmp_path))
    service.advance(store.workspace, "job", revision(original["plan_id"], method_node(tmp_path, "extra")))
    assert store.set_state("job", 0, "succeeded") is False
    assert apply_pending(store, "job", 0)
    assert len(store.get("job")["nodes"]) == 2


def test_output_reference_carries_inputs_not_executable_identity(tmp_path):
    first, second = method_node(tmp_path), method_node(tmp_path, "second")
    second["arguments"]["command"] = {"$ref": "first.outputs.command"}
    with pytest.raises(ValueError, match="argv"):
        service.plan(spec(first, second))
    second = method_node(tmp_path, "second")
    second["arguments"]["inputs"] = {"value": {"$ref": "first.outputs.value"}}
    prepared = service.plan(spec(first, second))
    assert str(tmp_path / "method.py") in prepared["inputs"]
    assert str(Path(sys.executable)) in prepared["inputs"]


@pytest.mark.skipif(os.name != "posix", reason="persistent worker requires Linux /proc")
def test_single_method_uses_existing_action_handle(tmp_path):
    from etalon.runtime.api import prepare_action, submit_action, read_action_result
    node = method_node(tmp_path)
    request = dict(schema="etalon-action/1", request_id="direct-method", operation="method.run",
                   arguments=node["arguments"], limits=node["resources"], max_seconds=60)
    prepared = prepare_action(request)
    handle = submit_action(request, workspace=tmp_path / "action", plan_id=prepared["plan_id"])
    final = until(lambda: (r if (r := read_action_result(handle))["verification"]["state"] == "passed" else None))
    assert final["outputs"][0]["result"]["outputs"]["value"] == 6
    assert final["observations"] == []
    repeated = submit_action(request, workspace=tmp_path / "action", plan_id=prepared["plan_id"])
    assert repeated["job_id"] == handle["job_id"]


def until(fn, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError("condition not reached before timeout")


@pytest.mark.skipif(os.name != "posix", reason="persistent worker requires Linux /proc")
def test_live_method_sidecar_then_native_recovery_without_recomputation(tmp_path):
    import signal
    from etalon.runtime import process
    first = method_node(tmp_path, inputs={"hold": True})
    second = method_node(tmp_path, "next", inputs={"values": [{"$ref": "first.outputs.value"}]})
    original = spec(first, second)
    planned = service.plan(original)
    workspace = tmp_path / "runtime"
    service.submit(original, workspace, job_id="job", expected_plan_id=planned["plan_id"])
    store = RuntimeStore(workspace)
    step = store.job_root("job") / "steps" / "first"
    until(lambda: (step / "result.started").exists())
    changed = copy.deepcopy(second)
    changed["arguments"]["inputs"]["factor"] = 2
    proposal = revision(planned["plan_id"], changed)
    service.advance(workspace, "job", proposal)
    assert service.status(workspace, "job")["pending_revision"] == "r1"
    owner = store.get("job")["process_identity"]
    assert process.alive(owner)
    os.kill(owner["pid"], signal.SIGKILL)  # This test's workflow worker only; native child survives.
    until(lambda: not process.alive(owner))
    (step / "result.release").write_text("continue")
    until(lambda: (step / "native" / "finished.json").exists())
    resumed = service.reconcile(workspace, "job", reason="test worker exit with live native handle", resume=True)
    until(lambda: service.status(workspace, "job")["state"] == "succeeded")
    job = store.get("job")
    assert [n["result"]["outputs"]["value"] for n in job["nodes"]] == [6, 12]
    assert all(n["state"] == "verified" for n in job["nodes"])
    assert job["llm_calls"] == 0 and resumed["plan"]["revision_id"] != planned["plan_id"]
    assert len([e for e in job["events"] if e["kind"] == "dispatched"]) == 2
    before = store.get("job")["sequence"]
    service.submit(original, workspace, job_id="job", expected_plan_id=planned["plan_id"])
    service.advance(workspace, "job", proposal)
    assert store.get("job")["sequence"] == before
    assert job["nodes"][0]["evidence"]["recovered"]
    result = job["nodes"][1]["result"]
    operations.verify("method.run", job["nodes"][1]["parameters"], result)
    Path(result["artifacts"]["result"]["path"]).write_text("changed")
    with pytest.raises((ValueError, StateError)):
        operations.verify("method.run", job["nodes"][1]["parameters"], result)


@pytest.mark.skipif(os.name != "posix", reason="persistent worker requires Linux /proc")
def test_malformed_method_result_is_failed_and_cost_is_retained(tmp_path):
    original = spec(method_node(tmp_path, inputs={"malformed": True}))
    workspace = tmp_path / "runtime"
    service.submit(original, workspace, job_id="job", expected_plan_id=service.plan(original)["plan_id"])
    until(lambda: service.status(workspace, "job")["state"] == "failed")
    node = RuntimeStore(workspace).get("job")["nodes"][0]
    assert node["state"] == "failed"
    assert node["costs"]["wall_second"]["spent"] > 0
    assert node["result"]["scientific_admission"] == "not_inferred"
