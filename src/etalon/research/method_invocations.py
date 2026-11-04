"""Optional note convenience over ordinary actions, with replayable bookkeeping.

The runtime remains the only execution store. These small sidecars remember the
selected resource and destination note so a host need not copy handles, outputs
and costs by hand. Collection never starts a process or makes a scientific claim.
"""
from __future__ import annotations

from pathlib import Path

from etalon.active.schema import canonical, digest
from etalon.runtime import api
from etalon.runtime.files import _consumer_lock, _read, _save
from etalon.runtime.schema import absolute, identifier
from etalon.runtime.store import RuntimeStore
from .notes import SCHEMA as NOTE_SCHEMA, entry_journal, entry_value, original, patch_progress, publish_note, read_note

SCHEMA = "etalon-note-method-invocation/1"
FEEDBACK_RESPONSE_BYTES = 16384


def _binding(value, branch_id):
    branch = branch_id or value["branch_id"]
    if branch not in value.get("branches", {value["branch_id"]: value["record_id"]}):
        raise ValueError("method invocation needs an existing note branch")
    return {"task_id": value["task_id"], "branch_id": branch,
            "memory_scope": value.get("memory_scope", "shared"),
            "journal": {k: value[k] for k in ("database", "workspace", "source_id") if k in value}}


def _current(intent):
    value = entry_value(intent["entry"])
    if _binding(value, intent["binding"]["branch_id"]) != intent["binding"]:
        raise ValueError("entry now belongs to a different task or journal; the original invocation remains recoverable")
    head = value.get("branches", {value["branch_id"]: value["record_id"]})[intent["binding"]["branch_id"]]
    note = read_note(entry_journal(value), head, verify_originals=False, expand_resources=False)["note"]
    return head, note


def _recorded_handle(intent):
    try:
        job = RuntimeStore(intent["workspace"]).get(intent["request"]["request_id"])
    except (FileNotFoundError, KeyError):
        return None
    if job["plan_id"] != intent["plan_id"]:
        raise ValueError("request id belongs to a different recorded action")
    return api._action_handle(job)


def _same_action(left, right):
    keys = ("schema", "backend", "workspace", "job_id", "node_id", "request_id", "plan_id")
    return isinstance(left, dict) and all(left.get(k) == right.get(k) for k in keys)


def _published_ancestor(journal, head, candidates):
    pending, seen = [head], set()
    while candidates and pending:
        rid = pending.pop()
        if rid in candidates:
            return rid
        if rid not in seen:
            seen.add(rid)
            record = journal.get(rid)
            if record.get("bindings", {}).get("schema") == NOTE_SCHEMA:
                pending.extend(record.get("parents", ()))
    return None


