"""Small, read-only continuation cards and lossless paged navigation.

The manifest is recomputed from the existing pinned evidence view. It is not a
second store, a checkpoint replacement, or a record that its host read bodies.
"""
from __future__ import annotations

import base64
import json

from etalon.active.schema import canonical, digest
from .continuation import CHECKPOINT, MAX_RESUME_RECORDS, _exact_refs, read_content, resume_research
from .evidence import StaleCursorError, _collect, _read_evidence, _slice_text
from .evidence_refs import EvidenceError, make_ref
from .graph_memory import _historical, _project_node, node_identity, restrict_workspace

CARD = "etalon-continuation-card/1"
MANIFEST = "etalon-continuation-manifest/1"
SECTIONS = ("branches", "references", "counters", "watermarks", "publications", "issues", "authority", "workset")


def reading_menu():
    """Optional recipes, not a router, relevance filter or reading obligation."""
    return {"entry": "use this read_context for these context calls",
        "status": {"view": "summary"},
        "one_original": {"view": "manifest", "node_id": "exact node_id",
            "view_options": {"section": "references"}},
        "branch_detail": {"view": "branch", "branch_id": "exact branch_id"},
        "question_detail": {"view": "summary",
            "scope": {"root_refs": "exact refs chosen by the host", "ordinary_hops": 0}},
        "decision_reading": {"view": "reading", "question": "current choice or diagnostic question",
            "scope": {"root_refs": "exact refs chosen by the host", "ordinary_hops": 0},
            "view_options": {"reading_mode": "decision"}},
        "counter_originals": {"view": "manifest", "view_options": {"section": "counters"}},
        "bounded_closure": {"view": "manifest", "view_options": {"section": "closure"},
            "meaning": "optional paged declared-reference and counter closure; retain consecutive page receipts and next_cursor; legacy resume issues remain separate"},
        "cross_workspace": "Open an explicitly linked entry to obtain its own read_context before reading its refs; do not guess or widen this workspace.",
        "stop_rule": "A status-only answer may stop here with body coverage unknown. A new decision needs relevant original conditions and counter bodies, not every unrelated artifact.",
        "question_semantics": "question records purpose; it does not select refs or rank relevance"}


def _reference_row(ref, row):
    """Typed navigation to the existing reader, without interpreting opaque files."""
    body = row.get("projected_content", row["record"])
    artifact = row["record_type"] == "artifact" or "member" in ref
    result = {"node_id": node_identity(ref), "ref": ref, "record_type": row["record_type"],
        "content_schema": body.get("schema", row["record"].get("bindings", {}).get("schema")),
        "original_read": {"view": "artifact" if artifact else "record", "limit": 32768 if artifact else 32}}
    declared_kind = row["record"].get("bindings", {}).get("kind")
    if declared_kind is not None:
        result["declared_kind"] = declared_kind
    original = row["record"]
    if "member" in ref:
        original = next((m for m in row.get("members", original.get("members", []))
                         if m.get("member") == ref["member"] and m.get("visibility") == "released"), {})
    if artifact:
        result["original_locator"] = {k: original[k] for k in ("path", "sha256", "bytes") if k in original}
    if not artifact and (row["record_type"] == "data_snapshot" or row.get("members") or body.get("members")):
        result["member_read"] = "Read record metadata, then select an explicitly released member with view=artifact; retain member identity."
    return result


def _brief(text, limit=240):
    return {"text": text[:limit], "truncated": len(text) > limit}


