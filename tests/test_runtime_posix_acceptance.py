"""Linux engineering acceptance: real OS workers/locks; explicit fault injection only.

These tests use cheap RDKit properties for races/recovery and a labelled external-child
fixture for unknown cancellation. No PRISM simulations or scientific acceptance claims.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from etalon.active.schema import CampaignSpec, Candidate, Endpoint
from etalon.active.store import CampaignStore, StateError
from etalon.runtime import api, process, service
from etalon.runtime.executors import prepare_executor, register_executor
from etalon.runtime.store import RuntimeStore
from etalon.campaign.design import component, compose

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real /proc identity and POSIX locks required")


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding="utf-8")


def until(predicate, *, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.025)
    raise AssertionError("real process condition did not become true")


def campaign(root, budget=1):
    root.mkdir(parents=True, exist_ok=True)
    configuration = {"cascade": compose("posix-cpu-fixture", [{"id": "measure", "title": "CPU properties",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}]),
        "readout": {"stage_id": "properties", "contract_id": "property/v1", "value_column": "mw"}}
    prepared = prepare_executor("molcascade", configuration,
        {"id": "mw", "target": "engineering-fixture", "quantity": "molecular_weight", "units": "Da", "cost": 1})
    store = CampaignStore(root / "campaign.sqlite")
    store.configure(CampaignSpec("mw", budget, "wall_second", "engineering-fixture"), [Endpoint(**prepared["endpoint"])])
    store.add_candidates([Candidate("one", "CCO", (1,)), Candidate("two", "CC", (2,))])
    register_executor(store.path, prepared, "Explicit cheap CPU engineering fixture; not affinity")
    request = {"schema": "etalon-action/1", "request_id": "first", "operation": "active.evaluate",
        "arguments": {"database": str(store.path), "candidate_id": "one", "endpoint_id": "mw"},
        "limits": {"campaign:wall_second": budget}, "method_use_refs": []}
    return store, request


def stopped(workspace, job):
    state = service.status(workspace, job)
    return state if not state["worker_alive"] and state["state"] not in {"queued", "starting"} else None


def confirm_owner_lock_released(workspace, job, destination, original_owner):
    """Observe the real flock becoming available; /proc death alone is insufficient."""
    runtime = RuntimeStore(workspace)
    attempts = []
    def probe():
        sample = {"at": time.time(), "original_owner": original_owner,
                  "current_identity": process.identity(original_owner["pid"]),
                  "group_alive": process.group_alive(original_owner)}
        try:
            with process.job_lock(runtime.job_root(job)):
                sample["exclusive_lock_acquired"] = True
        except StateError as error:
            if "workflow still has a live owner" not in str(error):
                raise
            sample.update(exclusive_lock_acquired=False, error=str(error))
        attempts.append(sample)
        save(destination, attempts)
        return sample["exclusive_lock_acquired"]
    until(probe)


@pytest.mark.parametrize("same_request,candidate_id", [(True, "one"), (False, "one"), (False, "two")])
def test_real_processes_compete_for_one_execution_and_final_budget(tmp_path, same_request, candidate_id):
    store, request = campaign(tmp_path)
    requests = [request, {**request, "request_id": "first" if same_request else "second",
                          "arguments": {**request["arguments"], "candidate_id": candidate_id}}]
    payloads = [{"request": r, "plan_id": api.prepare_action(r)["plan_id"], "workspace": str(tmp_path / "runtime")}
                for r in requests]
    barrier = tmp_path / "release"
    program = """import json,sys,time,os
