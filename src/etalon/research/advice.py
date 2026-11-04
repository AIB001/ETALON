"""Host-owned side advice over ordinary notes; no agent runner or scheduler.

One current ticket per topic lives in the shared note. Drafts and one final
report live in a private entry. The native host launches/resumes its subagent;
these helpers make that handoff, sparse return and decision recoverable.
"""
from __future__ import annotations

import json
from pathlib import Path

from etalon.active.schema import canonical, digest
from .notes import entry_evidence_paths, entry_journal, entry_value, fork_memory, original, patch_progress, read_entry

ACTIVE = {"pending", "reported", "try"}
OUTCOMES = {"try", "adopt", "retain", "defer", "reject", "no_change", "rollback"}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be nonempty text")
    return value


def _compact(value):
    if not isinstance(value, dict) or len(canonical(value).encode("utf-8")) > 8192:
        raise ValueError("advice payload must be an object within 8 KiB; keep detail in referenced originals")
    return json.loads(canonical(value))


def _path(entry):
    path = Path(entry).absolute()
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(value, dict) and set(value) == {"entry"}:
        target = Path(value["entry"])
        path = target if target.is_absolute() else path.parent / target
    return path


def _read(entry):
    return read_entry(entry, verify_originals=False, expand_resources=False)


def _published(entry, prefix):
    value = entry_value(entry)
    journal = entry_journal(value)
    with journal.store.connection() as db:
        rows = db.execute("SELECT key FROM metadata WHERE json_extract(body,'$.bindings.publication_id') LIKE ?",
                          (prefix + "-%",)).fetchall()
    wanted = {row[0].removeprefix("research:") for row in rows}
    # A journal write alone may have failed before the entry advanced. Only a
    # ticket in the visible branch ancestry establishes a retired old review.
    pending, seen = [value["record_id"]], set()
    while wanted and pending:
        rid = pending.pop()
        if rid in wanted:
            return True
        if rid not in seen:
            seen.add(rid)
            pending.extend(journal.get(rid).get("parents", ()))
    return False


def _write(entry, current, topic, slot, **kwargs):
    # An independent result cannot erase progress made after this read.
    return patch_progress(entry, {"advisories": {**current["note"].get("advisories", {}), topic: slot}},
        branch_id=current["branch_id"], expected_record=current["record_id"], **kwargs)


def _reply(entry, topic, slot, **extra):
    return {"entry": str(entry), "topic": topic, "review_key": slot["review_key"],
        "state": slot["state"], "advisor_entry": slot["advisor_entry"],
        "launch_required": False, "execution_performed": False,
        "needs_decision": slot["state"] == "reported",
        **({"handle": slot["handle"]} if "handle" in slot else {}),
        **({"decision": slot["decision"]} if "decision" in slot else {}), **extra}


def _feedback(entry, topic, slot):
    """Return the main decision to private memory without waking another model.

    Replay repairs an interrupted return. Copy only the released judgment, not
    the main transcript/knowledge; explicit evidence pointers remain on it.
    """
    decision = slot["decision"]
    feedback = {
        "shared_entry": str(entry), "topic": topic, "review_key": slot["review_key"],
        "decision": decision}
    current = _read(slot["advisor_entry"])
    if current["note"].get("advice_feedback") == feedback:
        return
    patch_progress(slot["advisor_entry"], {"advice_feedback": feedback,
        "status": "evaluating" if decision["outcome"] == "try" else "closed",
        "next_action": ("Main is evaluating the saved suggestion; do not duplicate its trial."
                        if decision["outcome"] == "try" else
                        "Reuse the main decision in advice_feedback; new evidence may justify a new host-owned review.")},
        expected_record=current["record_id"],
        publication_id="advice-feedback-" + digest([topic, slot["review_key"], decision["decision_id"], current["record_id"]]))


