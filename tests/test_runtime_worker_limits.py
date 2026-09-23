"""Observable worker limits with isolated processes and explicit fault fixtures.

The advisor is a local Transport, campaign creation uses the real operation, and
overruns use sealed fixture receipts. No paid model, network request or GPU runs.
Only the launcher seam and selected execution failures are injected; the worker,
controller, verifier, journal, cancellation and reconciliation code remain real.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from etalon.active.schema import canonical
from etalon.runtime import operations, process, service
from etalon.runtime.store import RuntimeStore

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="workers require Linux process identities")
ROOT = Path(__file__).resolve().parents[1]
JOB = "limits"


def _append(path, record):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _worker_entry(workspace, epoch, settings_path, gate_path):
    """Subprocess fixture entry; the service still owns all dispatch decisions."""
    settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    directory = Path(settings_path).parent
    deadline = time.monotonic() + 5
    while not Path(gate_path).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("fixture launcher did not publish ownership")
        time.sleep(0.01)

    class ProbeTransport:
        name = "local-worker-limit-fixture"

        def ask(self, prompt):
            observed = service.observe(workspace, JOB)
            _append(directory / "asks.jsonl", {"persisted_calls": service.status(workspace, JOB)["llm_calls"],
                "observation_id": observed["observation_id"], "ready": [n["id"] for n in observed["ready"]]})
            choice = {"observation_id": observed["observation_id"], "node_id": observed["ready"][0]["id"],
                      "reason": "Execute the next declared fixture operation"}
            reply = settings["reply"]
            if reply == "not-json":
                return "I completed the work."
            if reply == "extra":
                return json.dumps({**choice, "scientific_success": True})
            if reply == "duplicate":
                return json.dumps(choice)[:-1] + ',"node_id":"finish"}'
            if reply == "finish":
                return json.dumps({**choice, "node_id": "finish"})
            if reply == "transport-error":
                from etalon.judgment.advisor import AdvisorError

                raise AdvisorError("controlled transient transport failure", retryable=True)
            if reply == "wait":
                time.sleep(30)
            return json.dumps(choice)

    original_execute = operations.execute

    def execute(operation, arguments, ctx):
        _append(directory / "dispatches.jsonl", {"node_id": ctx.node, "operation": operation})
        behavior = settings["execution"]
        if behavior == "wait":
            ctx.receipt("fixture_started", {"status": "unknown outcome; deliberately held execution"})
            time.sleep(30)
        if behavior in {"over-budget", "over-budget-crash"}:
            from etalon.data.artifacts import new_run, seal, write_json

            root = new_run(ctx.root, "acquisition", arguments["request"])
            write_json(root / "records.json", [{"id": "fixture", "smiles": "CCO"}])
            result = seal(root, kind="import_catalog", result={"status": "complete", "parsed_records": 1},
                infrastructure={"fixture": "injected overrun receipt, not measured external traffic"},
                usage={"requests": 0, "response_bytes": 101})
            if behavior == "over-budget-crash":
                ctx.receipt("completed", {"result": result})
                os._exit(23)  # Simulate death after durable outcome publication, before settlement.
            return result
        return original_execute(operation, arguments, ctx)

    operations.execute = execute
    return service.run_worker(workspace, JOB, int(epoch), transport=ProbeTransport())


class WorkerHarness:
    def __init__(self, root, monkeypatch, *, reply="ready", execution="native"):
        self.root, self.workspace = root, root / "runtime"
        self.children = []
        self.settings = root / "fixture-settings.json"
        self.settings.write_text(json.dumps({"reply": reply, "execution": execution}), encoding="utf-8")
        monkeypatch.setattr(service, "_launch", self.launch)

    def launch(self, store, job_id):
        # This fixture substitutes only process construction so run_worker can
        # receive the local Transport. It publishes the normal ownership tuple.
        job = store.get(job_id)
        assert job_id == JOB and job["state"] == "queued"
        epoch = job["epoch"] + 1
        gate, log_path = self.root / f"start-{epoch}", self.root / f"worker-{epoch}.log"
        program = "import runpy,sys; module=runpy.run_path(sys.argv[1]); sys.exit(module['_worker_entry'](*sys.argv[2:]))"
        with log_path.open("x", encoding="utf-8") as log:
            child = subprocess.Popen([sys.executable, "-c", program, str(Path(__file__).resolve()),
                str(self.workspace), str(epoch), str(self.settings), str(gate)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
        self.children.append((child, log_path))
        owner = process.identity(child.pid)
        assert owner is not None
        with store.connection(write=True) as db:
            db.execute("UPDATE jobs SET state='starting',epoch=?,pid=?,process_identity=?,heartbeat=NULL WHERE id=?",
                       (epoch, child.pid, canonical(owner), job_id))
        gate.write_text("ownership committed\n", encoding="utf-8")

    def submit(self, spec):
        self.spec = spec
        self.prepared = service.plan(spec)
        return service.submit(spec, self.workspace, job_id=JOB, expected_plan_id=self.prepared["plan_id"])

    def wait(self, *, code=1):
        child, log = self.children[-1]
        actual = child.wait(timeout=15)
        assert actual == code, log.read_text(encoding="utf-8")
        report = service.status(self.workspace, JOB)
        assert report["worker_alive"] is False
        return report

    def records(self, name):
        path = self.root / (name + ".jsonl")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def until_recorded(self, name):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.records(name):
                return
            child, log = self.children[-1]
            assert child.poll() is None, log.read_text(encoding="utf-8")
            time.sleep(0.02)
        pytest.fail(f"worker never reached {name}")

    def close(self):
        for child, _ in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)


@pytest.fixture
def workers(tmp_path, monkeypatch):
    created = []

    def build(**settings):
        worker = WorkerHarness(tmp_path, monkeypatch, **settings)
        created.append(worker)
        return worker

    yield build
    for worker in created:
        worker.close()


def campaigns(*, mode="advisor", max_calls=1, count=2, max_seconds=30):
    node = {"id": "first", "operation": "campaign.create", "resources": {"work_units": 1}, "arguments": {
        "spec": {"objective": "fixture", "budget": 0, "cost_unit": "fixture", "representation": "fixture"},
        "endpoints": [{"id": "fixture", "target": "fixture", "quantity": "fixture", "units": "u",
            "protocol": "fixture", "cost": 0, "queryable": False, "requires_handoff": False}], "executors": []}}
    nodes = [copy.deepcopy(node) for _ in range(count)]
    for index, item in enumerate(nodes):
        item["id"] = f"step{index + 1}"
    return {"schema": "etalon-workflow/1", "objective": "Create only the declared fixture journals",
            "nodes": nodes, "limits": {"work_units": count}, "controller": {"mode": mode, "max_calls": max_calls},
            "max_seconds": max_seconds}


def test_advisor_allowance_caps_actual_asks_and_survives_resume(workers):
    worker = workers()
    worker.submit(campaigns(max_calls=1))
    report = worker.wait()
    assert report["state"] == "blocked" and report["llm_calls"] == 1
    assert [n["state"] for n in report["nodes"]] == ["verified", "waiting"]
    assert [record["persisted_calls"] for record in worker.records("asks")] == [1]
    assert [record["node_id"] for record in worker.records("dispatches")] == ["step1"]
    assert not (worker.workspace / "jobs" / JOB / "steps/step2").exists()
    service.reconcile(worker.workspace, JOB, resume=True, reason="Explicit retry must retain the original model allowance")
    repeated = worker.wait()
    assert repeated["state"] == "blocked" and repeated["llm_calls"] == 1
    assert len(worker.records("asks")) == len(worker.records("dispatches")) == 1


def test_exact_advisor_allowance_finishes_without_a_paid_finish_call(workers):
    worker = workers()
    worker.submit(campaigns(max_calls=2))
    report = worker.wait(code=0)
    assert report["state"] == "succeeded" and report["llm_calls"] == 2
    assert [record["persisted_calls"] for record in worker.records("asks")] == [1, 2]
    assert all(node["state"] == "verified" for node in report["nodes"])
    assert report["resources"]["work_units"]["spent"] == 2


@pytest.mark.parametrize("reply", ["not-json", "extra", "duplicate", "finish", "transport-error"])
def test_refused_advisor_response_costs_one_attempt_and_never_dispatches_or_retries(workers, reply):
    worker = workers(reply=reply)
    worker.submit(campaigns(max_calls=3, count=1))
    report = worker.wait()
    assert report["state"] == "blocked" and report["llm_calls"] == 1
    assert report["nodes"][0]["state"] == "waiting"
    assert len(worker.records("asks")) == 1 and worker.records("dispatches") == []
    assert report["resources"]["work_units"]["reserved"] == report["resources"]["work_units"]["spent"] == 0
    repeated = service.submit(worker.spec, worker.workspace, job_id=JOB, expected_plan_id=worker.prepared["plan_id"])
    assert repeated["state"] == "blocked" and repeated["llm_calls"] == 1
    assert len(worker.children) == 1 and len(worker.records("asks")) == 1


@pytest.mark.parametrize("stop", ["deadline", "cancel"])
def test_interrupted_execution_retains_unknown_resource_reservations(workers, stop):
    worker = workers(execution="wait")
    worker.submit(campaigns(mode="ordered", count=1, max_seconds=1 if stop == "deadline" else 30))
    worker.until_recorded("dispatches")
    if stop == "cancel":
        service.cancel(worker.workspace, JOB, reason="Cancel while the controlled executor is in progress")
    report = worker.wait()
    assert report["state"] == "reconciliation_required" and report["needs_reconciliation"]
    assert report["nodes"][0]["state"] == "reconciliation_required"
    assert report["resources"]["work_units"] == {"limit": 1, "spent": 0, "reserved": 1, "remaining": 0}
    assert report["nodes"][0]["costs"]["work_units"]["spent"] is None
    restored = service.reconcile(worker.workspace, JOB, resume=True, reason="Unknown scientific outcome must block resumption")
    assert restored["state"] == "reconciliation_required"
    assert restored["resources"] == report["resources"]
    assert len(worker.children) == len(worker.records("dispatches")) == 1


def test_deadline_interrupts_a_blocked_transport_without_scientific_dispatch(workers):
    worker = workers(reply="wait")
    worker.submit(campaigns(count=1, max_seconds=1))
    worker.until_recorded("asks")
    report = worker.wait()
    assert report["state"] == "cancelled" and report["llm_calls"] == 1
    assert report["nodes"][0]["state"] == "waiting" and worker.records("dispatches") == []
    assert report["resources"]["work_units"]["reserved"] == 0


@pytest.mark.parametrize("execution", ["over-budget", "over-budget-crash"])
def test_verified_final_overrun_is_blocked_in_execution_and_recovery(workers, tmp_path, execution):
    worker = workers(execution=execution)
    catalog = tmp_path / "catalog.csv"
    catalog.write_text("id,smiles\nfixture,CCO\n", encoding="utf-8")
    spec = {"schema": "etalon-workflow/1", "objective": "Inspect an explicit sealed overrun fixture",
        "controller": {"mode": "ordered"}, "limits": {"http_requests": 0, "http_bytes": 100},
        "nodes": [{"id": "acquire", "operation": "data.acquire", "arguments": {"request": {
            "kind": "import_catalog", "source": "chembl", "path": str(catalog),
            "options": {"source_version": "OFFLINE-FIXTURE"}},
            "budget": {"max_requests": 0, "max_bytes": 100, "max_seconds": 10}, "min_records": 1},
            "resources": {"http_requests": 0, "http_bytes": 100}}]}
    worker.submit(spec)
    report = worker.wait(code=23 if execution.endswith("crash") else 1)
    if execution.endswith("crash"):
        assert report["needs_reconciliation"] and report["nodes"][0]["state"] == "running"
        assert report["resources"]["http_bytes"]["reserved"] == 100
        report = service.reconcile(worker.workspace, JOB, reason="Recover the saved overrun without repeating acquisition")
    assert report["state"] == "blocked" and report["nodes"][0]["state"] == "verified"
    assert report["resources"]["http_bytes"] == {"limit": 100, "spent": 101, "reserved": 0, "remaining": -1}
    persisted = RuntimeStore(worker.workspace).get(JOB)
    assert persisted["nodes"][0]["evidence"]["snapshot_id"]
    repeated = service.reconcile(worker.workspace, JOB, resume=True, reason="Verified artifacts cannot erase an actual overrun")
    assert repeated["state"] == "blocked" and repeated["resources"] == report["resources"]
    assert len(worker.children) == len(worker.records("dispatches")) == 1
