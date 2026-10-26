"""Explicit bounded neighborhoods; counter incidence always scans the published universe.

Scope is retrieval intent, never an access policy or a claim of global coverage.
The existing collector/validator/byte limits remain authoritative.
"""
from __future__ import annotations

import base64
from collections import deque
import json

from etalon.active.schema import canonical, digest
from .continuation import _exact_refs
from .evidence import _read_evidence
from .evidence_refs import EvidenceError, make_ref, validate_ref
from .method_links import method_links
from .declared_relations import counter_refs as _counter_refs, checked_row as _checked_row, relation_index, record_identity

SCOPE = "etalon-research-graph-scope/1"
PAGE = "etalon-research-graph-page/1"


def _graph_rows(document):
    """Keep original values, splitting only collections into indexed rows.

    Oversized single values use the existing UTF-8 fragment transport. Nothing
    is hidden in an unmetered side channel; these rows reconstruct every field.
    """
    rows = []
    def fields(value, path):
        rows.append({"path": path, "value": {}})
        # Keep collection indices in their original order. Scoped traversal
        # already puts requested roots first, so field order alone can deliver
        # them before bulk edges, branch history and context bookkeeping.
        priority = {(): ("graph", "context"),
            ("graph",): ("schema", "checkpoint_ref", "continuation", "task_id", "nodes", "scope_receipt",
                "counter_node_ids", "counter_evidence_node_ids", "edges", "branches"),
            ("graph", "scope_receipt"): ("schema", "requested_scope", "counter_search_complete",
                "reference_coverage_complete", "global_reference_coverage_complete",
                "counter_relations", "counter_endpoint_refs")}.get(tuple(path), ())
        keys = [key for key in priority if key in value] + sorted(set(value) - set(priority))
        for key in keys:
            item = value[key]
            target = path + [key]
            if isinstance(item, list):
                rows.append({"path": target, "value": []})
                rows.extend({"path": target + [i], "value": v} for i, v in enumerate(item))
            elif key in {"graph", "context", "scope_receipt"} and isinstance(item, dict):
                fields(item, target)
            else:
                rows.append({"path": target, "value": item})
    fields(document, [])
    return rows


def graph_page(graph, *, cursor=None, limit=20, max_bytes=65536, handoff=None, context=None):
    """Lossless transport over the existing manifest cursor/fragment convention."""
    from .continuation_card import _position
    from .decision_reading import _page
    from .read_scope import implementation_identity
    if type(limit) is not int or not 1 <= limit <= 50:
        raise EvidenceError("graph page limit must be 1..50")
    document = {"graph": graph, "context": context or {}}
    rows = _graph_rows(document)
    projection_digest = digest(document)
    manifest_digest = digest({"projection_digest": projection_digest,
        "implementation": implementation_identity(), "read_context": (handoff or {}).get("read_context")})
    stamp = digest({"manifest_digest": manifest_digest, "section": "scoped_graph"})
    index, offset = _position(cursor, stamp, len(rows))
    envelope = {"schema": PAGE, "view": "graph", "section": "scoped_graph",
        "checkpoint_ref": graph["checkpoint_ref"], "graph_view_id": graph["view_id"],
        "projection_digest": projection_digest, "manifest_digest": manifest_digest,
        "projection_bytes": len(canonical(document).encode()), "total_count": len(rows),
        "page_start": {"index": index, "byte_offset": offset},
        "coverage": {"delivery": "this_page_only; earlier_and_later_pages_not_attested",
            "original_bodies_read_by_host": "not_established", "scientific_closure": "not_established",
            "included_originals_verified_this_call": True,
            "boundary_verification": "metadata_identity_only",
            "all_pages_delivered": False},
        "continuation_obligation": "retain consecutive pages from zero; same selection plus next_cursor; assemble_graph_pages verifies complete delivery",
        "snapshot_consistency": graph["snapshot_consistency"],
        "execution_permission": "not_granted", "automatic_retry": False}
    if handoff is not None:
        envelope["read_context"] = handoff["read_context"]
    # Leave the MCP ok envelope inside the caller's unchanged byte budget.
    page = _page(rows, envelope, stamp=stamp, cursor=cursor, limit=limit, max_bytes=max_bytes - 16)
    page["context_id"] = "graph-page-" + digest({k: v for k, v in page.items() if k != "context_id"})
    return page


