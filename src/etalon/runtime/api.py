"""Convenience plans that use the same durable workflow service and resource ledger."""

from __future__ import annotations

from pathlib import Path

from etalon.active.schema import canonical, finite
from etalon.active.store import CampaignStore, StateError
from etalon.runtime import operations, service
from etalon.runtime.schema import SCHEMA, absolute, bounded_int, identifier
from etalon.runtime.store import RuntimeStore


def _active_arguments(database: str | Path, max_rounds: int, min_new_admitted: int,
                      max_seconds: float) -> tuple[dict, float]:
    path = absolute(database)
    bounded_int(max_rounds, "max_rounds", 1, 100)
    bounded_int(min_new_admitted, "min_new_admitted", 1, 5000)
    finite(max_seconds, "max_seconds", minimum=1)
    if max_seconds > 30 * 86400:
        raise ValueError("workflow wall-clock limit exceeds 30 days")
    return {"database": str(path), "max_rounds": max_rounds,
            "min_new_admitted": min_new_admitted}, float(max_seconds)


def active_execution_plan(database: str | Path, *, max_rounds: int = 1,
                          min_new_admitted: int = 1, max_seconds: float = 3600) -> dict:
    """Plan registered live execution with an explicit reservation of remaining campaign funds.

    The plan does not choose molecules or execute experiments. The existing active
    policy chooses from the journal at dispatch time, under its existing protocol gates.
    """
    arguments, max_seconds = _active_arguments(database, max_rounds, min_new_admitted, max_seconds)
    store = CampaignStore(absolute(arguments["database"]), read_only=True)
    state = store.status()
    unit = "campaign:" + state["spec"]["cost_unit"]
    resources = {unit: max(0.0, state["balance"]["remaining"])}
    operations.preflight("active.run", arguments, resources)
    spec = {"schema": SCHEMA,
            "objective": f"Run the registered active campaign for at most {max_rounds} rounds "
                         f"and verify at least {min_new_admitted} new admitted observations.",
            "nodes": [{"id": "active", "operation": "active.run", "arguments": arguments,
                       "resources": resources}],
            "limits": resources, "max_seconds": max_seconds, "controller": {"mode": "ordered"}}
    return {**service.plan(spec), "campaign": {"database": str(store.path),
            "balance": state["balance"], "reservation": resources,
            "selection": "active policy selects from the authoritative journal at dispatch"}}


def active_submit(database: str | Path, workspace: str | Path, *, job_id: str,
                  expected_plan_id: str, max_rounds: int = 1, min_new_admitted: int = 1,
                  max_seconds: float = 3600) -> dict:
    """Submit the reviewed convenience plan, preserving the generic service's idempotency."""
    arguments, max_seconds = _active_arguments(database, max_rounds, min_new_admitted, max_seconds)
    identifier(job_id)
    root = absolute(workspace)
    try:
        existing = RuntimeStore(root).get(job_id)
    except (FileNotFoundError, KeyError):
        existing = None
    if existing is not None:
        spec = existing["plan"]["spec"]
        nodes = spec["nodes"]
        if (existing["plan_id"] != expected_plan_id or len(nodes) != 1
                or nodes[0]["id"] != "active" or nodes[0]["operation"] != "active.run"
                or canonical(nodes[0]["arguments"]) != canonical(arguments)
                or spec["max_seconds"] != max_seconds):
            raise StateError("existing workflow id belongs to a different active execution request")
        # Reuse the recorded reservation after funds were spent, while allowing
        # the generic service to recover a submitter that died before launch.
        return service.submit(spec, root, job_id=job_id, expected_plan_id=expected_plan_id)
    prepared = active_execution_plan(database, max_rounds=max_rounds,
                                     min_new_admitted=min_new_admitted, max_seconds=max_seconds)
    return service.submit(prepared["spec"], root, job_id=job_id, expected_plan_id=expected_plan_id)


