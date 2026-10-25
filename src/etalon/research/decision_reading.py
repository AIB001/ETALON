"""Optional, explicit decision reading over the existing verified archive.

This is a losslessly paged reading plan, not semantic search, a new evidence
store, an execution policy, or a receipt that a host understood originals.
"""
from __future__ import annotations

from collections import Counter

from etalon.active.schema import canonical, digest
from .continuation_card import _cursor, _position, _reference_row
from .entry import resolve_entry, make_read_context, workspace_from_context, continuation_status
from .evidence import _collect, _slice_text
from .evidence_refs import EvidenceError, make_ref, validate_ref
from .graph_memory import build_research_graph, node_identity, restrict_workspace
from .read_scope import bounded_read

SCHEMA = "etalon-decision-reading/1"
MAX_BYTES = 16384


def _conditions(value):
    if value is not None and (not isinstance(value, dict) or not value or any(
            not isinstance(k, str) or not k or v is not None and
            (not isinstance(v, str) or not v) for k, v in value.items())):
        raise EvidenceError("reading conditions require explicit strings or null unknowns")


def _relevance(value):
    if value is None:
        return {}
    if not isinstance(value, list) or len(value) > 128:
        raise EvidenceError("relevance requires at most 128 explicit annotations")
    result = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"ref", "why", "affects_decision"}:
            raise EvidenceError("relevance needs ref, why and affects_decision")
        ref = validate_ref(item["ref"])
        if any(not isinstance(item[k], str) or not item[k].strip() or len(item[k]) > 4096
               for k in ("why", "affects_decision")):
            raise EvidenceError("relevance reasons require nonempty bounded text")
        key = node_identity(ref)
        if key in result:
            raise EvidenceError("duplicate relevance annotation")
        result[key] = item
    return result


def _applicability(experience, conditions):
    expected = experience.get("conditions", {})
    checks = {key: "unknown" if (conditions or {}).get(key) is None else
              "match" if conditions[key] == value else "conflict"
              for key, value in expected.items()}
    unassessed = sorted(set(conditions or {}) - set(expected))
    return {"checks": checks, "status": "conflict" if "conflict" in checks.values() else
            "unknown" if not expected or conditions is None or unassessed or "unknown" in checks.values() else "match",
            "unassessed_host_dimensions": unassessed, "match_scope": "declared_dimensions_only",
            "scientific_applicability": "not_established",
            "invalid_when": experience.get("invalid_when"),
            "invalid_when_evaluation": "host_review_required",
            "automatic_adoption": False}


def _obligations(root_refs, records, receipt, annotations, checkpoint_ref, mode):
    """Exact selected basis, not every discovered task experience/control body."""
    from .continuation import _exact_refs
    selected = {node_identity(ref) for ref in root_refs} | set(annotations)
    direct = set(selected)
    fields = {"input_refs", "support_refs", "basis_refs", "receipt_refs", "check_refs",
              "check_ref", "validation_ref", "initial_conditions_ref", "protocol_ref"}
    if mode == "diagnosis":
        fields |= {"output_refs", "use_refs", "workflow_ref", "method_refs", "hypothesis_refs"}
    for ref in root_refs:
        row = records[ref["source_id"]][ref["record_id"]]
        body = row.get("projected_content", row["record"])
        for area in (body, body.get("detail", {})):
            direct.update(node_identity(r) for r in _exact_refs({k: v for k, v in area.items() if k in fields}))
    relevant = set(direct)
    cp_id = node_identity(checkpoint_ref)
    changed = True
    while changed:
        before = len(relevant)
        for edge in receipt["counter_relations"]:
            ends = {node_identity(edge[key]) for key in ("from_ref", "to_ref")}
            # The checkpoint lists all branches' controls. It must not turn a
            # local relevance review into a mandatory all-branch original read.
            if cp_id not in selected:
                ends.discard(cp_id)
            if ends & relevant:
                relevant.update(ends)
        changed = len(relevant) != before
    return direct, relevant


