"""Crash-resumable research publication, without scientific execution.

Explicit files and research steps enter an isolated ResearchJournal. An immutable
checkpoint/workspace is then published by compare-and-swap of one entry file.
The intent and stage receipts survive a process crash; replay only archives data.
"""
from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical, digest
from etalon.host import _consumer_lock
from .continuation import CHECKPOINT, STEP, MAX_CONTENT_BYTES, _content_result, _exact_refs, read_content, record_content
from .evidence_refs import (
    EvidenceError, _local, _text, file_identity, load_workspace, make_ref,
)
from .journal import ResearchJournal
from .schema import Artifact

INTENT = "etalon-research-publication/1"
STAGES = ("results_saved", "results_recorded", "steps_recorded", "checkpoint_recorded",
          "snapshot_saved", "workspace_saved", "entry_replaced", "complete")


def _read(path):
    path = Path(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise EvidenceError("publication metadata exceeds 4 MiB")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _save(path, value, *, replace=False):
    """Durable bytes; immutable by default. Only the index and entry are replaced."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (canonical(value) + "\n").encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".publication-", delete=False) as out:
        temporary = Path(out.name)
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    try:
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise EvidenceError("immutable publication metadata conflicts: " + path.name) from None
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _copy(source, target, identity):
    """A complete copy is recoverable even if the producer's original is gone."""
    target = Path(target)
    if target.exists():
        if file_identity(target) != identity:
            raise EvidenceError("saved publication result identity changed")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".publication-", delete=False) as out:
            temporary = Path(out.name)
            with Path(source).open("rb") as inp:
                shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        if file_identity(temporary) != identity:
            raise EvidenceError("producer result identity changed; no scientific retry performed")
        try:
            os.link(temporary, target)
        except FileExistsError:
            if file_identity(target) != identity:
                raise EvidenceError("saved publication result identity conflicts") from None
        _sync_directory(target.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _stage(root, phase, payload):
    """Persist one append-only boundary receipt (also a crash-injection seam)."""
    value = {"schema": "etalon-research-publication-stage/1", "phase": phase, **payload}
    _save(Path(root) / "stages" / f"{STAGES.index(phase) + 1:02d}-{phase}.json", value)
    return value


def _entry_identity(path):
    return file_identity(path) if Path(path).exists() else None


def _index_path(entry):
    return Path(str(entry) + ".publications.json")


def _resolve_aliases(value, refs):
    if isinstance(value, dict):
        if set(value) == {"publication_ref"}:
            try:
                return refs[value["publication_ref"]]
            except (KeyError, TypeError):
                raise EvidenceError("publication reference must name an earlier result or step") from None
        return {key: _resolve_aliases(item, refs) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_aliases(item, refs) for item in value]
    return value


def _frozen_sources(workspace):
    """No source is opened writable and no live/WAL journal is relabelled frozen."""
    ws = load_workspace(workspace)
    result = []
    for source in ws.manifest["sources"]:
        path = _local(ws.base, source["location"])
        if source["identity"].get("kind") != "snapshot_sha256":
            raise EvidenceError("publication parent requires frozen sources")
        identity = file_identity(path)
        if identity["sha256"] != source["identity"]["value"]:
            raise EvidenceError("publication parent source changed")
        wal = Path(str(path) + "-wal")
        if source["kind"] == "journal" and wal.exists() and wal.stat().st_size:
            raise EvidenceError("frozen parent journal has WAL content")
        result.append({"path": str(path), "identity": identity})
    return result


def prepare_publication(entry, directory=None, operation_id=None, expected_entry_identity=None, *,
                        parent_entry=None, results=(), steps=(), checkpoint_additions=None):
    """Persist explicit publication intent; do not write results or change entry.

    A missing destination entry requires ``parent_entry`` and expected identity
    None. For an existing entry, pass its exact file_identity. Reusing an operation
    id with different request content is rejected, even after successful publish.
    Results are declared files with {name,path,identity,source_kind,role,bindings}.
    Steps are {name,content}; {publication_ref: name} binds earlier named records.
    """
    from .research_context import load_entry
    entry = Path(entry).resolve()
    _text(operation_id)
    directory = Path(directory or entry.parent / ".etalon" / "publications").resolve()
    # Full request equality below detects a truncated-key collision. Keep native
    # Windows paths short enough for SQLite and hard-link APIs in ordinary roots.
    root = directory / digest({"entry": str(entry), "operation_id": operation_id})[:24]
    intent_path = root / "intent.json"
    parent = Path(parent_entry).resolve() if parent_entry is not None else entry
    request = {"entry": str(entry), "parent_entry": str(parent), "operation_id": operation_id,
               "expected_entry_identity": expected_entry_identity, "results": list(results),
               "steps": list(steps), "checkpoint_additions": checkpoint_additions or {}}
    request = json.loads(canonical(request))
    entry.parent.mkdir(parents=True, exist_ok=True)
    with _consumer_lock(entry.parent / (entry.name + ".publication")):
        index_path = _index_path(entry)
        if index_path.exists():
            index = _read(index_path)
            if index.get("entry") != str(entry) or not isinstance(index.get("intents"), list):
                raise EvidenceError("invalid publication index")
            for indexed in index["intents"]:
                indexed_path = Path(indexed)
                if indexed_path != intent_path and indexed_path.exists():
                    prior = _read(indexed_path)
                    if prior["request"]["operation_id"] == operation_id:
                        if prior["request"] != request:
                            raise EvidenceError("operation id already has different publication content")
                        return inspect_publication(indexed_path)
        if intent_path.exists():
            if _read(intent_path)["request"] != request:
                raise EvidenceError("operation id already has different publication content")
            _register(entry, intent_path)
            return inspect_publication(intent_path)
        if _entry_identity(entry) != expected_entry_identity:
            raise EvidenceError("entry compare-and-swap conflict before preparing publication")
        if not entry.exists() and parent_entry is None:
            raise EvidenceError("a new destination entry requires an explicit parent_entry")
        if entry.exists() and parent != entry:
            raise EvidenceError("an existing entry must continue its own selected parent")
        parent_identity = file_identity(parent)
        workspace_path, checkpoint_ref = load_entry(parent)
        workspace_path = Path(workspace_path).resolve()
        ws = load_workspace(workspace_path)
        protected = _frozen_sources(ws)
        checkpoint = read_content(ws, checkpoint_ref)
        if checkpoint["schema"] != CHECKPOINT:
            raise EvidenceError("publication parent must be a checkpoint")
        for guard in checkpoint["status_guards"]:
            if not Path(guard["location"]).is_absolute():
                raise EvidenceError("publication requires absolute authority locations")
        for authority in ws.manifest.get("status_refs", []):
            location = authority if isinstance(authority, str) else authority.get("location")
            if not isinstance(location, str) or not Path(location).is_absolute():
                raise EvidenceError("publication requires absolute status references")
        if len(results) > 32 or not 1 <= len(steps) <= 16:
            raise EvidenceError("publication requires 1..16 explicit steps and at most 32 results")
        names = set()
        for item in [*results, *steps]:
            name = item.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name) or name in names:
                raise EvidenceError("publication names must be unique bounded identifiers")
            names.add(name)
        for item in results:
            if set(item) != {"name", "path", "identity", "source_kind", "role", "bindings"}:
                raise EvidenceError("result requires explicit path, identity, provenance, role and bindings")
            if not Path(item["path"]).is_absolute() or file_identity(item["path"]) != item["identity"]:
                raise EvidenceError("result must identify existing exact file bytes by absolute path")
            Artifact(item["path"], **item["identity"], source_kind=item["source_kind"],
                     role=item["role"], bindings=item["bindings"])
        for item in steps:
            if set(item) != {"name", "content"} or item["content"].get("schema") != STEP:
                raise EvidenceError("steps must be explicit research-step content")
            if item["content"].get("task_id") != checkpoint["task_id"]:
                raise EvidenceError("publication step must remain in the parent task")
        if set(request["checkpoint_additions"]) - {"claim_refs", "counter_refs", "method_refs"}:
            raise EvidenceError("publication cannot silently change branches, pending, guards or research scope")
        if any(not isinstance(items, list) for items in request["checkpoint_additions"].values()):
            raise EvidenceError("checkpoint additions must be explicit reference lists")
        source_id = "publication-" + digest({"root": str(root), "operation_id": operation_id})[:20]
        if source_id in {source["source_id"] for source in ws.manifest["sources"]}:
            raise EvidenceError("publication source identity conflicts with parent")
        intent = {"schema": INTENT, "request": request, "source_id": source_id,
                  "parent_entry_identity": parent_identity, "parent_workspace": str(workspace_path),
                  "parent_workspace_identity": file_identity(workspace_path),
                  "parent_checkpoint_ref": checkpoint_ref, "parent_checkpoint": checkpoint,
                  "protected_sources": protected}
        if len(canonical(intent).encode()) > 4 * 1024 * 1024:
            raise EvidenceError("publication intent exceeds 4 MiB")
        # Publish discoverability first: death before the intent becomes durable
        # leaves an explicit unavailable operation, never a false empty frontier.
        _register(entry, intent_path)
        _save(intent_path, intent)
        return inspect_publication(intent_path)