def _graph_transcript(pages, *, require_complete):
    """Offline validation of saved delivery bytes, not a new original read."""
    from .continuation_card import _cursor
    from .graph_memory import validate_research_graph
    if not isinstance(pages, list) or not pages:
        raise EvidenceError("graph transcript requires consecutive pages from zero")
    if any(not isinstance(p, dict) or p.get("ok", True) is not True for p in pages):
        raise EvidenceError("graph transcript contains an unsuccessful response")
    pages = [{k: v for k, v in p.items() if k != "ok"} for p in pages]
    first, index, offset, fragment, rows = pages[0], 0, 0, bytearray(), []
    stamp = digest({"manifest_digest": first.get("manifest_digest"), "section": "scoped_graph"})
    identity_fields = ("schema", "manifest_digest", "projection_digest", "projection_bytes",
        "graph_view_id", "checkpoint_ref", "total_count", "read_context")
    total = first.get("total_count")
    if type(total) is not int or total <= 0:
        raise EvidenceError("invalid graph transcript count")
    for page in pages:
        expected = "graph-page-" + digest({k: v for k, v in page.items() if k != "context_id"})
        if (page.get("schema") != PAGE or page.get("context_id") != expected
                or any(page.get(k) != first.get(k) for k in identity_fields)
                or page.get("page_start") != {"index": index, "byte_offset": offset}
                or not page.get("items")
                or page.get("coverage", {}).get("included_originals_verified_this_call") is not True):
            raise EvidenceError("graph transcript identity, order or verification changed")
        for item in page["items"]:
            if item.get("index") != index or index >= total:
                raise EvidenceError("graph transcript skips, repeats or reorders rows")
            if "value" in item:
                if offset:
                    raise EvidenceError("graph transcript drops a partial row")
                rows.append(item["value"])
                index += 1
            elif "fragment" in item:
                part = item["fragment"]
                try:
                    raw = base64.b64decode(part["content"], validate=True)
                except (ValueError, KeyError):
                    raise EvidenceError("invalid graph transcript fragment") from None
                end = offset + len(raw)
                if (not raw or part.get("offset") != offset
                        or part.get("next_offset") not in (None, end)):
                    raise EvidenceError("graph transcript fragment has a gap")
                fragment.extend(raw)
                if part["next_offset"] is None:
                    try:
                        rows.append(json.loads(fragment.decode("utf-8")))
                    except (ValueError, UnicodeDecodeError):
                        raise EvidenceError("invalid graph transcript row") from None
                    fragment, index, offset = bytearray(), index + 1, 0
                else:
                    offset = end
            else:
                raise EvidenceError("graph transcript row or fragment required")
        next_cursor = _cursor(stamp, index, offset) if index < total else None
        if page.get("next_cursor") != next_cursor or page.get("page_complete") != (next_cursor is None):
            raise EvidenceError("graph transcript cursor differs from delivered range")
    complete = index == total and offset == 0
    if require_complete and not complete:
        raise EvidenceError("graph transcript is incomplete; follow next_cursor")
    document = None
    if complete:
        try:
            for row in rows:
                path, value = row["path"], json.loads(canonical(row["value"]))
                if not path:
                    if document is not None:
                        raise ValueError()
                    document = value
                    continue
                parent = document
                for key in path[:-1]:
                    parent = parent[key]
                key = path[-1]
                if isinstance(parent, list):
                    if type(key) is not int or key != len(parent):
                        raise ValueError()
                    parent.append(value)
                else:
                    if key in parent:
                        raise ValueError()
                    parent[key] = value
            if (digest(document) != first["projection_digest"]
                    or len(canonical(document).encode()) != first["projection_bytes"]
                    or document["graph"]["view_id"] != first["graph_view_id"]):
                raise ValueError()
            validate_research_graph(document["graph"])
        except (ValueError, KeyError, TypeError, IndexError):
            raise EvidenceError("graph transcript projection is missing or changed") from None
    receipt = {"schema": "etalon-research-graph-delivery/1", "graph_view_id": first["graph_view_id"],
        "manifest_digest": first["manifest_digest"], "projection_digest": first["projection_digest"],
        "page_count": len(pages), "delivered_rows": index, "partial_row_bytes": offset,
        "total_count": total, "all_pages_delivered": complete, "next_cursor": pages[-1]["next_cursor"],
        "original_bodies_read_by_host": "not_established", "scientific_closure": "not_established",
        "later_original_reverification": "not_performed_by_transcript_audit",
        "execution_permission": "not_granted", "automatic_retry": False}
    return document, receipt


def audit_graph_pages(pages):
    """Summarize recorded consecutive delivery; partial transcripts stay partial."""
    return _graph_transcript(pages, require_complete=False)[1]