def _page(rows, envelope, *, stamp, cursor, limit, max_bytes):
    """Reuse the manifest cursor/UTF-8 fragment convention without dropping rows."""
    index, offset = _position(cursor, stamp, len(rows))
    result = {**envelope, "items": [], "next_cursor": None}
    reserve = 384
    while index < len(rows) and len(result["items"]) < limit:
        item = {"index": index, "value": rows[index]}
        result["items"].append(item)
        if offset == 0 and len(canonical(result).encode()) <= max_bytes - reserve:
            index += 1
            continue
        result["items"].pop()
        if result["items"]:
            break
        raw = canonical(rows[index])
        if offset >= len(raw.encode()):
            raise EvidenceError("invalid reading fragment offset")
        size = (max_bytes - len(canonical(result).encode()) - reserve - 200) * 3 // 4
        if size < 1:
            raise EvidenceError("reading controls exceed byte bound")
        item = {"index": index, "fragment": _slice_text(raw, offset, size)}
        result["items"].append(item)
        if len(canonical(result).encode()) > max_bytes - reserve:
            raise EvidenceError("reading controls exceed byte bound")
        offset = item["fragment"]["next_offset"]
        if offset is None:
            index += 1
            offset = 0
        break
    if index < len(rows):
        result["next_cursor"] = _cursor(stamp, index, offset)
    result["page_complete"] = result["next_cursor"] is None
    result["context_id"] = "decision-reading-" + digest(result)
    if len(canonical(result).encode()) > max_bytes:
        raise EvidenceError("reading controls exceed byte bound")
    return result