def _register(entry, intent_path):
    path = _index_path(entry)
    index = _read(path) if path.exists() else {"schema": "etalon-research-publication-index/1",
                                               "entry": str(entry), "intents": []}
    if index.get("entry") != str(entry) or index.get("schema") != "etalon-research-publication-index/1":
        raise EvidenceError("publication index does not belong to this entry")
    if str(intent_path) not in index["intents"]:
        if len(index["intents"]) >= 256:
            raise EvidenceError("publication index full; explicit archival required")
        index["intents"].append(str(intent_path))
        _save(path, index, replace=True)


def _verify_parent(intent, *, check_entry=True):
    request = intent["request"]
    if (check_entry and request["parent_entry"] != request["entry"]
            and file_identity(request["parent_entry"]) != intent["parent_entry_identity"]):
        raise EvidenceError("parent entry changed after publication intent")
    if file_identity(intent["parent_workspace"]) != intent["parent_workspace_identity"]:
        raise EvidenceError("parent workspace changed after intent")
    protected = _frozen_sources(intent["parent_workspace"])
    if protected != intent["protected_sources"]:
        raise EvidenceError("parent frozen sources changed after intent")
    return load_workspace(intent["parent_workspace"])


def _verify_external(content, source_id, ws):
    from .evidence import _collect, _read_evidence
    refs = {canonical(ref): ref for ref in _exact_refs(content) if ref["source_id"] != source_id}
    if not refs:
        return
    collection = _collect(ws, list(dict.fromkeys(ref["source_id"] for ref in refs.values())))
    for ref in refs.values():
        result = _read_evidence(ws, ref, view="record", collection=collection)
        if result.get("verification", {}).get("record") != "verified":
            raise EvidenceError("external publication reference is not verified")
        if ref["record_id"].startswith("artifact-"):
            result = _read_evidence(ws, ref, view="artifact", limit=1, collection=collection)
            if result.get("verification", {}).get("artifact") != "verified":
                raise EvidenceError("external publication artifact is not verified")
    typed = []
    if content["schema"] == CHECKPOINT:
        typed.extend((ref, STEP) for ref in content["step_refs"])
        typed.extend((ref, "etalon-research-claim/1") for ref in content["claim_refs"])
    if content.get("operation") == "judgment":
        typed.extend((ref, "etalon-research-claim/1") for ref in content["detail"]["claim_refs"])
    typed.extend((ref, content["schema"]) for ref in content.get("supersedes", []))
    for ref, schema in typed:
        if ref["source_id"] != source_id:
            result = _read_evidence(ws, ref, view="artifact", limit=MAX_CONTENT_BYTES + 1, collection=collection)
            if _content_result(result)["schema"] != schema:
                raise EvidenceError("external publication reference has the wrong content schema")