def assemble_graph_pages(pages):
    """Reconstruct the exact graph and context controls from complete saved pages."""
    return _graph_transcript(pages, require_complete=True)[0]


def _unique_refs(refs):
    return [ref for _, ref in sorted({canonical(ref): ref for ref in refs}.items())]


def build_scoped_graph(ws, *, checkpoint_ref, checkpoint, resumed, collection,
                       universe, scope, policy, policy_receipt, temporal,
                       home_overrides, max_nodes, max_bytes):
    from .graph_memory import GRAPH, _ids, _project_node, node_identity, validate_research_graph

    if (not isinstance(scope, dict) or set(scope) - {"root_refs", "branch_id", "ordinary_hops"}
            or ("root_refs" in scope) == ("branch_id" in scope)):
        raise EvidenceError("scope requires either exact root_refs or branch_id")
    hops = scope.get("ordinary_hops", 1)
    if type(hops) is not int or hops not in (0, 1):
        raise EvidenceError("scope ordinary_hops must be 0 or 1")
    task_id = checkpoint["task_id"]
    if "root_refs" in scope:
        roots = scope["root_refs"]
        if not isinstance(roots, list) or not roots or len(roots) > max_nodes:
            raise EvidenceError("scope root_refs must be nonempty within the node bound")
        roots = _unique_refs(validate_ref(ref) for ref in roots)
        requested_scope = {"root_refs": roots, "ordinary_hops": hops}
    else:
        branch = scope["branch_id"]
        if not isinstance(branch, str) or not branch.strip():
            raise EvidenceError("scope branch_id must be explicit text")
        roots = []
        matched = [b for b in checkpoint["branches"] if b["branch_id"] == branch]
        for item in matched:
            roots.extend(item["basis_refs"] + item["next_read_refs"])
        for (sid, rid), row in sorted(universe.items()):
            body = row.get("projected_content", row["record"])
            if body.get("task_id") == task_id and body.get("branch_id") == branch:
                roots.append(make_ref(sid, rid, row["record"]))
        roots = _unique_refs(roots)
        if not roots:
            raise EvidenceError("scope branch is unavailable or has no exact roots")
        requested_scope = {"branch_id": branch, "ordinary_hops": hops}

    records, coverage, warnings = collection
    issues = []
    source_complete = all(c["status"] == "complete" for c in coverage) and not warnings
    # The collector intentionally suppresses hidden rows and their derivatives.
    # Compare only metadata identities; do not read or expose denied payloads/IDs.
    suppressed = 0
    for source in ws.manifest["sources"]:
        if source["source_id"] not in records:
            continue
        if source["kind"] in {"journal", "artifact_manifest"}:
            suppressed += len(_ids(ws, source) - records[source["source_id"]].keys())
    policy_narrowed = bool(policy_receipt["excluded_source_count"] or
        policy_receipt["restricted_record_rule_count"] or (policy or {}).get("record_ids"))
    if suppressed:
        issues.append({"reason": "published_records_hidden_or_dependency_suppressed", "count": suppressed})
    if policy_narrowed:
        issues.append({"reason": "policy_narrowed_counter_universe"})
    source_restricted = any("record_ids" in source for source in ws.manifest["sources"])
    if source_restricted:
        issues.append({"reason": "published_source_record_allowlist_limits_counter_universe"})
    if not source_complete:
        issues.append({"reason": "counter_search_source_coverage_incomplete"})

    refs, depths, selected, reads, ordinary = {}, {}, {}, {}, {}
    queue = deque()
    verified_envelopes = set()
    def checked_row(ref):
        return _checked_row(universe, ref)

    def verify(ref):
        key = canonical(ref)
        row = checked_row(ref)
        if key not in verified_envelopes:
            artifact = row["record_type"] == "artifact" or "member" in ref
            read = _read_evidence(ws, ref, view="artifact" if artifact else "record", limit=512,
                                  collection=collection)
            if (read.get("availability") != "available" or read.get("missing") or
                    (artifact and read.get("verification", {}).get("artifact") != "verified")):
                raise EvidenceError("scoped original unavailable or identity changed")
            reads.setdefault(node_identity(ref), read)
            verified_envelopes.add(key)
        return row

    def enqueue(ref, depth):
        # Envelopes with the same node identity may carry different member/hash
        # assertions. Verify every supplied envelope before deduplicating nodes.
        verify(ref)
        nid = node_identity(ref)
        if nid not in refs or depth < depths[nid]:
            refs.setdefault(nid, ref)
            depths[nid] = depth
            queue.append(nid)
        if len(refs) > max_nodes:
            raise EvidenceError("scoped closure exceeds graph node bound; choose narrower roots or ordinary_hops")

    indexed = relation_index(ws, collection)
    counter_relations, incidence = indexed["relations"], indexed["incidence"]

    control_refs = _unique_refs([checkpoint_ref] + checkpoint["step_refs"][-1:])
    for ref in roots:
        enqueue(ref, 0)
    for ref in control_refs:
        # Controls remain visible, but do not silently broaden the requested
        # ordinary neighborhood. Their full counter incidence still closes.
        enqueue(ref, hops)
    included_counters, edge_values = {}, {}

    def add_edge(a, b, kind, basis, relation=None):
        item = {"from": node_identity(a), "to": node_identity(b), "kind": kind, "basis_ref": basis}
        if relation is not None:
            item["relation"] = relation
        edge_values[canonical(item)] = item

    processed = {}
    while queue:
        nid = queue.popleft()
        depth = depths[nid]
        if processed.get(nid, hops + 1) <= depth:
            continue
        processed[nid] = depth
        ref = refs[nid]
        row = verify(ref)
        body = row.get("projected_content", row["record"])
        detail = body.get("detail", {})
        selected[nid] = _project_node(ref, row, task_id=task_id, home_overrides=home_overrides)
        if row["record_type"] == "artifact" and "projected_content" not in row:
            fragment = reads[nid].get("fragment", {})
            try:
                excerpt = base64.b64decode(fragment.get("content", "")).decode("utf-8")
                if excerpt and all(c.isprintable() or c in "\r\n\t" for c in excerpt):
                    selected[nid]["summary"] = {"text": excerpt, "truncated": fragment.get("next_offset") is not None}
            except (ValueError, UnicodeDecodeError):
                pass
        for key in sorted(incidence.get(record_identity(ref), ())):
            relation = counter_relations[key]
            included_counters[key] = relation
            for endpoint in (relation["from_ref"], relation["to_ref"]):
                enqueue(endpoint, hops)
            add_edge(relation["from_ref"], relation["to_ref"], "refutes", relation["basis_ref"])

        links = []
        counter_targets = {canonical(r) for r in _counter_refs(body)}
        for target in _exact_refs(body):
            if canonical(target) not in counter_targets:
                links.append((target, "exact_reference"))
        for target, relation in method_links(body, ref["source_id"], universe):
            if target is None:
                issues.append({"reason": "method_link_unavailable_in_source", "node_id": nid, "relation": relation})
            else:
                links.append((target, relation))
                add_edge(ref, target, "depends_on", ref, relation)
        for field, kind in (("input_refs", "depends_on"), ("output_refs", "derived_from"),
                            ("support_refs", "supports"), ("supersedes", "supersedes")):
            for target in body.get(field, []):
                a, b = (target, ref) if field in {"output_refs", "support_refs"} else (ref, target)
                add_edge(a, b, kind, ref)
        for target in detail.get("hypothesis_refs", []):
            add_edge(ref, target, "tests", ref)
        if body.get("operation") == "decision":
            for target in detail["basis_refs"]:
                add_edge(ref, target, "depends_on", ref)
        for target, relation in links:
            checked_row(target)  # A deliberate boundary cannot excuse a broken ref.
            tid = node_identity(target)
            ordinary[(nid, tid, relation)] = (ref, target, relation)
            if depth < hops:
                enqueue(target, depth + 1)

    # Instance flow edges are purely declared structure, never inferred order.
    workflows = {}
    for nid, node in selected.items():
        row = universe[(node["ref"]["source_id"], node["ref"]["record_id"])]
        detail = row.get("projected_content", {}).get("detail", {})
        if detail.get("workflow_ref") and detail.get("node_id"):
            workflows.setdefault(node_identity(detail["workflow_ref"]), {}).setdefault(detail["node_id"], []).append(nid)
    for wid, instances in workflows.items():
        if wid in selected:
            ref = refs[wid]
            template = universe[(ref["source_id"], ref["record_id"])].get("projected_content", {})
            for link in template.get("edges", []):
                for a in instances.get(link["from"], []):
                    for b in instances.get(link["to"], []):
                        add_edge(refs[a], refs[b], "flow" if link["kind"] == "control" else "depends_on", ref)

    boundary = [item for key, item in sorted(ordinary.items()) if key[1] not in selected]
    boundary_refs = _unique_refs(item[1] for item in boundary)
    counter_items = [item for _, item in sorted(included_counters.items())]
    endpoints = _unique_refs(ref for item in counter_items for ref in (item["from_ref"], item["to_ref"]))
    counter_ids = {node_identity(ref) for ref in endpoints}
    counter_search_complete = source_complete and not suppressed and not policy_narrowed and not source_restricted
    reference_complete = counter_search_complete and not issues
    receipt = {"schema": SCOPE, "requested_scope": requested_scope, "root_refs": roots,
        "control_refs": control_refs, "policy_digest": policy_receipt["digest"],
        "workspace_digest": digest(ws.manifest),
        "source_versions": [{"source_id": source["source_id"], "identity": source["identity"],
            **{k: cov[k] for k in ("snapshot", "read_watermark", "status") if k in cov}}
            for source in ws.manifest["sources"] for cov in coverage if cov["source_id"] == source["source_id"]],
        "closure_policy": {"ordinary": "outgoing_exact_refs_and_method_links_from_roots_to_requested_hops",
            "controls": "checkpoint_and_tail_without_additional_ordinary_expansion",
            "counters": "transitive_bidirectional_incidence_for_every_included_node_across_authorized_universe",
            "counter_direction": "declared_counter_ref_refutes_declaring_record",
            "counter_added_ordinary_dependencies": "deliberate_boundary_unless_already_in_root_neighborhood"},
        "deliberate_boundary_refs": boundary_refs,
        "boundary_relations": [{"from": node_identity(a), "to": node_identity(b), "relation": relation}
                               for a, b, relation in boundary],
        "counter_endpoint_refs": endpoints, "counter_relations": counter_items,
        "counter_search_universe": "all_permitted_published_records_in_every_workspace_source",
        "reference_semantics": "declared_structured_references_only; opaque_original_bodies_are_not_semantically_parsed; scientific_dependency_closure_not_established",
        "counter_search_records": len(universe), "suppressed_record_count": suppressed,
        "counter_search_complete": counter_search_complete,
        "original_verification_complete": True,
        "original_verification_scope": "included_nodes_and_all_supplied_counter_endpoint_envelopes; deliberate_boundary_refs_have_metadata_identity_checks_only",
        "verified_reference_envelope_count": len(verified_envelopes),
        "traversal_complete": True, "reference_coverage_complete": reference_complete,
        "counter_reference_coverage_complete": reference_complete,
        "global_reference_coverage_complete": False,
        "excerpt_coverage_complete": False,
        "excerpt_semantics": "bounded_projections_not_full_original_bodies",
        "issues": issues}
    result = {"schema": GRAPH, "workspace_id": ws.manifest["workspace_id"], "checkpoint_ref": checkpoint_ref,
        "task_id": task_id, "question": checkpoint["question"], "nodes": list(selected.values()),
        "edges": [e for _, e in sorted(edge_values.items()) if e["from"] in selected and e["to"] in selected],
        "branches": checkpoint["branches"],
        "checkpoint_progress": {"step_count": len(checkpoint["step_refs"]), "tail_step_refs": checkpoint["step_refs"][-1:],
            "order_semantics": "checkpoint_list_order_not_execution_order"},
        "counter_node_ids": sorted(counter_ids),
        "counter_evidence_node_ids": sorted({node_identity(r["from_ref"]) for r in counter_items}),
        "counter_context_semantics": "both_endpoints_of_declared_counter_relations; refutes_edge_direction_is_authoritative",
        "counter_context_complete": reference_complete,
        "coverage": [{k: c[k] for k in ("source_id", "status", "snapshot", "read_watermark", "visible_records") if k in c} for c in coverage],
        "policy": policy_receipt, "temporal": temporal, "scope_receipt": receipt,
        "continuation": {"authority_state": resumed["authority"]["state"], "review_required": resumed["review_required"],
            "pending": [{"request_id": p["handle"]["request_id"], "execution_state": p["execution_state"]} for p in resumed["pending"]],
            "issue_codes": sorted({i["reason"] for i in resumed["issues"]})},
        "issues": issues, "truncated": False, "execution_permission": "not_granted", "automatic_retry": False,
        "snapshot_consistency": "per_source_reads_not_global_atomic_snapshot"}
    result["view_id"] = "research-graph-" + digest(result)
    # This is an internal inventory, still subject to the same node, source,
    # policy, reference and original-byte checks. Public readers own delivery
    # bounds and page the inventory instead of discarding mandatory context.
    return validate_research_graph(result)
