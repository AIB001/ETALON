"""PRISM v1 executor for ActiveCampaign, with durable submission and reconciliation."""
from __future__ import annotations

import json
import time
from pathlib import Path

from etalon.active.runner import ExecutionPending
from etalon.active.schema import Action, Candidate, Endpoint, Evaluation, canonical, digest
from etalon.active.store import CampaignStore, StateError
from etalon.authority.grant import SpendAuthorization, require
from etalon.boundary.prism_v1 import (
    CLITransport, ContractError, ENDPOINT, RemoteError, SCHEMA, TERMINAL, TransportError,
    execution_cost, execution_records, protocol_sha256, validate_identity, validate_request, validate_result,
)


class PrismV1Executor:
    """Synchronous wait over a detached producer. A disconnect never resolves an action.

    Configuration and request receipts are immutable journal metadata. A restarted driver
    may reconcile the same action; it must not invent a replacement action for an unknown job.
    """

    def __init__(self, store: CampaignStore, transport: CLITransport, *, request_template: dict,
                 job_root: str, receptor_path: str | Path, seeds: list[int],
                 poll_seconds: float = 2, wait_seconds: float = 3600):
        self.store, self.transport = store, transport
        self.template = json.loads(canonical(request_template))
        self.job_root, self.receptor_path = job_root, Path(receptor_path)
        self.seeds = seeds
        self.poll_seconds, self.wait_seconds = poll_seconds, wait_seconds
        if len(seeds) != len(set(seeds)) or not seeds or any(type(s) is not int for s in seeds):
            raise ValueError("independent repetitions need distinct integer seeds")
        if poll_seconds <= 0 or wait_seconds <= 0:
            raise ValueError("poll and wait durations must be positive")
        validate_request(self.template)
        spec, _ = store.configuration()
        if spec.cost_unit != "wall_second":
            raise ValueError("PRISM v1 pricing is one wall_second per measured wall second")
        binding = {"template": self.template, "job_root": job_root, "seeds": seeds,
                   "transport": {"python": transport.python, "cwd": transport.cwd,
                                 "host": transport.host, "request_root": transport.request_root},
                   "receptor_path": str(self.receptor_path.resolve())}
        self._immutable("prism-v1:configuration", binding)

    def _immutable(self, key: str, value: dict) -> dict:
        with self.store.connection(write=True) as db:
            old = db.execute("SELECT body FROM metadata WHERE key=?", (key,)).fetchone()
            if old:
                if old[0] != canonical(value):
                    raise StateError(f"immutable PRISM receipt changed: {key}")
                return json.loads(old[0])
            db.execute("INSERT INTO metadata VALUES (?,?)", (key, canonical(value)))
            self.store._event(db, "prism_receipt", {"key": key, "value": value})
        return value

    def request_for(self, action: Action, candidate: Candidate, endpoint: Endpoint) -> dict:
        if action.replicate >= len(self.seeds):
            raise ValueError("no authorized seed for this action; next-round plan is not dispatch permission")
        if not endpoint.requires_handoff:
            raise ValueError("PRISM simulation requires SpendAuthorization and a handoff")
        if (action.candidate_id != candidate.id or action.endpoint_id != endpoint.id
                or endpoint.protocol != self.template["protocol_id"]
                or candidate.id != self.template["candidate_id"] or endpoint.id != self.template["endpoint_id"]
                or endpoint.quantity != ENDPOINT["name"] or endpoint.units != ENDPOINT["units"]):
            raise ContractError("action/candidate/endpoint binding mismatch")
        row = candidate.handoff
        if (row.get("chemical_state_id") != self.template["chemical_state_id"]
                or row.get("pose_id") != self.template["pose_id"]
                or row.get("ligand_sha256") != self.template["inputs"]["ligand"]["sha256"]):
            raise ContractError("handoff does not bind requested chemical state, pose and ligand bytes")
        request = {**self.template, "request_id": "etalon-" + digest([self.template["campaign_id"], action.id]),
                   "action_id": action.id, "replicate": action.replicate, "seed": self.seeds[action.replicate]}
        validate_request(request)
        return self._immutable("prism-v1:request:" + action.id, request)

    def __call__(self, action: Action, candidate: Candidate, endpoint: Endpoint,
                 grant: SpendAuthorization | None) -> Evaluation:
        require(candidate.handoff, {candidate.id: grant} if grant else {}, receptor_path=self.receptor_path)
        request = self.request_for(action, candidate, endpoint)
        # Checking both sides binds local authorization to the actual target-host input.
        if grant.receptor_sha256 != request["inputs"]["protein"]["sha256"]:
            raise ContractError("authorized receptor differs from request")
        try:
            for artifact in request["inputs"].values():
                self.transport.verify(artifact)
            path = self.transport.stage_request(request)
            submitted = self.transport.call("submit", request=path, root=self.job_root)
            validate_identity(request, submitted)
            job = submitted.get("job_directory")
            if not isinstance(job, str) or not job:
                raise ContractError("submit must return job_directory")
            self._immutable("prism-v1:job:" + action.id, {"job_id": submitted["job_id"], "job_directory": job})
            return self.wait_result(action, request, job)
        except ExecutionPending:
            raise
        except Exception as error:
            raise ExecutionPending(f"PRISM action {action.id} needs reconciliation: {error}") from error

    def wait_result(self, action: Action, request: dict, job: str) -> Evaluation:
        deadline = time.monotonic() + self.wait_seconds
        while True:
            state = self.transport.call("status", job=job)
            validate_identity(request, state)
            if state["status"] in TERMINAL:
                result = self.transport.call("result", job=job)
                return self.evaluation(action, request, result)
            if state["status"] == "needs_resume":
                # Only immutable, completed execution records may be costed before terminal.
                if state.get("cost", {}).get("executions"):
                    execution_cost(state)
                    self.store.record_execution_costs(action.id, execution_records(state))
                raise ExecutionPending(f"PRISM action {action.id} needs explicit resume: {job}")
            if time.monotonic() >= deadline:
                raise ExecutionPending(f"PRISM action {action.id} remains {state['status']}: {job}")
            time.sleep(min(self.poll_seconds, max(0, deadline - time.monotonic())))

    def evaluation(self, action: Action, request: dict, result: dict) -> Evaluation:
        validate_identity(request, result)
        with self.store.connection() as db:
            receipt = db.execute("SELECT body FROM metadata WHERE key=?",
                                 ("prism-v1:job:" + action.id,)).fetchone()
        if receipt is None or any(result.get(key) != value for key, value in json.loads(receipt[0]).items()):
            raise ContractError("result job identity differs from persisted submit receipt")
        cost = execution_cost(result)
        self.store.record_execution_costs(action.id, execution_records(result))
        validate_result(request, result, self.transport)
        self._immutable("prism-v1:result:" + action.id, result)
        obs = result.get("observation") or {}
        return Evaluation(action.candidate_id, action.endpoint_id, obs.get("value"), "count", cost,
                          uncertainty=obs.get("uncertainty"),
                          status="ok" if result["status"] == "succeeded" else "failed",
                          provenance={"bridge": SCHEMA, "request": request, "result": result,
                                      "endpoint_protocol": request["protocol_id"],
                                      "protocol_sha256": protocol_sha256(request),
                                      "cost_unit": "wall_second", "cost_basis": "sum measured execution wall_seconds",
                                      "scope": "engineering_only"})

    def resume(self, action: Action, candidate: Candidate, endpoint: Endpoint,
               grant: SpendAuthorization) -> dict:
        """Explicit recovery, same action/protocol, with fresh spend authorization."""
        require(candidate.handoff, {candidate.id: grant}, receptor_path=self.receptor_path)
        request = self.request_for(action, candidate, endpoint)
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM metadata WHERE key=?", ("prism-v1:job:" + action.id,)).fetchone()
        if row is None:
            raise StateError("cannot resume without a persisted job receipt")
        job = json.loads(row[0])["job_directory"]
        state = self.transport.call("status", job=job)
        validate_identity(request, state)
        if state["status"] != "needs_resume":
            raise StateError("resume requires needs_resume, never an independent repetition")
        if self.store.balance()["remaining"] < 0:
            raise StateError("measured spending exceeds budget; recovery not authorized")
        response = self.transport.call("resume", job=job)
        validate_identity(request, response)
        return response

    def read_existing(self, action: Action) -> Evaluation | None:
        """Read a previously acknowledged job only. Never submit, resume or create requests."""
        with self.store.connection() as db:
            request_row = db.execute("SELECT body FROM metadata WHERE key=?",
                                     ("prism-v1:request:" + action.id,)).fetchone()
            job_row = db.execute("SELECT body FROM metadata WHERE key=?",
                                 ("prism-v1:job:" + action.id,)).fetchone()
        if request_row is None or job_row is None:
            return None  # Unknown acknowledgement retains the reservation, not a fresh submit.
        request, job = json.loads(request_row[0]), json.loads(job_row[0])
        state = self.transport.call("status", job=job["job_directory"])
        validate_identity(request, state)
        if any(state.get(k) != v for k, v in job.items()):
            raise ContractError("status job differs from persisted receipt")
        if state["status"] not in TERMINAL:
            if state["status"] == "needs_resume" and all(
                    r.get("finished") is not None for r in state.get("cost", {}).get("executions", [])):
                execution_cost(state)
                self.store.record_execution_costs(action.id, execution_records(state))
            return None
        return self.evaluation(action, request, self.transport.call("result", job=job["job_directory"]))
