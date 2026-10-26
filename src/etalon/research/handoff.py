"""Bounded, local handoff of an explicitly published research entry.

This is an archival operation, not a publisher, transport, authorization token,
or scientific retry. Original records and files are never rewritten. Only the
new workspace's locations change; publication operations remain inert history.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from etalon.active.schema import canonical, digest
from .continuation import CHECKPOINT, read_content, resume_research
from .entry import READ_CONTEXT, resolve_entry
from .evidence import _collect
from .evidence_refs import EvidenceError, EvidenceResolver, _identity, _local, _sha, file_identity, load_workspace
from .publication import INTENT, inspect_publication
from .read_scope import verified_read

PLAN = "etalon-research-handoff-plan/1"
BUNDLE = "etalon-research-handoff/1"
DEFAULT_LIMITS = {"max_sources": 64, "max_records": 100000, "max_files": 10000,
                  "max_bytes": 512 * 1024 * 1024, "max_publications": 128}
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


def _limits(value):
    if value is not None and (not isinstance(value, dict) or set(value) - DEFAULT_LIMITS.keys()):
        raise EvidenceError("unknown handoff limits")
    result = {**DEFAULT_LIMITS, **(value or {})}
    if any(type(v) is not int or v <= 0 or v > DEFAULT_LIMITS[k] for k, v in result.items()):
        raise EvidenceError("handoff limits must be positive integers within hard bounds")
    return result


def _read(path, limit=MAX_MANIFEST_BYTES):
    path = Path(path)
    if path.stat().st_size > limit:
        raise EvidenceError("handoff JSON exceeds bounded read")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise EvidenceError("handoff JSON exceeds bounded read")
    return json.loads(raw.decode("utf-8-sig"))


def _relative(value):
    if (not isinstance(value, str) or not value or ":" in value or "\\" in value
            or PurePosixPath(value).is_absolute()
            or any(p in {"", ".", ".."} for p in value.split("/"))):
        raise EvidenceError("handoff target must be a canonical relative path")
    return value


def _plain(path):
    """Reject redirected output/archive paths, including Windows junctions."""
    if "://" in str(path) or str(path).startswith(("\\\\", "//")):
        raise EvidenceError("handoff supports local paths only, not network locations")
    path = Path(path).absolute()
    if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in (path, *path.parents)):
        raise EvidenceError("handoff paths cannot contain symlinks or junctions")
    return path


def _seal(value):
    return {**value, "digest": digest(value)}


def _unseal(value, schema):
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise EvidenceError("unsupported handoff schema")
    if value.get("digest") != digest({k: v for k, v in value.items() if k != "digest"}):
        raise EvidenceError("handoff digest mismatch")


def _rows_digest(rows):
    return digest({sid: {rid: row["record"] for rid, row in records.items()} for sid, records in rows.items()})


def _skill_overrides(values):
    from .skill_sources import MAX_SKILL_BUNDLES
    if not isinstance(values, (list, tuple)) or len(values) > MAX_SKILL_BUNDLES:
        raise EvidenceError("skill_bundles must be a bounded explicit list")
    result = {}
    for item in values:
        if not isinstance(item, dict) or set(item) != {"slug", "manifest_sha256", "root"}:
            raise EvidenceError("skill bundle override requires slug, manifest_sha256 and absolute root")
        if not isinstance(item["slug"], str) or not re.fullmatch(r"[a-z0-9_-]+", item["slug"]):
            raise EvidenceError("skill bundle override requires a lowercase slug")
        _sha(item["manifest_sha256"])
        if not isinstance(item["root"], str) or not Path(item["root"]).is_absolute():
            raise EvidenceError("skill bundle override root must be absolute")
        key = item["slug"], item["manifest_sha256"]
        if key in result:
            raise EvidenceError("duplicate or conflicting skill bundle override")
        result[key] = str(_plain(item["root"]))
    return result


def _workset_pins(value):
    from .meta_context import validate_workset
    value = validate_workset(value, verify=False)
    return [resource for resource in value["index"]["resources"] if resource["kind"] == "skill"]


def _record_workset_pins(row, resolver, artifact_path=None):
    body = row["record"]
    if row["record_type"] == "artifact":
        bindings = body.get("bindings", {})
        if (bindings.get("schema") == "etalon-task-workset/1"
                or bindings.get("meta_activation") == "etalon-task-workset/1"):
            from .meta_context import MAX_BYTES
            path = artifact_path if artifact_path is not None else resolver.resolve(
                body["path"], **{k: body[k] for k in ("sha256", "bytes")})
            return _workset_pins(_read(path, MAX_BYTES))
    elif body.get("schema") == "etalon-task-workset/1":
        return _workset_pins(body)
    return []


def _bundle_dependencies(ws, resources, snapshots, overrides, inventory):
    """Pin full exact bundles, including excluded workset resources and old maps."""
    from etalon import skills
    from .skill_sources import MAX_SKILL_BUNDLES, resolve_skill_bundle, validate_skill_bundle_map
    mapped = {(item["slug"], item["manifest_sha256"]): item for item in validate_skill_bundle_map(ws)}
    requested = {}
    for resource in resources:
        ref = resource["content_ref"]
        requested.setdefault((ref["slug"], ref["manifest_sha256"]), []).append(resource)
    keys = set(mapped) | set(requested)
    if len(keys) > MAX_SKILL_BUNDLES:
        raise EvidenceError("handoff skill bundle version limit exceeded")
    if set(overrides) - keys:
        raise EvidenceError("skill bundle override is not referenced by a workset or existing map")
    result = []
    for key in sorted(keys):
        slug, expected = key
        refs = requested.get(key, [])
        ref = refs[0]["content_ref"] if refs else {"slug": slug, "manifest_sha256": expected}
        if key in overrides:
            root, basis = Path(overrides[key]), "explicit_exact_version_override"
        elif key in mapped:
            root = ws.base / mapped[key]["root"]
            basis = "existing_workspace_skill_bundle_map"
        elif key in snapshots:
            root, basis = snapshots[key], "saved_method_version_snapshot"
        else:
            resolved = resolve_skill_bundle(ref, ws)
            root = Path(resolved["root"]) if resolved is not None else skills.bundle_root(slug)
            basis = "original_bundle_ref" if resolved is not None else "current_library_exact_hash_match"
        root = _plain(root)
        try:
            version, contents = skills.snapshot(slug, bundle_ref={"root": str(root), "manifest_sha256": expected})
        except (OSError, ValueError, KeyError) as exc:
            raise EvidenceError("required skill bundle version unavailable: " + slug + "@" + expected + ": " + str(exc)) from exc
        if any(not {part["part_id"] for part in resource["parts"]} <= set(contents) for resource in refs):
            raise EvidenceError("workset skill part is not an exact bundle member")
        target = "skills/" + digest({"slug": slug, "manifest_sha256": expected})[:24] + "/bundle"
        for member in version["files"]:
            name = _relative(member["path"])
            inventory.add(root / name, target + "/" + name, {k: member[k] for k in ("sha256", "bytes")})
        result.append({"slug": slug, "manifest_sha256": expected, "root": target,
                       "source_root": str(root), "source_basis": basis, "manifest": version})
    return result


class _Inventory:
    def __init__(self, limits):
        self.limits, self.files, self.originals, self.total = limits, {}, {}, 0

    def add(self, path, target, identity=None):
        path, target = _plain(path), _relative(target)
        if not path.is_file():
            raise EvidenceError("handoff original is not a regular file: " + str(path))
        if target not in self.files:
            if len(self.files) >= self.limits["max_files"] or self.total + path.stat().st_size > self.limits["max_bytes"]:
                raise EvidenceError("handoff file/byte limit exceeded; no partial plan is returned")
        if str(path) not in self.originals and len(self.originals) >= self.limits["max_files"]:
            raise EvidenceError("handoff original file limit exceeded")
        actual = file_identity(path)
        if identity is not None and actual != identity:
            raise EvidenceError("handoff original identity mismatch: " + str(path))
        if str(path) in self.originals and self.originals[str(path)] != actual:
            raise EvidenceError("handoff original changed during planning")
        self.originals[str(path)] = actual
        row = {"source": str(path), "target": target, **actual}
        if target in self.files:
            if {k: self.files[target][k] for k in ("sha256", "bytes")} != actual:
                raise EvidenceError("conflicting handoff target")
        else:
            self.files[target] = row
            self.total += actual["bytes"]
        return target


def _publication_history(entry, envelope, extra, inventory):
    """Traverse only explicit links, never guess adjacent publication folders."""
    entries, intents, history, trees = [entry], list(extra), [], []
    seen_entries, seen_intents, archived = set(), set(), {}
    while entries or intents:
        if len(seen_entries) + len(seen_intents) > 2 * inventory.limits["max_publications"]:
            raise EvidenceError("handoff publication discovery limit exceeded")
        if entries:
            path = _plain(entries.pop(0))
            if str(path) in seen_entries:
                continue
            seen_entries.add(str(path))
            value = envelope if path == entry else _read(path, 65536)
            tag = digest(str(path))[:24]
            inventory.add(path, "origin/entries/" + tag + ".json")
            publication = value.get("publication", {})
            if value.get("handoff", {}).get("schema") == BUNDLE:
                raise EvidenceError("entry is already an archived handoff; use relocate_research_handoff")
            if publication.get("archive_only"):
                declared = value.get("publication_history")
                if not isinstance(declared, list) or not declared or len(declared) > inventory.limits["max_publications"]:
                    raise EvidenceError("legacy archived entry needs a bounded declared publication_history")
                declared_paths = []
                for item in declared:
                    if not isinstance(item, dict) or item.get("archive_only") is not True:
                        raise EvidenceError("legacy publication must remain archive_only")
                    identity = item.get("intent_identity", {})
                    _identity(identity)
                    archive_path = _plain(_local(path.parent, item["archived_intent"]))
                    if str(archive_path) in archived and archived[str(archive_path)] != item:
                        raise EvidenceError("conflicting legacy archive declarations")
                    archived[str(archive_path)] = item
                    declared_paths.append(archive_path)
                if _plain(_local(path.parent, publication["intent_path"])) not in declared_paths:
                    raise EvidenceError("legacy active-looking pointer has no archived identity declaration")
                if Path(str(path) + ".publications.json").exists():
                    raise EvidenceError("legacy archive must not register active publication intents")
                intents.extend(declared_paths)
                continue
            if publication.get("intent_path"):
                intents.append(_local(path.parent, publication["intent_path"]))
            sidecar = Path(str(path) + ".publications.json")
            if sidecar.exists():
                index = _read(sidecar)
                if (index.get("schema") != "etalon-research-publication-index/1"
                        or index.get("entry") != str(path) or not isinstance(index.get("intents"), list)
                        or len(index["intents"]) > inventory.limits["max_publications"]):
                    raise EvidenceError("invalid or oversized publication index")
                inventory.add(sidecar, "origin/entries/" + tag + ".publications.json")
                intents.extend(_local(sidecar.parent, p) for p in index["intents"])
            continue
        path = _plain(intents.pop(0))
        if str(path) in seen_intents:
            continue
        seen_intents.add(str(path))
        if len(seen_intents) > inventory.limits["max_publications"]:
            raise EvidenceError("handoff publication limit exceeded")
        value = _read(path)
        legacy = archived.get(str(path))
        if legacy is not None:
            if (file_identity(path) != legacy["intent_identity"] or value.get("schema") != INTENT
                    or value.get("request", {}).get("operation_id") != legacy.get("operation_id")):
                raise EvidenceError("legacy archive intent identity or operation mismatch")
            # These bytes may contain foreign original request paths. They are
            # never interpreted as a live recovery operation on this machine.
            state = {"status": legacy.get("origin_status", "unknown"), "phase": legacy.get("phase"),
                     "needs_recovery_at_export": legacy.get("needs_recovery_at_export"),
                     "status_basis": "prior_export_declaration_not_live_state",
                     "operation_id": legacy["operation_id"], "original_request_not_followed": True}
        else:
            state = inspect_publication(path)
        root, prefix = path.parent, "publication-history/" + digest(str(path))[:24]
        # Bound traversal itself, including empty directories. No symlink is followed.
        pending, members, visited = [root], [], 0
        while pending:
            folder = pending.pop()
            for item in sorted(folder.iterdir()):
                visited += 1
                if visited > inventory.limits["max_files"]:
                    raise EvidenceError("handoff publication tree exceeds bounded scan")
                item = _plain(item)
                if item.is_dir():
                    pending.append(item)
                elif item.is_file():
                    relative = item.relative_to(root).as_posix()
                    inventory.add(item, prefix + "/" + relative)
                    members.append(relative)
                else:
                    raise EvidenceError("unsupported publication archive member")
        trees.append({"root": str(root), "members": sorted(members)})
        history.append({"origin_intent": str(path), "archived_intent": prefix + "/" + path.name,
                        "observation": state, "archive_only": True, "automatic_retry": False,
                        "execution_permission": "not_granted",
                        "inventory_scope": "declared_legacy_archive_directory_only" if legacy else "selected_original_operation_directory",
                        "prior_archive": legacy})
        if legacy is not None:
            continue
        request = value["request"]
        if request.get("parent_entry"):
            entries.append(_local(root, request["parent_entry"]))
        if request.get("entry") and Path(request["entry"]).is_file():
            entries.append(_local(root, request["entry"]))
        corrects = value.get("corrects") or {}
        if corrects.get("intent_path"):
            intents.append(_local(root, corrects["intent_path"]))
        replacement = ((state.get("disposition") or {}).get("request", {}).get("replacement") or {})
        if replacement.get("intent_path"):
            intents.append(_local(root, replacement["intent_path"]))
    return history, trees


def plan_research_handoff(entry, *, publication_intents=(), skill_bundles=(), limits=None):
    """Read/verify a complete explicit frozen workspace and return a sealed plan.

    Only local entry/one-hop locator files are accepted, never policy-restricted
    read contexts. All published records and original source bytes are retained;
    this is not a redaction or disclosure API. Bounds fail closed, not truncate.
    Selected authority guards must already be paused/stopped and unchanged.
    """
    limits = _limits(limits)
    overrides = _skill_overrides(skill_bundles)
    if not isinstance(entry, (str, Path)):
        raise EvidenceError("handoff requires a local entry file, not a read_context or policy projection")
    raw_entry = str(entry)
    if "://" in raw_entry or raw_entry.startswith(("\\\\", "//")):
        raise EvidenceError("handoff supports local paths only, not network locations")
    if PurePosixPath(raw_entry).is_absolute() and not Path(raw_entry).is_absolute():
        raise EvidenceError("foreign entry path must be resolved on its evidence server")
    # A deployment's current-entry symlink is a selector, not archived content.
    # Pin both its resolution and bytes, so a later retargeting invalidates the plan.
    selector = Path(entry).absolute()
    requested = _plain(selector.resolve(strict=True))
    if _read(requested, 65536).get("schema") == READ_CONTEXT:
        raise EvidenceError("handoff cannot export a policy-restricted read_context")
    if not isinstance(publication_intents, (list, tuple)) or len(publication_intents) > limits["max_publications"]:
        raise EvidenceError("publication_intents must be a bounded explicit list")
    inventory = _Inventory(limits)
    inventory.add(requested, "origin/requested-entry.json")
    with verified_read():
        workspace, checkpoint_ref, origin, context = resolve_entry(requested)
        if context is not None or not isinstance(workspace, (str, Path)) or origin is None:
            raise EvidenceError("handoff requires a concrete unfiltered entry and workspace")
        resolved_selector = Path(origin).absolute()
        origin = _plain(resolved_selector.resolve(strict=True))
        envelope = _read(origin, 65536)
        ws = load_workspace(workspace)
        manifest = copy.deepcopy(ws.manifest)
        if len(manifest["sources"]) > limits["max_sources"]:
            raise EvidenceError("handoff source limit exceeded")
        inventory.add(origin, "origin/entry.json")
        inventory.add(workspace, "origin/workspace.json")
        maps = {}

        def mapping(sid, original, path, identity, *, target=None):
            if target is None:
                target = "objects/" + identity["sha256"]
            target = inventory.add(path, target, identity)
            key = sid, original
            value = {"source_id": sid, "original": original, "location": target, **identity}
            if key in maps and maps[key] != value:
                raise EvidenceError("conflicting relocation for an original location")
            maps[key] = value
            return target

        for source in manifest["sources"]:
            if source["identity"]["kind"] != "snapshot_sha256":
                raise EvidenceError("handoff requires frozen snapshot sources; publish a snapshot first")
            if source.get("visibility", "released") != "released":
                raise EvidenceError("handoff cannot include an unpublished source")
            original = source["location"]
            path = _local(ws.base, original)
            if inventory.total + path.stat().st_size > limits["max_bytes"]:
                raise EvidenceError("handoff source byte limit exceeded")
            identity = file_identity(path)
            if identity["sha256"] != source["identity"]["value"]:
                raise EvidenceError("source snapshot hash mismatch")
            prefix = "sources/" + digest(source["source_id"])[:24]
            source["location"] = mapping(source["source_id"], original, path, identity,
                                          target=prefix + "/snapshot" + path.suffix)
        rows, coverage, issues = _collect(ws)
        if issues or any(c["status"] != "complete" for c in coverage):
            raise EvidenceError("handoff requires complete source coverage: " + canonical({"issues": issues, "coverage": coverage}))
        count = sum(len(r) for r in rows.values())
        if count > limits["max_records"]:
            raise EvidenceError("handoff record limit exceeded")
        checkpoint = read_content(ws, checkpoint_ref)
        if checkpoint["schema"] != CHECKPOINT:
            raise EvidenceError("handoff entry must select a research checkpoint")
        guards = {g["location"]: g for g in checkpoint["status_guards"]}
        status_locations = {s if isinstance(s, str) else s.get("location") for s in manifest.get("status_refs", [])}
        if status_locations != set(guards) or any(g["state"] not in {"paused", "stopped"} for g in guards.values()):
            raise EvidenceError("handoff needs matching paused/stopped authority guards; no authority is inferred")
        for item in ws.manifest.get("location_map", []):
            identity = {k: item[k] for k in ("sha256", "bytes")}
            key = item["source_id"], item["original"]
            target = maps[key]["location"] if key in maps else None
            if item["original"] in guards:
                target = "authority/" + digest(list(key))[:24]
            mapping(*key, _local(ws.base, item["location"], confined=True), identity, target=target)
        artifacts, members, skills = 0, 0, []
        workset_resources, saved_bundles = [], {}
        for sid, records in rows.items():
            resolver = EvidenceResolver._from_validated(ws, sid)
            for row in records.values():
                body = row["record"]
                if row["record_type"] == "artifact":
                    identity = {k: body[k] for k in ("sha256", "bytes")}
                    key = sid, body["path"]
                    artifact_path = resolver.resolve(body["path"], **identity)
                    target = ("authority/" + digest([sid, body["path"]])[:24]
                              if body["path"] in guards else maps[key]["location"] if key in maps else None)
                    mapping(sid, body["path"], artifact_path, identity,
                            target=target)
                    artifacts += 1
                    workset_resources.extend(_record_workset_pins(row, resolver, artifact_path))
                else:
                    workset_resources.extend(_record_workset_pins(row, resolver))
                for member in row.get("members", body.get("members", [])):
                    if member.get("visibility") != "released":
                        continue
                    identity = {k: member[k] for k in ("sha256", "bytes")}
                    path = resolver.resolve(member["path"], **identity)
                    target = None
                    if body.get("schema") == "etalon-data-snapshot/1":
                        prefix = "sources/" + digest(sid)[:24]
                        target = prefix + "/" + _relative(member["member"])
                    mapping(sid, member["path"], path, identity, target=target)
                    members += 1
                content_ref = body.get("content_ref")
                if isinstance(content_ref, dict) and "manifest_path" in content_ref:
                    from etalon.skills import verify_snapshot
                    skill = verify_snapshot(content_ref, resolver=resolver)
                    path = resolver.resolve_snapshot(content_ref)
                    prefix = "skills/" + digest(content_ref)[:24]
                    # An existing map can point at this same skill. Its structural
                    # layout must be kept so the existing snapshot verifier works.
                    maps.pop((sid, content_ref["manifest_path"]), None)
                    mapping(sid, content_ref["manifest_path"], path, file_identity(path), target=prefix + "/manifest.json")
                    for member in skill["files"]:
                        name = member["path"]
                        inventory.add(path.parent / "bundle" / name, prefix + "/bundle/" + _relative(name),
                                      {k: member[k] for k in ("sha256", "bytes")})
                    skills.append({"source_id": sid, "content_ref": content_ref})
                    saved_bundles[(skill["slug"], skill["manifest_sha256"])] = path.parent / "bundle"
        bundles = _bundle_dependencies(ws, workset_resources, saved_bundles, overrides, inventory)
        if bundles or "skill_bundle_map" in manifest:
            manifest["skill_bundle_map"] = [{k: b[k] for k in ("slug", "manifest_sha256", "root")} for b in bundles]
        for original, guard in guards.items():
            sid = checkpoint_ref["source_id"]
            identity = {k: guard[k] for k in ("sha256", "bytes")}
            mapping(sid, original, EvidenceResolver._from_validated(ws, sid).resolve(original, **identity),
                    identity, target="authority/" + digest([sid, original])[:24])
        manifest["location_map"] = list(maps.values())
        continuation = resume_research(ws, checkpoint_ref, read_mode="frozen_view")
    history, trees = _publication_history(origin, envelope, publication_intents, inventory)
    plan = _seal({"schema": PLAN, "limits": limits, "origin_entry": str(origin),
                  "entry_selector": {"path": str(selector), "resolved_path": str(requested),
                                     "identity": inventory.originals[str(requested)]},
                  "resolved_entry_selector": {"path": str(resolved_selector), "resolved_path": str(origin),
                                              "identity": inventory.originals[str(origin)]},
                  "origin_workspace": str(workspace), "checkpoint_ref": checkpoint_ref,
                  "checkpoint_content_digest": digest(checkpoint), "records_digest": _rows_digest(rows),
                  "workspace": manifest, "entry": envelope, "files": list(inventory.files.values()),
                  "originals": inventory.originals, "publication_history": history, "publication_trees": trees,
                  "skill_snapshots": skills, "skill_bundles": bundles,
                  "counts": {"sources": len(manifest["sources"]), "records": count, "skill_bundles": len(bundles),
                  "artifacts": artifacts, "members": members, "files": len(inventory.files), "bytes": inventory.total},
                  "continuation_at_export": {k: continuation[k] for k in ("issues", "warnings", "pending", "authority")},
                  "discovery_scope": "explicit workspace, entry/index, publication parent/correction/replacement links and explicit intents",
                  "history_coverage": {"unlinked_operations": "not_discovered_without_explicit_selection",
                      "legacy_archive_directories": sum(h["prior_archive"] is not None for h in history),
                      "legacy_completeness": "only_declared_archive_bytes; external_failure_archives_not_implied"},
                  "execution_permission": "not_granted", "automatic_retry": False})
    if len(canonical(plan).encode()) > MAX_MANIFEST_BYTES:
        raise EvidenceError("handoff plan exceeds bounded manifest size")
    _documents(plan)
    _verify_originals(plan)
    return plan


def _verify_originals(plan):
    for selector in (plan.get("entry_selector"), plan.get("resolved_entry_selector")):
        if selector is not None and (str(Path(selector["path"]).resolve(strict=True)) != selector["resolved_path"]
                or file_identity(selector["path"]) != selector["identity"]):
            raise EvidenceError("handoff entry selector changed after planning")
    for path, identity in plan["originals"].items():
        if file_identity(_plain(path)) != identity:
            raise EvidenceError("handoff original identity changed: " + path)
    for bundle in plan.get("skill_bundles", []):
        from etalon.skills import snapshot
        version, _ = snapshot(bundle["slug"], bundle_ref={"root": bundle["source_root"],
            "manifest_sha256": bundle["manifest_sha256"]})
        if version != bundle["manifest"]:
            raise EvidenceError("handoff original skill bundle inventory changed")
    for tree in plan["publication_trees"]:
        members = []
        pending = [_plain(tree["root"])]
        visited = 0
        while pending:
            for path in pending.pop().iterdir():
                path = _plain(path)
                visited += 1
                if visited > plan["limits"]["max_files"]:
                    raise EvidenceError("publication changed or exceeds bounded scan")
                if path.is_dir():
                    pending.append(path)
                else:
                    members.append(path.relative_to(tree["root"]).as_posix())
        if sorted(members) != tree["members"]:
            raise EvidenceError("publication membership changed after planning")


def _documents(plan):
    entry = copy.deepcopy(plan["entry"])
    entry.pop("publication", None)  # archived intents are never registered as recoverable operations
    entry.update(workspace="workspace.json", publication_history=plan["publication_history"],
                 handoff={"schema": BUNDLE, "plan_digest": plan["digest"], "manifest": "handoff.json",
                          "origin_entry": plan["origin_entry"], "archive_only": True,
                          "authority_semantics": "copied paused/stopped bytes; not a live remote control channel",
                          "execution_permission": "not_granted", "automatic_retry": False})
    if len(canonical(entry).encode()) > 65536 or len(canonical(plan["workspace"]).encode()) > 4 * 1024 * 1024:
        raise EvidenceError("handoff entry/workspace exceeds existing reader bounds")
    return {"workspace.json": plan["workspace"], "entry.json": entry}


def _write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical(value) + "\n")


def _copy_file(source, target, identity):
    target.parent.mkdir(parents=True, exist_ok=True)
    h, size = hashlib.sha256(), 0
    with Path(source).open("rb") as reader, target.open("xb") as writer:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            size += len(block)
            if size > identity["bytes"]:
                raise EvidenceError("handoff source grew while copying")
            h.update(block)
            writer.write(block)
    if {"sha256": h.hexdigest(), "bytes": size} != identity:
        raise EvidenceError("handoff source changed while copying")


def _validate_plan(plan):
    _unseal(plan, PLAN)
    limits = _limits(plan["limits"])
    targets, total = set(), 0
    if len(plan["files"]) > limits["max_files"] or len(plan["originals"]) > limits["max_files"]:
        raise EvidenceError("handoff file limit exceeded")
    if (len(plan["workspace"]["sources"]) > limits["max_sources"]
            or len(plan["publication_history"]) > limits["max_publications"]
            or len(plan["publication_trees"]) > limits["max_publications"]):
        raise EvidenceError("handoff source/publication limit exceeded")
    for identity in plan["originals"].values():
        _identity(identity)
    for item in plan["files"]:
        target = _relative(item["target"])
        _identity(item)
        if target in targets or target.split("/")[0] not in {"sources", "objects", "authority", "skills", "origin", "publication-history"}:
            raise EvidenceError("duplicate or reserved handoff target")
        targets.add(target)
        total += item["bytes"]
        if plan["originals"].get(item["source"]) != {k: item[k] for k in ("sha256", "bytes")}:
            raise EvidenceError("handoff file has no matching original identity")
    if total > limits["max_bytes"] or len(canonical(plan).encode()) > MAX_MANIFEST_BYTES:
        raise EvidenceError("handoff byte limit exceeded")


def export_research_handoff(plan, destination):
    """Verify pinned originals, then copy locally into a new, exclusive directory.

    Failures retain the partial directory for inspection. Existing directories
    are never merged, overwritten, deleted or retried; no network is used.
    """
    _validate_plan(plan)
    destination = _plain(destination)
    if destination.exists():
        raise EvidenceError("handoff destination already exists")
    _verify_originals(plan)
    destination.mkdir(parents=True, exist_ok=False)
    for item in plan["files"]:
        _copy_file(item["source"], destination / item["target"], {k: item[k] for k in ("sha256", "bytes")})
    for name, value in _documents(plan).items():
        _write(destination / name, value)
    _verify_originals(plan)
    _write(destination / "handoff.json", _seal({"schema": BUNDLE, "plan": plan}))
    return verify_research_handoff(destination)


def verify_research_handoff(directory, *, verify_originals=True):
    """Verify bundle bytes, unchanged semantic identities and selected authority.

    Original files are checked by default. Explicit ``False`` is useful after
    physical relocation and reports only ``bundle_verified``; it never claims
    that inaccessible originals or live remote authority were checked.
    """
    if type(verify_originals) is not bool:
        raise EvidenceError("verify_originals must be boolean")
    root = _plain(directory)
    bundle = _read(_plain(root / "handoff.json"))
    _unseal(bundle, BUNDLE)
    plan = bundle["plan"]
    _validate_plan(plan)
    for item in plan["files"]:
        path = _plain(root / item["target"])
        if file_identity(path) != {k: item[k] for k in ("sha256", "bytes")}:
            raise EvidenceError("handoff bundle file identity mismatch: " + item["target"])
    for name, expected in _documents(plan).items():
        if _read(_plain(root / name)) != expected:
            raise EvidenceError("handoff generated document mismatch: " + name)
    if Path(str(root / "entry.json") + ".publications.json").exists():
        raise EvidenceError("archived handoff cannot have an active publication index")
    with verified_read():
        ws = load_workspace(root / "workspace.json")
        rows, coverage, issues = _collect(ws)
        if issues or any(c["status"] != "complete" for c in coverage) or _rows_digest(rows) != plan["records_digest"]:
            raise EvidenceError("handoff record coverage or semantic digest mismatch")
        checkpoint = read_content(ws, plan["checkpoint_ref"])
        if digest(checkpoint) != plan["checkpoint_content_digest"]:
            raise EvidenceError("handoff checkpoint changed")
        for skill in plan["skill_snapshots"]:
            from etalon.skills import verify_snapshot
            verify_snapshot(skill["content_ref"], resolver=EvidenceResolver._from_validated(ws, skill["source_id"]))
        from .skill_sources import validate_skill_bundle_map
        checked_bundles = validate_skill_bundle_map(ws)
        bundle_keys = {(b["slug"], b["manifest_sha256"]) for b in checked_bundles}
        for sid, records in rows.items():
            resolver = EvidenceResolver._from_validated(ws, sid)
            for row in records.values():
                for resource in _record_workset_pins(row, resolver):
                    ref = resource["content_ref"]
                    if (ref["slug"], ref["manifest_sha256"]) not in bundle_keys:
                        raise EvidenceError("handoff missing exact workset skill bundle mapping; re-export from the original entry")
        for bundle in plan.get("skill_bundles", []):
            from etalon.skills import snapshot
            expected_map = {k: bundle[k] for k in ("slug", "manifest_sha256", "root")}
            if expected_map not in checked_bundles:
                raise EvidenceError("handoff skill bundle map is incomplete")
            version, _ = snapshot(bundle["slug"], bundle_ref={"root": str(ws.base / bundle["root"]),
                "manifest_sha256": bundle["manifest_sha256"]})
            if version != bundle["manifest"]:
                raise EvidenceError("handoff skill bundle member inventory changed")
        continuation = resume_research(ws, plan["checkpoint_ref"], read_mode="frozen_view")
        before = plan["continuation_at_export"]
        if (continuation["authority"]["state"] != before["authority"]["state"]
                or any(g["status"] != "unchanged" for g in continuation["authority"]["sources"])):
            raise EvidenceError("handoff authority changed or is unreviewed")
    if verify_originals:
        _verify_originals(plan)
    return {"schema": "etalon-research-handoff-verification/1", "entry": str(root / "entry.json"),
            "status": "verified" if verify_originals else "bundle_verified",
            "verification_scope": "bundle_and_originals" if verify_originals else "exported_bundle_only",
            "originals": "verified" if verify_originals else "not_checked", "plan_digest": plan["digest"],
            "checkpoint_ref": plan["checkpoint_ref"], "records_digest": plan["records_digest"],
            "counts": plan["counts"], "publication_history": plan["publication_history"],
            "history_coverage": plan.get("history_coverage", {"legacy_completeness": "not_inspected"}),
            "authority_state": continuation["authority"]["state"], "issues": continuation["issues"],
            "warnings": continuation["warnings"], "pending": continuation["pending"],
            "execution_permission": "not_granted", "automatic_retry": False,
            "remote_authority_checked": False, "publication_reactivated": False}


def relocate_research_handoff(directory, destination):
    """Copy a verified bundle locally. Relative runtime locations need no edits."""
    verify_research_handoff(directory, verify_originals=False)
    source, destination = _plain(directory), _plain(destination)
    if destination.exists():
        raise EvidenceError("handoff destination already exists")
    bundle = _read(source / "handoff.json")
    plan = bundle["plan"]
    destination.mkdir(parents=True, exist_ok=False)
    for item in plan["files"]:
        _copy_file(source / item["target"], destination / item["target"], {k: item[k] for k in ("sha256", "bytes")})
    for name in ("workspace.json", "entry.json", "handoff.json"):
        _copy_file(source / name, destination / name, file_identity(source / name))
    return verify_research_handoff(destination, verify_originals=False)
