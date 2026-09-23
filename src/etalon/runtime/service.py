"""Submit, supervise, cancel and reconcile bounded scientific workflows.

The worker owns the task graph, while CampaignStore owns experimental truth.
Recovery follows AiiDA-style persisted steps and preserves uncertain side effects:
https://doi.org/10.1016/j.commatsci.2020.110086
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path

from etalon.active.schema import canonical, digest
from etalon.active.store import CampaignStore, StateError
from etalon.data.artifacts import file_hash
from etalon.runtime import controller, operations, process
from etalon.runtime.schema import (
    absolute,
    amounts,
    fields,
    identifier,
    plan_id,
    resolve,
    text,
    workflow,
)
from etalon.runtime.store import RuntimeStore


def plan(spec: dict) -> dict:
    normalized = workflow(spec)
    inputs = operations.input_hashes([node["arguments"] for node in normalized["nodes"]])
    return {"plan_id": plan_id(normalized, inputs), "spec": normalized, "inputs": inputs,
            "scope": "bounded registered operations; quoted costs remain distinguished from measured usage"}


def _observation(job: dict) -> dict:
    # Heartbeats, PIDs and wall time are deliberately excluded from model observations.
    # Workflow events, node definitions, outcomes and states remain bound to the decision.
    nodes = [{k: node[k] for k in ("id", "definition", "state", "result")} for node in job["nodes"]]
    return controller.observation(job["plan"]["spec"], nodes, job["sequence"])


def observe(workspace: str | Path, job_id: str) -> dict:
    return _observation(RuntimeStore(workspace).get(job_id))


def status(workspace: str | Path, job_id: str) -> dict:
    job = RuntimeStore(workspace).get(job_id)
    job["worker_alive"] = process.alive(job["process_identity"])
    job["heartbeat_age_seconds"] = None if job["heartbeat"] is None else max(0, time.time() - job["heartbeat"])
    job["needs_reconciliation"] = (job["state"] in {"starting", "running", "cancel_requested"} and not job["worker_alive"]
                                    or any(n["state"] == "reconciliation_required" for n in job["nodes"]))
    job["plan"] = {"plan_id": job["plan_id"], "objective": job["plan"]["spec"]["objective"],
                   "controller": job["plan"]["spec"]["controller"], "max_seconds": job["plan"]["spec"]["max_seconds"]}
    for node in job["nodes"]:
        node["operation"] = node["definition"]["operation"]
        if node["result"] is not None:
            result = node.pop("result")
            node["result_sha256"] = digest(result)
            node["outputs"] = {key: value for key, value in result.items()
                               if isinstance(value, (str, int, float, bool, type(None)))}
        node.pop("definition", None)
        node.pop("parameters", None)
    job.pop("proposal", None)
    return job


def _launch(store: RuntimeStore, job_id: str) -> None:
    if process.identity(os.getpid()) is None:
        raise StateError("durable local workers require Linux /proc process-start identities")
    root = store.job_root(job_id)
    root.mkdir(parents=True, exist_ok=True)
    with store.connection(write=True) as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row["state"] != "queued":
            return
        epoch = row["epoch"] + 1
        log_path = root / f"worker-{epoch}.log"
        try:
            with log_path.open("x", encoding="utf-8") as log:
                env = dict(os.environ)
                env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(Path(__file__).resolve().parents[2]), env.get("PYTHONPATH", ""))))
                child = subprocess.Popen([sys.executable, "-m", "etalon.runtime.worker", str(store.workspace), job_id, str(epoch)],
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                           cwd=root, env=env, start_new_session=True)
            owner = process.identity(child.pid)
            if owner is None:
                raise StateError("could not identify the new worker; no scientific dispatch is authorized")
            db.execute("UPDATE jobs SET state='starting',epoch=?,pid=?,process_identity=?,heartbeat=NULL,updated=? WHERE id=?",
                       (epoch, child.pid, canonical(owner), time.time(), job_id))
            store.event(db, job_id, "worker_started", {"epoch": epoch, "process": owner, "log": str(log_path)})
            threading.Thread(target=child.wait, daemon=True).start()
        except Exception as error:
            db.execute("UPDATE jobs SET state='failed',error=?,updated=? WHERE id=?", (f"launch failed: {error}", time.time(), job_id))
            store.event(db, job_id, "launch_failed", {"error": str(error)})
            # Persist the failed launch; submitting the same id never silently retries.


def submit(spec: dict, workspace: str | Path, *, job_id: str, expected_plan_id: str) -> dict:
    identifier(job_id)
    prepared = plan(spec)
    if prepared["plan_id"] != expected_plan_id:
        raise StateError("workflow inputs or configuration changed; inspect a fresh plan")
    store = RuntimeStore(workspace, create=True)
    created = store.create(job_id, prepared)
    if created:
        if prepared["spec"]["controller"]["mode"] == "external":
            store.set_state(job_id, 0, "awaiting_decision")
        else:
            _launch(store, job_id)
    elif store.get(job_id)["state"] == "queued":
        # A submitter can die after recording the job but before launching it. A
        # worker cannot execute without the committed starting/PID/epoch record.
        _launch(store, job_id)
    return status(workspace, job_id)


def advance(workspace: str | Path, job_id: str, proposal: dict) -> dict:
    store = RuntimeStore(workspace)
    with process.job_lock(store.job_root(job_id)):
        job = store.get(job_id)
        if job["state"] != "awaiting_decision" or job["plan"]["spec"]["controller"]["mode"] != "external":
            raise StateError("workflow is not waiting for an external model decision")
        observed = _observation(job)
        decision = controller.decide(observed, proposal=proposal)
        with store.connection(write=True) as db:
            row = store.owned(db, job_id, job["epoch"])
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events WHERE job=?", (job_id,)).fetchone()[0]
            if sequence != job["sequence"] or row["cancel_requested"]:
                raise StateError("workflow changed while accepting the decision")
            db.execute("UPDATE jobs SET proposal=?,state='queued',updated=? WHERE id=?",
                       (canonical({"observation": observed, "proposal": proposal}), time.time(), job_id))
            store.event(db, job_id, "external_decision", decision)
    _launch(store, job_id)
    return status(workspace, job_id)


def cancel(workspace: str | Path, job_id: str, *, reason: str) -> dict:
    text(reason, "cancellation reason")
    store = RuntimeStore(workspace)
    with store.connection(write=True) as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier(job_id),)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row["state"] not in {"succeeded", "failed", "cancelled"}:
            state = "cancel_requested" if row["pid"] else "cancelled"
            db.execute("UPDATE jobs SET cancel_requested=1,state=?,error=?,updated=? WHERE id=?", (state, reason, time.time(), job_id))
            store.event(db, job_id, "cancel_requested", {"reason": reason})
        owner = json.loads(row["process_identity"]) if row["process_identity"] else None
    # Before first heartbeat the worker may not have installed its signal handler.
    # It will see the persisted flag before starting any operation.
    if row["heartbeat"] is not None and row["state"] in {"running", "cancel_requested"}:
        process.signal_cancel(owner)
    return status(workspace, job_id)


class Context:
    def __init__(self, store: RuntimeStore, job: str, epoch: int, node: str, *, check=None):
        self.store, self.job, self.epoch, self.node = store, job, epoch, node
        self.root = store.job_root(job) / "steps" / identifier(node)
        self.preflight: dict = {}
        self._check = check

    def checkpoint(self):
        if self._check is not None:
            self._check()

    def receipt(self, key: str, body: dict):
        self.store.receipt(self.job, self.epoch, self.node, key, body)

    def receipts(self) -> dict:
        return self.store.receipts(self.job, self.node)

    def process_observer(self, pid: int):
        owner = process.identity(pid)
        if owner is None:
            raise StateError("cannot persist a scientific child process identity")
        self.receipt("process:" + str(pid), owner)


def _resource_lock(parameters: dict):
    return (process.file_lock(Path(str(absolute(parameters["database"])) + ".runtime.lock"))
            if "database" in parameters else nullcontext())


def _verify_dependencies(job: dict, dependencies: list[str]) -> None:
    for node in job["nodes"]:
        if node["id"] in dependencies:
            if node["state"] != "verified":
                raise StateError("step dependency is not verified")
            kwargs = {}
            if node["definition"]["operation"] == "active.run":
                receipts = RuntimeStore(job["workspace"]).receipts(job["id"], node["id"])
                kwargs = {"active_start": receipts.get("active_start"), "action_receipts": receipts}
            elif node["definition"]["operation"].startswith("campaign."):
                kwargs = {"workspace": RuntimeStore(job["workspace"]).job_root(job["id"]) / "steps" / node["id"]}
            operations.verify(node["definition"]["operation"], node["parameters"], node["result"], **kwargs)


def _over_budget(job: dict) -> bool:
    return any(v["remaining"] is not None and v["remaining"] < -1e-12 for v in job["resources"].values())


def _complete(ctx: Context, node: dict, parameters: dict, result: dict, *, recovered: bool = False) -> bool:
    operation = node["definition"]["operation"]
    kwargs = {}
    if operation == "active.run":
        receipts = ctx.receipts()
        kwargs = {"active_start": receipts.get("active_start"), "action_receipts": receipts}
    spent, basis = operations.accounting(operation, result, node["definition"]["resources"],
                                         database=parameters.get("database"), **kwargs)
    try:
        verification = {**kwargs, "workspace": ctx.root} if operation.startswith("campaign.") else kwargs
        evidence = operations.verify(operation, parameters, result, **verification)
    except Exception as error:
        evidence = {"verified": False, "result_sha256": digest(result), "recovered": recovered}
        ctx.store.finish_node(ctx.job, ctx.epoch, ctx.node, result, evidence, spent, basis=basis, state="failed", error=str(error))
        return False
    evidence["recovered"] = recovered
    ctx.store.finish_node(ctx.job, ctx.epoch, ctx.node, result, evidence, spent, basis=basis)
    return True


def _control(store: RuntimeStore, job_id: str, epoch: int, checkpoint, *, transport=None) -> None:
    while True:
        checkpoint()
        job = store.get(job_id)
        operations.check_inputs(job["plan"]["inputs"])
        observed = _observation(job)
        if _over_budget(job):
            store.set_state(job_id, epoch, "blocked", error="actual resource usage exceeded its allowance; no further dispatch")
            return
        if observed["all_verified"]:
            _verify_dependencies(job, [n["id"] for n in job["nodes"]])
            store.set_state(job_id, epoch, "succeeded")
            return
        if any(n["state"] in {"failed", "reconciliation_required", "running"} for n in job["nodes"]):
            store.set_state(job_id, epoch, "reconciliation_required" if any(
                n["state"] in {"running", "reconciliation_required"} for n in job["nodes"]) else "failed")
            return
        mode = job["plan"]["spec"]["controller"]["mode"]
        if mode == "external":
            if job["proposal"] is None:
                store.set_state(job_id, epoch, "awaiting_decision")
                return
            decision = controller.decide(job["proposal"]["observation"], proposal=job["proposal"]["proposal"])
            with store.connection(write=True) as db:
                store.owned(db, job_id, epoch)
                db.execute("UPDATE jobs SET proposal=NULL WHERE id=?", (job_id,))
        elif mode == "advisor":
            if transport is None:
                from etalon.judgment.providers import transport_from_env

                transport = transport_from_env()
            store.count_call(job_id, epoch)
            job = store.get(job_id)
            observed = _observation(job)
            decision = controller.decide(observed, transport=transport)
        else:
            decision = controller.decide(observed)
        checkpoint()
        if decision["node_id"] == "pause":
            store.set_state(job_id, epoch, "blocked", error=decision["reason"])
            return
        selected = next((n for n in job["nodes"] if n["id"] == decision["node_id"]), None)
        if selected is None or selected["state"] != "waiting":
            raise StateError("decision no longer selects an undispatched node")
        definition = selected["definition"]
        _verify_dependencies(job, definition["depends_on"])
        parameters = resolve(definition["arguments"], {n["id"]: n["result"] for n in job["nodes"] if n["state"] == "verified"})
        with _resource_lock(parameters):
            prepared = operations.preflight(definition["operation"], parameters, definition["resources"])
            ctx = Context(store, job_id, epoch, selected["id"], check=checkpoint)
            ctx.preflight = prepared
            store.start_node(job_id, epoch, definition, parameters, decision)
            try:
                ctx.root.mkdir(parents=True, exist_ok=False)
                ctx.receipt("preflight", prepared)
                result = operations.execute(definition["operation"], parameters, ctx)
                # Publish the original outcome before admission checks, so a crash during
                # verification can be reconciled without running the tool a second time.
                ctx.receipt("completed", {"result": result})
                operations.check_inputs(prepared["inputs"])
                if not _complete(ctx, selected, parameters, result):
                    store.set_state(job_id, epoch, "failed", error="a step did not satisfy its fixed verification contract")
                    return
            except BaseException as error:
                store.unresolved(job_id, epoch, selected["id"], f"{type(error).__name__}: {error}")
                raise


def run_worker(workspace: str | Path, job_id: str, epoch: int, *, transport=None) -> int:
    store = RuntimeStore(workspace)
    stop = threading.Event()
    previous_handler = signal.getsignal(signal.SIGTERM)
    interrupted = False

    def terminate(_signum, _frame):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt("workflow cancellation or wall-clock deadline")

    signal.signal(signal.SIGTERM, terminate)
    started, initial = time.monotonic(), store.get(job_id)
    base_elapsed = initial["elapsed"]

    def checkpoint():
        job = store.get(job_id)
        if job["epoch"] != epoch:
            raise KeyboardInterrupt("worker ownership changed")
        if job["cancel_requested"] or base_elapsed + time.monotonic() - started >= job["plan"]["spec"]["max_seconds"]:
            raise KeyboardInterrupt("workflow cancellation or wall-clock deadline")

    def heartbeat():
        notified = False
        while not stop.wait(0.5):
            try:
                elapsed = base_elapsed + time.monotonic() - started
                cancelled = store.heartbeat(job_id, epoch, elapsed=elapsed)
                if not notified and (cancelled or elapsed >= initial["plan"]["spec"]["max_seconds"]):
                    notified = True
                    os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                # Losing durable ownership is a reason to stop dispatching, not to steal it.
                if not notified:
                    notified = True
                    os.kill(os.getpid(), signal.SIGTERM)
                return

    try:
        with process.job_lock(store.job_root(job_id)):
            with store.connection(write=True) as db:
                row = store.owned(db, job_id, epoch)
                if row["state"] != "starting" or row["pid"] != os.getpid():
                    raise StateError("only the launched worker can claim this workflow")
                db.execute("UPDATE jobs SET state='running',heartbeat=?,updated=? WHERE id=?", (time.time(), time.time(), job_id))
                store.event(db, job_id, "running", {"epoch": epoch})
            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            try:
                _control(store, job_id, epoch, checkpoint, transport=transport)
            except KeyboardInterrupt as error:
                current = store.get(job_id)
                state = "reconciliation_required" if any(n["state"] in {"running", "reconciliation_required"} for n in current["nodes"]) else "cancelled"
                store.set_state(job_id, epoch, state, error=str(error))
            except Exception as error:
                current = store.get(job_id)
                state = "reconciliation_required" if any(n["state"] in {"running", "reconciliation_required"} for n in current["nodes"]) else "blocked"
                store.set_state(job_id, epoch, state, error=f"{type(error).__name__}: {error}")
            finally:
                stop.set()
                thread.join(timeout=2)
                store.heartbeat(job_id, epoch, elapsed=base_elapsed + time.monotonic() - started)
    finally:
        stop.set()
        signal.signal(signal.SIGTERM, previous_handler)
    return 0 if store.get(job_id)["state"] in {"succeeded", "awaiting_decision"} else 1


def _settle_failure(ctx: Context, node: dict, settlement: dict) -> None:
    fields(settlement, {"costs", "reason", "evidence"}, {"action_costs"})
    text(settlement["reason"], "settlement reason")
    amounts(settlement["costs"])
    if set(settlement["costs"]) != set(node["costs"]):
        raise ValueError("settlement must account for every reserved resource")
    evidence_path = (ctx.root / settlement["evidence"]).resolve()
    if not evidence_path.is_relative_to(ctx.root.resolve()) or not evidence_path.is_file():
        raise ValueError("failure settlement needs an existing evidence file inside this step's workspace")
    evidence_sha = file_hash(evidence_path)
    frozen = ctx.receipts().get("failure_settlement")
    if frozen and (canonical(frozen["settlement"]) != canonical(settlement) or frozen["evidence_sha256"] != evidence_sha):
        raise StateError("failure settlement or its evidence changed after the settlement was recorded")
    prepared = {}
    if node["definition"]["operation"] == "active.run":
        from etalon.active.schema import Evaluation

        store = CampaignStore(absolute(node["parameters"]["database"]))
        start = ctx.receipts().get("active_start")
        if start is None:
            raise StateError("missing active ownership receipt; cannot settle unknown campaign actions")
        if frozen:
            prepared = {key: Evaluation.from_dict(value) for key, value in frozen["actions"].items()}
        else:
            pending = [a for a in store.actions() if a["id"] not in start["action_ids"] and a["status"] in {"running", "reserved"}]
            costs = settlement.get("action_costs", {})
            amounts(costs)
            if set(costs) != {a["id"] for a in pending}:
                raise ValueError("provide the documented cost for every unresolved action, without inventing a scientific value")
            endpoints = store.configuration()[1]
            actual = operations.active_result(store, start)
            unit = "campaign:" + actual["cost_unit"]
            if settlement["costs"].get(unit) != actual["cost"] + sum(costs.values()):
                raise ValueError("workflow settlement must equal existing and newly reconciled campaign charges")
            for action in pending:
                prepared[action["id"]] = Evaluation(action["candidate_id"], action["endpoint_id"], None,
                                    endpoints[action["endpoint_id"]].units, costs[action["id"]], status="failed",
                                    provenance={"reason": settlement["reason"], "evidence_sha256": evidence_sha,
                                                "cost_basis": "explicit failure reconciliation; caller assertion"})
        journal = store.actions()
        if [action["id"] for action in journal[:len(start["action_ids"])]] != start["action_ids"]:
            raise StateError("active starting action prefix changed during failure settlement")
        interval = journal[len(start["action_ids"]):]
        batch = {"settlement": settlement, "evidence_sha256": evidence_sha,
                 "actions": {key: value.as_dict() for key, value in prepared.items()},
                 "active_start_sha256": digest(start), "action_ids": [action["id"] for action in interval]}
        if frozen is not None and canonical(frozen) != canonical(batch):
            raise StateError("active action ownership changed after the failure settlement was recorded")
        actions = {action["id"]: action for action in interval}
        observations = {row["action_id"]: row for row in store.observations()}
        receipts = ctx.receipts()
        total = 0.0
        for action_id, action in actions.items():
            if action["status"] in {"running", "reserved"}:
                if action_id not in prepared:
                    raise StateError("failure settlement does not cover every unresolved action")
                total += prepared[action_id].cost
            else:
                recorded = receipts.get("action:" + action_id, receipts.get("settlement:" + action_id, {})).get("result")
                if recorded is None and action_id in prepared:
                    recorded = prepared[action_id].as_dict()
                observation = observations.get(action_id)
                if (recorded is None or observation is None or canonical(recorded) != canonical(observation["result"])
                        or action["cost"] != observation["result"]["cost"]):
                    raise StateError("resolved action costs or results disagree with this runtime's receipts")
                total += action["cost"]
        if settlement["costs"].get("campaign:" + store.configuration()[0].cost_unit) != total:
            raise StateError("failure settlement no longer equals the authoritative campaign charges")
        # Freeze the entire validated batch before the first scientific journal
        # commit, including ownership. Recovery cannot absorb unrelated actions.
        ctx.receipt("failure_settlement", batch)
        for action_id, result in prepared.items():
            action = actions.get(action_id)
            if action is None or action_id in start["action_ids"] or (result.candidate_id, result.endpoint_id) != (action["candidate_id"], action["endpoint_id"]):
                raise StateError("failure settlement does not belong to this active execution")
            ctx.receipt("settlement:" + action_id, {"result": result.as_dict()})
            if action["status"] in {"running", "reserved"}:
                store.resolve(action_id, result)
            elif action_id not in observations or canonical(observations[action_id]["result"]) != canonical(result.as_dict()):
                raise StateError("previously resolved action disagrees with its recorded failure settlement")
        store.recover_idle_rounds(reason=settlement["reason"])
    else:
        ctx.receipt("failure_settlement", {"settlement": settlement, "evidence_sha256": evidence_sha, "actions": {}})
    final_lock = store.connection(write=True) if node["definition"]["operation"] == "active.run" else nullcontext()
    with final_lock as db:
        if db is not None:
            # Other callers need not honor the runtime's file lock. Hold the
            # authoritative journal's SQLite writer lock through publication so
            # no action can appear between the final cost check and settlement.
            snapshot = store._snapshot(db)
            interval = operations._active_interval(snapshot, start)
            if [action["id"] for action in interval] != batch["action_ids"]:
                raise StateError("active action ownership changed while the failure batch was being reconciled")
            owned = {action["id"] for action in interval}
            outcomes = operations._action_receipt_results(ctx.receipts(), snapshot, owned)
            if set(outcomes) != owned or any(action["status"] in {"running", "reserved"} for action in interval):
                raise StateError("failure settlement still has unowned or unresolved actions")
            unit = "campaign:" + snapshot["spec"].cost_unit
            if settlement["costs"].get(unit) != sum(action["cost"] for action in interval):
                raise StateError("failure settlement no longer equals the final authoritative campaign charges")
        if file_hash(evidence_path) != evidence_sha:
            raise StateError("failure evidence changed before settlement publication")
        ctx.store.finish_node(ctx.job, ctx.epoch, ctx.node, None,
                {"failure_evidence_sha256": evidence_sha, "reason": settlement["reason"]},
                settlement["costs"], basis=dict.fromkeys(settlement["costs"], "explicit failure cost reconciliation; caller assertion"),
                state="failed", error=settlement["reason"])


def reconcile(workspace: str | Path, job_id: str, *, resume: bool = False, reason: str,
              settlements: dict | None = None) -> dict:
    text(reason, "reconciliation reason")
    if type(resume) is not bool or not isinstance(settlements or {}, dict):
        raise ValueError("resume is boolean and settlements is a node-id mapping")
    store = RuntimeStore(workspace)
    with process.job_lock(store.job_root(job_id)):
        job = store.get(job_id)
        if set(settlements or {}) - {n["id"] for n in job["nodes"] if n["state"] in {"running", "reconciliation_required"}}:
            raise ValueError("failure settlements may only name unresolved nodes in this workflow")
        if process.alive(job["process_identity"]) or (job["process_identity"] and process.group_alive(job["process_identity"])):
            raise StateError("previous worker or its process group is still alive")
        for node in job["nodes"]:
            for key, record in store.receipts(job_id, node["id"]).items():
                if key.startswith("process:") and process.group_alive(record):
                    raise StateError("a recorded scientific child session is still alive; cannot reconcile or resume")
        with store.connection(write=True) as db:
            store.owned(db, job_id, job["epoch"])
            epoch = job["epoch"] + 1
            db.execute("UPDATE jobs SET epoch=?,pid=NULL,process_identity=NULL,updated=? WHERE id=?", (epoch, time.time(), job_id))
            store.event(db, job_id, "reconcile", {"reason": reason, "epoch": epoch})
        for node in job["nodes"]:
            if node["state"] not in {"running", "reconciliation_required"}:
                continue
            ctx = Context(store, job_id, epoch, node["id"])
            ctx.preflight = ctx.receipts().get("preflight", {})
            with _resource_lock(node["parameters"]):
                try:
                    frozen = ctx.receipts().get("failure_settlement")
                    if frozen is not None:
                        _settle_failure(ctx, node, (settlements or {}).get(node["id"], frozen["settlement"]))
                        continue
                    result = operations.recover(node["definition"]["operation"], node["parameters"], ctx)
                    if result is not None:
                        operations.check_inputs(ctx.preflight.get("inputs", {}))
                        _complete(ctx, node, node["parameters"], result, recovered=True)
                    elif node["id"] in (settlements or {}):
                        _settle_failure(ctx, node, settlements[node["id"]])
                    else:
                        store.unresolved(job_id, epoch, node["id"], "no complete result receipt; reservations retained")
                except Exception as error:
                    store.unresolved(job_id, epoch, node["id"], f"reconciliation failed: {error}")
        current = store.get(job_id)
        if any(n["state"] in {"running", "reconciliation_required"} for n in current["nodes"]):
            state = "reconciliation_required"
        elif any(n["state"] == "failed" for n in current["nodes"]):
            state = "failed"
        elif _over_budget(current):
            state = "blocked"
        elif all(n["state"] == "verified" for n in current["nodes"]):
            _verify_dependencies(current, [n["id"] for n in current["nodes"]])
            state = "succeeded"
        elif current["cancel_requested"]:
            state = "cancelled"
        else:
            state = "queued" if resume else "paused"
        store.set_state(job_id, epoch, state)
    if state == "queued":
        _launch(store, job_id)
    return status(workspace, job_id)


def read_artifact(workspace: str | Path, job_id: str, node_id: str, **kwargs) -> dict:
    from etalon.runtime.artifacts import read_result

    job = RuntimeStore(workspace).get(job_id)
    node = next((n for n in job["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise KeyError(node_id)
    result = node["result"]
    if result is None:
        result = RuntimeStore(workspace).receipts(job_id, node_id).get("completed", {}).get("result")
    if result is None:
        raise StateError("step has no saved result; inspect its retained log and reconciliation status")
    return read_result(result, **kwargs)