def _remember(intent, path, handle, result):
    """Merge only this call's facts; never replace host prose or another handle."""
    complete = (result["execution_state"] in {"failed", "blocked", "cancelled"}
                or result["execution_state"] == "completed" and result["verification"]["state"] in {"passed", "failed"})
    complete = complete and not result["recovery"]["required"]
    # Pending polling needs no new note every time. Terminal receipts retain the
    # full result on disk, with a compact named resource in ordinary note storage.
    # Polling diagnostics (heartbeat age, worker exit, review advice) are not a
    # new result. Keep immutable execution/verification/output/cost facts here;
    # the original runtime reference still supplies live controller status.
    facts = {k: v for k, v in result.items() if k not in {"native_status", "rsi_review", "recovery"}}
    receipt = {"schema": "etalon-note-method-receipt/1", "invocation": str(path),
               "resource_version": intent["resource_version"], "handle": handle,
               "result": facts, "scientific_admission": "not_inferred"} if complete else intent
    source = path
    if complete:
        source = path.parent / ("result-" + digest(receipt) + ".json")
        _save(source, receipt)
    descriptor = {"kind": "result", "form": "method-invocation",
        "summary": ("Completed " if complete else "Pending ") + intent["method_summary"],
        "method_pointer": intent["pointer"], "method_resource_version": intent["resource_version"],
        "handle": handle, "source": original(source), "scientific_admission": "not_inferred",
        "collect": {"tool": "etalon_method_collect", "arguments": {"invocation_path": str(path)}}}
    if complete:
        descriptor.update(execution_state=result["execution_state"], verification=result["verification"],
                          accounting_refs=result["accounting_refs"])
    resource_id = "method-call-" + digest({"workspace": intent["workspace"], "request_id": handle["request_id"]})[:24]
    publication = "method-call-" + digest({"intent": digest(intent), "receipt": digest(receipt)})
    published_path = path.parent / (publication + ".published.json")
    if published_path.exists():
        if _binding(entry_value(intent["entry"]), intent["binding"]["branch_id"]) != intent["binding"]:
            raise ValueError("entry now belongs to a different task or journal; the original invocation remains recoverable")
        return {**_read(published_path), "replayed": True}
    # The adapter serializes its calls; publication's CAS also protects ordinary
    # concurrent host progress. A retry re-merges just these disjoint facts.
    lock = Path(intent["entry"]).parent / (Path(intent["entry"]).name + ".method-calls")
    with _consumer_lock(lock):
        for attempt in range(3):
            head, note = _current(intent)
            # Recover a publication committed before its small completion marker
            # was written. Do not rebuild it against later host interpretation.
            journal = entry_journal(entry_value(intent["entry"]))
            with journal.store.connection() as db:
                rows = db.execute("SELECT key FROM metadata WHERE json_extract(body,'$.bindings.schema')=? "
                    "AND json_extract(body,'$.bindings.task_id')=? AND json_extract(body,'$.bindings.branch_id')=? "
                    "AND json_extract(body,'$.bindings.publication_id') LIKE ? "
                    "AND COALESCE(json_extract(body,'$.bindings.memory_scope'),'shared')=?",
                    (NOTE_SCHEMA, intent["binding"]["task_id"], intent["binding"]["branch_id"],
                     publication + "%", intent["binding"]["memory_scope"])).fetchall()
            candidates = {row[0].removeprefix("research:") for row in rows}
            visible = _published_ancestor(journal, head, candidates)
            if visible is not None:
                recorded = {"record_id": visible}
                break
            # An Artifact may have been committed before publish_note updated the
            # entry. Resume that exact record only if its parent is still current.
            resumable = next((rid for rid in candidates if head in journal.get(rid).get("parents", ())), None)
            if resumable is not None:
                try:
                    publish_note(intent["entry"], journal, resumable, activate=False)
                    recorded = {"record_id": resumable}
                    break
                except ValueError as error:
                    if attempt == 2 or not str(error).startswith("entry changed"):
                        raise
                    continue
            # The host may have advanced while an unpublished record was left
            # behind. Merge this call's fields onto that new head under a distinct
            # publication id; the old Artifact stays preserved, never promoted.
            current_publication = publication + "-" + digest(head)[:16] if candidates else publication
            handles = note.get("pending_handles", [])
            if not isinstance(handles, list):
                raise ValueError("note pending_handles must be a list; original handle remains in the invocation")
            handles = [item for item in handles if not _same_action(item, handle)]
            if not complete:
                handles.append(handle)
            try:
                recorded = patch_progress(intent["entry"], {"pending_handles": handles},
                    branch_id=intent["binding"]["branch_id"], expected_record=head,
                    publication_id=current_publication, resources={resource_id: descriptor}, activate=False)
                break
            except ValueError as error:
                if attempt == 2 or not str(error).startswith("entry changed"):
                    raise
        published = {"state": "recorded", "record_id": recorded["record_id"],
                     "resource_pointer": "/resources/" + resource_id,
                     "receipt": original(source), "completed": complete}
        _save(published_path, published)
    return published


