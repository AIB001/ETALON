"""Small research notes in the existing journal; execution does not depend on them.

One note holds a scoped judgment and references original outputs. The entry is
only a pointer to its current immutable note. No plan, opportunity, graph,
publication state machine or scientific status is manufactured by this module.
"""
from __future__ import annotations
import json
from contextvars import ContextVar
from pathlib import Path

from etalon.active.store import CampaignStore
from etalon.active.schema import canonical, digest
from etalon.runtime.files import file_identity, _save, _read, _consumer_lock
from .journal import ResearchJournal
from .schema import Artifact
from .selection import MISSING, byte_size, children, pointer as _pointer, resolve

SCHEMA = "etalon-research-note/1"
ENTRY = "etalon-note-entry/1"
SHARED_SCOPE = "shared"
_CONTINUATION = ("question", "summary", "conditions", "status", "state", "outcome",
                 "pending_handles", "completed_refs", "completed", "wait_for", "next_action",
                 "invalid_when", "reconsider_when", "residuals", "feedback", "shared_entry")
_ORIGINAL_CHECKS = ContextVar("etalon_note_publication_originals", default=None)
_CONTROLS = ("state", "status", "authorization", "constraints", "controls", "budget",
             "other_pauses", "pending_handles", "conditions", "completed_refs", "completed", "wait_for")


def _selected(value, pointer):
    """Resolve a literal JSON Pointer, without searching or relevance ranking."""
    result = resolve(value, pointer)
    if result is MISSING:
        raise ValueError("note selector is absent: " + pointer)
    return result


def _children(value, pointer):
    return children(value, pointer, sort_keys=True)


def _descriptor(pointer, value):
    row = {"pointer": pointer, "kind": type(value).__name__}
    if isinstance(value, (list, dict)):
        row["items"] = len(value)
    title = next((value[k] for k in ("title", "name", "key", "question", "rule", "statement", "lesson")
                  if isinstance(value.get(k), str)), None) if isinstance(value, dict) else value
    if isinstance(title, str):
        row.update(preview=title[:120], preview_complete=len(title) <= 120)
    return row


def _read_recipe(view, *, selectors=None, offset=0, limit=20, max_bytes=None):
    return {"view": view, **({"selectors": selectors} if selectors is not None else {}),
            "offset": offset, "limit": limit,
            **({"max_bytes": max_bytes} if max_bytes is not None else {})}


def _omitted(pointer, value, *, max_bytes):
    return {**_descriptor(pointer, value), "body_omitted": True,
            "next_read": _read_recipe("selected", selectors=[pointer], max_bytes=max_bytes)}


def _bounded_controls(response, note, *, max_bytes):
    """Long controls remain exact selectable fields, never implicit permission."""
    response["controls"] = {}
    omitted = []
    for key in _CONTROLS:
        if key not in note:
            continue
        response["controls"][key] = note[key]
        if byte_size(response["controls"]) > max_bytes // 6:
            response["controls"].pop(key)
            omitted.append(_pointer("", key))
    if omitted:
        response["coverage"].update(controls_complete=False, omitted_controls=omitted)
        response["followup"]["controls"] = _read_recipe("selected", selectors=omitted, max_bytes=max_bytes)


