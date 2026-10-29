"""Ordinary note entries use the existing handoff and evidence location map.

No note is rewritten or projected into a graph. SQLite backup copies the caller's
existing journal; it creates neither a configured campaign nor an execution.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from etalon.active.schema import canonical, digest
from etalon.active.store import CampaignStore
from .evidence_refs import EvidenceError, EvidenceWorkspace, load_workspace, validate_ref
from .journal import ResearchJournal
from .notes import SCHEMA, entry_journal, entry_value, read_entry, read_note


def _values(value):
    """Visit declared JSON values only; no directory or external job discovery."""
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _values(child)


def _note_records(journal, entry, limit):
    with journal.store.connection() as db:
        stored = db.execute("SELECT key,body FROM metadata WHERE key LIKE 'research:%' ORDER BY key LIMIT ?",
                            (limit + 1,)).fetchall()
    if len(stored) > limit:
        raise EvidenceError("handoff journal record limit exceeded")
    records = {key.removeprefix("research:"): json.loads(body) for key, body in stored}
    heads = entry.get("branches", {entry["branch_id"]: entry["record_id"]})
    if not isinstance(heads, dict) or heads.get(entry["branch_id"]) != entry["record_id"]:
        raise EvidenceError("note entry current branch differs from its recorded head")
    selected = {rid for rid, record in records.items() if
                record.get("bindings", {}).get("schema") == SCHEMA and
                record["bindings"].get("task_id") == entry["task_id"] and
                record["bindings"].get("memory_scope", "shared") == entry.get("memory_scope", "shared")}
    if not set(heads.values()) <= selected:
        raise EvidenceError("note entry head is missing or belongs to another task")
    pending, visited, notes = list(selected), set(), {}
    while pending:
        rid = pending.pop()
        if rid in visited:
            continue
        visited.add(rid)
        body = records[rid]
        content = body
        if body.get("bindings", {}).get("schema") == SCHEMA:
            content = read_note(journal, rid)["note"]
            notes[rid] = content
        for value in (*_values(body), *_values(content)):
            if isinstance(value, str) and value in records and value not in visited:
                pending.append(value)
    for branch, rid in heads.items():
        if notes[rid]["branch_id"] != branch:
            raise EvidenceError("note entry branch differs from its note")
    return sorted(visited), notes


def plan_note_handoff(origin, resolved_selector, requested, selector, inventory, overrides, publication_intents):
    from .handoff import (BUNDLE, PLAN, MAX_MANIFEST_BYTES, _archive_workspace, _backup_database,
                          _documents, _plain, _publication_history, _rows_digest, _seal, _verify_originals)
    limits = inventory.limits
    envelope = json.loads(origin.read_text(encoding="utf-8-sig"))
    if envelope.get("handoff", {}).get("schema") == BUNDLE:
        raise EvidenceError("entry is already an archived handoff; use relocate_research_handoff")
    value = entry_value(origin)
    journal = entry_journal(value)
    database = journal.store.path
    inventory.add(origin, "origin/entry.json")
    binding = value.get("task_workset")
    bound = None
    if binding is not None:
        if not isinstance(binding, dict) or set(binding) != {"workspace", "checkpoint_ref"}:
            raise EvidenceError("note task_workset needs a published workspace and checkpoint_ref")
        bound = load_workspace(binding["workspace"])
        inventory.add(binding["workspace"], "origin/workset-workspace.json")
    sid = value.get("source_id", "note-journal-" + digest(value["database"])[:16])
    if bound is not None and sid in {s["source_id"] for s in bound.manifest["sources"]}:
        # A previous handoff placed notes and their optional workset in one
        # workspace. After ordinary progress, the note source is its live
        # descendant, while the workset continues to cite frozen records.
        # Reuse the source id only after checking every pinned old record.
        from .evidence import _source
        if binding["checkpoint_ref"]["source_id"] == sid:
            raise EvidenceError("note journal and workset checkpoint have conflicting source ids")
        prior, coverage = _source(bound, bound.source(sid))
        if coverage["status"] != "complete" or any(journal.get(rid) != row["record"] for rid, row in prior.items()):
            raise EvidenceError("note journal differs from its workset's pinned history")
        trimmed = bound.manifest
        trimmed["sources"] = [s for s in trimmed["sources"] if s["source_id"] != sid]
        trimmed["location_map"] = [m for m in trimmed.get("location_map", []) if m["source_id"] != sid]
        bound = EvidenceWorkspace(bound.base, canonical(trimmed))
    with tempfile.TemporaryDirectory(prefix="etalon-handoff-") as temporary:
        snapshot = Path(temporary) / "snapshot.sqlite"
        identity = _backup_database(database, snapshot, limits["max_bytes"])
        frozen = ResearchJournal(CampaignStore(snapshot, read_only=True), resolver=journal.resolver)
        record_ids, notes = _note_records(frozen, value, limits["max_records"])
        source = {"source_id": sid, "kind": "journal", "location": str(snapshot),
                  "identity": {"kind": "snapshot_sha256", "value": identity["sha256"]},
                  "record_ids": record_ids}
        # Reuse existing explicit relocations for a source already read elsewhere.
        maps, source_maps = [], []
        if journal.resolver is not None:
            for item in journal.resolver.workspace.manifest.get("location_map", []):
                if item["source_id"] == sid and item["original"] != value["database"]:
                    from .evidence_refs import _local
                    path = _local(journal.resolver.workspace.base, item["location"], confined=True)
                    maps.append((sid, item["original"], path, {k: item[k] for k in ("sha256", "bytes")}))
                    source_maps.append(item)
        originals, resources, snapshot_refs, linked_refs = list(maps), [], [], []
        available_sources = {sid} | ({s["source_id"] for s in bound.manifest["sources"]} if bound else set())
        for note in notes.values():
            for ref in [*note.get("evidence", []), *note.get("counterevidence", [])]:
                path = journal.resolver.resolve(ref["path"], **ref["identity"]) if journal.resolver else _plain(ref["path"])
                originals.append((sid, ref["path"], path, ref["identity"]))
            for ref in _values(note):
                if not isinstance(ref, dict):
                    continue
                if ref.get("schema") == "etalon-evidence-ref/1":
                    validate_ref(ref)
                    if ref.get("source_id") not in available_sources:
                        raise EvidenceError("note evidence reference has no explicit source workspace: " + str(ref.get("source_id")))
                    linked_refs.append(ref)
                if "slug" in ref and "manifest_sha256" in ref:
                    resources.append({"content_ref": ref, "parts": []})
                if set(ref) == {"manifest_path", "manifest_sha256"}:
                    snapshot_refs.append((sid, ref))
        ws = EvidenceWorkspace(journal.resolver.workspace.base if journal.resolver else origin.parent,
            canonical({"schema": "etalon-evidence-workspace/1",
            "workspace_id": "note-handoff-" + digest({"database": value["database"], "task_id": value["task_id"]}),
            "sources": [source], "location_map": source_maps}))
        archived = _archive_workspace(ws, inventory, {} if bound else overrides,
            source_backups={sid: str(database)}, note_originals=originals,
            extra_resources=() if bound else resources, extra_skill_refs=snapshot_refs, source_only=bound is not None)
        manifest, rows, count, checkpoint, continuation, artifacts, members, skills, bundles = archived
        if set(rows[sid]) != set(record_ids):
            raise EvidenceError("handoff note history has missing or unpublished dependencies")
        # The original entry's database locator may differ from its resolved DB.
        database_map = next(m for m in manifest["location_map"] if m["source_id"] == sid and m["original"] == str(database))
        if value["database"] != str(database):
            manifest["location_map"].append({**database_map, "original": value["database"]})
        if bound is not None:
            other = _archive_workspace(bound, inventory, overrides, binding["checkpoint_ref"],
                                       extra_resources=resources, require_paused=False)
            extra, extra_rows, extra_count, checkpoint, continuation, a, m, s, b = other
            if checkpoint["task_id"] != value["task_id"]:
                raise EvidenceError("note task_workset belongs to another task")
            manifest["sources"].extend(extra["sources"])
            manifest["location_map"].extend(extra["location_map"])
            for key in ("skill_bundle_map", "status_refs"):
                if key in extra:
                    manifest[key] = extra[key]
            rows.update(extra_rows)
            count += extra_count
            artifacts, members, skills, bundles = artifacts + a, members + m, skills + s, bundles + b
        for ref in linked_refs:
            row = rows.get(ref["source_id"], {}).get(ref["record_id"])
            if row is None or digest(row["record"]) != ref["record_digest"]:
                raise EvidenceError("note evidence reference is missing or its record identity differs")
    if count > limits["max_records"] or len(manifest["sources"]) > limits["max_sources"]:
        raise EvidenceError("handoff source/record limit exceeded")
    history, trees = _publication_history(origin, envelope, publication_intents, inventory)
    pending = [{"record_id": rid, "handles": notes[rid]["pending_handles"], "execution_migration": "not_performed"}
               for rid in value.get("branches", {value["branch_id"]: value["record_id"]}).values()
               if notes[rid].get("pending_handles")]
    continuation = continuation or {"authority": {"state": "not_granted", "sources": []},
                                    "issues": [], "warnings": [], "pending": pending}
    plan = _seal({"schema": PLAN, "entry_kind": "note", "limits": limits, "origin_entry": str(origin),
        "entry_selector": {"path": str(selector), "resolved_path": str(requested),
                           "identity": inventory.originals[str(requested)]},
        "resolved_entry_selector": {"path": str(resolved_selector), "resolved_path": str(origin),
                                    "identity": inventory.originals[str(origin)]},
        "origin_workspace": binding["workspace"] if binding else None,
        "checkpoint_ref": binding["checkpoint_ref"] if binding else None,
        "checkpoint_content_digest": digest(checkpoint) if checkpoint else None,
        "records_digest": _rows_digest(rows), "workspace": manifest, "entry": envelope,
        "note_source_id": sid, "note_records": {rid: digest(note) for rid, note in notes.items()},
        "files": list(inventory.files.values()), "originals": inventory.originals,
        "publication_history": history, "publication_trees": trees, "skill_snapshots": skills, "skill_bundles": bundles,
        "counts": {"sources": len(manifest["sources"]), "records": count, "notes": len(notes),
                   "skill_bundles": len(bundles), "artifacts": artifacts, "members": members,
                   "files": len(inventory.files), "bytes": inventory.total},
        "continuation_at_export": {k: continuation[k] for k in ("issues", "warnings", "pending", "authority")},
        "discovery_scope": "notes for the entry task and memory scope, explicit references, originals and optional published workset",
        "memory_scope": value.get("memory_scope", "shared"),
        "history_coverage": {"notes": "all_task_scope_notes_in_snapshot_including_unpublished_heads",
                             "scope_boundary": "entry memory_scope; SQLite backup is not a confidential redacted export",
                             "unlinked_operations": "not_discovered_without_explicit_selection",
                             "external_jobs": "handles_preserved; jobs_not_copied_or_resumed"},
        "execution_permission": "not_granted", "automatic_retry": False})
    if len(canonical(plan).encode()) > MAX_MANIFEST_BYTES:
        raise EvidenceError("handoff plan exceeds bounded manifest size")
    _documents(plan)
    _verify_originals(plan)
    return plan


def verify_notes(entry, plan):
    value = entry_value(entry)
    journal = entry_journal(value)
    for rid, expected in plan["note_records"].items():
        if digest(read_note(journal, rid)["note"]) != expected:
            raise EvidenceError("handoff note content identity mismatch")
    for branch in value.get("branches", {value["branch_id"]: value["record_id"]}):
        read_entry(value, branch_id=branch)


def writable_note_journal(resolver, snapshot):
    """Continue a verified snapshot using the existing mutable-journal contract.

    The archive's source/workspace and original files remain unchanged. The
    caller's next save_progress advances only its current entry to the live copy.
    Reopening the same archived entry reuses this copy, including interrupted
    note publications; it never starts a fresh campaign or replays an action.
    """
    from .evidence_refs import EvidenceResolver, file_identity
    from .handoff import _plain
    from etalon.runtime.files import _consumer_lock, _copy, _read, _save
    ws, sid = resolver.workspace, resolver.source_id
    key = digest({"workspace": ws.manifest, "source_id": sid})[:24]
    root = _plain(ws.base)
    workspace = root / ("active-workspace-" + key + ".json")
    database = _plain(root / "working" / key / "journal.sqlite")
    with CampaignStore(snapshot, read_only=True).connection() as db:
        configuration = json.loads(db.execute("SELECT body FROM metadata WHERE key='configuration'").fetchone()[0])
    active = ws.manifest
    for item in active["sources"]:
        if item["source_id"] == sid:
            item["location"] = database.relative_to(root).as_posix()
            item["identity"] = {"kind": "journal_identity", "key": "configuration", "value": configuration}
            item.pop("record_ids", None)
    # Keep the workspace beside its archived parent, so all original file and
    # exact skill mappings retain the same base without copying their bytes.
    with _consumer_lock(workspace):
        if workspace.exists():
            if _read(workspace) != active:
                raise EvidenceError("active note workspace conflicts with the archived source")
        else:
            _copy(snapshot, database, file_identity(snapshot))
            _save(workspace, active)
    live = EvidenceResolver(workspace, sid)
    path = live.resolve_source(str(database))
    journal = ResearchJournal(CampaignStore(path), resolver=live)
    journal._note_parent_database = Path(snapshot).resolve()
    journal._note_entry_binding = {"database": str(database), "workspace": str(workspace), "source_id": sid}
    return journal