def _verify_published(root, intent, *, check_parent_entry=False):
    """A saved pointer alone is insufficient for an idempotent-success receipt."""
    _verify_parent(intent, check_entry=check_parent_entry)
    entry = _read(root / "entry.json")
    workspace_receipt = _read(root / "stages" / "06-workspace_saved.json")
    snapshot_receipt = _read(root / "stages" / "05-snapshot_saved.json")
    for path, identity in ((root / "entry.json", workspace_receipt["entry_identity"]),
                           (Path(entry["workspace"]), workspace_receipt["workspace_identity"]),
                           (root / "snapshot.sqlite", snapshot_receipt["snapshot_identity"]),
                           (root / "research.sqlite", snapshot_receipt["journal_identity"])):
        if file_identity(path) != identity:
            raise EvidenceError("published archival bytes changed")
    for item in intent["request"]["results"]:
        if file_identity(root / "results" / item["identity"]["sha256"]) != item["identity"]:
            raise EvidenceError("published result bytes changed")
    checkpoint = read_content(entry["workspace"], entry["checkpoint_ref"])
    ws = load_workspace(entry["workspace"])
    _verify_external(checkpoint, None, ws)
    for ref in checkpoint["step_refs"]:
        content = read_content(ws, ref)
        _verify_external(content, None, ws)


