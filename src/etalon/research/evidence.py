"""Bounded, read-only evidence access over explicitly published local sources."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

from etalon.active.schema import canonical, digest
from .evidence_refs import (
    CONTENT_SCHEMAS, EvidenceError, EvidenceResolver, _identity, _local, file_identity,
    load_workspace, make_ref, validate_claim, validate_ref,
)

FILTERS = {"source_ids", "record_types", "task_ids", "entity_ids", "method_refs", "statuses", "roles",
           "branch_ids", "node_ids", "capability_ids", "workflow_refs", "evaluation_targets"}
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 100000
MAX_QUERY_BYTES = 32768
MAX_READ_BYTES = 65536
MAX_DEPENDENCY_SOURCES = 64


class StaleCursorError(EvidenceError):
    pass


def _json_file(path):
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise EvidenceError("source exceeds bounded JSON read size")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _visible(body):
    if isinstance(body, dict):
        if body.get("role") == "sealed_holdout" or body.get("visibility") in {"sealed", "sealed_holdout", "private"}:
            return False
        return all(_visible(v) for v in body.values())
    if isinstance(body, list):
        return all(_visible(v) for v in body)
    return True


def _references(body):
    """Extract explicit relationship fields, never arbitrary matching text."""
    keys = {"parents", "inputs", "evidence", "evidence_refs", "support_refs", "counter_refs",
            "method_refs", "supersedes", "experiment_id", "contrast_ids", "method_ref",
            "method_version_ref", "usage_method_ref", "evaluation_refs", "use_refs",
            "action_ids", "accounting_refs", "trial_refs", "producer_method_ref", "producer_use_ref",
            "base_method_ref", "candidate_version_refs", "problem_refs", "visible_evidence_refs",
            "protocol_ref", "task_split_ref", "initial_conditions_ref", "result_refs"}
    result = []
    def walk(value):
        if isinstance(value, dict):
            if value.get("schema") == "etalon-evidence-ref/1":
                result.append(value)
            else:
                for key, item in value.items():
                    if key in keys or (value.get("schema") == "etalon-method-evaluation/1"
                                       and key in {"subjects", "comparators"}):
                        for ref in item if isinstance(item, list) else [item]:
                            if isinstance(ref, str):
                                result.append(ref)
                            elif isinstance(ref, dict):
                                # Compiled programs also have `inputs` and `parents`.
                                # Their port/configuration objects are not evidence IDs.
                                # Still surface malformed explicit refs for validation.
                                if ((isinstance(ref.get("schema"), str) and ref["schema"].startswith("etalon-evidence-ref/"))
                                        or {"source_id", "record_id"} & ref.keys()
                                        or key.endswith(("_ref", "_refs"))):
                                    result.append(ref)
                                else:
                                    walk(ref)
                    elif isinstance(item, (list, dict)):
                        walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(body)
    return result


def _relationships(row):
    return _references(row["record"]) + _references(row.get("projected_content", {}))


def _project_contents(ws, source, indexed):
    """Project only declared known-schema Artifacts; never discover arbitrary files."""
    explicit = {p["record_id"]: p["schema"] for p in source.get("content_projections", [])}
    attempted, total = 0, 0
    failures = []
    for rid, row in indexed.items():
        body = row["record"]
        declared = body.get("bindings", {}).get("schema")
        schema = explicit.get(rid, declared)
        if schema not in CONTENT_SCHEMAS:
            continue
        row["projection"] = {"schema": schema, "status": "unavailable"}
        try:
            if row["record_type"] != "artifact":
                raise EvidenceError("content projection requires an Artifact")
            if declared in CONTENT_SCHEMAS and declared != schema:
                raise EvidenceError("projection schema conflicts with Artifact declaration")
            attempted += 1
            total += body["bytes"]
            if attempted > 64 or body["bytes"] > 1024 * 1024 or total > 8 * 1024 * 1024:
                raise EvidenceError("projection read budget exceeded")
            path = EvidenceResolver(ws, source["source_id"]).resolve(
                body["path"], sha256=body["sha256"], bytes=body["bytes"])
            with path.open("rb") as handle:
                data = handle.read(1024 * 1024 + 1)
            if len(data) != body["bytes"] or hashlib.sha256(data).hexdigest() != body["sha256"]:
                raise EvidenceError("projection artifact hash/size mismatch")
            payload = json.loads(data.decode("utf-8-sig"))
            if not isinstance(payload, dict) or payload.get("schema") != schema:
                raise EvidenceError("projection content schema mismatch")
            if schema == "etalon-research-claim/1":
                validate_claim(payload)
            elif schema == "etalon-method-revision/1":
                from .method_revision import build_method_revision
                build_method_revision(**{k: v for k, v in payload.items() if k != "schema"})
            else:
                from .continuation import validate_content
                validate_content(payload)
            if not _visible(payload):
                raise EvidenceError("projection content is not published")
            row["projected_content"] = payload
            row["projection"] = {"schema": schema, "status": "verified",
                "artifact_ref": {**make_ref(source["source_id"], rid, body),
                                 "sha256": body["sha256"], "bytes": body["bytes"], "selector": "bytes"}}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            reason = str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__
            row["projection"]["reason"] = reason
            failures.append({"record_id": rid, "reason": reason})
    for rid in explicit.keys() - indexed.keys():
        # Do not identify or open unpublished records in diagnostics.
        failures.append({"reason": "explicit_projection_record_not_published"})
    return failures


def _source(ws, source):
    """Read one source atomically; errors become explicit unavailable coverage."""
    sid = source["source_id"]
    path = _local(ws.base, source["location"])
    if not path.is_file():
        raise FileNotFoundError("published source is missing")
    identity = source["identity"]
    before = file_identity(path) if identity["kind"] == "snapshot_sha256" else None
    if before and before["sha256"] != identity["value"]:
        raise EvidenceError("source snapshot hash mismatch")
    records = []
    watermark = None
    if source["kind"] == "journal":
        wal = Path(str(path) + "-wal")
        if before and wal.exists() and wal.stat().st_size:
            raise EvidenceError("snapshot journal has WAL content outside its identity; publish a consistent snapshot")
        # No CampaignStore constructor; unknown paths can never create a DB.
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            if identity["kind"] == "journal_identity":
                row = db.execute("SELECT body FROM metadata WHERE key=?", (identity["key"],)).fetchone()
                if row is None or json.loads(row[0]) != identity["value"]:
                    raise EvidenceError("journal identity mismatch")
            watermark = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
            rows = db.execute("SELECT key,body FROM metadata WHERE key LIKE 'research:%' OR key LIKE 'budget:execution:%' ORDER BY key")
            total_bytes = 0
            for key, raw in rows:
                total_bytes += len(raw.encode("utf-8"))
                if len(records) >= MAX_RECORDS or total_bytes > MAX_SOURCE_BYTES:
                    raise EvidenceError("journal exceeds bounded scan; publish a smaller view")
                rid = key[len("research:"):] if key.startswith("research:") else key
                records.append({"record_id": rid, "record_type": rid.split("-", 1)[0] if key.startswith("research:") else "execution_receipt",
                                "record": json.loads(raw)})
        finally:
            db.close()
    else:
        document = _json_file(path)
        if document.get("schema") == "etalon-data-snapshot/1" and source["kind"] == "data_snapshot":
            from etalon.data.artifacts import digest as snapshot_digest
            sealed = {k: v for k, v in document.items() if k != "snapshot_id"}
            if snapshot_digest(sealed) != document.get("snapshot_id"):
                raise EvidenceError("data snapshot semantic identity mismatch")
            members = []
            for name, identity_item in document.get("files", {}).items():
                _identity(identity_item)
                local_path = _local(path.parent, name, confined=True)
                members.append({"member": name, "path": str(local_path), **identity_item, "visibility": "released"})
            records = [{"record_id": document["snapshot_id"], "record_type": "data_snapshot",
                        "record": document, "members": members}]
        elif document.get("schema") not in {"etalon-artifact-manifest/1", "etalon-evidence-data-snapshot/1"}:
            raise EvidenceError("unsupported evidence source document; publish an explicit artifact manifest")
        else:
            if not isinstance(document.get("records"), list):
                raise EvidenceError("source must explicitly list records")
            records = document["records"]
        if len(records) > MAX_RECORDS:
            raise EvidenceError("source exceeds record scan limit")
    if before and file_identity(path) != before:
        raise EvidenceError("source changed while being read")
    allowed = source.get("record_ids")
    indexed = {}
    denied = set()
    for row in records:
        rid, body = row.get("record_id"), row.get("record")
        if source["kind"] == "artifact_manifest" and row.get("visibility") != "released":
            if isinstance(rid, str):
                denied.add(rid)
            continue
        if not isinstance(rid, str) or not rid or not isinstance(body, dict) or not isinstance(row.get("record_type"), str):
            raise EvidenceError("malformed source record")
        if rid in indexed:
            raise EvidenceError("duplicate record identity")
        if (allowed is not None and rid not in allowed) or not _visible(body):
            denied.add(rid)
            continue
        if row["record_type"] == "artifact":
            _identity(body)
            if not isinstance(body.get("path"), str) or not body.get("source_kind") or not body.get("role"):
                raise EvidenceError("artifact needs location, identity and explicit provenance/role")
        if body.get("schema") == "etalon-research-claim/1":
            validate_claim(body)
        indexed[rid] = row
    projection_failures = _project_contents(ws, source, indexed)
    # Suppress derived local records referring to hidden records, transitively.
    while True:
        newly = {rid for rid, row in indexed.items() if any(
            (isinstance(ref, str) and ref in denied) or
            (isinstance(ref, dict) and ref.get("source_id") == sid and ref.get("record_id") in denied)
            for ref in _relationships(row))}
        if not newly:
            break
        denied.update(newly)
        for rid in newly:
            del indexed[rid]
    stamp = digest({"records": indexed, "event_watermark": watermark, "identity": identity})
    return indexed, {"source_id": sid, "status": "partial" if projection_failures else "complete", "read_watermark": watermark,
                     "index_watermark": watermark, "snapshot": stamp, "index": "ephemeral",
                     "projection_failures": projection_failures,
                     "visibility": "published_view_only", "visible_records": len(indexed)}


def _collect(ws, source_ids=None):
    """Read requested sources plus explicit evidence dependencies, never unrelated ones.

    Dependency reads preserve the existing transitive visibility rule. A source
    filter limits result candidates, not the checks required by their references.
    All visited sources are reported; unknown/hidden dependencies suppress derived
    records just as in the original full-workspace read.
    """
    records, coverage, warnings = {}, [], []
    sources = {s["source_id"]: s for s in ws.manifest["sources"]}
    known = set(sources)
    if source_ids is not None and set(source_ids) - known:
        raise EvidenceError("unknown source_ids filter")
    requested = known if source_ids is None else set(source_ids)
    pending = [sid for sid in sources if sid in requested]
    scheduled = set(pending)
    for sid in pending:
        source = sources[sid]
        if source.get("visibility", "released") != "released":
            coverage.append({"source_id": sid, "status": "unavailable", "read_watermark": None,
                             "index_watermark": None, "reason": "source_not_published",
                             "scope": "requested" if sid in requested else "dependency"})
            continue
        try:
            rows, cov = _source(ws, source)
            records[sid] = rows
            cov["scope"] = "requested" if sid in requested else "dependency"
            coverage.append(cov)
            if cov["projection_failures"]:
                warnings.append({"source_id": sid, "code": "content_projection_unavailable",
                                 "count": len(cov["projection_failures"])})
            dependencies = {ref["source_id"] for row in rows.values() for ref in _relationships(row)
                            if isinstance(ref, dict) and ref.get("schema") == "etalon-evidence-ref/1"
                            and ref.get("source_id") in known}
            for dependency in sorted(dependencies - scheduled):
                if len(scheduled - requested) >= MAX_DEPENDENCY_SOURCES:
                    raise EvidenceError("dependency source bound exceeded; publish a smaller workspace")
                scheduled.add(dependency)
                pending.append(dependency)
        except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
            # No filenames or source content in errors: unpublished data stays out.
            reason = str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__
            coverage.append({"source_id": sid, "status": "unavailable", "read_watermark": None,
                             "index_watermark": None, "reason": reason,
                             "scope": "requested" if sid in requested else "dependency"})
            records.pop(sid, None)
            coverage[:] = [c for c in coverage if c["source_id"] != sid] + [coverage[-1]]
            warnings.append({"source_id": sid, "code": "source_unavailable", "reason": reason})
    # Cross-source explicit refs to unpublished/unavailable records suppress derivatives too.
    while True:
        removed = []
        for sid, rows in records.items():
            for rid, row in rows.items():
                if any(isinstance(ref, dict) and ref.get("schema") == "etalon-evidence-ref/1"
                       and ref.get("record_id") not in records.get(ref.get("source_id"), {})
                       for ref in _relationships(row)):
                    removed.append((sid, rid))
        if not removed:
            break
        for sid, rid in removed:
            del records[sid][rid]
    for cov in coverage:
        if cov["source_id"] in records:
            cov["visible_records"] = len(records[cov["source_id"]])
    return records, coverage, warnings


def _scope_identity(ws, coverage):
    """Unrelated manifest edits must not invalidate a scoped pagination cursor."""
    visited = {c["source_id"] for c in coverage}
    manifest = ws.manifest
    return {"workspace_id": manifest["workspace_id"],
            "sources": [s for s in manifest["sources"] if s["source_id"] in visited],
            "location_map": [m for m in manifest.get("location_map", []) if m["source_id"] in visited]}


def _strings(value):
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return set().union(*(_strings(v) for v in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(_strings(v) for v in value.values())) if value else set()
    return set()


def _matches(row, filters):
    bodies = [row["record"], row.get("projected_content", {})]
    content = row.get("projected_content", {})
    if content.get("schema") == "etalon-research-step/1":
        bodies.append(content["detail"])
    if row["record"].get("schema") == "etalon-method-evaluation/1":
        bodies.append({"method_refs": row["record"]["subjects"] + row["record"]["comparators"]})
    if content.get("operation") == "evaluation_plan":
        bodies.append({"method_refs": content["detail"]["subjects"] + content["detail"]["comparators"]})
    fields = {"task_ids": ("task_id", "task_ids"), "entity_ids": ("entity_id", "entity_ids", "subjects", "bindings"),
              "method_refs": ("method_ref", "method_refs", "method_id", "method_version_ref", "usage_method_ref", "selections", "selected_methods", "base_method_ref", "producer_method_ref", "candidate_version_refs"),
              "statuses": ("status", "state", "epistemic_status"), "roles": ("role", "roles"),
              "branch_ids": ("branch_id",), "node_ids": ("node_id",), "capability_ids": ("capability_id",),
              "workflow_refs": ("workflow_ref",), "evaluation_targets": ("target",)}
    if "record_types" in filters and row["record_type"] not in filters["record_types"]:
        return False
    for name, keys in fields.items():
        if name in filters:
            values = set()
            for body in bodies:
                for key in keys:
                    value = body.get(key)
                    if key == "selected_methods" and isinstance(value, list):
                        values.update(m["method_version_ref"] for m in value
                                      if isinstance(m, dict) and isinstance(m.get("method_version_ref"), str))
                    else:
                        values.update(_strings(value))
            if not set(filters[name]) & values:
                return False
    return True


def _item(sid, row):
    body = row["record"]
    content = row.get("projected_content", body)
    summary = canonical(content)
    result = {"ref": make_ref(sid, row["record_id"], body), "record_type": row["record_type"],
            "source": {"source_id": sid}, "summary": summary[:600], "truncated": len(summary) > 600,
            "conditions": content.get("conditions"), "related_refs": _relationships(row)[:10],
            "verification": {"record": "verified", "artifact": "not_checked"},
            "availability": {"record": "available", "artifact": "not_checked"}}
    if "projection" in row:
        result["projection"] = row["projection"]
        result["verification"]["artifact"] = row["projection"]["status"]
        result["availability"]["artifact"] = "available" if "projected_content" in row else "unavailable"
    return result


def _bounded_int(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise EvidenceError(f"{name} must be {low}..{high}")


def query_evidence(workspace, *, query="", filters=None, limit=10, cursor=None):
    ws = load_workspace(workspace)
    _bounded_int(limit, 1, 50, "limit")
    if not isinstance(query, str) or len(query) > 4096:
        raise EvidenceError("query must be text of at most 4096 characters")
    filters = {} if filters is None else filters
    if not isinstance(filters, dict) or set(filters) - FILTERS:
        raise EvidenceError("unsupported evidence filters")
    for values in filters.values():
        if not isinstance(values, (list, tuple)) or any(not isinstance(x, str) for x in values):
            raise EvidenceError("filter values must be lists of strings")
    records, coverage, warnings = _collect(ws, filters.get("source_ids"))
    if "source_ids" in filters:
        known = {s["source_id"] for s in ws.manifest["sources"]}
        if set(filters["source_ids"]) - known:
            raise EvidenceError("unknown source_ids filter")
    stamp = digest({"workspace": _scope_identity(ws, coverage), "query": query, "filters": filters, "coverage": coverage})
    start = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError()
            state = json.loads(base64.urlsafe_b64decode(cursor).decode())
            if state["stamp"] != stamp:
                raise StaleCursorError("stale cursor: query, filters, workspace or source watermark changed")
            start = state["offset"]
            _bounded_int(start, 0, MAX_RECORDS * max(1, len(coverage)), "cursor offset")
        except StaleCursorError:
            raise
        except (ValueError, KeyError, TypeError):
            raise EvidenceError("invalid evidence cursor") from None
    matches = [(sid, row) for sid, rows in sorted(records.items()) for rid, row in sorted(rows.items())
               if ("source_ids" not in filters or sid in filters["source_ids"])
               and _matches(row, filters) and query.casefold() in canonical(
                   [row["record"], row.get("projected_content", {})]).casefold()]
    result = {"items": [], "next_cursor": None, "coverage": coverage, "warnings": warnings}
    if len(canonical(result).encode()) > MAX_QUERY_BYTES - 2048:
        raise EvidenceError("coverage exceeds response bound; publish a smaller workspace")
    index = start
    for sid, row in matches[start:start + limit]:
        item = _item(sid, row)
        if len(canonical(item).encode()) > 10000:
            item["conditions"], item["related_refs"], item["truncated"] = None, [], True
        if len(canonical(result).encode()) + len(canonical(item).encode()) > MAX_QUERY_BYTES - 1024:
            break
        result["items"].append(item)
        index += 1
    if index < len(matches):
        result["next_cursor"] = base64.urlsafe_b64encode(canonical({"stamp": stamp, "offset": index}).encode()).decode()
    return result


def _slice_text(text, offset, limit):
    # offsets/limits are UTF-8 bytes; decode is intentionally not attempted mid-codepoint.
    data = text.encode("utf-8")
    end = min(len(data), offset + limit)
    return {"encoding": "base64", "content": base64.b64encode(data[offset:end]).decode(),
            "offset": offset, "next_offset": end if end < len(data) else None, "total_bytes": len(data)}


def read_evidence(workspace, ref, *, view="record", member=None, selector=None, offset=0, limit=100):
    return _read_evidence(workspace, ref, view=view, member=member, selector=selector, offset=offset, limit=limit)


def _read_evidence(workspace, ref, *, view="record", member=None, selector=None, offset=0, limit=100,
                   collection=None):
    """Internal single-call reuse; callers must validate the collected view's lifetime."""
    ws, ref = load_workspace(workspace), validate_ref(ref)
    _bounded_int(offset, 0, 2**63 - 1, "offset")
    _bounded_int(limit, 1, 32768, "limit")
    if view not in {"record", "summary", "lineage", "artifact"}:
        raise EvidenceError("unsupported evidence view")
    selector = selector if selector is not None else ref.get("selector") if view == "artifact" else None
    if selector not in (None, "bytes"):
        raise EvidenceError("only the bytes selector is supported")
    if member is not None and (not isinstance(member, str) or not member):
        raise EvidenceError("member must be explicit text")
    if view != "artifact" and (member is not None or selector is not None):
        raise EvidenceError("member/selector only apply to artifact view")
    ws.source(ref["source_id"])
    records, coverage, warnings = collection if collection is not None else _collect(ws, [ref["source_id"]])
    row = records.get(ref["source_id"], {}).get(ref["record_id"])
    if row is None:
        result = {"ref": ref, "availability": "unavailable", "verification": "not_checked",
                  "missing": ["record_not_in_published_view"], "coverage": [], "warnings": []}
        for cov in coverage:
            if len(canonical(result).encode()) + len(canonical(cov).encode()) > MAX_READ_BYTES - 1024:
                result["truncated"] = True
                break
            result["coverage"].append(cov)
        result["warnings"] = [{"code": "see_source_coverage"}] if warnings else []
        return result
    body = row["record"]
    if digest(body) != ref["record_digest"]:
        raise EvidenceError("record digest mismatch")
    result = {"ref": ref, "view": view, "availability": "available", "verification": {"record": "verified", "artifact": "not_checked"},
              "source": {"source_id": ref["source_id"]}, "missing": []}
    if "projection" in row:
        result["projection"] = row["projection"]
        result["verification"]["artifact"] = row["projection"]["status"]
        if row["projection"]["status"] != "verified":
            result["missing"].append("content_projection_unavailable")
    if view == "summary":
        result["item"] = _item(ref["source_id"], row)
    elif view == "record":
        if offset == 0 and len(canonical(body).encode()) <= min(32768, limit * 1024):
            result["record"] = body
            result["next_offset"] = None
        else:
            result["fragment"] = _slice_text(canonical(body), offset, min(limit, 32768))
    elif view == "lineage":
        refs = []
        details = []
        targets = _relationships(row)
        consumed = 0
        for target in targets[offset:offset + min(limit, 50)]:
            sid = target.get("source_id") if isinstance(target, dict) else ref["source_id"]
            rid = target.get("record_id") if isinstance(target, dict) else target
            linked = records.get(sid, {}).get(rid)
            kind = "exact" if isinstance(target, dict) else "legacy_string"
            status = "unavailable"
            resolved = target
            if isinstance(target, dict):
                validate_ref(target)
                if linked:
                    status = "verified" if digest(linked["record"]) == target["record_digest"] else "digest_mismatch"
                    if status == "verified" and ("member" in target or "sha256" in target):
                        artifact = linked["record"]
                        if "member" in target:
                            members = linked.get("members", artifact.get("members", []))
                            candidates = [m for m in members if m.get("member") == target["member"] and m.get("visibility") == "released"]
                            artifact = candidates[0] if len(candidates) == 1 else {}
                        if "sha256" in target and (target["sha256"], target["bytes"]) != (artifact.get("sha256"), artifact.get("bytes")):
                            status = "artifact_identity_mismatch"
                        elif "member" in target and not artifact:
                            status = "member_unavailable"
            elif linked:
                resolved = make_ref(sid, rid, linked["record"])
                status = "resolved_current_metadata"
            detail = {"ref": resolved, "reference_kind": kind, "status": status,
                      "verification_scope": "record_and_declared_member_metadata"}
            trial_refs, trial_missing = list(refs), list(result["missing"])
            if status in {"verified", "resolved_current_metadata"}:
                trial_refs.append(resolved)
            else:
                trial_missing.append({"ref": target, "reason": status})
            trial = {**result, "items": trial_refs, "relationships": details + [detail], "missing": trial_missing}
            if len(canonical(trial).encode()) > MAX_READ_BYTES - 1024:
                break
            refs, result["missing"] = trial_refs, trial_missing
            details.append(detail)
            consumed += 1
        result["items"] = refs
        result["relationships"] = details
        end = offset + consumed
        result["next_offset"] = end if end < len(targets) else None
        result["scope"] = "direct_published_relationships"
    else:
        member = member if member is not None else ref.get("member")
        if ref.get("member") is not None and member != ref["member"]:
            raise EvidenceError("member conflicts with ref")
        artifact = body
        if member is not None:
            members = row.get("members", body.get("members", []))
            matches = [m for m in members if m.get("member") == member and m.get("visibility") == "released"]
            if len(matches) != 1:
                raise EvidenceError("member is not uniquely published")
            artifact = matches[0]
        elif row["record_type"] != "artifact":
            raise EvidenceError("record is not an artifact; select an explicitly published member")
        _identity(artifact)
        if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (artifact["sha256"], artifact["bytes"]):
            raise EvidenceError("ref artifact identity mismatch")
        try:
            path = EvidenceResolver(ws, ref["source_id"]).resolve(artifact["path"], sha256=artifact["sha256"], bytes=artifact["bytes"])
            with path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read(limit)
            # Detect replacement between validation and fragment read.
            if file_identity(path) != {"sha256": artifact["sha256"], "bytes": artifact["bytes"]}:
                raise EvidenceError("artifact changed during read")
            result["verification"]["artifact"] = "verified"
            result["ref"] = {**ref, "sha256": artifact["sha256"], "bytes": artifact["bytes"], "selector": "bytes"}
            if member is not None:
                result["ref"]["member"] = member
            end = offset + len(data)
            result["fragment"] = {"encoding": "base64", "content": base64.b64encode(data).decode(), "offset": offset,
                                  "next_offset": end if end < artifact["bytes"] else None, "total_bytes": artifact["bytes"]}
        except OSError as exc:
            result.update(availability="unavailable", missing=[type(exc).__name__])
    if len(canonical(result).encode()) > MAX_READ_BYTES:
        # Metadata can be arbitrarily large too. Return a bounded original JSON fragment.
        result = {"ref": ref, "view": view, "truncated": True,
                  "fragment": _slice_text(canonical(body), offset, min(limit, 32768)),
                  "verification": {"record": "verified", "artifact": "not_checked"}}
    return result
