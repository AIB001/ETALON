"""Crash-resumable research publication, without scientific execution.

Explicit files and research steps enter an isolated ResearchJournal. An immutable
checkpoint/workspace is then published by compare-and-swap of one entry file.
The intent and stage receipts survive a process crash; replay only archives data.
"""
from __future__ import annotations

import json
import hashlib
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
from .continuation import (CHECKPOINT, STEP, MAX_CONTENT_BYTES, _content_result, _exact_refs,
                           read_content, record_content, validate_content)
from .evidence_refs import (
    EvidenceError, _local, _text, file_identity, load_workspace, make_ref,
)
from .journal import ResearchJournal
from .schema import Artifact

INTENT = "etalon-research-publication/1"
STAGES = ("results_saved", "results_recorded", "steps_recorded", "checkpoint_recorded",
          "snapshot_saved", "workspace_saved", "entry_replaced", "complete")
DISPOSITION = "etalon-research-publication-disposition/1"
TERMINAL_DISPOSITIONS = {"withdrawn", "replaced"}


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
        if "publication_ref" in value:
            if set(value) != {"publication_ref"}:
                raise EvidenceError("publication alias must contain only publication_ref")
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
                        parent_entry=None, results=(), steps=(), checkpoint_additions=None,
                        corrects_intent=None, correction_reason=None, branch_updates=None):
    """Persist explicit publication intent; do not write results or change entry.

    A missing destination entry requires ``parent_entry`` and expected identity
    None. For an existing entry, pass its exact file_identity. Reusing an operation
    id with different request content is rejected, even after successful publish.
    Results are declared files with {name,path,identity,source_kind,role,bindings}.
    Steps are {name,content}; {publication_ref: name} binds earlier named records.
    Optional branch_updates bind existing branch transitions to a new decision
    step. They never change authority, pending handles or task scope.
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
    if branch_updates is not None:
        request["branch_updates"] = branch_updates
    # Keep absent optional fields absent: old intent requests remain byte-for-byte
    # compatible with exact replay of the original API.
    if corrects_intent is not None or correction_reason is not None:
        if (corrects_intent is None or not isinstance(correction_reason, str)
                or not correction_reason.strip() or len(correction_reason) > 4096):
            raise EvidenceError("a correction requires an old intent and an explicit reason")
        request["corrects_intent"] = str(Path(corrects_intent).resolve(strict=True))
        request["correction_reason"] = correction_reason
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
            if not isinstance(item, dict):
                raise EvidenceError("publication results and steps must be explicit objects")
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
            if (set(item) != {"name", "content"} or not isinstance(item["content"], dict)
                    or item["content"].get("schema") != STEP):
                raise EvidenceError("steps must be explicit research-step content")
            if item["content"].get("task_id") != checkpoint["task_id"]:
                raise EvidenceError("publication step must remain in the parent task")
        if set(request["checkpoint_additions"]) - {"claim_refs", "counter_refs", "method_refs"}:
            raise EvidenceError("publication cannot silently change branches, pending, guards or research scope")
        if any(not isinstance(items, list) for items in request["checkpoint_additions"].values()):
            raise EvidenceError("checkpoint additions must be explicit reference lists")
        if "branch_updates" in request and (not isinstance(request["branch_updates"], list)
                or not 1 <= len(request["branch_updates"]) <= 16):
            raise EvidenceError("branch updates require 1..16 explicit decisions")
        source_id = "publication-" + digest({"root": str(root), "operation_id": operation_id})[:20]
        if source_id in {source["source_id"] for source in ws.manifest["sources"]}:
            raise EvidenceError("publication source identity conflicts with parent")
        correction = None
        if corrects_intent is not None:
            old_path = Path(request["corrects_intent"])
            old = _read(old_path)
            old_status = inspect_publication(old_path)
            if (old["request"]["entry"] != str(entry)
                    or old["parent_checkpoint"]["task_id"] != checkpoint["task_id"]):
                raise EvidenceError("correction must remain in the same destination entry and task")
            if old_status["status"] in {"published", "superseded", "withdrawn", "replaced"}:
                raise EvidenceError("correction requires an unfinished publication")
            correction = {"intent_path": str(old_path), "identity": file_identity(old_path),
                          "operation_id": old["request"]["operation_id"]}
        # All shape/enumeration, alias-order, existing/local typed links and the
        # final aggregated checkpoint are checked without a temporary database or
        # copied result. Runtime-dependent availability is still rechecked on resume.
        try:
            _preflight(request, root, source_id, checkpoint, checkpoint_ref, ws)
        except (KeyError, TypeError, AttributeError, IndexError) as exc:
            raise EvidenceError("invalid publication content or reference: " + type(exc).__name__) from exc
        intent = {"schema": INTENT, "request": request, "source_id": source_id,
                  "parent_entry_identity": parent_identity, "parent_workspace": str(workspace_path),
                  "parent_workspace_identity": file_identity(workspace_path),
                  "parent_checkpoint_ref": checkpoint_ref, "parent_checkpoint": checkpoint,
                  "protected_sources": protected}
        if correction is not None:
            intent["corrects"] = correction
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


def _verify_external(content, source_id, ws, *, collection=None):
    from .evidence import _collect, _read_evidence
    refs = {canonical(ref): ref for ref in _exact_refs(content) if ref["source_id"] != source_id}
    if not refs:
        return
    if collection is None:
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


def _advance_checkpoint(request, parent, parent_ref, refs, step_refs):
    """Apply only explicit, evidence-bound updates; no interpretation of output prose.

    The same pure transition is used before persistence and during crash recovery.
    A decision changes research state, never execution authority or runtime state.
    """
    checkpoint = json.loads(canonical(parent))
    checkpoint["step_refs"].extend(ref for ref in step_refs if ref not in checkpoint["step_refs"])
    for key, additions in request["checkpoint_additions"].items():
        for ref in _resolve_aliases(additions, refs):
            if ref not in checkpoint[key]:
                checkpoint[key].append(ref)
    branches = {b["branch_id"]: b for b in checkpoint["branches"]}
    decisions = {canonical(refs[item["name"]]): _resolve_aliases(item["content"], refs)
                 for item in request["steps"]}
    seen = set()
    for raw in request.get("branch_updates", []):
        if not isinstance(raw, dict) or set(raw) != {
            "branch_id", "expected_state", "state", "basis_refs", "next_read_refs",
            "reconsider_when", "decision_ref"
        }:
            raise EvidenceError("branch update requires state, exact prior state, evidence and a decision reference")
        update = _resolve_aliases(raw, refs)
        bid = update["branch_id"]
        if not isinstance(bid, str) or bid not in branches or bid in seen:
            raise EvidenceError("branch update must name a distinct existing branch")
        seen.add(bid)
        old = branches[bid]
        if update["expected_state"] != old["state"]:
            raise EvidenceError("branch update prior state differs from selected checkpoint")
        decision = decisions.get(canonical(update["decision_ref"]))
        if (decision is None or decision.get("operation") != "decision"
                or decision.get("branch_id") != bid or decision.get("task_id") != parent["task_id"]):
            raise EvidenceError("branch transition requires a new same-branch decision step")
        detail = decision["detail"]
        allowed = {"open": {"reconsider", "fork"}, "active": {"proceed", "reconsider", "fork"},
                   "waiting": {"defer"}, "resolved": {"reuse", "stop", "merge"}, "stopped": {"stop"}}
        if update["state"] not in allowed or detail["choice"] not in allowed[update["state"]]:
            raise EvidenceError("branch state does not match its explicit decision")
        if old["state"] in {"resolved", "stopped"} and update["state"] in {"open", "active", "waiting"}:
            if detail["choice"] != "reconsider":
                raise EvidenceError("terminal branch reopening requires explicit reconsideration")
        if (update["basis_refs"] != detail["basis_refs"]
                or update["reconsider_when"] != detail["reconsider_when"]):
            raise EvidenceError("branch evidence and reopening condition must match the decision")
        if not isinstance(update["next_read_refs"], list) or any(
                ref not in update["basis_refs"] for ref in update["next_read_refs"]):
            raise EvidenceError("branch next reads must be selected from its decision evidence")
        old.update({k: update[k] for k in ("state", "basis_refs", "next_read_refs", "reconsider_when")})
        # Keep the author and reason directly reachable from the branch as well
        # as the append-only step list; the evidence itself is not replaced.
        if update["decision_ref"] not in old["basis_refs"]:
            old["basis_refs"].append(update["decision_ref"])
    checkpoint["supersedes"] = [parent_ref]
    return validate_content(checkpoint)


class _PreflightJournal:
    """Read-only in-memory records for the existing composition link validator."""

    def __init__(self):
        self.records, self.contents, self.paths = {}, {}, {}

    def get(self, identifier):
        try:
            return self.records[identifier]
        except KeyError:
            raise EvidenceError("publication reference does not name an earlier record") from None

    def artifact_path(self, artifact):
        if artifact.id in self.contents:
            text = canonical(self.contents[artifact.id])

            class Content:
                def read_text(self, *args, **kwargs):
                    return text

            return Content()
        path = self.paths.get(artifact.id)
        if path is None or file_identity(path) != {"sha256": artifact.sha256, "bytes": artifact.bytes}:
            raise EvidenceError("preflight result changed or cannot be read")
        return path


def _preflight(request, root, source_id, parent_checkpoint, parent_ref, ws):
    """Resolve the exact future records, validate them, and persist nothing.

    No synthetic journal, action, observation or handle is created. Artifact
    identity construction matches record_content; that function remains the
    authoritative writer and revalidates dynamic conditions during recovery.
    """
    from .evidence import _collect, _read_evidence, _visible
    from .evidence_refs import CONTENT_SCHEMAS
    from .continuation import _check_frozen_view
    collection = _collect(ws)
    journal, refs, step_refs = _PreflightJournal(), {}, []

    def body(ref):
        if ref["source_id"] == source_id:
            return journal.get(ref["record_id"])
        return _read_evidence(ws, ref, view="record", collection=collection)["record"]

    def validate_links(content):
        _verify_external(content, source_id, ws, collection=collection)
        from .continuation import validate_revision_links
        validate_revision_links(journal, content, source_id, external_workspace=ws)
        local = {}
        for ref in _exact_refs(content):
            if ref["source_id"] != source_id:
                continue
            value = journal.get(ref["record_id"])
            if digest(value) != ref["record_digest"] or not _visible(value):
                raise EvidenceError("local publication reference is changed or not visible")
            if "member" in ref:
                raise EvidenceError("local member refs require an explicitly published source envelope")
            if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (value["sha256"], value["bytes"]):
                raise EvidenceError("local publication artifact identity mismatch")
            local[ref["record_id"]] = value
        typed = [(ref, content["schema"]) for ref in content.get("supersedes", [])]
        if content["schema"] == CHECKPOINT:
            typed.extend((ref, STEP) for ref in content["step_refs"])
            typed.extend((ref, "etalon-research-claim/1") for ref in content["claim_refs"])
        elif content.get("operation") == "judgment":
            typed.extend((ref, "etalon-research-claim/1") for ref in content["detail"]["claim_refs"])
        for ref, schema in typed:
            if ref["source_id"] == source_id:
                value = journal.get(ref["record_id"])
                if value.get("bindings", {}).get("schema") != schema:
                    raise EvidenceError("local publication reference has the wrong content schema")
                payload = validate_content(json.loads(journal.artifact_path(Artifact(**value)).read_text("utf-8-sig")))
                if payload["schema"] != schema:
                    raise EvidenceError("local publication artifact differs from its declared schema")
        if (content.get("operation") in {"node", "evaluation_plan", "evaluation"}
                or content["schema"] == "etalon-research-workflow/1"):
            from .composition import validate_local_links
            validate_local_links(journal, content, source_id, local, external_workspace=ws)
        if content.get("operation") == "result":
            detail = content["detail"]
            experiments = {(ref["source_id"], ref["record_id"]) for ref in detail["experiment_refs"]}
            if any(not ref["record_id"].startswith("experiment-") for ref in detail["experiment_refs"]):
                raise EvidenceError("result requires existing Experiment references")
            for ref in detail["observation_refs"]:
                if (not ref["record_id"].startswith("observation-") or
                        (ref["source_id"], body(ref).get("experiment_id")) not in experiments):
                    raise EvidenceError("result Observation must belong to the referenced Experiment")

    def remember(content):
        content = validate_content(content)
        validate_links(content)
        data = (canonical(content) + "\n").encode()
        external = any(ref["source_id"] != source_id for ref in _exact_refs(content))
        artifact = Artifact(str(root / "content" / (digest(content) + ".json")),
            sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), source_kind="existing_real_artifact",
            role="diagnostic", bindings={"schema": content["schema"], "source_id": source_id,
                "task_id": content.get("task_id", ""),
                "reference_verification": "local_checked_external_unresolved" if external else "local_checked"})
        journal.records[artifact.id] = artifact.as_dict()
        journal.contents[artifact.id] = content
        return make_ref(source_id, artifact.id, artifact.as_dict())

    result_contents, opportunity_heads = [], {}
    for item in request["results"]:
        artifact = Artifact(str(root / "results" / item["identity"]["sha256"]), **item["identity"],
            source_kind=item["source_kind"], role=item["role"], bindings=item["bindings"])
        journal.records[artifact.id] = artifact.as_dict()
        journal.paths[artifact.id] = Path(item["path"])
        refs[item["name"]] = make_ref(source_id, artifact.id, artifact.as_dict())
        declared = item["bindings"].get("schema")
        if declared in CONTENT_SCHEMAS:
            payload = validate_content(json.loads(journal.artifact_path(artifact).read_text("utf-8-sig")))
            if payload["schema"] != declared:
                raise EvidenceError("result content differs from its declared schema")
            from .opportunity_state import DISPOSITION as STATE_DISPOSITION, REOPEN as STATE_REOPEN
            if declared in {STATE_DISPOSITION, STATE_REOPEN}:
                previous = (payload["previous_state_ref"] if declared == STATE_DISPOSITION
                            else payload["previous_terminal_ref"])
                key = canonical([payload["opportunity_ref"], previous])
                ref = refs[item["name"]]
                if key in opportunity_heads and opportunity_heads[key] != ref:
                    raise EvidenceError("ambiguous concurrent opportunity heads in publication results")
                opportunity_heads[key] = ref
            result_contents.append(payload)
    for content in result_contents:
        validate_links(content)
    for item in request["steps"]:
        ref = remember(_resolve_aliases(item["content"], refs))
        refs[item["name"]] = ref
        step_refs.append(ref)
    checkpoint = _advance_checkpoint(request, parent_checkpoint, parent_ref, refs, step_refs)
    remember(checkpoint)
    _check_frozen_view(ws, *collection[:2])


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
    ws = load_workspace(entry["workspace"])
    from .evidence import _collect, _read_evidence
    from .continuation import _check_frozen_view
    collection = None
    if all(s["identity"]["kind"] == "snapshot_sha256" for s in ws.manifest["sources"]):
        candidate = _collect(ws, [entry["checkpoint_ref"]["source_id"]])
        if not candidate[2] and all(c["status"] == "complete" for c in candidate[1]):
            collection = candidate
    def content(ref):
        if collection is None:
            return read_content(ws, ref)
        return _content_result(_read_evidence(ws, ref, view="artifact",
            limit=MAX_CONTENT_BYTES + 1, collection=collection))
    checkpoint = content(entry["checkpoint_ref"])
    _verify_external(checkpoint, None, ws, collection=collection)
    for ref in checkpoint["step_refs"]:
        _verify_external(content(ref), None, ws, collection=collection)
    if collection is not None:
        _check_frozen_view(ws, *collection[:2])


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
    # Skill roots are canonical workspace-relative locators, not ordinary
    # location_map entries. Preserve each pinned version when changing base;
    # never substitute an installed bundle or retain the old relative root.
    if manifest.get("skill_bundle_map"):
        from etalon import skills
        from .skill_sources import _root as bundle_root
        for mapping in manifest["skill_bundle_map"]:
            source_root = bundle_root(ws.base, mapping["root"])
            pinned = {"root": str(source_root), "manifest_sha256": mapping["manifest_sha256"]}
            version, _ = skills.snapshot(mapping["slug"], bundle_ref=pinned)
            relative = "skills/" + mapping["manifest_sha256"] + "/bundle"
            destination = bundle_root(root, relative)
            for member in version["files"]:
                target = bundle_root(root, relative + "/" + member["path"])
                _copy(source_root / member["path"], target,
                      {key: member[key] for key in ("sha256", "bytes")})
            # Recheck both inventories, including on replay where _copy may
            # already have complete targets. Extra/changed members fail closed.
            skills.snapshot(mapping["slug"], bundle_ref=pinned)
            skills.snapshot(mapping["slug"], bundle_ref={
                "root": str(destination), "manifest_sha256": mapping["manifest_sha256"]})
            mapping["root"] = relative
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
        if state["status"] in TERMINAL_DISPOSITIONS:
            raise EvidenceError("terminal publication cannot be resumed; create an explicit new operation")
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
            from .evidence_refs import CONTENT_SCHEMAS
            if item["bindings"].get("schema") in CONTENT_SCHEMAS:
                content = validate_content(json.loads(journal.artifact_path(value).read_text("utf-8-sig")))
                if content["schema"] != item["bindings"]["schema"]:
                    raise EvidenceError("result content differs from its declared schema")
                _verify_external(content, intent["source_id"], ws)
                from .continuation import validate_revision_links
                validate_revision_links(journal, content, intent["source_id"], external_workspace=ws)
            identifier = journal.record(value)
            refs[item["name"]] = make_ref(intent["source_id"], identifier, journal.get(identifier))
        _stage(root, "results_recorded", {"refs": refs})
        step_refs = []
        for item in request["steps"]:
            content = _resolve_aliases(item["content"], refs)
            _verify_external(content, intent["source_id"], ws)
            identifier = record_content(journal, content, root / "content", source_id=intent["source_id"],
                                        source_kind="existing_real_artifact", external_workspace=ws)
            ref = make_ref(intent["source_id"], identifier, journal.get(identifier))
            refs[item["name"]] = ref
            step_refs.append(ref)
        _stage(root, "steps_recorded", {"refs": refs, "step_refs": step_refs})
        checkpoint = _advance_checkpoint(request, intent["parent_checkpoint"],
                                         intent["parent_checkpoint_ref"], refs, step_refs)
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
    disposition_path = root / "disposition.json"
    disposition = None
    if disposition_path.exists():
        disposition = _read(disposition_path)
        if (disposition.get("schema") != DISPOSITION
                or disposition.get("request", {}).get("disposition") not in TERMINAL_DISPOSITIONS
                or disposition.get("request", {}).get("intent_identity") != file_identity(path)
                or disposition.get("operation_id") != request["operation_id"]
                or disposition.get("entry") != request["entry"]):
            raise EvidenceError("invalid or mismatched terminal publication disposition")
        if status in {"published", "superseded"}:
            raise EvidenceError("terminal disposition conflicts with a successful publication")
        status = disposition["request"]["disposition"]
    checkpoint_ref = next((r["checkpoint_ref"] for r in receipts if r["phase"] == "checkpoint_recorded"), None)
    return {"schema": "etalon-research-publication-status/1", "operation_id": request["operation_id"],
        "intent_path": str(path), "entry": request["entry"], "source_id": intent["source_id"],
        "status": status, "phase": phase, "needs_recovery": status in {"prepared", "in_progress", "conflict"},
        "checkpoint_ref": checkpoint_ref, "entry_conflict": status == "conflict",
        "intent_identity": file_identity(path), "terminal": disposition is not None,
        "disposition": disposition,
        "published_entry_identity": published_identity, "current_entry_identity": current,
        "stages": [r["phase"] for r in receipts], "automatic_retry": False,
        "execution_permission": "not_granted", "verification": "metadata_only_use_resume_for_full_verification"}


def close_publication(intent_path, *, disposition, reason, expected_intent_identity, replacement_intent=None):
    """Explicitly finish an unsuccessful publication, retaining all historical bytes.

    ``withdrawn`` needs a reason. ``replaced`` additionally requires a prepared
    correction operation that names this exact intent. Neither action publishes
    the replacement or resumes science. A successful publication cannot be closed
    as a failure, including the pointer-replaced/before-receipt crash window.
    """
    if disposition not in TERMINAL_DISPOSITIONS:
        raise EvidenceError("publication disposition must be withdrawn or replaced")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
        raise EvidenceError("terminal publication requires an explicit bounded reason")
    if (disposition == "replaced") != (replacement_intent is not None):
        raise EvidenceError("only replaced disposition requires a replacement intent")
    path = Path(intent_path).resolve(strict=True)
    intent = _read(path)
    if intent.get("schema") != INTENT or file_identity(path) != expected_intent_identity:
        raise EvidenceError("terminal publication intent identity mismatch")
    entry = Path(intent["request"]["entry"])
    with _consumer_lock(entry.parent / (entry.name + ".publication")):
        if file_identity(path) != expected_intent_identity:
            raise EvidenceError("terminal publication intent changed during review")
        state = inspect_publication(path)
        replacement = None
        if replacement_intent is not None:
            replacement_path = Path(replacement_intent).resolve(strict=True)
            candidate = _read(replacement_path)
            expected_correction = {"intent_path": str(path), "identity": expected_intent_identity,
                                   "operation_id": intent["request"]["operation_id"]}
            if (replacement_path == path or candidate.get("schema") != INTENT
                    or candidate.get("corrects") != expected_correction
                    or candidate["request"]["entry"] != str(entry)
                    or candidate["parent_checkpoint"]["task_id"] != intent["parent_checkpoint"]["task_id"]):
                raise EvidenceError("replacement must explicitly correct this intent in the same entry and task")
            replacement = {"intent_path": str(replacement_path), "identity": file_identity(replacement_path),
                           "operation_id": candidate["request"]["operation_id"]}
        terminal_request = {"disposition": disposition, "reason": reason,
                            "intent_identity": expected_intent_identity, "replacement": replacement}
        terminal_path = path.parent / "disposition.json"
        if terminal_path.exists():
            if _read(terminal_path).get("request") != terminal_request:
                raise EvidenceError("terminal publication disposition is immutable")
            return state
        if state["status"] in {"published", "superseded"}:
            raise EvidenceError("a successful publication cannot be closed as a failure")
        if replacement_intent is not None:
            candidate_state = inspect_publication(replacement["intent_path"])
            if candidate_state["status"] in TERMINAL_DISPOSITIONS:
                raise EvidenceError("replacement operation is already terminal")
        _save(terminal_path, {"schema": DISPOSITION, "request": terminal_request,
            "operation_id": intent["request"]["operation_id"], "entry": str(entry),
            "phase_at_close": state["phase"], "entry_identity_at_close": state["current_entry_identity"],
            "execution_permission": "not_granted", "automatic_retry": False})
        return inspect_publication(path)


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