def _action_request(request: dict) -> dict:
    import json
    from etalon.runtime.schema import amounts, fields, text
    fields(request, {"schema", "request_id", "operation", "arguments", "limits"},
           {"max_seconds", "method_use_refs", "evidence_refs"})
    if request["schema"] != "etalon-action/1":
        raise ValueError("action schema must be etalon-action/1")
    identifier(request["request_id"])
    operations.validate_arguments(request["operation"], request["arguments"])
    normalized = json.loads(canonical(request))
    normalized["limits"] = amounts(request["limits"])
    normalized["max_seconds"] = request.get("max_seconds", 3600)
    finite(normalized["max_seconds"], "max_seconds", minimum=1)
    if normalized["max_seconds"] > 30 * 86400:
        raise ValueError("action wall-clock limit exceeds 30 days")
    for key in ("method_use_refs", "evidence_refs"):
        refs = request.get(key, [])
        if not isinstance(refs, list):
            raise ValueError(f"{key} must be a list")
        normalized[key] = refs
        for ref in refs:
            if not isinstance(ref, (str, dict)) or not ref:
                raise ValueError("references must be nonempty identifiers or explicit locator mappings")
    return normalized


def _check_method_refs(request: dict) -> None:
    from etalon.research.journal import ResearchJournal
    from etalon.runtime.schema import fields
    for ref in request["method_use_refs"]:
        if isinstance(ref, str):
            database, record_id = request["arguments"].get("database"), ref
        else:
            fields(ref, {"database", "record_id"})
            database, record_id = ref["database"], ref["record_id"]
        ResearchJournal(CampaignStore(absolute(database), read_only=True)).resolve_method_use_refs([record_id])


def prepare_action(request: dict) -> dict:
    """Read-only preparation of a single supported operation; no strategy or skill gate."""
    request = _action_request(request)
    _check_method_refs(request)
    binding = operations.action_binding(request["operation"], request["arguments"])
    preflight = operations.preflight(request["operation"], request["arguments"], request["limits"])
    spec = {"schema": SCHEMA, "objective": "Execute host-selected " + request["operation"],
            "nodes": [{"id": "action", "operation": request["operation"],
                       "arguments": request["arguments"], "resources": request["limits"]}],
            "limits": request["limits"], "max_seconds": request["max_seconds"],
            "controller": {"mode": "ordered"}, "action_request": request, "action_binding": binding}
    planned = service.plan(spec)
    import os
    return {"schema": "etalon-prepared-action/1", **planned, "request": request,
            "binding": binding, "preflight": preflight,
            "quote": preflight.get("quote", request["limits"]),
            "verification": {"validator": "etalon.runtime.operations.verify/1"},
            "capabilities": {"durable_worker": os.name == "posix",
                "unsupported_reason": None if os.name == "posix" else "detached workers require Linux /proc process identities",
                "limits": "dispatch reservation; actual charges may exceed quotes and remain recorded",
                "max_seconds": "worker cancellation request; unresolved child outcomes require reconciliation"},
            "recovery": {"backend": "runtime", "operation": "workflow.reconcile", "automatic_retry": False}}


def _action_handle(job: dict) -> dict:
    request = job["plan"]["spec"]["action_request"]
    return {"schema": "etalon-action-handle/1", "backend": "runtime", "workspace": job["workspace"],
            "request_id": request["request_id"], "job_id": job["id"], "plan_id": job["plan_id"],
            "node_id": "action", "state": job["state"]}


def submit_action(request: dict, *, workspace: str | Path, plan_id: str) -> dict:
    """Reuse the recorded plan before inspecting mutable balances or dispatch prerequisites."""
    request = _action_request(request)
    root = absolute(workspace)
    try:
        store = RuntimeStore(root)
        old = store.get(request["request_id"])
    except (FileNotFoundError, KeyError):
        old = None
    if old is not None:
        previous_request = old["plan"]["spec"].get("action_request")
        semantics = lambda value: {k: v for k, v in (value or {}).items()
                                   if k not in {"request_id", "method_use_refs", "evidence_refs"}}
        if old["plan_id"] != plan_id or canonical(semantics(previous_request)) != canonical(semantics(request)):
            raise StateError("action request conflict: existing request has different frozen semantics")
        if old["state"] == "queued":
            # No re-quote. The worker checks frozen inputs before any dispatch.
            service._launch(store, old["id"])
            old = store.get(old["id"])
        return _action_handle(old)
    prepared = prepare_action(request)
    if prepared["plan_id"] != plan_id:
        raise StateError("action plan stale: relevant inputs or execution configuration changed")
    service.submit(prepared["spec"], root, job_id=request["request_id"], expected_plan_id=plan_id)
    return _action_handle(RuntimeStore(root).get(request["request_id"]))


