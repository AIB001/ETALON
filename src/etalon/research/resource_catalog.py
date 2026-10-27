"""Read-only resource inventory over a published scope and explicit skill bundles.

Discovery is not selection, activation, consumption, or scientific applicability.
No catalog database or ambient directory traversal is created. Cursor state is
disposable; the ordinary evidence readers recheck originals on every request.
"""
from __future__ import annotations

import base64
import json

from etalon import skills
from etalon.active.schema import canonical, digest
from .evidence import StaleCursorError, query_evidence, read_evidence
from .evidence_refs import CONTENT_SCHEMAS, EvidenceError, load_workspace
from .meta_context import (
    INDEX, KINDS, RESOURCE, WORKSET, _dependencies, _index_check,
    describe_resource, index_resources, validate_workset,
)
from .read_scope import bounded_read

SCHEMA = "etalon-resource-catalog/1"
MAX_RESPONSE_BYTES = 65536
MAX_SKILLS = 64
QUERY_PAGE = 50
TYPED_KINDS = {
    "etalon-research-note/1": "experience",
    "etalon-method-version/1": "method",
    "etalon-research-workflow/1": "workflow",
    "etalon-research-claim/1": "claim",
    "etalon-method-experience/1": "experience",
}


def _note_catalog(entry, *, kinds, skill_refs, purpose, limit, cursor, max_bytes, source_ids):
    """Expose current project heads through the existing catalog, not a new index."""
    from .notes import read_entry
    selected = _selection(kinds if kinds is not None else sorted(KINDS), "kinds", allowed=KINDS)
    if purpose not in {None, "research"} or source_ids is not None:
        raise EvidenceError("note catalog uses its project journal in research scope")
    skill_rows = _skill_inventory(skill_refs, None)
    branches = sorted(entry.get("branches", {entry["branch_id"]: entry["record_id"]}))
    stamp = digest({"entry": entry, "kinds": selected, "skills": skill_rows})
    offset = 0
    if cursor is not None:
        try:
            saved = json.loads(base64.urlsafe_b64decode(cursor))
            if saved["stamp"] != stamp:
                raise StaleCursorError("project heads or selection changed; restart catalog")
            _bounded(saved["offset"], 0, len(branches) + len(skill_rows), "offset")
            offset = saved["offset"]
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, EvidenceError):
                raise
            raise EvidenceError("invalid project catalog cursor") from None
    rows = []
    def render(end):
        more = end < len(branches) + len(skill_rows)
        return dict(schema=SCHEMA, items=rows,
            next_cursor=base64.urlsafe_b64encode(canonical({"stamp": stamp, "offset": end}).encode()).decode() if more else None,
            coverage={"scope": "current_project_heads_and_selected_skills", "discovery_complete": not more,
                "status": "partial" if more else "complete", "originals": "not_read",
                "outside_scope": "historical notes remain available through find_notes and exact references"},
            warnings=[], automatic_adoption=False, execution_permission="not_granted")
    while offset < len(branches) + len(skill_rows) and len(rows) < limit:
        if offset < len(branches):
            context = read_entry(entry, branch_id=branches[offset], view="knowledge")
            k = context["knowledge"]
            row = dict(kind="experience", resource_id=context["record_id"], branch_id=context["branch_id"],
                summary=k["summary"], conditions=k.get("conditions", {}), outcome=k.get("outcome"),
                content_ref=context["details"], coverage=context["coverage"], applicability="host_selects")
            if "definitions" in context:
                row["definitions"] = context["definitions"]
        else:
            row = skill_rows[offset - len(branches)]
        if row["kind"] in selected:
            rows.append(row)
            if len(canonical(render(offset + 1)).encode()) > max_bytes:
                rows.pop()
                if not rows:
                    raise EvidenceError("one resource exceeds delivery budget; use its exact knowledge entry")
                break
        offset += 1
    return render(offset)


def _bounded(value, lower, upper, name):
    if type(value) is not int or not lower <= value <= upper:
        raise EvidenceError(f"{name} must be {lower}..{upper}")


def _selection(value, name, *, allowed=None):
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) or not v for v in value):
        raise EvidenceError(name + " must be an explicit list of identifiers")
    if len(set(value)) != len(value) or allowed is not None and not set(value) <= allowed:
        raise EvidenceError("duplicate or unknown " + name)
    return sorted(value)


