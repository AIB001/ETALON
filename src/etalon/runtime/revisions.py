"""Optional side-channel edits to undispatched work, consumed between nodes.

No strategy model, scheduler or second journal. Existing workers continue when
there is no edit; the caller supplies both the new method and its rationale.
"""
from __future__ import annotations

import json
import time

from etalon.active.schema import canonical
from etalon.active.store import StateError
from etalon.runtime.schema import fields, identifier, text
from etalon.runtime.store import RuntimeStore


def _changed(job, proposal):
    by_id = {n["id"]: n for n in job["nodes"]}
    changes = proposal["nodes"]
    removed = proposal.get("remove_nodes", [])
    if (not isinstance(changes, list) or not isinstance(removed, list)
            or not changes and not removed):
        raise ValueError("a revision needs node changes or removals")
    ids = [identifier(n["id"]) for n in changes]
    removed = [identifier(n) for n in removed]
    if len(set(ids)) != len(ids) or len(set(removed)) != len(removed) or set(ids) & set(removed):
        raise ValueError("revision node identities must be distinct")
    if set(removed) - by_id.keys():
        raise ValueError("cannot remove an unknown node")
    for key in set(ids) | set(removed):
        if key in by_id and by_id[key]["state"] != "waiting":
            raise StateError(f"node {key} already started; keep its method and result")
    return set(ids), set(removed)


def queue(workspace, job_id, proposal):
    from etalon.runtime import operations, service

    fields(proposal, {"kind", "revision_id", "expected_plan_id", "nodes", "reason"}, {"remove_nodes"})
    identifier(proposal["revision_id"])
    text(proposal["reason"], "revision reason")
    store = RuntimeStore(workspace)
    job = store.get(job_id)
    # The same revision has one queue event, even if the client lost its reply.
    with store.connection() as db:
        old = [json.loads(r[0]) for r in db.execute(
            "SELECT body FROM events WHERE job=? AND kind='revision_queued'", (job_id,))]
    previous = next((v for v in old if v["proposal"]["revision_id"] == proposal["revision_id"]), None)
    if previous is not None:
        if canonical(previous["proposal"]) != canonical(proposal):
            raise StateError("revision id already has a different proposal")
        return service.status(workspace, job_id)
    if (job["plan"]["spec"]["controller"]["mode"] != "ordered"
            or "action_request" in job["plan"]["spec"]):
        raise StateError("side-channel revisions require an ordered workflow; single actions stay immutable")
    if job["state"] not in {"queued", "starting", "running", "paused", "reconciliation_required"}:
        raise StateError("workflow cannot accept future work in its current state; submit a successor workflow")
    if proposal["expected_plan_id"] != job["plan"]["plan_id"]:
        raise StateError("revision is based on a different plan")
    changed, removed = _changed(job, proposal)
    edits = {n["id"]: n for n in proposal["nodes"]}
    nodes = [edits.pop(n["id"], n["definition"]) for n in job["nodes"] if n["id"] not in removed]
    nodes.extend(edits.values())
    operations.check_inputs(job["plan"]["inputs"])
    prepared = service.plan({**job["plan"]["spec"], "nodes": nodes})
    pending = {"kind": "revision", "proposal": proposal, "prepared": prepared}
    with store.connection(write=True) as db:
        row = store.owned(db, job_id, job["epoch"])
        current = json.loads(row["plan"])
        if (current["plan_id"] != proposal["expected_plan_id"] or row["proposal"]
                or row["cancel_requested"] or row["state"] not in {
                    "queued", "starting", "running", "paused", "reconciliation_required"}):
            raise StateError("workflow changed or already has a pending proposal")
        for key in changed | removed:
            state = db.execute("SELECT state FROM nodes WHERE job=? AND id=?", (job_id, key)).fetchone()
            if state and state[0] != "waiting":
                raise StateError(f"node {key} started before the revision was queued")
        db.execute("UPDATE jobs SET proposal=?,updated=? WHERE id=?", (canonical(pending), time.time(), job_id))
        store.event(db, job_id, "revision_queued", pending)
    return service.status(workspace, job_id)


def apply_pending(store, job_id, epoch):
    """Called by the owning worker at a node boundary, or during reconciliation."""
    from etalon.runtime import operations

    job = store.get(job_id)
    pending = job["proposal"]
    if not pending or pending.get("kind") != "revision":
        return False
    proposal, prepared = pending["proposal"], pending["prepared"]
    try:
        if proposal["expected_plan_id"] != job["plan"]["plan_id"]:
            raise StateError("revision base changed")
        changed, removed = _changed(job, proposal)
        operations.check_inputs(prepared["inputs"])
    except (ValueError, OSError, StateError) as exc:
        # A late or inapplicable suggestion must not stall the original plan.
        with store.connection(write=True) as db:
            store.owned(db, job_id, epoch)
            db.execute("UPDATE jobs SET proposal=NULL WHERE id=?", (job_id,))
            store.event(db, job_id, "revision_not_applied", {"revision_id": proposal["revision_id"], "reason": str(exc)})
        return False
    prepared = {**prepared, "initial_plan": job["plan"].get("initial_plan", job["plan"])}
    with store.connection(write=True) as db:
        row = store.owned(db, job_id, epoch)
        if row["proposal"] != canonical(pending):
            raise StateError("pending revision changed")
        for key in removed:
            db.execute("DELETE FROM nodes WHERE job=? AND id=? AND state='waiting'", (job_id, key))
        for node in prepared["spec"]["nodes"]:
            if node["id"] in changed:
                db.execute("INSERT INTO nodes(job,id,definition) VALUES (?,?,?) "
                           "ON CONFLICT(job,id) DO UPDATE SET definition=excluded.definition",
                           (job_id, node["id"], canonical(node)))
        db.execute("UPDATE jobs SET plan=?,proposal=NULL,updated=? WHERE id=?",
                   (canonical(prepared), time.time(), job_id))
        store.event(db, job_id, "revision_applied", {"revision_id": proposal["revision_id"],
                    "from_plan_id": proposal["expected_plan_id"], "to_plan_id": prepared["plan_id"],
                    "changed_nodes": sorted(changed), "removed_nodes": sorted(removed)})
    return True