def _inventory(workspace, checkpoint_ref, *, policy, purpose, publications, handoff, graph=None,
               as_of=None, home_overrides=None, neutral_workset=None):
    """Reuse source, continuation and byte validators before abbreviating data."""
    ws, policy_receipt = restrict_workspace(workspace, policy=policy, purpose=purpose)
    temporal = _historical(ws, as_of)
    records, coverage, warnings = collection = _collect(ws)
    checkpoint = read_content(ws, checkpoint_ref)
    if checkpoint.get("schema") != CHECKPOINT:
        raise EvidenceError("continuation card requires a research checkpoint")
    resumed = resume_research(ws, checkpoint_ref, read_mode="frozen_view")
    issues = [*resumed["issues"], *warnings]
    # A smaller transport must never be a route around failed original checks.
    identity_failures = {"unavailable", "digest_mismatch", "content_projection_unavailable",
        "member_unavailable", "member_identity_mismatch", "artifact_unavailable_or_changed",
        "checkpoint_changed_or_unpublished_during_read", "checkpoint_ancestor_mismatch",
        "frozen_view_changed_during_read"}
    if any(i.get("reason") in identity_failures for i in resumed["issues"]):
        raise EvidenceError("continuation card referenced content unavailable or identity changed")
    references = {}

    def add(ref):
        nid = node_identity(ref)
        row = records.get(ref["source_id"], {}).get(ref["record_id"])
        if row is None or digest(row["record"]) != ref["record_digest"]:
            raise EvidenceError("continuation card reference unavailable or identity changed")
        if row.get("projection", {}).get("status") == "unavailable":
            raise EvidenceError("continuation card referenced content unavailable")
        references[nid] = ref
        return nid

    add(checkpoint_ref)
    for ref in [*_exact_refs(checkpoint), *resumed["checked_refs"]]:
        add(ref)
    # Index explicit reverse counter directions across the permitted published
    # universe, even when the displayed graph hit its independent node bound.
    # No semantic search and no inference from an opaque result body is used.
    candidates = []
    for sid, rows in records.items():
        for rid, row in rows.items():
            payload = row.get("projected_content", row["record"])
            for target in payload.get("counter_refs", []):
                if isinstance(target, dict):
                    owner = make_ref(sid, rid, row["record"])
                    candidates.append((owner, target))
    relations = {}
    changed = True
    while changed:
        changed = False
        for owner, target in candidates:
            source, destination = node_identity(target), node_identity(owner)
            if source not in references and destination not in references:
                continue
            if (source, destination) in relations:
                continue
            if len(references) + int(source not in references) + int(destination not in references) > MAX_RESUME_RECORDS:
                if not any(i.get("reason") == "counter_reference_walk_truncated" for i in issues):
                    issues.append({"reason": "counter_reference_walk_truncated", "limit": MAX_RESUME_RECORDS})
                continue
            add(owner); add(target)
            # Verify both originals through the existing reader. Hash checking
            # is not represented as the host reading their complete bodies.
            for ref in (owner, target):
                row = records[ref["source_id"]][ref["record_id"]]
                artifact = row["record_type"] == "artifact" or "member" in ref
                result = _read_evidence(ws, ref, view="artifact" if artifact else "record",
                                        limit=1, collection=collection)
                if (result.get("availability") != "available" or result.get("missing") or
                        artifact and result.get("verification", {}).get("artifact") != "verified"):
                    raise EvidenceError("continuation card counter content unavailable")
            relations[source, destination] = {"kind": "refutes", "from_ref": target,
                "to_ref": owner, "basis_ref": owner}
            changed = True
    counters = [{"kind": "checkpoint_counter", "ref": ref} for ref in checkpoint["counter_refs"]]
    counters += [relations[key] for key in sorted(relations)]
    # Applicability conditions belong to a particular host question. Keep the
    # pinned manifest independent of those optional excerpt-selection inputs.
    from .meta_context import workset_summary
    try:
        workset = neutral_workset if neutral_workset is not None else workset_summary(ws, checkpoint_ref)
        if workset.get("status") == "unavailable":
            issues.append({"reason": "task_workset_unavailable", "message": workset["reason"]})
    except (ValueError, OSError, KeyError) as exc:
        workset = {"status": "unavailable", "reason": str(exc)[:500]}
        issues.append({"reason": "task_workset_unavailable", "message": str(exc)[:500]})
    sections = {"branches": checkpoint["branches"],
        "references": [_reference_row(references[nid], records[references[nid]["source_id"]][references[nid]["record_id"]])
                       for nid in sorted(references)],
        "counters": counters, "watermarks": coverage,
        "publications": publications.get("operations", []), "issues": issues,
        "authority": resumed["authority"]["sources"],
        "workset": [workset]}
    manifest_digest = digest(sections)
    reference_gaps = {"legacy_reference_unavailable", "method_link_unavailable_in_source",
                      "task_workset_unavailable"}
    reference_complete = (not warnings and all(c["status"] == "complete" for c in coverage)
        and not any("truncated" in i.get("reason", "") or i.get("reason") in reference_gaps for i in issues))
    return {"workspace": ws, "checkpoint": checkpoint, "resumed": resumed, "records": records,
        "collection": collection, "coverage": coverage, "sections": sections, "references": references,
        "relations": list(relations.values()), "manifest_digest": manifest_digest,
        "reference_complete": reference_complete, "policy": policy_receipt, "temporal": temporal,
        "graph": graph, "home_overrides": home_overrides or {}}