def _skill_inventory(refs, ws):
    from .skill_sources import resolve_skill_bundle
    if not isinstance(refs, (list, tuple)) or len(refs) > MAX_SKILLS:
        raise EvidenceError("skill_refs must select at most 64 exact bundles")
    rows, seen = [], set()
    for selected in refs:
        selected = {"slug": selected} if isinstance(selected, str) else selected
        if not isinstance(selected, dict) or set(selected) - {"slug", "manifest_sha256", "bundle_ref"}:
            raise EvidenceError("skill reference requires slug and optional manifest_sha256/bundle_ref")
        slug = selected.get("slug")
        # An unpinned explicit slug selects the installed version now. A pinned
        # version uses existing relocation rules and never falls back on error.
        bundle = (resolve_skill_bundle(selected, ws) if "manifest_sha256" in selected
                  else selected.get("bundle_ref"))
        try:
            manifest, _ = skills.snapshot(slug, bundle_ref=bundle)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise EvidenceError("selected skill unavailable or changed: " + str(exc)) from exc
        if selected.get("manifest_sha256", manifest["manifest_sha256"]) != manifest["manifest_sha256"]:
            raise EvidenceError("selected skill manifest digest changed")
        from pathlib import Path
        from .read_scope import CURRENT
        root = Path(bundle["root"]) if bundle is not None else skills.bundle_root(slug)
        if CURRENT.get() is not None:
            for member in manifest["files"]:
                CURRENT.get().observe_file(root / member["path"], {k: member[k] for k in ("sha256", "bytes")})
        content_ref = {"slug": slug, "manifest_sha256": manifest["manifest_sha256"]}
        if bundle is not None:
            content_ref["bundle_ref"] = bundle
        key = slug, manifest["manifest_sha256"]
        if key in seen:
            raise EvidenceError("duplicate skill version")
        seen.add(key)
        rows.append(dict(catalog_id="skill-" + digest(content_ref), kind="skill", resource_id=slug,
            content_ref=content_ref, origin_ref=None, summary=manifest["description"],
            descriptor=None, parts=[dict(part_id=f["path"], identity={k: f[k] for k in ("sha256", "bytes")},
                                        conditions=None, applicability="unknown") for f in manifest["files"]],
            declared_scope=None, support_refs=[], counter_refs=[], supersedes=[],
            provenance={"discovery": "explicit_skill_selection", "manifest": manifest},
            verification={"record": "not_applicable", "artifact": "verified", "descriptor": "not_declared"},
            availability="available", applicability="unknown", missing=["declared_applicability"],
            execution_permission="not_granted"))
    return sorted(rows, key=lambda r: r["catalog_id"])


def _base_row(item, body):
    ref = item["ref"]
    return dict(catalog_id="record-" + digest(ref), kind=TYPED_KINDS.get(body.get("schema"), "evidence"),
        resource_id=ref["record_id"], content_ref=ref, origin_ref=ref,
        summary=item["summary"], summary_truncated=item["truncated"], descriptor=None, parts=[],
        declared_scope={k: body[k] for k in ("conditions", "scope", "invalid_when", "roles") if k in body} or None,
        support_refs=body.get("support_refs", []), counter_refs=body.get("counter_refs", []),
        supersedes=body.get("supersedes", []),
        provenance={"discovery": "published_record", "source": item["source"],
                    "record_type": item["record_type"], "schema": body.get("schema"),
                    "declarations": {k: body[k] for k in ("parents", "evidence_refs", "content_ref", "role",
                        "source_kind", "verification_basis", "status", "outcome", "epistemic_status") if k in body}},
        verification={"record": "verified", "artifact": "not_checked", "descriptor": "not_declared"},
        availability="available", applicability="unknown", missing=["declared_resource_parts"],
        execution_permission="not_granted")


def _descriptor_row(ws, descriptor, origin_ref, purpose, discovery):
    # Filter before summary/parts are exposed, just as the existing index does.
    if descriptor.get("visibility") == "evaluation_only" and purpose != "evaluation":
        return None
    verified = index_resources([descriptor], workspace=ws, purpose=purpose)["resources"][0]
    # Reuse the constructor as the sole descriptor contract, without inventing
    # conditions for records that did not carry one.
    rebuilt = describe_resource(**{k: v for k, v in verified.items() if k not in {"schema", "digest"}})
    return dict(catalog_id="descriptor-" + digest([origin_ref, rebuilt["digest"]]),
        kind=rebuilt["kind"], resource_id=rebuilt["resource_id"], content_ref=rebuilt["content_ref"],
        origin_ref=origin_ref, summary=rebuilt["summary"], descriptor=rebuilt, parts=rebuilt["parts"],
        declared_scope={"parts": "conditions in exact published descriptor", "visibility": rebuilt["visibility"]},
        support_refs=rebuilt["support_refs"], counter_refs=rebuilt["counter_refs"], supersedes=[],
        provenance={"discovery": discovery, "descriptor_digest": rebuilt["digest"],
                    "activation": "not_inferred_from_inventory"},
        verification={"record": "verified", "artifact": "verified_where_referenced",
                      "descriptor": "verified", "support_and_counter_refs": "verified"},
        availability="available", applicability="declared_only", missing=[], execution_permission="not_granted")


