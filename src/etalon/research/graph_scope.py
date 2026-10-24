"""Explicit bounded neighborhoods; counter incidence always scans the published universe.

Scope is retrieval intent, never an access policy or a claim of global coverage.
The existing collector/validator/byte limits remain authoritative.
"""
from __future__ import annotations

import base64
from collections import deque

from etalon.active.schema import canonical, digest
from .continuation import _exact_refs
from .evidence import _read_evidence
from .evidence_refs import EvidenceError, make_ref, validate_ref
from .method_links import method_links

SCOPE = "etalon-research-graph-scope/1"


def _counter_refs(value):
    """Explicit declarations, including Artifact bindings; no text heuristics."""
    if isinstance(value, dict) and value.get("schema") != "etalon-evidence-ref/1":
        for key, child in value.items():
            if key == "counter_refs":
                if not isinstance(child, list):
                    raise EvidenceError("counter_refs must be explicit references")
                for ref in child:
                    yield validate_ref(ref)
            elif isinstance(child, (dict, list)):
                yield from _counter_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _counter_refs(child)


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
    counter_relations, incidence = {}, {}

    def checked_row(ref):
        ref = validate_ref(ref)
        row = universe.get((ref["source_id"], ref["record_id"]))
        if row is None:
            raise EvidenceError("scoped reference unavailable, hidden or suppressed")
        if digest(row["record"]) != ref["record_digest"]:
            raise EvidenceError("scoped reference record identity changed")
        artifact = row["record"]
        if "member" in ref:
            members = row.get("members", artifact.get("members", []))
            matches = [m for m in members if m.get("member") == ref["member"] and m.get("visibility") == "released"]
            if len(matches) != 1:
                raise EvidenceError("scoped reference member is not uniquely published")
            artifact = matches[0]
        if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (artifact.get("sha256"), artifact.get("bytes")):
            raise EvidenceError("scoped reference artifact identity mismatch")
        if row.get("projection", {}).get("status") == "unavailable":
            raise EvidenceError("scoped reference content unavailable")
        return row

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

    # Full authorized-universe incidence, independent of task/home/root ordering.
    # A record declaring counter_refs says each target refutes that record.
    for (sid, rid), row in sorted(universe.items()):
        owner = make_ref(sid, rid, row["record"])
        declarations = _unique_refs(list(_counter_refs(row["record"])) +
            list(_counter_refs(row.get("projected_content", {}))))
        for target in declarations:
            # Validate before keying incidence: an incorrect digest/member would
            # otherwise produce a different node ID and silently erase a reverse
            # counter declaration from an otherwise complete-looking search.
            checked_row(target)
            relation = {"from_ref": target, "to_ref": owner, "kind": "refutes", "basis_ref": owner}
            key = canonical(relation)
            counter_relations[key] = relation
            for nid in (node_identity(target), node_identity(owner)):
                incidence.setdefault(nid, set()).add(key)

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
        for key in sorted(incidence.get(nid, ())):
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
    if len(canonical(result).encode()) > max_bytes:
        raise EvidenceError("scoped graph and required boundary/counter receipt exceed graph byte bound; choose narrower roots or ordinary_hops")
    return validate_research_graph(result)