def read_action_result(handle: dict) -> dict:
    """Compatibility view over existing receipts and authoritative scientific charges."""
    import json
    from etalon.active.schema import digest
    from etalon.runtime.schema import fields
    fields(handle, {"schema", "backend", "workspace", "request_id", "job_id", "plan_id", "node_id"}, {"state"})
    if handle["schema"] != "etalon-action-handle/1" or handle["backend"] != "runtime" or handle["node_id"] != "action":
        raise ValueError("unsupported action handle")
    runtime = RuntimeStore(absolute(handle["workspace"]))
    job = runtime.get(handle["job_id"])
    request = job["plan"]["spec"].get("action_request")
    if request is None or handle["request_id"] != request["request_id"] or handle["plan_id"] != job["plan_id"]:
        raise StateError("handle does not identify the recorded action")
    node = job["nodes"][0]
    receipts = runtime.receipts(job["id"], node["id"])
    result = node["result"] or receipts.get("completed", {}).get("result")
    native = {"job": job["state"], "node": node["state"], "actions": [],
              "result": result.get("status", result.get("run", {}).get("status") if isinstance(result.get("run"), dict) else None) if result else None}
    execution = {"queued": "pending", "starting": "pending", "running": "running", "succeeded": "completed",
                 "failed": "failed", "blocked": "blocked", "cancelled": "cancelled",
                 "cancel_requested": "running", "reconciliation_required": "unresolved",
                 "awaiting_decision": "pending"}.get(job["state"], "unresolved")
    if "completed" in receipts:
        execution = "completed"
    verification = {"state": "passed" if node["state"] == "verified" else
                    "failed" if node["state"] == "failed" and result is not None else "pending",
                    "validator": "etalon.runtime.operations.verify/1", "evidence": node["evidence"], "reason": node["error"]}
    refs = [{"kind": "runtime", "workspace": str(runtime.workspace), "job_id": job["id"], "node_id": node["id"]}]
    observations, accounting = [], []
    if request["operation"] in {"active.run", "active.evaluate"} and "active_start" in receipts:
        database = request["arguments"]["database"]
        campaign = CampaignStore(database, read_only=True)
        state = campaign.snapshot()
        actions = operations._active_interval(state, receipts["active_start"],
                                               result.get("end_action_ids") if result else None)
        ids = {a["id"] for a in actions}
        unit = state["spec"].cost_unit
        for action in actions:
            ref = {"kind": "campaign", "database": str(campaign.path), "action_id": action["id"]}
            refs.append(ref)
            native["actions"].append({"action_id": action["id"], "status": action["status"]})
            pending = action["status"] in {"reserved", "running"}
            observed = next((o for o in state["observations"] if o["action_id"] == action["id"]), None)
            with campaign.connection() as db:
                execution_costs = [{"metadata_key": row["key"], **json.loads(row["body"])}
                    for row in db.execute("SELECT key,body FROM metadata WHERE key LIKE 'execution-cost:%'")
                    if json.loads(row["body"])["action_id"] == action["id"]]
            accounting.append({**ref, "execution_cost_refs": execution_costs,
                               "unit": unit, "charged": action["cost"],
                               "reserved": action["reserved_cost"] if pending else 0,
                               "basis": observed["result"].get("provenance", {}).get("cost_basis", "executor-declared charge; see original receipt") if observed else "partial charges and unresolved reservation",
                               "coverage": "campaign action only; unmeasured overhead unknown", "overhead": None})
        for observation in state["observations"]:
            if observation["action_id"] in ids:
                observations.append({**observation,
                    "reference": {"database": str(campaign.path), "action_id": observation["action_id"]},
                    "admission": "accepted" if observation["admitted"] else "rejected"})
        if any(a["status"] in {"reserved", "running"} for a in actions):
            execution = "running" if job["state"] == "running" else "unresolved"
        elif observations and all(o["result"]["status"] in {"failed", "blocked"} for o in observations):
            execution = "failed" if any(o["result"]["status"] == "failed" for o in observations) else "blocked"
    accounting.append({"kind": "runtime", "workspace": str(runtime.workspace), "job_id": job["id"],
                       "costs": node["costs"], "derived_from": refs[1:],
                       "additive": not bool(refs[1:]), "coverage": "runtime declared resources; see per-unit basis"})
    return {"schema": "etalon-action-result/1", "request_id": request["request_id"], "plan_id": job["plan_id"],
            "execution_refs": refs, "execution_state": execution, "native_status": native,
            "outputs": [] if result is None else [{"kind": "runtime_result", "sha256": digest(result),
                        "workspace": str(runtime.workspace), "job_id": job["id"], "node_id": node["id"], "result": result}],
            "verification": verification, "observations": observations, "accounting_refs": accounting,
            "recovery": {"backend": "runtime", "workspace": str(runtime.workspace), "job_id": job["id"],
                         "operation": "workflow.reconcile", "automatic_retry": False,
                         "required": execution == "unresolved"},
            "method_use_refs": request["method_use_refs"]}