def _rows_for_record(ws, item, purpose):
    from .improvement import read_material
    from .evidence import _visible
    result = read_evidence(ws, item["ref"], limit=32)
    body = result.get("record")
    if result.get("availability") != "available" or body is None:
        raise EvidenceError("catalog record is unavailable or exceeds the record bound")
    if body.get("visibility") == "evaluation_only" and purpose != "evaluation":
        return [], 1, []
    content, artifact_verification = body, "not_applicable"
    if item["record_type"] == "artifact":
        checked = read_evidence(ws, item["ref"], view="artifact", limit=1)
        artifact_verification = checked.get("verification", {}).get("artifact", "not_checked")
        if artifact_verification != "verified":
            raise EvidenceError("catalog artifact original is unavailable or unverified")
        schema = item.get("projection", {}).get("schema", body.get("bindings", {}).get("schema"))
        if schema in CONTENT_SCHEMAS | {RESOURCE, INDEX, WORKSET}:
            material = read_material(ws, item["ref"])
            if not material["complete"] or not isinstance(material["content"], dict):
                raise EvidenceError("catalog typed content exceeds 32 KiB or is not JSON")
            content = material["content"]
            if content.get("schema") != schema:
                raise EvidenceError("catalog content schema differs from its declaration")
    if not _visible(content) or content.get("visibility") == "evaluation_only" and purpose != "evaluation":
        return [], 1, []
    schema = content.get("schema")
    if schema in {RESOURCE, INDEX, WORKSET}:
        if schema == RESOURCE:
            descriptors, discovery = [content], "published_descriptor"
        elif schema == INDEX:
            _index_check(content)
            descriptors, discovery = content["resources"], "published_resource_index"
        else:
            validate_workset(content, verify=False)
            if body.get("bindings", {}).get("evidence_refs") != _dependencies(content):
                raise EvidenceError("workset must bind all content, support and counter dependencies")
            descriptors, discovery = content["index"]["resources"], "published_workset_declaration"
        if schema == INDEX and content["purpose"] == "evaluation" and purpose != "evaluation":
            return [], len(descriptors), []
        rows, hidden, gaps = [], 0, []
        for descriptor in descriptors:
            try:
                row = _descriptor_row(ws, descriptor, item["ref"], purpose, discovery)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # Do not leak an unverifiable descriptor's summary or conditions.
                gaps.append({"origin_ref": item["ref"], "code": "descriptor_unavailable",
                             "reason": str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__})
                continue
            if row is None:
                hidden += 1
            else:
                rows.append(row)
        return rows, hidden, gaps
    row = _base_row(item, content)
    row["verification"]["artifact"] = artifact_verification
    if content is not body:
        row["provenance"]["artifact_record"] = body
        summary = canonical(content)
        row.update(summary=summary[:600], summary_truncated=len(summary) > 600)
    row["missing"] += result.get("missing", [])
    # Every declared counter/support reference is checked, never just the first
    # display page. Preserve the reference even when its original is missing.
    from .meta_context import _verify_evidence
    failures = []
    for key in ("support_refs", "counter_refs"):
        for ref in row[key]:
            try:
                _verify_evidence(ws, ref)
            except (OSError, ValueError, KeyError, TypeError):
                row["missing"].append(key + "_unavailable")
                failures.append({"origin_ref": item["ref"], "ref": ref, "code": key + "_unavailable"})
    row["verification"]["support_and_counter_refs"] = "unavailable" if failures else "verified"
    if row["kind"] == "method":
        # MethodVersion record identity is not proof of its opaque content_ref.
        row["verification"]["method_content"] = "not_checked"
        row["missing"].append("method_content_not_verified")
    return [row], 0, failures


