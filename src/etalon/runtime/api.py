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