from pathlib import Path
from etalon.runtime.api import submit_action
payload=json.loads(Path(sys.argv[1]).read_text());ready=Path(sys.argv[2]);ready.write_text(str(os.getpid()))
while not Path(sys.argv[3]).exists():time.sleep(.005)
try: result={'ok':True,'handle':submit_action(**payload)}
except Exception as error:result={'ok':False,'type':type(error).__name__,'error':str(error)}
Path(sys.argv[4]).write_text(json.dumps(result))
"""
    children=[]
    for i,payload in enumerate(payloads):
        save(tmp_path / f"request-{i}.json", payload)
        children.append(subprocess.Popen([sys.executable, "-c", program, str(tmp_path/f"request-{i}.json"),
            str(tmp_path/f"ready-{i}"), str(barrier), str(tmp_path/f"response-{i}.json")]))
    until(lambda: all((tmp_path/f"ready-{i}").exists() for i in range(2)))
    save(tmp_path/"submitter-processes.json", [process.identity(p.pid) for p in children])
    barrier.write_text("release two independent processes")
    for child in children: assert child.wait(timeout=30) == 0
    responses=[json.loads((tmp_path/f"response-{i}.json").read_text()) for i in range(2)]
    save(tmp_path/"submit-responses.json",responses)
    jobs={r["handle"]["job_id"] for r in responses if r["ok"]}
    assert jobs
    for job in jobs:
        until(lambda: stopped(tmp_path/"runtime", job))
    state=store.export();save(tmp_path/"campaign-after.json",state)
    runtime=RuntimeStore(tmp_path/"runtime")
    for job in jobs:save(tmp_path/f"job-{job}.json",runtime.get(job))
    assert len(store.actions()) == 1 and len(store.observations()) == 1
    assert store.balance()["spent"] == 1 and store.balance()["reserved"] == 0 and store.balance()["remaining"] == 0
    if same_request:
        assert all(r["ok"] for r in responses) and len(jobs)==1
        assert runtime.get("first")["epoch"]==1
    for response in responses:
        if response["ok"]:
            original=response["handle"]
            replay=api.submit_action(payloads[responses.index(response)]["request"],workspace=tmp_path/"runtime",plan_id=original["plan_id"])
            assert replay["job_id"]==original["job_id"]
    assert len(store.actions())==1


def injector(tmp_path, monkeypatch, code):
    root=tmp_path/"fault-injection";root.mkdir()
    (root/"sitecustomize.py").write_text(code,encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(root)+os.pathsep+os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("ETALON_FIXTURE_ROOT", str(tmp_path))
    return root


def test_real_worker_killed_after_durable_receipt_recovers_without_second_execution(tmp_path, monkeypatch):
    store,request=campaign(tmp_path)
    injector(tmp_path,monkeypatch,"""import os,signal,json
from pathlib import Path
from etalon.runtime.service import Context
original=Context.receipt
def receipt(self,key,body):
 original(self,key,body)
 if key.startswith('action:'):
  root=Path(os.environ['ETALON_FIXTURE_ROOT'])
  with (root/'execution-count.jsonl').open('a') as f:f.write(json.dumps({'pid':os.getpid(),'action':body['action']['id']})+'\\n')
  os.kill(os.getpid(),signal.SIGKILL)
Context.receipt=receipt
""")
    prepared=api.prepare_action(request)
    handle=api.submit_action(request,workspace=tmp_path/"runtime",plan_id=prepared["plan_id"])
    dead=until(lambda: stopped(tmp_path/"runtime",handle["job_id"]))
    save(tmp_path/"killed-worker.json",dead);save(tmp_path/"before-recovery.json",store.export())
    assert store.balance()["reserved"]==1 and not store.observations()
    original_owner=dead["process_identity"];assert original_owner and not process.alive(original_owner)
    before=RuntimeStore(tmp_path/"runtime").receipts("first","action")
    assert len([k for k in before if k.startswith("action:")])==1
    confirm_owner_lock_released(tmp_path/"runtime", "first", tmp_path/"owner-lock-observations.json", original_owner)
    assert len((tmp_path/"execution-count.jsonl").read_text().splitlines()) == 1
    assert RuntimeStore(tmp_path/"runtime").receipts("first", "action") == before
    restored=service.reconcile(tmp_path/"runtime","first",reason="Real SIGKILL after durable receipt in CPU engineering fixture")
    save(tmp_path/"after-recovery.json",restored);save(tmp_path/"campaign-recovered.json",store.export())
    assert restored["state"]=="succeeded" and len(store.observations())==1
    assert store.balance()["spent"]==1 and store.balance()["reserved"]==0
    assert len((tmp_path/"execution-count.jsonl").read_text().splitlines())==1
    api.submit_action(request,workspace=tmp_path/"runtime",plan_id=prepared["plan_id"])
    assert len((tmp_path/"execution-count.jsonl").read_text().splitlines())==1


_CHILD_INJECTOR = r'''import os,sys,time,subprocess,json
from pathlib import Path
from etalon.runtime import executors
from etalon.active.store import CampaignStore
from etalon.active.runner import ExecutionPending
root=Path(os.environ['ETALON_FIXTURE_ROOT'])
def build(database,workspace,checkpoint=None,process_observer=None,endpoint_ids=None):
 def execute(action,candidate,endpoint,grant):
  code="""import os,sys,time,json,signal