@bounded_read
def build_decision_reading(entry, *, question, scope=None, reading_mode="decision",
                           purpose="research", policy=None, conditions=None,
                           relevance=None, decision_changed=None, max_bytes=MAX_BYTES,
                           cursor=None, limit=20, as_of=None, home_overrides=None):
    """Select exact task/branch/root context and explain required/conditional reads.

    Free question text never ranks evidence. ``scope`` uses the existing graph
    contract (branch_id OR root_refs, ordinary_hops 0/1). Without it, select the
    published task's tail step, or checkpoint if no step exists. Same-task and
    explicitly shared-capability experiences remain visible even when their
    conditions conflict. Explicit counter incidence spans all permitted sources.
    ``relevance`` is host-authored explanation for selected refs, not evidence.
    ``decision_changed`` records intent, not a finding that science changed.
    """
    if not isinstance(question, str) or not question.strip() or len(question) > 4096:
        raise EvidenceError("reading question requires nonempty text of at most 4096 characters")
    if reading_mode not in {"status", "decision", "diagnosis"}:
        raise EvidenceError("reading_mode must be status, decision or diagnosis")
    if type(max_bytes) is not int or not 4096 <= max_bytes <= MAX_BYTES:
        raise EvidenceError("reading max_bytes must be 4096..16384")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise EvidenceError("reading limit must be 1..50")
    if decision_changed is not None and type(decision_changed) is not bool:
        raise EvidenceError("decision_changed must be boolean or null unknown")
    _conditions(conditions)
    annotations = _relevance(relevance)
    workspace, checkpoint_ref, entry_path, pinned = resolve_entry(entry)
    if pinned is not None:
        if policy is not None and policy != pinned["policy"]:
            raise EvidenceError("pinned context policy cannot change; resolve the entry explicitly")
        if purpose != pinned["purpose"]:
            raise EvidenceError("pinned context purpose cannot change; resolve the entry explicitly")
        policy = pinned["policy"]
    read_context = pinned or make_read_context(workspace, checkpoint_ref,
        entry_path=entry_path, policy=policy, purpose=purpose)
    if read_context is not None:
        workspace = workspace_from_context(read_context, apply_policy=False)
    ws = restrict_workspace(workspace, policy=policy, purpose=purpose)[0]
    records, coverage, warnings = collection = _collect(ws)
    row = records.get(checkpoint_ref["source_id"], {}).get(checkpoint_ref["record_id"])
    if row is None or digest(row["record"]) != checkpoint_ref["record_digest"]:
        raise EvidenceError("reading checkpoint unavailable, restricted or identity changed")
    cp = row.get("projected_content", {})
    if cp.get("schema") != "etalon-research-checkpoint/1":
        raise EvidenceError("reading requires a verified research checkpoint")
    chosen_scope = scope if scope is not None else {
        "root_refs": cp["step_refs"][-1:] or [checkpoint_ref],
        "ordinary_hops": 0}
    options = dict(checkpoint_ref=checkpoint_ref, policy=policy, purpose=purpose,
                   as_of=as_of, home_overrides=home_overrides, max_nodes=128, max_bytes=131072)
    if (not isinstance(chosen_scope, dict) or set(chosen_scope) - {"root_refs", "branch_id", "ordinary_hops"}
            or ("root_refs" in chosen_scope) == ("branch_id" in chosen_scope)):
        raise EvidenceError("scope requires either exact root_refs or branch_id")
    root_refs = chosen_scope.get("root_refs")
    if root_refs is None:
        branch = chosen_scope["branch_id"]
        if not isinstance(branch, str) or not branch.strip():
            raise EvidenceError("scope branch_id must be explicit text")
        root_refs = [ref for b in cp["branches"] if b["branch_id"] == branch
                     for ref in b["basis_refs"] + b["next_read_refs"]]
        root_refs += [make_ref(sid, rid, row["record"]) for sid, source in records.items()
                      for rid, row in source.items()
                      if row.get("projected_content", row["record"]).get("task_id") == cp["task_id"]
                      and row.get("projected_content", row["record"]).get("branch_id") == branch]
    if not isinstance(root_refs, list) or not root_refs or len(root_refs) > 128:
        raise EvidenceError("scope roots or branch unavailable, empty or beyond node bound")
    root_refs = [validate_ref(ref) for ref in root_refs]
    # The existing source collector already exposes permitted metadata. Avoid
    # semantic retrieval: task/capability identity alone admits experience, and
    # condition mismatch is a delivered conflict rather than an exclusion.
    capabilities = set()
    for ref in root_refs:
        body = records.get(ref["source_id"], {}).get(ref["record_id"])
        if body is None or digest(body["record"]) != ref["record_digest"]:
            raise EvidenceError("scoped reference unavailable, restricted or identity changed")
        body = body.get("projected_content", body["record"])
        value = body.get("capability_id", body.get("detail", {}).get("capability_id"))
        if value:
            capabilities.add(value)
    experiences = {}
    for sid, source in records.items():
        for rid, candidate in source.items():
            body = candidate.get("projected_content", candidate["record"])
            if body.get("schema") == "etalon-method-experience/1" and (
                    body.get("task_id") == cp["task_id"] or body.get("capability_id") in capabilities):
                ref = make_ref(sid, rid, candidate["record"])
                experiences[node_identity(ref)] = ref
    selected = {node_identity(ref) for ref in root_refs}
    extras = [ref for nid, ref in sorted(experiences.items()) if nid not in selected]
    expanded = {"root_refs": root_refs + extras, "ordinary_hops": chosen_scope.get("ordinary_hops", 1)}
    graph = build_research_graph(workspace, scope=expanded if extras else chosen_scope, **options)
    receipt = graph["scope_receipt"]
    direct_obligations, relevant_obligations = _obligations(
        root_refs, records, receipt, annotations, checkpoint_ref, reading_mode)
    from .research_context import _publication_context
    from .meta_context import workset_summary
    publications = _publication_context(entry_path or entry)
    try:
        workset = workset_summary(ws, checkpoint_ref, conditions=conditions)
    except (ValueError, OSError, KeyError) as exc:
        workset = {"status": "unavailable", "reason": str(exc)[:500]}
    sources = {source["source_id"]: source for source in ws.manifest["sources"]}
    roots = {node_identity(ref) for ref in receipt["root_refs"]}
    counters = set(graph["counter_node_ids"])
    controls = {node_identity(ref) for ref in receipt["control_refs"]}
    rows, ref_rows = [], {}
    # Conditions and full source receipts are paged, never silently abbreviated.
    rows.append({"section": "selection", "question": question, "scope": chosen_scope,
                 "relevance_basis": "host_exact_scope" if scope is not None else "published_task_tail_default",
                 "experience_selection": "same_task_or_explicit_shared_capability_in_permitted_sources",
                 "conditions": conditions, "decision_changed": decision_changed})
    control_row = {"section": "current_controls", "branches": cp["branches"],
                 "checkpoint_progress": graph["checkpoint_progress"],
                 "publications": publications, "task_workset": workset}
    superseded = {node_identity(ref) for node in graph["nodes"]
                  for ref in node.get("experience", {}).get("supersedes", [])}
    for node in graph["nodes"]:
        nid, ref = node["node_id"], node["ref"]
        source_row = records[ref["source_id"]][ref["record_id"]]
        navigation = _reference_row(ref, source_row)
        reasons = []
        if nid in controls:
            reasons.append("current_checkpoint_or_tail")
        if nid in roots:
            reasons.append("explicit_reading_root")
        if nid in counters:
            reasons.append("counter_relation_endpoint_across_permitted_sources")
        relations = [edge for edge in graph["edges"] if nid in (edge["from"], edge["to"])]
        reasons.extend(sorted({edge.get("relation", edge["kind"]) for edge in relations}))
        section = ("experience" if "experience" in node else "counterevidence" if nid in counters else
                   "current_decision" if "decision" in node or "research_decision" in node else
                   "current_controls" if nid in controls else "direct_support")
        required = nid in relevant_obligations and (reading_mode != "status" or decision_changed is True)
        item = {"section": section, **navigation,
                "source_scope": {"workspace_id": graph["workspace_id"], "source_id": ref["source_id"],
                                 "source_identity": sources[ref["source_id"]]["identity"],
                                 "scope": node["scope"], "branch_id": node["branch_id"]},
                "why": reasons or ["exact_dependency_in_selected_scope"],
                "affects_decision": "current controls" if nid in controls else
                    "choice or scope challenged by explicit counter relation" if nid in counters else
                    "transfer applicability" if "experience" in node else
                    "selected decision basis or diagnostic attribution",
                "relevance_provenance": {"kind": "declared_structured_relation", "scientific_relevance": "host_review_required"},
                "read_requirement": "required_before_changed_decision_or_causal_claim" if required else
                    "conditional_host_relevance_review",
                "metadata_requirement": "current_controls_required" if nid in controls else "context_for_host_review",
                "obligation_basis": "exact_selected_basis" if nid in direct_obligations else
                    "counter_challenges_selected_basis" if nid in relevant_obligations else "discovered_context",
                "summary": node["summary"], "original_bodies_read_by_host": "not_established"}
        for key in ("decision", "research_decision", "claim_scope", "usage", "method_use", "evaluation", "trace", "experience"):
            if key in node:
                item[key] = node[key]
        if "experience" in node:
            item["applicability"] = _applicability(node["experience"], conditions)
            item["activation"] = "superseded_in_selected_scope" if nid in superseded else "not_inferred_from_retrieval"
        if nid in annotations:
            if annotations[nid]["ref"] != ref:
                raise EvidenceError("relevance envelope differs from verified selected reference")
            item["host_relevance"] = annotations[nid]
            item["relevance_provenance"]["host_declared"] = True
        ref_rows[nid] = item
    if set(annotations) - set(ref_rows):
        raise EvidenceError("relevance ref unavailable in selected scope; select exact roots explicitly")
    priority = {"current_decision": 0, "counterevidence": 1, "current_controls": 2,
                "direct_support": 3, "experience": 4}
    rows.extend(sorted(ref_rows.values(), key=lambda r: (priority[r["section"]], r["node_id"])))
    rows.append(control_row)
    for relation in receipt["counter_relations"]:
        rows.append({"section": "counter_relation", **relation,
                     "meaning": "from_ref refutes to_ref; direction is declared, not inferred"})
    for ref in receipt["deliberate_boundary_refs"]:
        source_row = records[ref["source_id"]][ref["record_id"]]
        rows.append({"section": "optional_detail", **_reference_row(ref, source_row),
                     "source_scope": {"workspace_id": graph["workspace_id"], "source_id": ref["source_id"]},
                     "why": "explicit dependency beyond requested ordinary_hops",
                     "affects_decision": "may close a support or diagnostic attribution gap",
                     "relevance_provenance": {"kind": "declared_boundary_relation"},
                     "read_requirement": "required_before_changed_decision_or_causal_claim"
                         if node_identity(ref) in relevant_obligations and (reading_mode != "status" or decision_changed is True)
                         else "conditional_host_relevance_review",
                     "original_verification": "metadata_identity_only",
                     "expand": {"scope": {"root_refs": [ref], "ordinary_hops": 1}},
                     "original_bodies_read_by_host": "not_established"})
    gaps = [*graph["issues"], *warnings]
    if receipt["deliberate_boundary_refs"]:
        gaps.append({"reason": "ordinary_dependency_boundary", "count": len(receipt["deliberate_boundary_refs"])})
    required_boundary = {node_identity(ref) for ref in receipt["deliberate_boundary_refs"]} & relevant_obligations
    if required_boundary:
        gaps.append({"reason": "selected_basis_counter_closure_requires_boundary_expansion", "count": len(required_boundary)})
    if not receipt["counter_reference_coverage_complete"]:
        gaps.append({"reason": "unresolved_counter_coverage"})
    unreviewed = set(ref_rows) - relevant_obligations - controls
    if unreviewed:
        gaps.append({"reason": "discovered_context_relevance_requires_host_review", "count": len(unreviewed)})
    if workset.get("status") in {"unavailable", "review_required"}:
        gaps.append({"reason": "workset_" + workset["status"]})
    if publications["incomplete_count"] is None:
        gaps.append({"reason": "publication_status_unknown"})
    elif publications["incomplete_count"]:
        gaps.append({"reason": "publication_incomplete", "count": publications["incomplete_count"]})
    rows.append({"section": "gaps", "items": gaps,
                 "unresolved": ["host_original_reading_and_comprehension_not_established",
                                "opaque_body_semantic_dependencies_not_inferred",
                                "global_or_cross_workspace_evidence_closure_not_established"]})
    rows.append({"section": "reading_rules",
                 "decision": "Read relevant originals fully, preserving method/branch labels, support, counters and applicability; resolve material gaps before changing a decision.",
                 "diagnosis": "Follow exact execution, input, output and check references; missing links and unknown cause stay unknown.",
                 "status": "May stop once requested controls and progress are known, provided no changed decision or causal claim is made.",
                 "expand": "Expand for a material gap, changed condition, missing label, conflict or insufficient exact dependency.",
                 "cross_workspace": "Use an explicitly published entry locator to obtain its own read_context; never guess or widen this workspace.",
                 "originals": "Use exact ref and original_read with etalon_evidence_read, preserving read_context; follow fragment.next_offset to null. Identity verification and delivered pages do not establish comprehension."})
    rows.append({"section": "source_scope", "scope_receipt": receipt,
                 "coverage": coverage, "policy": graph["policy"], "temporal": graph["temporal"]})
    counts = dict(Counter(item["section"] for item in rows))
    stamp = digest({"schema": SCHEMA, "read_context": read_context, "graph": graph["view_id"],
                    "mode": reading_mode, "rows": rows})
    envelope = {"schema": SCHEMA, "view": "reading", "reading_mode": reading_mode,
                "read_context": read_context, "entry_status": continuation_status(read_context),
                "checkpoint_ref": checkpoint_ref, "task_id": cp["task_id"], "purpose": purpose,
                "question": {"text": question[:240], "truncated": len(question) > 240},
                "continuation": graph["continuation"],
                "publications": {k: publications[k] for k in ("status", "incomplete_count")},
                "reading_digest": stamp, "total_count": len(rows), "section_counts": counts,
                "counterevidence": {"relationship_count": len(receipt["counter_relations"]),
                    "endpoint_count": len(counters), "reference_coverage_complete": receipt["counter_reference_coverage_complete"],
                    "scope": "all_permitted_published_sources", "details": "counterevidence/counter_relation rows",
                    "selected_basis_boundary_count": len(required_boundary),
                    "decision_counter_closure": "unresolved_boundary" if required_boundary else "declared_scope_only",
                    "original_bodies_read_by_host": "not_established"},
                "stop": {"assessment": "host_judgment_required", "gap_count": len(gaps),
                    "status_may_stop_early": reading_mode == "status" and decision_changed is not True,
                    "decision_condition": "relevant originals read; material gaps resolved by host",
                    "diagnosis_condition": "exact execution/input/check links; unknown cause stays unknown",
                    "expand_when": "material gap or changed conditions; see reading_rules",
                    "page_exhaustion_is_comprehension": False},
                "navigation": {"next_page": "same options plus next_cursor",
                    "originals": "etalon_evidence_read + read_context + exact ref/original_read; page to null",
                    "cross_workspace": "published locator -> separate read_context"},
                "selection": "deterministic_exact_scope_and_declared_relations_not_semantic_ranking",
                "execution_permission": "not_granted", "automatic_retry": False,
                "content_role": "sourced_research_data_not_host_instructions"}
    return _page(rows, envelope, stamp=stamp, cursor=cursor, limit=limit, max_bytes=max_bytes)