def _bounded_metadata(response, note, *, max_bytes):
    definitions = note.get("definitions")
    if definitions is not None and (byte_size(definitions) > max_bytes // 8
            or byte_size(response.get("details", {}).get("definitions", {})) > max_bytes // 8):
        # The immutable record pins all definitions. A compact digest preserves
        # the override restriction without duplicating thousands of bindings.
        response.pop("definitions", None)
        response["details"].pop("definitions", None)
        response["details"]["definitions_digest"] = digest(definitions)
        response["definition_binding"] = {"digest": digest(definitions), "body_omitted": True,
            "next_read": _read_recipe("selected", selectors=["/definitions"], max_bytes=max_bytes)}
    heads = response.get("branches")
    if heads is not None and byte_size(heads) > max_bytes // 8:
        response["branches"] = {response["branch_id"]: response["record_id"]}
        response["branch_navigation"] = {"total_items": len(heads), "body_complete": False,
            "entry": response.pop("_project_entry", "project_entry"),
            "sdk": "etalon.research.resource_catalog.build_resource_catalog",
            "next_read": {"limit": 20, "max_bytes": max_bytes},
            "scope": "current heads; catalog cursor detects head changes; historical notes use find_notes"}


def _selective_note(response, note, *, view, selectors, offset, limit, max_bytes, response_envelope=None):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("note pages require offset >= 0 and limit 1..50")
    if type(max_bytes) is not int or not 4096 <= max_bytes <= 65536:
        raise ValueError("note max_bytes must be 4096..65536")
    response.pop("note")
    response["view"] = view
    response["coverage"] = {"knowledge": "partial", "note_identity": "verified",
        "originals": "verified" if response["originals_verified"] is not None else "not_read",
        "support_count": len(note.get("evidence", [])), "counter_count": len(note.get("counterevidence", [])),
        "scope": "explicit fields/items in this pinned note; no relevance ranking or archive coverage"}
    response["followup"] = {"entry": "details", "sdk": "etalon.research.notes.read_entry",
        "select": _read_recipe("selected", selectors=["/question", "/summary"]),
        "counterevidence": _read_recipe("selected", selectors=["/counterevidence"])
            if "counterevidence" in note else {"status": "not_declared"},
        "full": {"view": "full"}}
    _bounded_metadata(response, note, max_bytes=max_bytes)
    _bounded_controls(response, note, max_bytes=max_bytes)
    fits = lambda: byte_size(response, response_envelope) <= max_bytes - 384
    if view == "index":
        if selectors is not None:
            raise ValueError("selectors require view='selected'")
        # Keep small continuation bodies useful; explicitly label every abbreviated field.
        response["knowledge"], abbreviated = {}, []
        delivered = set(response["controls"])
        for key in _CONTINUATION:
            if key not in note:
                continue
            value = note[key]
            if len(canonical(value).encode("utf-8")) <= 240:
                response["knowledge"][key] = value
                delivered.add(key)
            else:
                response["knowledge"][key] = value[:200] if isinstance(value, str) else {
                    "pointer": _pointer("", key), "total_items": len(value),
                    "preview": [_descriptor(p, v) for p, v in _children(value, _pointer("", key))[:2]]}
                abbreviated.append(_pointer("", key))
        # Leave room for at least one inventory descriptor and its continuation.
        # Removed continuation bodies remain in the ordinary exact inventory.
        while byte_size(response, response_envelope) > max_bytes - 1200 and response["knowledge"]:
            key = max(response["knowledge"], key=lambda k: byte_size(response["knowledge"][k]))
            response["knowledge"].pop(key)
            delivered.discard(key)
            if _pointer("", key) not in abbreviated:
                abbreviated.append(_pointer("", key))
        response["coverage"]["abbreviated_fields"] = abbreviated
        inventory = []
        for key in sorted(note, key=lambda k: (k != "knowledge", k)):
            if key in delivered or key in {"schema", "task_id", "branch_id", "publication_id"}:
                continue
            pointer, value = _pointer("", key), note[key]
            inventory.extend(_children(value, pointer) if key == "knowledge" and isinstance(value, (list, dict))
                             else [(pointer, value)])
        page = {"inventory_policy": "undelivered-fields/1", "offset": offset,
                "total_items": len(inventory), "items": [], "next_offset": offset}
        response["index"] = page
        for pointer, value in inventory[offset:offset + limit]:
            page["items"].append(_descriptor(pointer, value))
            if not fits():
                page["items"].pop()
                break
        end = offset + len(page["items"])
        page["next_offset"] = end if end < len(inventory) else None
        page["omitted_items"] = len(inventory) - len(page["items"])
        if page["items"]:
            # A usable batch example in structural inventory order, not a ranking.
            response["followup"]["select"] = _read_recipe("selected",
                selectors=[item["pointer"] for item in page["items"][:3]])
        if page["next_offset"] is not None:
            response["followup"]["next_page"] = _read_recipe("index", offset=end, limit=limit)
        if not page["items"] and offset < len(inventory):
            raise ValueError("note controls/continuation exceed index budget; increase max_bytes or select explicit fields")
    else:
        if (not isinstance(selectors, list) or not selectors or len(selectors) > 20
                or not all(isinstance(p, str) for p in selectors) or len(set(selectors)) != len(selectors)):
            raise ValueError("selected view requires 1..20 distinct JSON Pointer selectors")
        values = [_selected(note, pointer) for pointer in selectors]
        if offset and any(not isinstance(value, (dict, list)) for value in values):
            raise ValueError("offset applies only to collection selectors")
        response["selected"] = []
        remaining = list(selectors)

        def continuation():
            # Reserve the actual remaining arguments, including long pointers,
            # before filling the page; fixed byte reserves cannot cover a batch.
            response["coverage"].update(requested_selector_count=len(selectors),
                undelivered_selector_count=len(remaining), selected_body_complete=not remaining
                and all(row["body_complete"] for row in response["selected"]))
            if remaining:
                response["followup"]["select"] = _read_recipe("selected", selectors=list(remaining),
                    offset=offset, limit=limit, max_bytes=max_bytes)
            else:
                incomplete = [row["next_read"] for row in response["selected"] if "next_read" in row]
                incomplete += [item["next_read"] for row in response["selected"]
                    for item in row.get("items", []) if isinstance(item, dict) and "next_read" in item]
                response["followup"]["select"] = incomplete[0] if incomplete else _read_recipe(
                    "selected", selectors=["/question", "/summary"], max_bytes=max_bytes)

        def collection_page(row):
            end = offset + len(row["items"])
            row.update(next_offset=end if end < row["total_items"] else None,
                omitted_items=row["total_items"] - len(row["items"]), body_complete=offset == 0
                and end == row["total_items"] and not any(i.get("body_omitted") for i in row["items"]))
            row.pop("next_read", None)
            if row["next_offset"] is not None:
                row["next_read"] = _read_recipe("selected", selectors=[row["pointer"]],
                    offset=end, limit=limit, max_bytes=max_bytes)
            continuation()

        def fits_on_fresh_page(row, item):
            start = offset + len(row["items"]) - 1
            end = start + 1
            alone = {**row, "offset": start, "items": [item],
                "next_offset": end if end < row["total_items"] else None,
                "omitted_items": row["total_items"] - 1,
                "body_complete": start == 0 and end == row["total_items"]}
            alone.pop("next_read", None)
            recipe = _read_recipe("selected", selectors=["/question", "/summary"], max_bytes=max_bytes)
            if alone["next_offset"] is not None:
                recipe = alone["next_read"] = _read_recipe("selected", selectors=[row["pointer"]],
                    offset=end, limit=limit, max_bytes=max_bytes)
            trial = {**response, "selected": [alone],
                "coverage": {**response["coverage"], "requested_selector_count": 1,
                    "undelivered_selector_count": 0, "selected_body_complete": alone["body_complete"]},
                "followup": {**response["followup"], "select": recipe}}
            return byte_size(trial, response_envelope) <= max_bytes - 16

        fits = lambda: byte_size(response, response_envelope) <= max_bytes - 16
        continuation()
        if not fits():
            raise ValueError("selected selector parameters exceed budget; request fewer or shorter selectors")
        for pointer, value in zip(selectors, values):
            remaining.pop(0)
            row = {"pointer": pointer}
            response["selected"].append(row)
            if isinstance(value, (dict, list)):
                children = _children(value, pointer)
                row.update(kind=type(value).__name__, offset=offset, total_items=len(children), items=[])
                collection_page(row)
                for child, body in children[offset:offset + limit]:
                    row["items"].append({"pointer": child, "value": body})
                    collection_page(row)
                    if not fits() and not fits_on_fresh_page(row, row["items"][-1]):
                        row["items"][-1] = _omitted(child, body, max_bytes=max_bytes)
                        collection_page(row)
                    if not fits():
                        row["items"].pop()
                        collection_page(row)
                        break
            else:
                row.update(value=value, body_complete=True)
                continuation()
                if not fits() and len(selectors) > 1:
                    row.clear()
                    row.update(_omitted(pointer, value, max_bytes=max_bytes), body_complete=False)
                    continuation()
            if not fits():
                response["selected"].pop()
                remaining.insert(0, pointer)
                continuation()
                break
        if not response["selected"]:
            raise ValueError("selected value or selector parameters exceed budget; select a nested field or narrower batch")
    if byte_size(response, response_envelope) > max_bytes:
        raise ValueError("selected note exceeds budget; request fewer selectors, a nested field, or increase max_bytes")
    return response


def _brief_note(response, note, *, max_bytes, response_envelope=None):
    """Deliver declared facts with exact omissions; never choose scientific relevance."""
    if type(max_bytes) is not int or not 4096 <= max_bytes <= 65536:
        raise ValueError("note max_bytes must be 4096..65536")
    response.pop("note")
    response.update(view="brief", knowledge={})
    keys = ("question", "summary", "conditions", "status", "state", "outcome", "limits",
        "invalid_when", "reconsider_when", "residuals", "lesson", "knowledge", "decision",
        "next_action", "completed_refs", "completed", "wait_for", "capability_id", "method_choices", "result_read", "scientific_result", "feedback", "shared_entry")
    pending = [k for k in keys if k in note]
    response["coverage"] = {"knowledge": "partial", "note_identity": "verified",
        "originals": "verified" if response["originals_verified"] is not None else "not_read",
        "support_count": len(note.get("evidence", [])), "counter_count": len(note.get("counterevidence", [])),
        "source_bodies": "not_delivered", "selection": "declared_fields_only; no relevance ranking"}
    response["references"] = {}
    response["followup"] = {"entry": "details", "sdk": "etalon.research.notes.read_entry"}
    _bounded_metadata(response, note, max_bytes=max_bytes)
    _bounded_controls(response, note, max_bytes=max_bytes)

    if isinstance(note.get("resources"), dict):
        response["resource_navigation"] = {"declared_in_this_note": len(note["resources"]),
            "sdk": "etalon.research.resource_catalog.build_resource_catalog",
            "mcp_view": "resources", "detail_pointer": "/resources", "bodies_loaded": False}

    def update():
        response["coverage"]["omitted_fields"] = [_pointer("", k) for k in pending]
        if pending:
            response["followup"]["select"] = _read_recipe("selected",
                selectors=[_pointer("", k) for k in pending[:20]], max_bytes=max_bytes)
        else:
            response["followup"].pop("select", None)

    # Reserve exact source followups before filling the body. Counter references
    # precede support references, with original order retained within each set.
    for key in ("counterevidence", "evidence"):
        if key in note:
            response["references"][key] = {"items": [], "total_items": len(note[key]), "next_offset": 0 if note[key] else None}
            response["followup"][key] = _read_recipe("selected", selectors=["/" + key], max_bytes=max_bytes)
    update()
    if byte_size(response, response_envelope) > max_bytes:
        raise ValueError("note controls/definitions and continuation exceed budget; increase max_bytes")
    # Reserve scope, limits and declared contrary references before potentially
    # large optional knowledge bodies. This is a structural policy, not ranking.
    def offer(keys):
        for key in keys:
            response["knowledge"][key] = note[key]
            pending.remove(key)
            update()
            if byte_size(response, response_envelope) > max_bytes:
                response["knowledge"].pop(key)
                pending.append(key)
                update()

    deferred = {"knowledge", "scientific_result", "result_read", "method_choices", "feedback"}
    offer([key for key in pending if key not in deferred])
    for key, page in response["references"].items():
        for index, ref in enumerate(note[key]):
            page["items"].append({"pointer": f"/{key}/{index}", "value": ref})
            page["next_offset"] = index + 1 if index + 1 < len(note[key]) else None
            response["followup"][key] = _read_recipe("selected", selectors=["/" + key],
                offset=index + 1 if page["next_offset"] is not None else 0, max_bytes=max_bytes)
            if byte_size(response, response_envelope) > max_bytes:
                page["items"].pop()
                page["next_offset"] = index
                response["followup"][key] = _read_recipe("selected", selectors=["/" + key], offset=index, max_bytes=max_bytes)
                break
    offer([key for key in pending if key in deferred])
    response["coverage"]["knowledge"] = "partial" if pending else "verified"
    if byte_size(response, response_envelope) > max_bytes:
        raise ValueError("note brief exceeds budget; increase max_bytes or select explicit fields")
    return response


def original(path):
    path = Path(path).absolute()
    return {"path": str(path), "identity": file_identity(path)}


def _original_selection(entry, pointer, branch_id):
    # Read only the immutable note, never every cited support/counter original.
    value = entry_value(entry)
    result = read_entry(value, branch_id=branch_id, verify_originals=False)
    pin = {k: value[k] for k in ("schema", "database", "task_id", "workspace", "source_id",
                                 "definitions", "definitions_digest", "memory_scope") if k in value}
    result["details"] = {**pin, "branch_id": result["branch_id"], "record_id": result["record_id"]}
    selected = _selected(result["note"], pointer)
    return result, selected, entry_journal(value).resolver


def open_original(entry, pointer, *, branch_id=None, max_loaded_bytes=256 * 1024 * 1024):
    """Open one exact original reference selected from a note by JSON Pointer.

    with open_original(pin, '/evidence/0') as saved:
        decisions = saved.array('decisions')  # loaded once; ordinary NumPy below
    """
    from .evidence import OriginalReader
    _, ref, resolver = _original_selection(entry, pointer, branch_id)
    if not isinstance(ref, dict) or not {"path", "identity"} <= ref.keys():
        raise ValueError("select an original reference with path and identity")
    return OriginalReader(ref, resolver=resolver, max_loaded_bytes=max_loaded_bytes)


def read_original(entry, pointer, *, branch_id=None, selector=None, offset=0, limit=20, max_bytes=16384):
    """Bounded metadata or selected saved results; defaults never dump arrays."""
    with open_original(entry, pointer, branch_id=branch_id) as saved:
        return saved.read(selector=selector, offset=offset, limit=limit, max_bytes=max_bytes)


def resolve_method(entry, pointer, *, branch_id=None, max_bytes=16384):
    """Describe exactly the host-selected method; never import, invoke or rank it.

    Existing descriptor fields remain unchanged. An optional source original or
    source_ref JSON Pointer is resolved through the existing relocation map.
    Signature/conditions are producer declarations, not validated science.
    """
    from .evidence_refs import resolve_artifact
    result, description, resolver = _original_selection(entry, pointer, branch_id)
    if not isinstance(description, dict):
        raise ValueError("select an explicit method description object")
    output = {"record_id": result["record_id"], "details": result["details"],
              "pointer": pointer, "description": description,
              "execution_performed": False, "signature_verification": "producer_declared"}
    source = description.get("source")
    if isinstance(description.get("source_ref"), str) and description["source_ref"].startswith("/"):
        source = _selected(result["note"], description["source_ref"])
    if isinstance(source, dict) and {"path", "identity"} <= source.keys():
        path = resolve_artifact({"path": source["path"], **source["identity"]}, resolver=resolver)
        output["source"] = {"path": str(path), "identity": source["identity"], "verification": "verified"}
    else:
        output["source_verification"] = "not_resolved; use the descriptor's explicit source reader"
    if type(max_bytes) is not int or not 4096 <= max_bytes <= 65536:
        raise ValueError("method max_bytes must be 4096..65536")
    if byte_size(output) > max_bytes:
        raise ValueError("method description exceeds response bound; select its fields with read_entry")
    return output


def _verify(note, resolver=None):
    # Different annotations of the same source do not require another full read.
    seen = set()
    for ref in [*note.get("evidence", []), *note.get("counterevidence", [])]:
        path = str(Path(ref["path"]).resolve()) if resolver is None else ref["path"]
        key = (id(resolver), path, canonical(ref["identity"]))
        scope = _ORIGINAL_CHECKS.get()
        if key not in seen and (scope is None or key not in scope):
            if resolver is None:
                if file_identity(path) != ref["identity"]:
                    raise ValueError("research note original changed: " + ref["path"])
            else:
                resolver.resolve(ref["path"], **ref["identity"])
            if scope is not None:
                scope[key] = (ref, resolver)
        seen.add(key)
    return len(seen)


def _recheck_publication_originals():
    """A second byte check immediately before advancing a published head.

    The scope belongs only to save_progress; no verdict survives its return.
    Standalone record/read/publish calls always run their own ordinary checks.
    """
    scope = _ORIGINAL_CHECKS.get()
    if scope is not None:
        token = _ORIGINAL_CHECKS.set(None)
        try:
            for ref, resolver in scope.values():
                _verify({"evidence": [ref]}, resolver)
        finally:
            _ORIGINAL_CHECKS.reset(token)


def _check_result_meanings(note):
    """A typed producer result cannot be indexed under different meanings.

    Opaque historical result fields remain opaque; no missing definition is
    invented, and ordinary notes need no scientific-result profile.
    """
    result = note.get("scientific_result")
    if not isinstance(result, dict) or result.get("schema") != "etalon-scientific-result/1":
        return
    from .result_semantics import validate_result_binding
    binding = validate_result_binding(result)
    if "definitions" in note:
        from .meta_context import compare_definitions
        checked = compare_definitions(binding["definitions"], note["definitions"])
        if checked["missing"] or checked["mismatched"]:
            raise ValueError("note definitions differ from its scientific result")


def record_note(journal, note, directory, *, source_kind="existing_real_artifact"):
    """Save one note, without changing an execution, spending or selecting a method."""
    note = json.loads(canonical(note))
    for key in ("task_id", "branch_id", "question", "summary"):
        if not isinstance(note.get(key), str) or not note[key].strip():
            raise ValueError("note requires " + key)
    if not isinstance(note.get("conditions", {}), dict):
        raise ValueError("note conditions must be explicit fields")
    if note.get("schema", SCHEMA) != SCHEMA:
        raise ValueError("unsupported research note")
    note["schema"] = SCHEMA
    scope = note.get("memory_scope", SHARED_SCOPE)
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("memory_scope must be a nonempty name")
    _check_result_meanings(note)
    _verify(note, journal.resolver)
    supersedes = tuple(note.get("supersedes", []))
    for rid in supersedes:
        old = read_note(journal, rid, verify_originals=False)["note"]
        if (old["task_id"], old["branch_id"], old.get("memory_scope", SHARED_SCOPE)) != (
                note["task_id"], note["branch_id"], scope):
            raise ValueError("a note may supersede only its own task branch")
    path = Path(directory).absolute() / (digest(note) + ".json")
    _save(path, note)
    bindings = {k: note[k] for k in ("schema", "task_id", "branch_id", "capability_id", "publication_id", "memory_scope") if k in note}
    return journal.record(Artifact(str(path), **file_identity(path), source_kind=source_kind,
                                   role="diagnostic", bindings=bindings, parents=supersedes))


def read_note(journal, record_id, *, verify_originals=True):
    record = journal.get(record_id)
    if record.get("bindings", {}).get("schema") != SCHEMA or record.get("role") != "diagnostic":
        raise ValueError("explicit diagnostic research note required")
    note = _read(journal.artifact_path(Artifact(**record)))
    _check_result_meanings(note)
    if note.get("schema") != SCHEMA:
        raise ValueError("research note schema differs from its record")
    count = _verify(note, journal.resolver) if verify_originals else None
    return {"record_id": record_id, "note": note, "originals_verified": count}


def publish_note(entry, journal, record_id, *, activate=True):
    """Advance a branch in a stable project entry, preserving other branch heads."""
    current = read_note(journal, record_id)
    note = current["note"]
    value = {"schema": ENTRY, "database": str(journal.store.path.absolute()),
             "record_id": record_id, "task_id": note["task_id"], "branch_id": note["branch_id"]}
    if "memory_scope" in note:
        value["memory_scope"] = note["memory_scope"]
    entry = Path(entry).absolute()
    entry.parent.mkdir(parents=True, exist_ok=True)
    with _consumer_lock(entry):
        if entry.exists():
            old = entry_value(entry)
            if (old.get("schema") != ENTRY or not _same_journal(old, journal)
                    or old.get("task_id") != value["task_id"]
                    or old.get("memory_scope", SHARED_SCOPE) != value.get("memory_scope", SHARED_SCOPE)):
                raise ValueError("entry changed; explicitly supersede the current note")
            heads = old.get("branches", {old["branch_id"]: old["record_id"]})
            previous = heads.get(note["branch_id"])
            if previous != record_id and previous is not None and previous not in note.get("supersedes", []):
                raise ValueError("entry changed; explicitly supersede the current note")
            value["branches"] = {**heads, note["branch_id"]: record_id}
            for key in ("read_recipe", "definitions", "task_workset", "workspace", "source_id"):
                if key in old:
                    value[key] = old[key]
            value["database"] = old["database"]
            if not activate:
                value.update(branch_id=old["branch_id"], record_id=value["branches"][old["branch_id"]])
            if old == value:
                return old
        else:
            value["branches"] = {note["branch_id"]: record_id}
        binding = getattr(journal, "_note_entry_binding", None)
        if binding is not None:
            value.update(binding)
        _recheck_publication_originals()
        _save(entry, value, replace=True)
    return value


def save_progress(journal, entry, note, *, directory=None, activate=True, publication_id=None):
    """Record meaningful progress in the caller's existing journal and stable entry.

    Repeating a completed publication is a no-op. An interrupted publication can
    be repeated with the same body; an intervening branch update is not overwritten.
    This function neither configures a campaign nor launches scientific work.
    """
    token = _ORIGINAL_CHECKS.set({})
    try:
        return _save_progress(journal, entry, note, directory=directory,
            activate=activate, publication_id=publication_id)
    finally:
        _ORIGINAL_CHECKS.reset(token)


def patch_progress(entry, patch, *, branch_id=None, expected_record=None,
                   publication_id=None, evidence=(), counterevidence=(), activate=True):
    """Publish changed fields through the same entry used by CLI and producers.

    Omitted fields (including contrary evidence) remain. A replay is rebuilt
    against its original publication, not a newer host interpretation. File
    arguments append originals without callers reading or copying old arrays.
    This function publishes data; it does not choose or execute an action.
    """
    entry = Path(entry).absolute()
    locator = _read(entry)
    if isinstance(locator, dict) and set(locator) == {"entry"}:
        target = Path(locator["entry"])
        entry = target if target.is_absolute() else entry.parent / target
    if not isinstance(patch, dict):
        raise ValueError("progress patch must be an object")
    reserved = {"schema", "task_id", "branch_id", "publication_id", "supersedes", "memory_scope"}
    if reserved.intersection(patch):
        raise ValueError("patch replaces note fields only; use a complete note for task/branch metadata")
    value = entry_value(entry)
    branch = branch_id or value["branch_id"]
    heads = value.get("branches", {value["branch_id"]: value["record_id"]})
    if branch not in heads:
        raise ValueError("patch requires an existing branch; use a complete note for a new branch")
    journal = entry_journal(value)
    prior = None
    if publication_id is not None:
        with journal.store.connection() as db:
            rows = db.execute("SELECT key FROM metadata WHERE json_extract(body,'$.bindings.schema')=? "
                "AND json_extract(body,'$.bindings.task_id')=? AND json_extract(body,'$.bindings.branch_id')=? "
                "AND json_extract(body,'$.bindings.publication_id')=? "
                "AND COALESCE(json_extract(body,'$.bindings.memory_scope'),'shared')=?",
                (SCHEMA, value["task_id"], branch, publication_id, value.get("memory_scope", SHARED_SCOPE))).fetchall()
        if len(rows) > 1:
            raise ValueError("publication identity has conflicting records")
        if rows:
            prior = read_note(journal, rows[0][0].removeprefix("research:"), verify_originals=False)["note"]
    base = prior or read_note(journal, heads[branch], verify_originals=False)["note"]
    note = {**base, **patch}
    note.pop("publication_id", None)
    note["supersedes"] = base.get("supersedes", []) if prior else [heads[branch]]
    if expected_record is not None:
        parents = prior.get("supersedes", []) if prior else [heads[branch]]
        if expected_record not in parents:
            raise ValueError("entry changed; expected record is not the current publication parent")
        note["supersedes"] = [expected_record]
    for role, paths in (("evidence", evidence), ("counterevidence", counterevidence)):
        for path in paths:
            ref = original(path)
            refs = note.setdefault(role, [])
            if ref not in refs:
                refs.append(ref)
    rid = save_progress(entry_journal(value, read_only=False), entry, note,
                        activate=activate, publication_id=publication_id)
    current = entry_value(entry)
    return {"ok": True, "entry": str(entry), "task_id": value["task_id"],
            "branch_id": branch, "record_id": rid,
            "current_record_id": current["record_id"], "execution_performed": False}


def _save_progress(journal, entry, note, *, directory, activate, publication_id):
    entry = Path(entry).absolute()
    body = json.loads(canonical(note))
    body.pop("publication_id", None)
    content = lambda n: {k: v for k, v in n.items() if k not in {"schema", "supersedes"}}
    if publication_id is not None:
        body["publication_id"] = publication_id
        with journal.store.connection() as db:
            prior = db.execute("SELECT key FROM metadata WHERE json_extract(body,'$.bindings.schema')=? "
                "AND json_extract(body,'$.bindings.task_id')=? AND json_extract(body,'$.bindings.branch_id')=? "
                "AND json_extract(body,'$.bindings.publication_id')=? "
                "AND COALESCE(json_extract(body,'$.bindings.memory_scope'),'shared')=?",
                (SCHEMA, body["task_id"], body["branch_id"], publication_id, body.get("memory_scope", SHARED_SCOPE))).fetchall()
        if prior:
            if len(prior) != 1:
                raise ValueError("publication identity has conflicting records")
            rid = prior[0][0].removeprefix("research:")
            old = read_note(journal, rid)["note"]
            if content(old) != content(body):
                raise ValueError("publication identity reused for different content")
            current = entry_value(entry) if entry.exists() else None
            if current is not None and (not _same_journal(current, journal)
                    or current.get("task_id") != body["task_id"]
                    or current.get("memory_scope", SHARED_SCOPE) != body.get("memory_scope", SHARED_SCOPE)):
                raise ValueError("progress must use the existing project journal and task")
            head = current.get("branches", {current["branch_id"]: current["record_id"]}).get(body["branch_id"]) if current else None
            if head is None or head in old.get("supersedes", []):
                publish_note(entry, journal, rid, activate=activate)
            # Replaying a producer must not replace later host interpretation.
            return rid
    if entry.exists():
        current = entry_value(entry)
        if (current.get("schema") != ENTRY or not _same_journal(current, journal)
                or current.get("task_id") != body["task_id"]
                or current.get("memory_scope", SHARED_SCOPE) != body.get("memory_scope", SHARED_SCOPE)):
            raise ValueError("progress must use the existing project journal and task")
        head = current.get("branches", {current["branch_id"]: current["record_id"]}).get(body["branch_id"])
        if head is not None:
            old = read_note(journal, head, verify_originals=False)["note"]
            if content(old) == content(body):
                _verify(old, journal.resolver)
                if activate and current["record_id"] != head:
                    publish_note(entry, journal, head, activate=activate)
                return head
            body.setdefault("supersedes", [head])
    rid = record_note(journal, body, directory or entry.parent / "memory")
    publish_note(entry, journal, rid, activate=activate)
    return rid


def entry_value(entry):
    """Read entry metadata, including the existing one-hop locator form."""
    base = Path(entry).absolute().parent if isinstance(entry, (str, Path)) else None
    value = _read(entry) if isinstance(entry, (str, Path)) else entry
    if isinstance(value, dict) and set(value) == {"entry"}:
        target = Path(value["entry"])
        if not target.is_absolute():
            if base is None:
                raise ValueError("an inline entry locator requires an absolute path")
            target = base / target
        value = _read(target)
        base = target.absolute().parent
    value = json.loads(canonical(value))
    for holder in (value, value.get("task_workset", {})):
        if isinstance(holder, dict) and "workspace" in holder:
            location = Path(holder["workspace"])
            if not location.is_absolute():
                if base is None:
                    raise ValueError("an inline note entry requires an absolute workspace path")
                location = base / location
            holder["workspace"] = str(location.absolute())
    return value


def entry_journal(value, *, read_only=True):
    """Resolve portable history; explicit write access continues in a live copy."""
    if "workspace" in value or "source_id" in value:
        from .evidence_refs import EvidenceResolver
        if not value.get("workspace") or not value.get("source_id"):
            raise ValueError("portable note entries require workspace and source_id")
        resolver = EvidenceResolver(value["workspace"], value["source_id"])
        path = resolver.resolve_source(value["database"])
        if not read_only and resolver.workspace.source(value["source_id"])["identity"]["kind"] == "snapshot_sha256":
            from .note_handoff import writable_note_journal
            return writable_note_journal(resolver, path)
        return ResearchJournal(CampaignStore(path, read_only=read_only), resolver=resolver)
    return ResearchJournal(CampaignStore(value["database"], read_only=read_only))


def _same_journal(value, journal):
    path = entry_journal(value).store.path if "workspace" in value else Path(value["database"])
    parent = getattr(journal, "_note_parent_database", None)
    return path.resolve() == journal.store.path.resolve() or parent is not None and path.resolve() == parent


def read_entry(entry, *, verify_originals=None, conditions=None, definitions=None, branch_id=None, view="full",
               selectors=None, offset=0, limit=20, max_bytes=16384, response_envelope=None):
    """Knowledge first when requested; full preserves the original checked reader.

    Knowledge bodies are always identity-checked. Their cited originals are read
    only in full mode or on explicit request; no cached verification is claimed.
    index gives continuation bodies and a paged key/title inventory. selected
    batches literal JSON Pointers; offset/limit page each selected collection.
    Follow details to pin the note, and returned next_read arguments to continue.
    """
    if view not in {"full", "knowledge", "brief", "index", "selected"}:
        raise ValueError("note view must be full, knowledge, brief, index or selected")
    if view in {"full", "knowledge", "brief"} and (selectors is not None or offset or limit != 20):
        raise ValueError("selectors/offset/limit require index or selected view")
    if verify_originals is None:
        verify_originals = view == "full"
    value = entry_value(entry)
    if value.get("schema") != ENTRY:
        raise ValueError("research note entry required")
    if "definitions" in value:
        from .meta_context import compare_definitions
        if definitions is not None:
            override = compare_definitions(value["definitions"], definitions)
            if override["missing"] or override["mismatched"]:
                raise ValueError("definitions override cannot discard or change entry bindings")
        else:
            definitions = value["definitions"]
    journal = entry_journal(value)
    heads = value.get("branches", {value["branch_id"]: value["record_id"]})
    if heads.get(value["branch_id"]) != value["record_id"]:
        raise ValueError("entry current branch differs from its recorded head")
    selected = branch_id or value["branch_id"]
    if selected not in heads:
        raise ValueError("requested branch is absent from the project entry")
    result = read_note(journal, heads[selected], verify_originals=verify_originals)
    note = result["note"]
    if "definitions_digest" in value:
        if digest(note.get("definitions", {})) != value["definitions_digest"]:
            raise ValueError("pinned definitions digest differs from this note")
        from .meta_context import compare_definitions
        if definitions is not None:
            override = compare_definitions(note.get("definitions", {}), definitions)
            if override["missing"] or override["mismatched"]:
                raise ValueError("definitions override cannot discard or change pinned bindings")
        else:
            definitions = note.get("definitions", {})
    if conditions is not None and any(note.get("conditions", {}).get(k) != v for k, v in conditions.items()):
        raise ValueError("requested conditions differ from this note; select another entry or query find_notes")
    if definitions is not None:
        from .meta_context import compare_definitions
        compared = compare_definitions(definitions, note.get("definitions", {}))
        if compared["missing"] or compared["mismatched"]:
            raise ValueError("requested definitions are missing or differ from this note; query find_notes")
    if value["task_id"] != note["task_id"] or selected != note["branch_id"]:
        raise ValueError("entry scope differs from its note")
    if value.get("memory_scope", SHARED_SCOPE) != note.get("memory_scope", SHARED_SCOPE):
        raise ValueError("entry memory_scope differs from its note")
    # Shape shared with existing context delivery; graph expansion is unnecessary.
    response = {**result, "context_id": "note-" + digest({"entry": value, "selected": selected}),
            "task_id": note["task_id"], "branch_id": note["branch_id"],
            "memory_scope": note.get("memory_scope", SHARED_SCOPE),
            "source_watermarks": [{"source_id": value["database"], "status": "complete",
                                   "record_id": heads[selected]}],
            "source_issues": [], "publications": {"status": "complete", "operations": []},
            "execution_permission": "not_granted"}
    if "definitions" in note:
        response["definitions"] = note["definitions"]
    if "task_workset" in value:
        from .meta_context import note_workset_summary
        response["task_workset"] = note_workset_summary(value, task_id=note["task_id"],
            conditions=conditions if conditions is not None else note.get("conditions"),
            definitions=definitions if definitions is not None else note.get("definitions"))
    if view in {"index", "selected", "brief"}:
        response["details"] = {"schema": ENTRY, "database": value["database"],
            "task_id": note["task_id"], "branch_id": selected, "record_id": heads[selected]}
        response["details"].update({k: value[k] for k in ("workspace", "source_id", "memory_scope") if k in value})
        if definitions is not None:
            response["details"]["definitions"] = definitions
        if "task_workset" in value:
            response["details"]["task_workset"] = value["task_workset"]
        if view == "brief":
            response["branches"] = heads
            if byte_size(heads) > max_bytes // 8 and isinstance(entry, (str, Path)):
                response["_project_entry"] = str(Path(entry).absolute())
            return _brief_note(response, note, max_bytes=max_bytes, response_envelope=response_envelope)
        return _selective_note(response, note, view=view, selectors=selectors,
                               offset=offset, limit=limit, max_bytes=max_bytes, response_envelope=response_envelope)
    if view == "knowledge":
        response.pop("note")
        response["controls"] = {k: note[k] for k in (
            "controls", "authorization", "constraints", "budget", "other_pauses") if k in note}
        response["knowledge"] = {k: note[k] for k in (
            "question", "summary", "conditions", "status", "outcome", "lesson", "knowledge",
            "invalid_when", "reconsider_when", "residuals", "next_action", "pending_handles",
            "capability_id", "method_choices", "feedback") if k in note}
        response["branches"] = heads
        response["details"] = {"schema": ENTRY, "database": value["database"],
            "task_id": note["task_id"], "branch_id": selected, "record_id": heads[selected]}
        response["details"].update({k: value[k] for k in ("workspace", "source_id", "memory_scope") if k in value})
        if definitions is not None:
            response["details"]["definitions"] = definitions
        if "task_workset" in value:
            response["details"]["task_workset"] = value["task_workset"]
        response["coverage"] = {"knowledge": "verified", "originals": "verified" if verify_originals else "not_read",
            "support_count": len(note.get("evidence", [])), "counter_count": len(note.get("counterevidence", [])),
            "expand": "read_entry(details, view='full'); original references remain in the note"}
        if byte_size(response, response_envelope) > max_bytes:
            response["note"] = note
            if byte_size(heads) > max_bytes // 8 and isinstance(entry, (str, Path)):
                response["_project_entry"] = str(Path(entry).absolute())
            return _brief_note(response, note, max_bytes=max_bytes, response_envelope=response_envelope)
    return response


def read_current(entry, *, recipe=None, conditions=None, definitions=None, branch_id=None,
                 max_bytes=16384, response_envelope=None):
    """Execute an explicit read recipe, otherwise return the bounded current brief.

    Recipes select only view/fields; they cannot replace source, task, branch,
    pin, controls or authority. The original read_entry defaults remain intact.
    """
    value = entry_value(entry)
    recipe = value.get("read_recipe") if recipe is None else recipe
    options = {"view": "brief"} if recipe is None else dict(recipe)
    if set(options) - {"view", "selectors", "offset", "limit", "verify_originals"}:
        raise ValueError("read recipe may only select view, selectors, offset, limit, verify_originals")
    if options.get("view", "brief") == "full":
        raise ValueError("current read recipes must be bounded; request full explicitly with read_entry")
    options.setdefault("view", "brief")
    response = read_entry(entry, conditions=conditions, definitions=definitions, branch_id=branch_id,
        max_bytes=max_bytes, response_envelope=response_envelope, **options)
    if byte_size(response, response_envelope) > max_bytes:
        raise ValueError("current read recipe exceeds budget; select exact fields or use brief")
    return response


def find_notes(journal, *, task_id, conditions, definitions=None, capability_id=None, limit=20, offset=0, verify_originals=True,
               memory_scope=SHARED_SCOPE):
    """Exact scoped retrieval, including negative notes and applicability gaps."""
    if not isinstance(conditions, dict) or not 1 <= limit <= 50 or offset < 0:
        raise ValueError("bounded page and explicit query conditions required")
    sql = "SELECT key FROM metadata WHERE json_extract(body,'$.bindings.schema')=? AND json_extract(body,'$.bindings.task_id')=?"
    if not isinstance(memory_scope, str) or not memory_scope.strip():
        raise ValueError("select one explicit memory_scope")
    sql += " AND COALESCE(json_extract(body,'$.bindings.memory_scope'),'shared')=?"
    params = [SCHEMA, task_id, memory_scope]
    if capability_id is not None:
        sql += " AND json_extract(body,'$.bindings.capability_id')=?"
        params.append(capability_id)
    with journal.store.connection() as db:
        ids = [r[0].removeprefix('research:') for r in db.execute(
            sql + " ORDER BY key LIMIT ? OFFSET ?", [*params, limit + 1, offset])]
    matches, related = [], []
    for rid in ids[:limit]:
        row = read_note(journal, rid, verify_originals=verify_originals)
        declared = row["note"].get("conditions", {})
        missing = sorted(set(conditions) - set(declared))
        mismatch = sorted(k for k, v in declared.items() if conditions.get(k) != v)
        definition_check = None
        if definitions is not None:
            from .meta_context import compare_definitions
            definition_check = compare_definitions(definitions, row["note"].get("definitions", {}))
        if missing or mismatch or (definition_check and (definition_check["missing"] or definition_check["mismatched"])):
            related.append({**row, "unknown_conditions": missing, "unmatched_conditions": mismatch,
                **({"definitions": definition_check} if definition_check is not None else {})})
        else:
            matches.append(row)
    return {"matches": matches, "related": related,
            "memory_scope": memory_scope,
            "next_offset": offset + limit if len(ids) > limit else None,
            "selection": "host_decides; inspect supersedes and counterevidence"}


def fork_memory(entry, destination, *, memory_scope, question):
    """Start independent working memory in the same journal, sharing originals.

    No transcript, main hypotheses or skills are copied. This is retrieval and
    writing isolation, not a filesystem ACL. Explicit original reads stay possible.
    Bind this new entry to the advisor's own host session/hook installation.
    """
    if not isinstance(memory_scope, str) or not memory_scope.strip() or memory_scope == SHARED_SCOPE:
        raise ValueError("choose a named private memory_scope")
    source = entry_value(entry)
    if source.get("memory_scope", SHARED_SCOPE) != SHARED_SCOPE:
        raise ValueError("fork from a shared research entry")
    current = read_entry(source, verify_originals=False)
    parent = current["note"]
    note = {"task_id": parent["task_id"], "branch_id": "working",
        "memory_scope": memory_scope, "question": question,
        "summary": "Independent working memory; read shared results as needed.",
        "conditions": parent.get("conditions", {}),
        "shared_entry": str(Path(entry).absolute()) if isinstance(entry, (str, Path)) else source,
        "shared_basis": {"record_id": current["record_id"], "branch_id": parent["branch_id"]},
        "knowledge": {}, "evidence": [], "counterevidence": [],
        "next_action": "Read relevant shared results; propose only useful changes. No automatic main-memory merge."}
    for key in ("constraints", "authorization", "other_pauses", "budget", "pending_handles", "definitions"):
        if key in parent:
            note[key] = parent[key]
    target = Path(destination).absolute()
    if target.exists():
        existing = read_entry(target, verify_originals=False)
        if (existing["note"].get("memory_scope") != memory_scope
                or existing["task_id"] != parent["task_id"]
                or existing["note"].get("shared_entry") != note["shared_entry"]):
            raise ValueError("destination already belongs to another memory")
        return {"entry": str(target), "record_id": existing["record_id"], "reused": True}
    journal = entry_journal(source, read_only=False)
    rid = save_progress(journal, target, note, publication_id="memory-" + digest([memory_scope, note["shared_entry"]]))
    return {"entry": str(target), "record_id": rid, "reused": False}