def _prepare(entry, current, topic, key, payload):
    previous = current["note"].get("advisories", {}).get(topic)
    if previous and previous["review_key"] == key:
        return _reply(entry, topic, previous, reused=True,
            next_action=("Recover the saved handle/report or inspect the native host before any relaunch."
                         if previous["state"] in ACTIVE else
                         "Review already disposed; reuse its decision and continue useful main research."))
    if previous and previous["state"] in ACTIVE:
        return _reply(entry, topic, previous, deferred_review_key=key,
            next_action="Finish or explicitly defer the existing review; main research can continue.")
    question = _text(payload.get("question"), "question")
    scope = payload.get("execution_scope")
    if not isinstance(scope, dict) or not scope:
        raise ValueError("execution_scope must declare delegated work/paths; main authority is not inherited")
    identity = digest([current["task_id"], current["branch_id"], topic, key])
    publication_prefix = "advice-ticket-" + identity
    target = entry.parent / "advisors" / identity / "ENTRY.json"
    existed = target.exists()
    fork_memory(entry, target, memory_scope="advisor-" + identity, question=question,
                inherit_execution=False)
    private = _read(target)["note"]
    request = private.get("advice_request")
    if request and previous and _published(entry, publication_prefix):
        # Old keys stay retired after the topic moves on. Draft history remains
        # privately readable; it does not reopen a completed job.
        return {"entry": str(entry), "topic": topic, "review_key": key,
            "state": "archived", "advisor_entry": str(target), "launch_required": False,
            "execution_performed": False, "reused": True}
    if request is None:
        request = {"topic": topic, "review_key": key, "question": question,
            "brief": payload.get("brief", {}), "execution_scope": scope,
            "shared_basis": private["shared_basis"], "shared_entry": str(entry),
            "conditions": current["note"].get("conditions", {})}
        if previous:
            request["previous_feedback"] = previous.get("decision", {"state": previous["state"]})
        patch_progress(target, {"advice_request": request, "summary": question,
            "status": "working", "next_action": "Read the scoped brief; return one useful final report or no_change through research advice report. Main computation belongs to its owner."},
            publication_id="advice-request-" + identity)
    slot = {"review_key": key, "state": "pending", "advisor_entry": str(target),
            "question": question, "basis_record_id": request["shared_basis"]["record_id"]}
    _write(entry, current, topic, slot,
           publication_id=publication_prefix + "-" + current["record_id"])
    return _reply(entry, topic, slot, launch_required=not existed, reused=existed,
        dispatch={"entry": str(target), "question": question, "execution_scope": scope,
            "context": "Fresh native subagent; read this private entry and selected shared originals, not the main transcript.",
            "return": "Save one final report, then send only its entry and short actionable summary. No progress polling messages."},
        next_action="Main host launches its native sub and binds the handle; after uncertain launch inspect native host first.")


def _report(entry, topic, key, payload):
    note = _read(entry)["note"]
    request = note.get("advice_request", {})
    if (note.get("memory_scope", "shared") == "shared"
            or (request.get("topic"), request.get("review_key")) != (topic, key)):
        raise ValueError("report requires the matching private advisory entry")
    if payload.get("outcome") not in {"suggestion", "no_change"}:
        raise ValueError("report outcome must be suggestion or no_change")
    _text(payload.get("summary"), "summary")
    report = dict(payload)
    for role in ("evidence", "counterevidence"):
        paths = payload.get(role, [])
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            raise ValueError(role + " must contain original file paths")
        report[role] = [original(p) for p in paths]
    _compact(report)
    old = note.get("advice_report")
    if old is not None:
        if old != report:
            raise ValueError("final report already saved; changed evidence needs a new review key")
        return {"entry": str(entry), "state": "reported", "report_digest": digest(report),
                "reused": True, "notify_main": False, "execution_performed": False}
    saved = patch_progress(entry, {"advice_report": report, "summary": report["summary"],
        "status": "reported", "next_action": "Main host collects the final report and records its choice; no recurring notification."},
        publication_id="advice-report-" + digest([topic, key]),
        evidence=payload.get("evidence", ()), counterevidence=payload.get("counterevidence", ()))
    return {**saved, "state": "reported", "report_digest": digest(report),
            "notify_main": report["outcome"] != "no_change", "reused": False}