def read_events(log_ref: dict, *, after_sequence: int | dict = 0, limit: int = 100) -> dict:
    """Read committed event pages. Sequence numbers have meaning only within this log scope."""
    import json
    from etalon.active.schema import digest
    from etalon.runtime.schema import fields
    if not isinstance(log_ref, dict):
        raise ValueError("log_ref must name an existing runtime or campaign log")
    if log_ref.get("kind") == "runtime":
        fields(log_ref, {"kind", "workspace"}, {"job_id"})
        store = RuntimeStore(absolute(log_ref["workspace"]))
        scope = {"kind": "runtime", "workspace": str(store.workspace)}
        if "job_id" in log_ref:
            scope["job_id"] = identifier(log_ref["job_id"])
    elif log_ref.get("kind") == "campaign":
        fields(log_ref, {"kind", "database"})
        store = CampaignStore(absolute(log_ref["database"]), read_only=True)
        scope = {"kind": "campaign", "database": str(store.path)}
    else:
        raise ValueError("unknown event log kind")
    bounded_int(limit, "limit", 1, 1000)
    with store.connection() as db:
        stat = store.path.stat()
        log_id = digest({"scope": scope, "file_identity": stat.st_ino,
                         "created": getattr(stat, "st_birthtime_ns", None)})
        if isinstance(after_sequence, dict):
            fields(after_sequence, {"log_id", "after_sequence"})
            if after_sequence["log_id"] != log_id:
                raise StateError("event cursor belongs to another log")
            after_sequence = after_sequence["after_sequence"]
        bounded_int(after_sequence, "after_sequence", 0, 2**63 - 1)
        sql, args = "SELECT * FROM events WHERE sequence > ?", [after_sequence]
        if scope.get("job_id"):
            sql += " AND job = ?"
            args.append(scope["job_id"])
        rows = db.execute(sql + " ORDER BY sequence LIMIT ?", (*args, limit + 1)).fetchall()
    events = [{**dict(row), "body": json.loads(row["body"]),
               "event_ref": {"log_id": log_id, "sequence": row["sequence"], "event_type": row["kind"]}}
              for row in rows[:limit]]
    return {"events": events, "has_more": len(rows) > limit, "log_id": log_id, "log_ref": scope,
            "next_cursor": {"log_id": log_id, "after_sequence": events[-1]["sequence"] if events else after_sequence}}