def _decode_cursor(cursor, stamp):
    state = dict(phase="evidence", evidence_cursor=None, item_index=0, row_offset=0,
                 page_stamp=None, candidate_stamp=None, skill_index=0, prior_gap_count=0)
    if cursor is None:
        return state
    try:
        if not isinstance(cursor, str) or len(cursor) > 8192:
            raise ValueError()
        parsed = json.loads(base64.urlsafe_b64decode(cursor).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError()
        if parsed.pop("stamp") != stamp:
            raise StaleCursorError("catalog cursor scope, purpose, source or skill version changed")
        if set(parsed) != set(state) or parsed["phase"] not in {"evidence", "skills"}:
            raise ValueError()
        for key, maximum in (("item_index", QUERY_PAGE), ("row_offset", 64), ("skill_index", MAX_SKILLS),
                             ("prior_gap_count", 2**31 - 1)):
            _bounded(parsed[key], 0, maximum, key)
        if parsed["evidence_cursor"] is not None and not isinstance(parsed["evidence_cursor"], str):
            raise ValueError()
        return parsed
    except StaleCursorError:
        raise
    except (ValueError, TypeError, KeyError):
        raise EvidenceError("invalid catalog cursor") from None


@bounded_read
def build_resource_catalog(workspace=None, *, entry=None, read_context=None,
                           source_ids=None, kinds=None, skill_refs=(), purpose=None,
                           limit=20, cursor=None, scan_limit=50, max_bytes=65536):
    """Inventory a published scope, with bounded output and continued discovery.

    Supply exactly one workspace/entry/read_context. ``source_ids=None`` selects
    that published workspace only; ``[]`` selects no evidence. Skills are never
    ambient: name selected installed slugs or exact content-reference objects.
    ``purpose=None`` inherits a pinned read context, otherwise means research.

    A non-null next_cursor must be followed even when items is empty (a kind
    filter or scan bound can produce an empty partial page). Descriptor rows are
    mechanically verified declarations, never proof of applicability or use.
    """
    from .entry import READ_CONTEXT, resolve_entry, resolve_read_workspace
    _bounded(limit, 1, 50, "limit")
    _bounded(scan_limit, 1, 200, "scan_limit")
    _bounded(max_bytes, 4096, MAX_RESPONSE_BYTES, "max_bytes")
    if entry is not None and workspace is None and read_context is None:
        from .notes import ENTRY, entry_value
        candidate = entry_value(entry)
        if candidate.get("schema") == ENTRY:
            return _note_catalog(candidate, kinds=kinds, skill_refs=skill_refs, purpose=purpose,
                limit=limit, cursor=cursor, max_bytes=max_bytes, source_ids=source_ids)
    # Validate pinned contexts before inspecting their fields, and enforce the
    # exactly-one selector rule before any optional entry metadata is consulted.
    ws = load_workspace(resolve_read_workspace(workspace, entry=entry, read_context=read_context))
    context = read_context
    if entry is not None:
        _, _, _, inherited = resolve_entry(entry)
        context = inherited
    if purpose is None:
        purpose = context["purpose"] if context is not None else "research"
    if purpose not in {"research", "evaluation"}:
        raise EvidenceError("catalog purpose must be research or evaluation")
    if context is not None and context.get("schema") == READ_CONTEXT and purpose != context["purpose"]:
        raise EvidenceError("catalog purpose cannot change a pinned read context")
    selected = _selection(source_ids if source_ids is not None else
                          [s["source_id"] for s in ws.manifest["sources"]], "source_ids",
                          allowed={s["source_id"] for s in ws.manifest["sources"]})
    selected_kinds = _selection(kinds if kinds is not None else sorted(KINDS), "kinds", allowed=KINDS)
    skill_rows = _skill_inventory(skill_refs, ws)
    stamp = digest({"workspace": ws.manifest, "base": str(ws.base), "source_ids": selected,
                    "kinds": selected_kinds, "purpose": purpose,
                    "skills": [r["content_ref"] for r in skill_rows]})
    state = _decode_cursor(cursor, stamp)
    if state["skill_index"] > len(skill_rows):
        raise EvidenceError("catalog cursor skill is outside the selected library")
    rows, coverage, warnings, gaps = [], [], [], []
    scanned, hidden = 0, 0
    byte_bound = False

    def render():
        complete = state["phase"] == "skills" and state["skill_index"] == len(skill_rows)
        continuation = {**state, "prior_gap_count": state["prior_gap_count"] + len(gaps), "stamp": stamp}
        next_cursor = None if complete else base64.urlsafe_b64encode(canonical(continuation).encode()).decode()
        return dict(schema=SCHEMA, items=rows, next_cursor=next_cursor,
            coverage={"scope": "explicit_published_workspace_and_selected_skill_bundles", "global_search": False,
                "source_ids": selected, "kinds": selected_kinds, "purpose": purpose,
                "sources": coverage, "discovery_complete": complete,
                "status": "partial" if not complete or continuation["prior_gap_count"] or warnings or
                    any(c["status"] != "complete" for c in coverage) else "complete",
                "scanned_records_this_page": scanned, "hidden_descriptors_this_page": hidden,
                "selected_skill_count": len(skill_rows), "scan_limit": scan_limit, "max_bytes": max_bytes,
                "byte_bound_reached": byte_bound, "gaps": gaps,
                "gap_observations_prior_pages": state["prior_gap_count"],
                "outside_scope": "other workspaces, unselected installed skills and unpublished material"},
            warnings=warnings, automatic_adoption=False, execution_permission="not_granted",
            applicability="declared conditions only; host review required; no scientific applicability inferred")

    def fits():
        return len(canonical(render()).encode("utf-8")) <= max_bytes

    while state["phase"] == "evidence" and scanned < scan_limit and len(rows) < limit:
        page = query_evidence(ws, filters={"source_ids": selected}, limit=QUERY_PAGE,
                              cursor=state["evidence_cursor"])
        coverage, warnings = page["coverage"], page["warnings"]
        page_stamp = digest({"items": [i["ref"] for i in page["items"]], "coverage": coverage})
        if state["page_stamp"] is not None and state["page_stamp"] != page_stamp:
            raise StaleCursorError("catalog source page changed; restart discovery")
        state["page_stamp"] = page_stamp
        if state["item_index"] > len(page["items"]):
            raise EvidenceError("catalog cursor item is outside its page")
        while state["item_index"] < len(page["items"]) and scanned < scan_limit and len(rows) < limit:
            item = page["items"][state["item_index"]]
            scanned += 1
            try:
                candidates, excluded, failures = _rows_for_record(ws, item, purpose)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                candidates, excluded = [], 0
                failures = [{"origin_ref": item["ref"], "code": "resource_unavailable",
                             "reason": str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__}]
            hidden += excluded
            gaps.extend(failures)
            candidates = [r for r in candidates if r["kind"] in selected_kinds]
            candidate_stamp = digest(candidates)
            if state["candidate_stamp"] is not None and state["candidate_stamp"] != candidate_stamp:
                raise StaleCursorError("catalog descriptor verification changed; restart discovery")
            state["candidate_stamp"] = candidate_stamp
            if state["row_offset"] > len(candidates):
                raise StaleCursorError("catalog descriptor rows changed; restart discovery")
            while state["row_offset"] < len(candidates) and len(rows) < limit:
                row = candidates[state["row_offset"]]
                rows.append(row)
                state["row_offset"] += 1
                if not fits():
                    rows.pop()
                    state["row_offset"] -= 1
                    if not rows:
                        raise EvidenceError("resource and complete references exceed max_bytes; increase budget or narrow source scope")
                    byte_bound = True
                    break
            if state["row_offset"] == len(candidates):
                state["item_index"] += 1
                state["row_offset"] = 0
                state["candidate_stamp"] = None
            if byte_bound:
                break
        if state["item_index"] == len(page["items"]):
            state.update(evidence_cursor=page["next_cursor"], item_index=0, row_offset=0,
                         page_stamp=None, candidate_stamp=None)
            if page["next_cursor"] is None:
                state["phase"] = "skills"
        if byte_bound:
            break
    # Even a skill-only continuation reports source coverage, never an invented
    # successful empty evidence scope.
    if not coverage:
        page = query_evidence(ws, filters={"source_ids": selected}, limit=1)
        coverage, warnings = page["coverage"], page["warnings"]
    while state["phase"] == "skills" and state["skill_index"] < len(skill_rows) and len(rows) < limit:
        row = skill_rows[state["skill_index"]]
        state["skill_index"] += 1
        if row["kind"] in selected_kinds:
            rows.append(row)
            if not fits():
                rows.pop()
                state["skill_index"] -= 1
                if not rows:
                    raise EvidenceError("skill manifest and exact members exceed max_bytes; increase budget")
                byte_bound = True
                break
    result = render()
    if not fits():
        raise EvidenceError("catalog metadata exceeds max_bytes; increase budget or select fewer sources/scan_limit")
    return result