def _workspace(intent, root, snapshot):
    ws = load_workspace(intent["parent_workspace"])
    manifest = ws.manifest
    for source in manifest["sources"]:
        source["location"] = str(_local(ws.base, source["location"]))
    for mapping in manifest.get("location_map", []):
        identity = {key: mapping[key] for key in ("sha256", "bytes")}
        destination = root / "mapped" / mapping["sha256"]
        _copy(_local(ws.base, mapping["location"], confined=True), destination, identity)
        mapping["location"] = str(destination)
    manifest["sources"].append({"source_id": intent["source_id"], "kind": "journal",
        "location": str(snapshot), "identity": {"kind": "snapshot_sha256", "value": file_identity(snapshot)["sha256"]}})
    # Relocated authority mappings remain live authority reads for the new CP.
    # Existing absolute local guards work directly; foreign guards need an explicit
    # current-host authority path rather than a frozen copy of an authority file.
    for guard in intent["parent_checkpoint"]["status_guards"]:
        if not Path(guard["location"]).is_file():
            raise EvidenceError("publication needs a local current authority; do not freeze a relocated authority")
    manifest["workspace_id"] += "/" + intent["source_id"]
    path = root / "workspace.json"
    _save(path, manifest)
    load_workspace(path)
    return path


def resume_publication(intent_path):
    """Finish archival stages, then CAS the entry. Never execute/retry research."""
    intent_path = Path(intent_path).resolve(strict=True)
    intent = _read(intent_path)
    if intent.get("schema") != INTENT:
        raise EvidenceError("unsupported publication intent")
    root, request = intent_path.parent, intent["request"]
    entry = Path(request["entry"])
    with _consumer_lock(entry.parent / (entry.name + ".publication")):
        state = inspect_publication(intent_path)
        if state["status"] in {"published", "superseded"}:
            _verify_published(root, intent)
            # The pointer may have been replaced immediately before process death.
            if not (root / "stages" / "08-complete.json").exists():
                _stage(root, "entry_replaced", {"entry_identity": state["published_entry_identity"]})
                _stage(root, "complete", {"entry_identity": state["published_entry_identity"]})
            return inspect_publication(intent_path)
        if state["status"] == "conflict":
            raise EvidenceError("entry compare-and-swap conflict; preserved publication requires explicit rebase")
        ws = _verify_parent(intent)
        refs = {}
        saved_results = []
        for item in request["results"]:
            destination = root / "results" / item["identity"]["sha256"]
            _copy(item["path"], destination, item["identity"])
            saved_results.append({**item, "saved_path": str(destination)})
        _stage(root, "results_saved", {"results": saved_results})
        store = CampaignStore(root / "research.sqlite")
        store.configure(CampaignSpec("archive", 0, "wall_second", "research-publication/1"),
            [Endpoint("archive", intent["parent_checkpoint"]["task_id"], "archival_only", "count",
                      "research-publication/1", 0, queryable=False)])
        journal = ResearchJournal(store)
        for item in saved_results:
            value = Artifact(item["saved_path"], **item["identity"], source_kind=item["source_kind"],
                             role=item["role"], bindings=item["bindings"])
            identifier = journal.record(value)
            refs[item["name"]] = make_ref(intent["source_id"], identifier, journal.get(identifier))
        _stage(root, "results_recorded", {"refs": refs})
        step_refs = []
        for item in request["steps"]:
            content = _resolve_aliases(item["content"], refs)
            _verify_external(content, intent["source_id"], ws)
            identifier = record_content(journal, content, root / "content", source_id=intent["source_id"],
                                        source_kind="existing_real_artifact")
            ref = make_ref(intent["source_id"], identifier, journal.get(identifier))
            refs[item["name"]] = ref
            step_refs.append(ref)
        _stage(root, "steps_recorded", {"refs": refs, "step_refs": step_refs})
        checkpoint = json.loads(canonical(intent["parent_checkpoint"]))
        checkpoint["step_refs"].extend(ref for ref in step_refs if ref not in checkpoint["step_refs"])
        for key, additions in request["checkpoint_additions"].items():
            for ref in _resolve_aliases(additions, refs):
                if ref not in checkpoint[key]:
                    checkpoint[key].append(ref)
        checkpoint["supersedes"] = [intent["parent_checkpoint_ref"]]
        _verify_external(checkpoint, intent["source_id"], ws)
        identifier = record_content(journal, checkpoint, root / "content", source_id=intent["source_id"],
                                    source_kind="existing_real_artifact")
        checkpoint_ref = make_ref(intent["source_id"], identifier, journal.get(identifier))
        _stage(root, "checkpoint_recorded", {"checkpoint_ref": checkpoint_ref, "refs": refs})
        snapshot = root / "snapshot.sqlite"
        if not snapshot.exists():
            temporary = root / "snapshot.pending.sqlite"
            with journal.store.connection() as source, closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.link(temporary, snapshot)
            _sync_directory(root)
        _stage(root, "snapshot_saved", {"journal_identity": file_identity(store.path),
                                        "snapshot_identity": file_identity(snapshot)})
        workspace = _workspace(intent, root, snapshot)
        new_entry = {"workspace": str(workspace), "checkpoint_ref": checkpoint_ref,
                     "publication": {"operation_id": request["operation_id"], "intent_path": str(intent_path)}}
        _save(root / "entry.json", new_entry)
        published_identity = file_identity(root / "entry.json")
        _stage(root, "workspace_saved", {"workspace_identity": file_identity(workspace),
                                         "entry_identity": published_identity})
        _verify_published(root, intent, check_parent_entry=True)
        # Check again after all durable data has been saved. The shared per-entry
        # OS lock serializes cooperating publishers; hash CAS detects stale parents.
        _verify_parent(intent)
        if _entry_identity(entry) != request["expected_entry_identity"]:
            raise EvidenceError("entry compare-and-swap conflict; all archived data preserved")
        _save(entry, new_entry, replace=True)
        _stage(root, "entry_replaced", {"entry_identity": published_identity})
        _stage(root, "complete", {"entry_identity": published_identity})
        return inspect_publication(intent_path)


