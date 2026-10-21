"""Source-scoped evidence identities and explicit, verified local relocation.

No registry, ledger, directory discovery, downloads, or scientific admission.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

from etalon.active.schema import canonical, digest

WORKSPACE_SCHEMA = "etalon-evidence-workspace/1"
REF_SCHEMA = "etalon-evidence-ref/1"
CONTENT_SCHEMAS = {"etalon-method-revision/1", "etalon-research-claim/1"}


class EvidenceError(ValueError):
    """An explicit evidence contract or identity failure."""


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError("nonempty text required")
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise EvidenceError("SHA256 must be 64 lowercase hexadecimal characters")
    return value


def file_identity(path):
    h = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
            size += len(block)
    return {"sha256": h.hexdigest(), "bytes": size}


def _identity(value):
    _sha(value.get("sha256"))
    if type(value.get("bytes")) is not int or value["bytes"] < 0:
        raise EvidenceError("nonnegative byte count required")


def _local(base, location, *, confined=False):
    _text(location)
    win, posix = PureWindowsPath(location), PurePosixPath(location)
    native = Path(location)
    if "://" in location or location.startswith(("\\\\", "//")):
        raise EvidenceError("only local file locations are supported")
    if win.drive and not native.is_absolute():
        raise EvidenceError("foreign Windows location requires an explicit location_map")
    # On Windows /root is drive-relative and must not resolve against the cwd drive.
    if posix.is_absolute() and not native.is_absolute():
        raise EvidenceError("foreign POSIX location requires an explicit location_map")
    path = (native if native.is_absolute() else base / native).resolve()
    if confined or not native.is_absolute():
        if not path.is_relative_to(base):
            raise EvidenceError("location escapes manifest directory")
    return path


@dataclass(frozen=True)
class EvidenceWorkspace:
    """Validated JSON plus its explicit location base; no mutable shared config."""
    base: Path
    payload: str

    @property
    def manifest(self):
        return json.loads(self.payload)

    def source(self, source_id):
        for source in self.manifest["sources"]:
            if source["source_id"] == source_id:
                return source
        raise EvidenceError("unknown source_id")


def load_workspace(workspace):
    if isinstance(workspace, EvidenceWorkspace):
        base, data = workspace.base, workspace.manifest
        if not isinstance(base, Path) or not base.is_absolute():
            raise EvidenceError("validated workspace requires an absolute base")
    else:
        if not isinstance(workspace, (str, Path)):
            raise EvidenceError("workspace must be a manifest path or validated EvidenceWorkspace")
        path = Path(workspace).resolve(strict=True)
        if path.stat().st_size > 4 * 1024 * 1024:
            raise EvidenceError("workspace manifest exceeds 4 MiB")
        base, data = path.parent, json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("schema") != WORKSPACE_SCHEMA:
        raise EvidenceError("unsupported workspace schema")
    _text(data.get("workspace_id"))
    if not isinstance(data.get("sources"), list):
        raise EvidenceError("sources must be an explicit list")
    seen = set()
    for source in data["sources"]:
        sid = _text(source.get("source_id"))
        if sid in seen:
            raise EvidenceError("duplicate source_id")
        seen.add(sid)
        if source.get("kind") not in {"journal", "data_snapshot", "artifact_manifest"}:
            raise EvidenceError("unsupported source kind")
        _text(source.get("location"))
        identity = source.get("identity", {})
        if identity.get("kind") == "snapshot_sha256":
            _sha(identity.get("value"))
        elif identity.get("kind") == "journal_identity" and source["kind"] == "journal":
            _text(identity.get("key"))
            if "value" not in identity:
                raise EvidenceError("journal identity needs its existing metadata value")
        else:
            raise EvidenceError("unsupported source identity")
        if "record_ids" in source and (not isinstance(source["record_ids"], list)
                or any(not isinstance(x, str) or not x for x in source["record_ids"])):
            raise EvidenceError("record_ids must be explicit strings")
        projections = source.get("content_projections", [])
        if not isinstance(projections, list) or len(projections) > 64:
            raise EvidenceError("content_projections must list at most 64 explicit records")
        projected_ids = set()
        for projection in projections:
            if not isinstance(projection, dict) or set(projection) != {"record_id", "schema"}:
                raise EvidenceError("content projection requires record_id and schema")
            rid = _text(projection["record_id"])
            if rid in projected_ids or projection["schema"] not in CONTENT_SCHEMAS:
                raise EvidenceError("duplicate projection or unsupported content schema")
            projected_ids.add(rid)
    maps = data.get("location_map", [])
    if not isinstance(maps, list):
        raise EvidenceError("location_map must be a list")
    keys = set()
    for entry in maps:
        if entry.get("source_id") not in seen:
            raise EvidenceError("location map refers to an unknown source")
        key = (entry["source_id"], _text(entry.get("original")))
        if key in keys:
            raise EvidenceError("conflicting location mappings")
        keys.add(key)
        _identity(entry)
        _local(base, entry.get("location"), confined=True)
    return EvidenceWorkspace(base.resolve(), canonical(data))


def validate_ref(ref):
    if not isinstance(ref, dict) or ref.get("schema") != REF_SCHEMA:
        raise EvidenceError("unsupported evidence ref")
    for field in ("source_id", "record_id"):
        _text(ref.get(field))
    _sha(ref.get("record_digest"))
    if "sha256" in ref or "bytes" in ref:
        _identity(ref)
    if "member" in ref:
        _text(ref["member"])
    if "selector" in ref and ref["selector"] != "bytes":
        raise EvidenceError("only the bytes selector is supported")
    if len(canonical(ref).encode()) > 4096:
        raise EvidenceError("evidence ref exceeds 4 KiB")
    return json.loads(canonical(ref))


def make_ref(source_id, record_id, record):
    return validate_ref({"schema": REF_SCHEMA, "source_id": source_id,
                         "record_id": record_id, "record_digest": digest(record)})


class EvidenceResolver:
    def __init__(self, workspace, source_id):
        self.workspace = load_workspace(workspace)
        self.source_id = source_id
        self.workspace.source(source_id)

    def resolve(self, original, *, sha256, bytes):
        _identity({"sha256": sha256, "bytes": bytes})
        matches = [entry for entry in self.workspace.manifest.get("location_map", [])
                   if entry["source_id"] == self.source_id and entry["original"] == original]
        if matches:
            entry = matches[0]
            if (entry["sha256"], entry["bytes"]) != (sha256, bytes):
                raise EvidenceError("location mapping identity conflicts with original")
            path = _local(self.workspace.base, entry["location"], confined=True)
        else:
            path = _local(self.workspace.base, original)
        if file_identity(path) != {"sha256": sha256, "bytes": bytes}:
            raise EvidenceError("artifact hash/size mismatch")
        return path

    def resolve_snapshot(self, content_ref):
        """Locate a saved skill manifest; caller must verify semantic bundle hash."""
        original = content_ref["manifest_path"]
        matches = [entry for entry in self.workspace.manifest.get("location_map", [])
                   if entry["source_id"] == self.source_id and entry["original"] == original]
        if matches:
            entry = matches[0]
            return self.resolve(original, sha256=entry["sha256"], bytes=entry["bytes"])
        return _local(self.workspace.base, original)

    def resolve_source(self, original):
        """Bind a historical journal scope to this explicitly pinned source."""
        source = self.workspace.source(self.source_id)
        if source["kind"] != "journal":
            raise EvidenceError("subject scope requires a journal source")
        path = _local(self.workspace.base, source["location"])
        matches = [entry for entry in self.workspace.manifest.get("location_map", [])
                   if entry["source_id"] == self.source_id and entry["original"] == original]
        if matches:
            entry = matches[0]
            mapped = self.resolve(original, sha256=entry["sha256"], bytes=entry["bytes"])
            if mapped != path:
                raise EvidenceError("scope mapping does not point to the bound source")
        elif _local(self.workspace.base, original) != path:
            raise EvidenceError("scope has no explicit mapping to the bound source")
        # Reuse the same read-only identity validation as query; no new store.
        from .evidence import _source
        _source(self.workspace, source)
        return path


def resolve_artifact(artifact, *, resolver=None):
    record = artifact if isinstance(artifact, dict) else artifact.as_dict()
    _identity(record)
    if resolver is not None:
        return resolver.resolve(record["path"], sha256=record["sha256"], bytes=record["bytes"])
    path = Path(record["path"])
    if file_identity(path) != {"sha256": record["sha256"], "bytes": record["bytes"]}:
        raise EvidenceError("artifact hash/size mismatch")
    return path


def validate_claim(claim):
    if not isinstance(claim, dict) or claim.get("schema") != "etalon-research-claim/1":
        raise EvidenceError("unsupported research claim")
    for key in ("question", "statement"):
        _text(claim.get(key))
    if claim.get("epistemic_status") not in {"extracted_report", "hypothesis", "interpretation"}:
        raise EvidenceError("unsupported epistemic_status")
    for key in ("subjects", "conditions", "limitations"):
        if key not in claim or not isinstance(claim[key], (list, dict, str)):
            raise EvidenceError("claim requires " + key)
    for key in ("support_refs", "counter_refs", "method_refs", "supersedes"):
        if not isinstance(claim.get(key), list):
            raise EvidenceError("claim requires a list of " + key)
        for ref in claim[key]:
            validate_ref(ref)
    return json.loads(canonical(claim))