def advice(entry, action, topic, review_key, payload=None):
    """Prepare/bind/report/collect/decide using the existing journal and entry.

    prepare reserves a topic/key; main launches a native sub and binds its handle.
    The sub reports once; main collects and decides. New chats recover the
    ticket instead of regenerating work. No ACL or scientific gain is inferred.
    """
    _text(topic, "topic")
    _text(review_key, "review_key")
    payload = _compact({} if payload is None else payload)
    if action == "report":
        for role in ("evidence", "counterevidence"):
            if role in payload:
                payload[role] = entry_evidence_paths(entry, payload[role])
    entry = _path(entry)
    if action == "report":
        return _report(entry, topic, review_key, payload)
    current = _read(entry)
    if current["note"].get("memory_scope", "shared") != "shared":
        raise ValueError("main-side advice operations require a shared entry")
    if action == "prepare":
        return _prepare(entry, current, topic, review_key, payload)
    slot = current["note"].get("advisories", {}).get(topic)
    if slot is None or slot["review_key"] != review_key:
        raise ValueError("select the current topic/review_key from the shared entry")
    if action == "bind":
        handle = payload.get("handle")
        if not isinstance(handle, dict) or not handle:
            raise ValueError("bind requires the native host's returned handle")
        if "handle" in slot:
            if slot["handle"] != handle:
                if payload.get("replaces") != slot["handle"]:
                    raise ValueError("a native handle is already bound; recover it or explicitly replace the inspected handle")
                _text(payload.get("reason"), "native handle recovery reason")
            else:
                return _reply(entry, topic, slot, reused=True)
        if slot["state"] != "pending":
            raise ValueError("only a pending review needs a native handle")
        slot = {**slot, "handle": handle}
        if "replaces" in payload:
            slot["handle_recovery"] = {"previous": payload["replaces"], "reason": payload["reason"]}
        _write(entry, current, topic, slot)
        return _reply(entry, topic, slot, reused=False)
    if action == "collect":
        if "report" in slot or slot["state"] not in ACTIVE:
            return _reply(entry, topic, slot, deliver=False,
                          **({"decision": slot["decision"]} if "decision" in slot else {}))
        private = read_entry(slot["advisor_entry"], expand_resources=False)["note"]
        request = private.get("advice_request", {})
        if (request.get("topic"), request.get("review_key"), private.get("task_id")) != (
                topic, review_key, current["task_id"]):
            raise ValueError("advisory entry no longer matches the assigned task")
        report = private.get("advice_report")
        if report is None:
            return _reply(entry, topic, slot, deliver=False,
                          next_action="Continue main work; await native sub completion.")
        slot = {**slot, "state": "reported", "report": report, "report_digest": digest(report)}
        # Import released advice only, never a private record id that would
        # cause main's portable handoff to traverse private draft history.
        _write(entry, current, topic, slot,
               evidence=[v["path"] for v in report["evidence"]],
               counterevidence=[v["path"] for v in report["counterevidence"]])
        return _reply(entry, topic, slot, deliver=True, report=report)
    if action == "decide":
        _text(payload.get("decision_id"), "decision_id")
        _text(payload.get("reason"), "reason")
        outcome = payload.get("outcome")
        if outcome not in OUTCOMES:
            raise ValueError("choose a declared advice disposition")
        previous = slot.get("decision")
        if previous and previous["decision_id"] == payload["decision_id"]:
            if previous != payload:
                raise ValueError("decision_id was reused for different feedback")
            _feedback(entry, topic, slot)
            return _reply(entry, topic, slot, reused=True, decision=previous)
        if slot["state"] not in ACTIVE:
            raise ValueError("review is closed; changed evidence needs a new review key")
        if "report" not in slot and outcome not in {"defer", "reject", "no_change"}:
            raise ValueError("collect the final report before trying or adopting its suggestion")
        slot = {**slot, "state": outcome, "decision": payload}
        _write(entry, current, topic, slot)
        _feedback(entry, topic, slot)
        return _reply(entry, topic, slot, reused=False, decision=payload,
            benefit="host-reported evaluation; no benefit inferred from disposition")
    raise ValueError("action must be prepare, bind, report, collect or decide")