def inspect_publication(intent_path):
    """Observe metadata, pointer identity and progress. Does not repair anything."""
    path = Path(intent_path).resolve(strict=True)
    intent = _read(path)
    if intent.get("schema") != INTENT:
        raise EvidenceError("unsupported publication intent")
    request, root = intent["request"], path.parent
    receipts = [_read(p) for p in sorted((root / "stages").glob("*.json"))]
    phase = receipts[-1]["phase"] if receipts else "prepared"
    current = _entry_identity(request["entry"])
    published = root / "entry.json"
    published_identity = file_identity(published) if published.exists() else None
    completed = any(r["phase"] in {"entry_replaced", "complete"} for r in receipts)
    if published_identity is not None and current == published_identity:
        status = "published"
    elif completed:
        status = "superseded"
    elif current != request["expected_entry_identity"]:
        status = "conflict"
    else:
        status = "in_progress" if receipts else "prepared"
    checkpoint_ref = next((r["checkpoint_ref"] for r in receipts if r["phase"] == "checkpoint_recorded"), None)
    return {"schema": "etalon-research-publication-status/1", "operation_id": request["operation_id"],
        "intent_path": str(path), "entry": request["entry"], "source_id": intent["source_id"],
        "status": status, "phase": phase, "needs_recovery": status in {"prepared", "in_progress", "conflict"},
        "checkpoint_ref": checkpoint_ref, "entry_conflict": status == "conflict",
        "published_entry_identity": published_identity, "current_entry_identity": current,
        "stages": [r["phase"] for r in receipts], "automatic_retry": False,
        "execution_permission": "not_granted", "verification": "metadata_only_use_resume_for_full_verification"}


def inspect_publications(directory, *, entry=None):
    """Read explicitly selected publication directory; never traverse elsewhere."""
    result = []
    for path in sorted(Path(directory).glob("*/intent.json")):
        state = inspect_publication(path)
        if entry is None or state["entry"] == str(Path(entry).resolve()):
            result.append(state)
    return {"schema": "etalon-research-publications/1", "operations": result,
            "incomplete_count": sum(s["needs_recovery"] for s in result)}


def discover_publications(entry):
    """Only the entry's explicit sidecar index is read; suitable for read-only hooks."""
    entry = Path(entry).resolve()
    path = _index_path(entry)
    operations = []
    if path.exists():
        index = _read(path)
        if index.get("schema") != "etalon-research-publication-index/1" or index.get("entry") != str(entry):
            raise EvidenceError("invalid publication index")
        if not isinstance(index.get("intents"), list) or len(index["intents"]) > 256:
            raise EvidenceError("publication index exceeds bounded read")
        for intent in index["intents"]:
            try:
                state = inspect_publication(intent)
                if state["entry"] != str(entry):
                    raise EvidenceError("indexed intent belongs to another entry")
                operations.append(state)
            except (OSError, ValueError, KeyError) as exc:
                operations.append({"intent_path": intent, "status": "unavailable", "needs_recovery": True,
                                   "reason": type(exc).__name__, "automatic_retry": False})
    return {"schema": "etalon-research-publications/1", "entry": str(entry), "operations": operations,
            "incomplete_count": sum(s["needs_recovery"] for s in operations), "automatic_retry": False}