def call(entry, pointer, *, invocation, workspace, branch_id=None, query=None):
    """Submit one host-selected method and retain enough context for collection."""
    from .saved_methods import resolve_saved_method
    entry = absolute(entry)
    workspace = absolute(workspace)
    if not isinstance(invocation, dict) or "request_id" not in invocation:
        raise ValueError("invocation requires request_id")
    identifier(invocation["request_id"])
    root = workspace / "method-invocations" / invocation["request_id"]
    root.mkdir(parents=True, exist_ok=True)
    path = root / "invocation.json"
    selection = {"entry": str(entry), "pointer": pointer, "invocation": invocation,
                 "branch_id": branch_id, "query": query}
    submitted = False
    with _consumer_lock(root / "call"):
        if path.exists():
            intent = _read(path)
            if intent.get("schema") != SCHEMA:
                raise ValueError("saved method invocation required")
            if canonical(intent["selection"]) != canonical(selection):
                raise ValueError("request id already belongs to a different method invocation")
        else:
            resolved = resolve_saved_method(entry, pointer, branch_id=branch_id, invocation=invocation, query=query)
            intent = {"schema": SCHEMA, "selection": selection, "entry": str(entry), "workspace": str(workspace),
                "binding": _binding(entry_value(entry), branch_id), "pointer": pointer,
                "resource_version": resolved["resource_version"], "method_summary": resolved["description"]["summary"],
                "request": resolved["request"], "plan_id": resolved["plan_id"]}
            # Freeze the experience actually available when this method was
            # selected. Collection/retries never resolve today's project head.
            intent["method_entry"] = resolved["details"]
            if "related_feedback" in resolved:
                intent["related_feedback"] = resolved["related_feedback"]
            _save(path, intent)
        handle_path = root / "handle.json"
        if not handle_path.exists():
            submitted = True
            try:
                if _binding(entry_value(entry), intent["binding"]["branch_id"]) != intent["binding"]:
                    raise ValueError("entry now belongs to a different task; recover the original invocation without dispatch")
                handle = api.submit_action(intent["request"], workspace=workspace, plan_id=intent["plan_id"])
            except Exception:
                # Submission may have committed before its caller disconnected.
                # Read that action; never invent a new request or assume failure.
                handle = _recorded_handle(intent)
                if handle is None:
                    raise
            _save(handle_path, handle)
    response = collect(path)
    response.pop("execution_performed", None)
    response["submission"] = "submitted" if submitted else "existing"
    return response


def collect(invocation_path):
    """Recover a handle/result and update its original note; execution is read-only."""
    path = absolute(invocation_path)
    with _consumer_lock(path.parent / "call"):
        intent = _read(path)
        if intent.get("schema") != SCHEMA:
            raise ValueError("saved method invocation required")
        handle_path = path.parent / "handle.json"
        handle = _read(handle_path) if handle_path.exists() else _recorded_handle(intent)
        if handle is None:
            return _with_captured_feedback(
                {"invocation_path": str(path), "state": "not_submitted", "execution_performed": False}, intent)
        _save(handle_path, handle)
        result = api.read_action_result(handle)
        try:
            publication = _remember(intent, path, handle, result)
        except Exception as error:
            # Archival trouble must not hide a usable result or cause a retry of
            # completed computation. Collection can safely be repeated later.
            publication = {"state": "pending", "reason": str(error),
                           "retry_tool": "etalon_method_collect"}
        response = {"invocation_path": str(path), "handle": handle,
                    "task_id": intent["binding"]["task_id"], "branch_id": intent["binding"]["branch_id"],
                    "resource_version": intent["resource_version"],
                    "execution_state": result["execution_state"], "verification": result["verification"],
                    "recovery": result["recovery"], "note_publication": publication,
                    "accounting_refs": result["accounting_refs"],
                    "execution_performed": False}
        outputs = result["outputs"]
        # Large arrays should not hide the author's conclusion or actual error.
        # Keep the old small-body/bulk-locator contract alongside this preview.
        response["output_summaries"] = [api._output_summary(output) for output in outputs]
        if len(canonical(outputs).encode()) <= 8192:
            response["outputs"] = outputs
        else:
            response["outputs"] = {"read_tool": "etalon_action_result", "arguments": {"handle": handle}}
        return _with_captured_feedback(response, intent)


def _with_captured_feedback(response, intent):
    """Return captured experience without a journal read or another publication.

    Optional delivery stays inside spare response space. Existing runtime output
    and handle contracts retain priority; old invocations are not backfilled.
    """
    from .notes import byte_size
    from .resource_selection import feedback_read_link

    pin = intent.get("method_entry")
    parts = intent["pointer"].split("/")
    link = None
    if pin is not None and len(parts) == 3 and parts[:2] == ["", "resources"]:
        name = parts[2].replace("~1", "/").replace("~0", "~")
        link = feedback_read_link(pin, name, query=intent["selection"].get("query"),
                                  resource_version=intent["resource_version"], limit=5)
    fallback = ({"status": "not_loaded", "reason": "response_budget", "next_read": link}
                if link else {"status": "not_captured", "reason": "legacy_invocation"})
    for feedback in (intent.get("related_feedback", fallback), fallback):
        enriched = {**response, "related_feedback": feedback}
        if byte_size(enriched) <= FEEDBACK_RESPONSE_BYTES:
            return enriched
    minimal = {**response, "related_feedback_omitted": True}
    return minimal if byte_size(minimal) <= FEEDBACK_RESPONSE_BYTES else response