from pathlib import Path
root=Path(sys.argv[1]);signal.signal(signal.SIGTERM,lambda *a:None)
while not (root/'allow-child').exists():time.sleep(.01)
started=time.monotonic();(root/'child-ready').write_text(str(os.getpid()))
while not (root/'release-child').exists():time.sleep(.01)
(root/'child-result.json').write_text(json.dumps({'elapsed':time.monotonic()-started,'pid':os.getpid(),'status':'fixture_completed_after_controller_cancel'}))
"""
  child=subprocess.Popen([sys.executable,'-c',code,str(root)],start_new_session=True)
  process_observer(child.pid)
  (root/'allow-child').write_text('identity receipt committed before work')
  started=time.monotonic();time.sleep(.1)
  measured=time.monotonic()-started
  CampaignStore(database).record_execution_costs(action.id,[{'execution_id':'partial-fixture','wall_seconds':measured,'kind':'controlled_cpu_fixture'}])
  (root/'partial-recorded').write_text(str(measured))
  while child.poll() is None:time.sleep(.05)
  raise ExecutionPending('fixture intentionally supplies no final scientific outcome receipt')
 return execute,None
executors.build_executor=build
'''


def test_real_child_cancellation_keeps_unknown_and_measured_cost_until_reconciliation(tmp_path,monkeypatch):
    store,request=campaign(tmp_path,budget=10)
    injector(tmp_path,monkeypatch,_CHILD_INJECTOR)
    prepared=api.prepare_action(request)
    handle=api.submit_action(request,workspace=tmp_path/"runtime",plan_id=prepared["plan_id"])
    until(lambda:(tmp_path/"child-ready").exists() and (tmp_path/"partial-recorded").exists())
    runtime=RuntimeStore(tmp_path/"runtime")
    receipts=runtime.receipts("first","action")
    owner=next(v for k,v in receipts.items() if k.startswith("process:"))
    assert process.alive(owner);save(tmp_path/"child-identity.json",owner)
    action=store.actions()[0];partial=store.balance()["spent"];assert partial>0
    try:
        cancelled=service.cancel(tmp_path/"runtime","first",reason="Controlled real-child cancellation fixture")
        save(tmp_path/"cancel-return.json",cancelled)
        assert cancelled["state"]!="cancelled" and process.alive(owner)
        stopped_worker=until(lambda:stopped(tmp_path/"runtime","first"));save(tmp_path/"cancelled-worker-stopped.json",stopped_worker)
        assert stopped_worker["state"]=="reconciliation_required"
        confirm_owner_lock_released(tmp_path/"runtime", "first", tmp_path/"cancel-owner-lock.json", stopped_worker["process_identity"])
        assert store.balance()["reserved"]==1 and store.balance()["spent"]==partial
        with pytest.raises(StateError,match="child session"):
            service.reconcile(tmp_path/"runtime","first",reason="Child still demonstrably active")
        with pytest.raises(StateError,match="reconciliation"):
            api.prepare_action({**request,"request_id":"cannot-bypass"})
    finally:
        (tmp_path/"release-child").write_text("release only this fixture child")
        until(lambda:not process.group_alive(owner))
    unknown=service.reconcile(tmp_path/"runtime","first",reason="Child exited but no scientific final receipt exists")
    save(tmp_path/"unknown-after-exit.json",unknown)
    assert unknown["state"]=="reconciliation_required" and store.balance()["reserved"]==1
    assert store.balance()["spent"]==partial
    measured=json.loads((tmp_path/"child-result.json").read_text())["elapsed"]
    evidence=runtime.job_root("first")/"steps/action/fixture-exit-evidence.json"
    save(evidence,{"source":"controlled real process fixture","process":owner,"elapsed":measured,"outcome":"explicit failed scientific action, no scalar observation"})
    settled=service.reconcile(tmp_path/"runtime","first",reason="Actual child completion evidence supplies final failure cost",settlements={
        "action":{"costs":{"campaign:wall_second":measured},"action_costs":{action["id"]:measured},
                  "reason":"Measured fixture duration; no scientific scalar result", "evidence":evidence.name}})
    save(tmp_path/"final-settlement.json",settled);save(tmp_path/"campaign-final.json",store.export())
    assert settled["state"]=="failed"
    assert store.balance()["reserved"]==0 and store.balance()["spent"]==measured
    assert store.observations()[0]["result"]["value"] is None