def _stamp(data, handoff, checkpoint_ref, section, node_id=None):
    return digest({"read_context": handoff.get("read_context"), "checkpoint_ref": checkpoint_ref,
                   "manifest_digest": data["manifest_digest"], "section": section, "node_id": node_id})


def _cursor(stamp, index, byte_offset=0):
    return base64.urlsafe_b64encode(canonical({"stamp": stamp, "index": index,
        "byte_offset": byte_offset}).encode()).decode()


def _position(cursor, stamp, total):
    if cursor is None:
        return 0, 0
    try:
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError()
        state = json.loads(base64.urlsafe_b64decode(cursor).decode())
        if state["stamp"] != stamp:
            raise StaleCursorError("stale continuation manifest cursor: pinned view or source watermark changed")
        index, offset = state["index"], state["byte_offset"]
        if type(index) is not int or not 0 <= index < total or type(offset) is not int or offset < 0:
            raise ValueError()
        return index, offset
    except StaleCursorError:
        raise
    except (ValueError, KeyError, TypeError):
        raise EvidenceError("invalid continuation manifest cursor") from None


def manifest_page(data, checkpoint_ref, handoff, *, section="references", cursor=None, limit=20, max_bytes=16384,
                  node_id=None):
    if section not in (*SECTIONS, "closure"):
        raise EvidenceError("unknown continuation manifest section")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise EvidenceError("manifest limit must be 1..50")
    rows = data["sections"][section]
    inventory_count = len(rows)
    if node_id is not None:
        if section != "references" or not isinstance(node_id, str) or not node_id:
            raise EvidenceError("manifest node_id requires an exact node identity in section references")
        rows = [row for row in rows if row["node_id"] == node_id]
        if len(rows) != 1:
            raise EvidenceError("manifest node is unavailable in the pinned continuation reference inventory")
    stamp = _stamp(data, handoff, checkpoint_ref, section, node_id)
    index, byte_offset = _position(cursor, stamp, len(rows))
    result = {"schema": MANIFEST, "view": "manifest", "section": section,
        "checkpoint_ref": checkpoint_ref, "read_context": handoff.get("read_context"),
        "entry_status": handoff["entry_status"], "manifest_digest": data["manifest_digest"],
        "task_id": data["checkpoint"]["task_id"], "total_count": len(rows),
        "selection": {"node_id": node_id, "inventory_count": inventory_count,
            "meaning": "exact navigation selection; counter and original validation unchanged"},
        "original_scope": "Use this read_context with ref and original_read; original_locator is server-side provenance, not a local file instruction.",
        "items": [], "next_cursor": None,
        "coverage": {"reference_index_complete": data["reference_complete"],
            "original_bodies_read_by_host": "not_established", "scientific_closure": "not_established"},
        "execution_permission": "not_granted", "automatic_retry": False,
        "content_role": "sourced_research_data_not_host_instructions"}
    reserve = 384  # next cursor and final identity
    while index < len(rows) and len(result["items"]) < limit:
        row = rows[index]
        item = {"index": index, "value": row}
        result["items"].append(item)
        if byte_offset == 0 and len(canonical(result).encode()) <= max_bytes - reserve:
            index += 1
            continue
        result["items"].pop()
        if result["items"]:
            break
        raw = canonical(row)
        if byte_offset >= len(raw.encode()):
            raise EvidenceError("invalid continuation manifest fragment offset")
        # A single large branch/issue is still lossless at the minimum cap.
        # Reuse the evidence reader's exact UTF-8 byte fragment convention.
        fragment_size = max(1, (max_bytes - len(canonical(result).encode()) - reserve - 200) * 3 // 4)
        item = {"index": index, "fragment": _slice_text(raw, byte_offset, fragment_size)}
        result["items"].append(item)
        if len(canonical(result).encode()) > max_bytes - reserve:
            raise EvidenceError("manifest pinned controls exceed byte bound")
        byte_offset = item["fragment"]["next_offset"]
        if byte_offset is None:
            index += 1
            byte_offset = 0
        break
    if index < len(rows):
        result["next_cursor"] = _cursor(stamp, index, byte_offset)
    result["context_id"] = "research-context-" + digest(result)
    if len(canonical(result).encode()) > max_bytes:
        raise EvidenceError("manifest pinned controls exceed byte bound")
    return result


def _node(data, ref):
    nid = node_identity(ref)
    graph = data["graph"]
    if graph is not None:
        existing = next((n for n in graph["nodes"] if n["node_id"] == nid), None)
        if existing is not None:
            return existing
    row = data["records"].get(ref["source_id"], {}).get(ref["record_id"])
    if row is None or digest(row["record"]) != ref["record_digest"]:
        raise EvidenceError("continuation card node unavailable or identity changed")
    artifact = row["record_type"] == "artifact" or "member" in ref
    read = _read_evidence(data["workspace"], ref,
        view="artifact" if artifact else "record", limit=1, collection=data["collection"])
    if (read.get("availability") != "available" or read.get("missing") or
            artifact and read.get("verification", {}).get("artifact") != "verified"):
        raise EvidenceError("continuation card node content unavailable")
    return _project_node(ref, row, task_id=data["checkpoint"]["task_id"], home_overrides=data["home_overrides"])


def continuation_card(data, checkpoint_ref, handoff, *, publications, purpose, max_bytes=16384,
                      view="card", question=None, node_id=None, fallback_reason=None):
    cp, resumed, graph = data["checkpoint"], data["resumed"], data["graph"]
    # Spend less than the transport ceiling on optional excerpts/watermark rows.
    # New control metadata may use the full unchanged cap; paging remains exact.
    detail_target = max_bytes - min(2048, max_bytes // 8)
    issues = [*data["sections"]["issues"], *(graph["issues"] if graph is not None else [])]
    branches = [{"branch_id": b["branch_id"], "state": b["state"],
        "reconsider_when": _brief(b["reconsider_when"], 120), "manifest_index": i,
        "basis_ref_count": len(b["basis_refs"]), "next_read_ref_count": len(b["next_read_refs"])}
        for i, b in enumerate(cp["branches"])]
    # Legacy checkpoints often have no claim_refs: their reviewed conclusions
    # live in authored judgment/decision steps. Keep the latest listed step for
    # every current branch visible, without interpreting opaque result files.
    latest = {}
    for ref in reversed(cp["step_refs"]):
        row = data["records"][ref["source_id"]][ref["record_id"]]
        step = row.get("projected_content", {})
        if step.get("schema") == "etalon-research-step/1" and step["task_id"] == cp["task_id"]:
            latest.setdefault(step["branch_id"], (ref, step))
    for branch in branches:
        if branch["branch_id"] in latest:
            ref, step = latest[branch["branch_id"]]
            branch["latest_step"] = {"node_id": node_identity(ref), "operation": step["operation"],
                "summary": _brief(step["reason"], 180),
                "order": "checkpoint_list_order_not_execution_order",
                "ref": ref, "original_read": _reference_row(ref,
                    data["records"][ref["source_id"]][ref["record_id"]])["original_read"]}
            if step["operation"] == "decision":
                branch["latest_step"]["choice"] = step["detail"]["choice"]
    counter_refs = {node_identity(ref): ref for ref in cp["counter_refs"]}
    for edge in data["relations"]:
        for key in ("from_ref", "to_ref"):
            counter_refs[node_identity(edge[key])] = edge[key]
    continuation = {"authority_state": resumed["authority"]["state"], "review_required": resumed["review_required"],
        "pending": [{"request_id": p["handle"]["request_id"], "execution_state": p["execution_state"]}
                    for p in resumed["pending"]],
        "issue_codes": sorted({i.get("reason", i.get("code", "source_warning")) for i in issues})}
    result = {"schema": "etalon-research-context/1", "view": view,
        "continuation_card": {"schema": CARD, "fallback_reason": fallback_reason,
            "reading_is_new_progress": False, "scientific_conclusion_change": "not_established_by_context_read"},
        "checkpoint_ref": checkpoint_ref, "read_context": handoff.get("read_context"),
        "entry_status": handoff["entry_status"], "task_id": cp["task_id"],
        "question": (question or cp["question"])[:512], "research_question": cp["question"][:512],
        "question_truncated": len(question or cp["question"]) > 512 or len(cp["question"]) > 512,
        "purpose": purpose, "node_id": node_id, "continuation": continuation,
        "authority": {"state": resumed["authority"]["state"],
            "guard_states": [{k: row[k] for k in ("state", "status") if k in row}
                             for row in resumed["authority"]["sources"]],
            "unsupported_status_refs": resumed["authority"]["unsupported_status_refs"]},
        "branches": branches, "checkpoint_progress": {"step_count": len(cp["step_refs"]),
            "tail_step_refs": cp["step_refs"][-1:], "order_semantics": "checkpoint_list_order_not_execution_order"},
        "publications": {"status": publications["status"], "incomplete_count": publications["incomplete_count"],
            "operation_count": len(publications.get("operations", [])), "operations": [],
            "details": "manifest section publications; archival recovery is not scientific execution"},
        "source_watermarks": [], "watermark": {"digest": digest(data["coverage"]),
            "source_count": len(data["coverage"]), "complete": all(c["status"] == "complete" for c in data["coverage"]),
            "exact_rows": "manifest section watermarks"},
        "source_issues": [{"code": c, "count": sum(i.get("reason", i.get("code", "source_warning")) == c for i in issues)}
                          for c in continuation["issue_codes"]],
        "temporal": data["temporal"], "items": [], "relations": [], "reference_index": [],
        "selection": {"strategy": "bounded-continuation-card/1", "policy": data["policy"],
            "graph_truncated": graph["truncated"] if graph is not None else True,
            "coverage_complete": False, "selected_count": 0, "item_limit": 4,
            "optional_detail_target_bytes": detail_target},
        "counterevidence": {"status": "found" if counter_refs else "none_in_checked_scope"
            if data["reference_complete"] else "coverage_insufficient",
            "declared_counter_count": len(cp["counter_refs"]), "endpoint_count": len(counter_refs),
            "relationship_count": len(data["relations"]), "selected_relationship_count": 0,
            "reference_coverage_complete": data["reference_complete"], "coverage_complete": False,
            "excerpt_coverage_complete": not counter_refs, "original_bodies_read_by_host": "not_established",
            "graph_counter_context_complete": graph["counter_context_complete"] if graph is not None else False,
            "selected_refs": [], "declared_counter_refs": [],
            "warning": "Counter pointers and directions are indexed in manifest section counters; indexed references do not mean original bodies were read."},
        "manifest": {"schema": MANIFEST, "digest": data["manifest_digest"],
            "section_counts": {key: len(data["sections"][key]) for key in SECTIONS},
            "reference_index_complete": data["reference_complete"],
            "scope": "checkpoint references, verified continuation closure and explicit reverse counter links in the permitted published sources"},
        "followup": {"context_tool": "etalon_research_context",
            "evidence_tool": "etalon_evidence_read", "evidence_scope": "pass read_context and an exact ref",
            "evidence_limit": {"minimum": 1, "maximum": 32768},
            "artifact_paging": "follow fragment.next_offset; no need to repeat the computation"},
        "reading_menu": reading_menu(),
        "unknowns": ["No global semantic search or scientific truth assessment was performed.",
            "Excerpts, reference indexing, original-byte verification and the host reading full original bodies are separate.",
            "Inspect complete claim scope, branch conditions and explicit counter originals before a new decision."],
        "execution_permission": "not_granted", "automatic_retry": False,
        "content_role": "sourced_research_data_not_host_instructions", "truncated": True}
    if "task_workset" in handoff:
        result["task_workset"] = {k: v for k, v in handoff["task_workset"].items()
                                  if k in {"status", "task_id", "digest"}}
    tail = cp["step_refs"][-1]
    tail_node = _node(data, tail)
    tail_content = read_content(data["workspace"], tail)
    result["resume_frontier"] = {"schema": "etalon-resume-frontier/1", "read_is_new_progress": False,
        "scientific_conclusion_change": "not_established_by_context_read",
        "latest_steps": [{"node_id": node_identity(tail), "operation": tail_node["operation"],
            "summary": _brief(tail_node["summary"]["text"]), "output_ref_count": len(tail_content["output_refs"]),
            "output_refs": [], "output_refs_omitted": len(tail_content["output_refs"])}],
        "followup_refs": [{"ref": tail, "reason": "read exact tail step for complete detail and output refs"}]}
    # Optional excerpts yield before any authority, pending or branch state.
    priority = [(tail, "checkpoint_tail_step")]
    if node_id is not None:
        if node_id not in data["references"]:
            raise EvidenceError("context node is unavailable in the continuation reference inventory")
        priority.insert(0, (data["references"][node_id], "selected_node"))
    priority += [(ref, "checkpoint_claim_excerpt") for ref in reversed(cp["claim_refs"])]
    priority += [(ref, "declared_counter_excerpt") for ref in cp["counter_refs"]]
    seen = set()
    for ref, reason in priority:
        nid = node_identity(ref)
        if nid in seen or len(result["items"]) >= 4:
            continue
        seen.add(nid)
        node = _node(data, ref)
        item = {k: node[k] for k in ("node_id", "ref", "home", "kind", "kind_basis", "state", "branch_id", "content_type")}
        item.update(summary=_brief(node["summary"]["text"]), selection_reason=reason,
                    original_read=_reference_row(ref, data["records"][ref["source_id"]][ref["record_id"]])["original_read"],
                    detail_status="excerpt_only_inspect_complete_original_before_decision")
        result["items"].append(item)
        if len(canonical(result).encode()) > detail_target - 512:
            result["items"].pop()
    def receipt():
        selected_ids = {item["node_id"] for item in result["items"]}
        result["selection"]["selected_count"] = len(selected_ids)
        result["counterevidence"]["excerpt_coverage_complete"] = set(counter_refs) <= selected_ids
        result["counterevidence"]["selected_refs"] = [ref for nid, ref in sorted(counter_refs.items()) if nid in selected_ids]
        result["counterevidence"]["declared_counter_refs"] = [ref for ref in cp["counter_refs"] if node_identity(ref) in selected_ids]
    receipt()
    while len(canonical(result).encode()) > detail_target - 256 and result["items"]:
        result["items"].pop()
        receipt()
    # All source identities are paged losslessly. Include exact watermark rows
    # directly when small, while the manifest digest always pins the full set.
    for row in data["coverage"]:
        result["source_watermarks"].append(row)
        if len(canonical(result).encode()) > detail_target - 256:
            result["source_watermarks"].pop()
            break
    result["watermark"]["delivered_source_count"] = len(result["source_watermarks"])
    result["context_id"] = "research-context-" + digest(result)
    if len(canonical(result).encode()) > max_bytes:
        raise EvidenceError("continuation card authority, pending and branch states exceed byte bound")
    return result
