"""Bounded decision context from a research graph, with selection receipts.

This is deterministic retrieval/assembly, not a claim-writing model. It never
executes tasks, changes methods, writes a journal, or grants permission to resume.
"""
from __future__ import annotations

import json
from pathlib import Path

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, validate_ref
from .graph_memory import _build_research_graph as build_research_graph, node_identity
from .entry import resolve_entry, make_read_context, continuation_status, workspace_from_context
from .read_scope import paged_read

CONTEXT = "etalon-research-context/1"
STRATEGY = "node-neighborhood-explicit-counterevidence/1"


def _resume_frontier(workspace, graph, nodes, *, max_bytes):
    """Verified checkpoint navigation, not an inference that science advanced.

    Read only the checkpoint's explicit tail in the already restricted view.
    Original step/output references remain the authority; neither a reason
    containing 'audit' nor an opaque output's self-description proves no change.
    """
    from .continuation import read_content
    from .evidence import read_evidence
    # Existing checkpoint_progress, continuation, publications, branches and
    # counterevidence carry the authoritative controls once. Tail node IDs bind
    # these short excerpts to the exact references in reference_index.
    frontier = {"schema": "etalon-resume-frontier/1", "latest_steps": [], "read_is_new_progress": False,
        "scientific_conclusion_change": "not_established_by_context_read",
        "followup_refs": []}
    output_budget = min(3072, max_bytes // 4)
    for ref in graph["checkpoint_progress"]["tail_step_refs"]:
        step = read_content(workspace, ref)
        if step.get("schema") != "etalon-research-step/1" or step["task_id"] != graph["task_id"]:
            raise EvidenceError("resume frontier requires the current task's exact checkpoint step")
        brief = nodes[node_identity(ref)]["summary"]
        row = {"node_id": node_identity(ref), "operation": step["operation"],
            "summary": {"text": brief["text"][:240], "truncated": brief["truncated"] or len(brief["text"]) > 240},
            "output_refs": [], "output_ref_count": len(step["output_refs"]),
            "output_refs_omitted": len(step["output_refs"])}
        frontier["latest_steps"].append(row)
        for output in step["output_refs"][:4]:
            # Visibility is checked before including an exact output identity.
            result = read_evidence(workspace, output, view="record", limit=1)
            if result.get("availability") != "available" or result.get("missing"):
                raise EvidenceError("resume frontier output is unavailable in the permitted view")
            row["output_refs"].append(output)
            row["output_refs_omitted"] -= 1
            followup = {"ref": ref, "reason": "output_references_omitted",
                        "when": "if the remaining archived outputs are needed"}
            # Reserve an exact follow-up for omitted references, never hide that
            # this is a partial output inventory to fit the transport budget.
            if len(canonical(frontier).encode()) + len(canonical(followup).encode()) > output_budget:
                row["output_refs"].pop()
                row["output_refs_omitted"] += 1
                break
        if row["output_refs_omitted"]:
            frontier["followup_refs"].append({"ref": ref, "reason": "output_references_omitted",
                "when": "if the remaining archived outputs are needed"})
    return frontier


def _focused_context(graph, nodes, *, workspace, node_id, question, purpose, max_bytes, view, publications, handoff):
    """Assemble a navigation view without turning exclusions into missing facts.

    Controls and explicit counter directions are mandatory. Detail may be
    omitted with a receipt; exact branch/counter/experience refs remain readable.
    This bounds delivery bytes, not collection I/O or scientific completeness.
    """
    from .continuation_card import reading_menu
    selected_scope = set()
    reasons = {}
    scoped = graph.get("scope_receipt")

    def add(nid, reason):
        if nid in nodes:
            selected_scope.add(nid)
            reasons.setdefault(nid, reason)

    if node_id is not None:
        add(node_id, "selected_node")
    if scoped is not None:
        # The graph reader already selected and verified this explicit scope.
        # Do not run another implicit neighborhood expansion over its excerpts.
        roots = {node_identity(ref) for ref in scoped["root_refs"]}
        tails = {node_identity(ref) for ref in graph["checkpoint_progress"]["tail_step_refs"]}
        for nid in nodes:
            add(nid, "scope_root" if nid in roots else "checkpoint_tail_step" if nid in tails else "explicit_scope_reference")
    elif view == "node":
        add(node_id, "selected_node")
        branch = nodes[node_id]["branch_id"]
        for nid, node in nodes.items():
            if branch is not None and node["branch_id"] == branch:
                add(nid, "same_branch")
        for edge in graph["edges"]:
            if node_id in (edge["from"], edge["to"]):
                add(edge["from"], "adjacent_" + edge["kind"])
                add(edge["to"], "adjacent_" + edge["kind"])
        # Follow provenance dependencies, never infer truth from proximity.
        # Counter edges are followed in both directions while retaining their
        # original from/to semantics in the returned relation.
        changed = True
        while changed:
            before = len(selected_scope)
            for edge in graph["edges"]:
                a, b, kind = edge["from"], edge["to"], edge["kind"]
                if kind == "refutes" and (a in selected_scope or b in selected_scope):
                    add(a, "related_counter_relationship"); add(b, "related_counter_relationship")
                elif kind == "depends_on" and a in selected_scope:
                    add(b, "required_dependency")
                # Follow explicit outcome/choice attribution back from an applied
                # use. Do not expand every task sharing a method or input file.
                elif kind == "depends_on" and b in selected_scope and edge.get("relation") in {
                        "applied_use", "evaluated_use", "evaluated_node", "evaluation_plan", "evaluation_result",
                        "selection_basis", "selection_output"}:
                    add(a, "attributed_" + edge["relation"])
                elif kind in {"supports", "derived_from"} and b in selected_scope:
                    add(a, "required_dependency")
            changed = before != len(selected_scope)
    else:
        for ref in graph["checkpoint_progress"]["tail_step_refs"]:
            if ref.get("schema") != "etalon-evidence-ref/1" or node_identity(ref) not in nodes:
                raise EvidenceError("checkpoint tail step is unavailable in bounded graph; narrow the published checkpoint")
            add(node_identity(ref), "checkpoint_tail_step")
        for nid, node in nodes.items():
            if "experience" in node:
                add(nid, "current_task_experience")
        for branch in graph["branches"]:
            for ref in branch["basis_refs"] + branch["next_read_refs"]:
                if ref.get("schema") == "etalon-evidence-ref/1":
                    add(node_identity(ref), "branch_reference")
        for nid in graph["counter_node_ids"]:
            add(nid, "declared_counter_relationship_context")
        for nid, node in nodes.items():
            if node["kind"] == "J":
                add(nid, "current_judgment")

    expected_edges = [e for e in graph["edges"] if e["kind"] == "refutes" and
                      (scoped is not None or view == "summary" or e["from"] in selected_scope or e["to"] in selected_scope)]
    references = {}

    def reference(ref):
        if ref.get("schema") != "etalon-evidence-ref/1":
            return None
        nid = node_identity(ref)
        references[nid] = ref
        return nid

    branches = []
    for branch in graph["branches"]:
        item = {k: v for k, v in branch.items() if k not in {"basis_refs", "next_read_refs"}}
        for key in ("basis_refs", "next_read_refs"):
            ids = [reference(ref) for ref in branch[key]]
            item[key.replace("_refs", "_node_ids")] = [nid for nid in ids if nid is not None]
            if None in ids:
                item["reference_gap"] = True
        branches.append(item)
    for edge in expected_edges:
        reference(nodes[edge["from"]]["ref"])
        reference(nodes[edge["to"]]["ref"])
    for nid in set(graph["counter_node_ids"]) & selected_scope:
        reference(nodes[nid]["ref"])
    for ref in graph["checkpoint_progress"]["tail_step_refs"]:
        reference(ref)
    # Conditions may be large; even omitted experiences retain an exact way to
    # inspect them. No omitted condition is treated as an applicability match.
    for nid in selected_scope:
        if scoped is not None or "experience" in nodes[nid] or nid == node_id:
            reference(nodes[nid]["ref"])
    if scoped is not None:
        # These pointers are mandatory navigation, not optional body excerpts.
        # Some belong to an explicitly reported ordinary-hop boundary.
        for key in ("root_refs", "control_refs", "counter_endpoint_refs", "deliberate_boundary_refs"):
            for ref in scoped[key]:
                reference(ref)
        for relation in scoped["counter_relations"]:
            for key in ("from_ref", "to_ref", "basis_ref"):
                reference(relation[key])
        for relation in graph["edges"]:
            reference(relation["basis_ref"])
        expected_edges = [_compact_relation(edge) for edge in expected_edges]

    complete = not graph["issues"] and all(c["status"] == "complete" for c in graph["coverage"])
    result = {"schema": CONTEXT, "view": view, "checkpoint_ref": graph["checkpoint_ref"], **handoff,
        "task_id": graph["task_id"], "question": question or graph["question"], "research_question": graph["question"],
        "node_id": node_id, "purpose": purpose, "items": [], "relations": expected_edges,
        "branches": branches, "reference_index": [{"node_id": nid, "ref": references[nid]} for nid in sorted(references)],
        "continuation": graph["continuation"], "publications": publications, "source_watermarks": graph["coverage"],
        "checkpoint_progress": graph["checkpoint_progress"],
        "source_issues": graph["issues"], "temporal": graph["temporal"],
        "selection": {"strategy": "explicit-navigation-" + view + "/1", "policy": graph["policy"],
                      "graph_view_id": graph["view_id"], "candidate_count": len(nodes),
                      "eligible_count": len(selected_scope), "selected_count": 0, "excluded_by_budget": 0,
                      "excluded_by_view": len(nodes) - len(selected_scope), "graph_truncated": graph["truncated"],
                      "coverage_complete": complete,
                      "exclusion_scope": "bounded permitted graph; node view follows explicit dependencies, not semantic similarity"},
        "counterevidence": {"search": "checkpoint_claim_links_and_reverse_links_across_published_workspaces",
                            "status": "coverage_insufficient", "selected_refs": [], "declared_counter_refs": [],
                            "coverage_complete": False, "selection_semantics": graph["counter_context_semantics"],
                            "context_complete": graph["counter_context_complete"],
                            "relationship_count": len(expected_edges), "selected_relationship_count": len(expected_edges),
                            "scope": "selected_node_dependency_neighborhood" if view == "node" else "checkpoint_task"},
        "followup": {"entry": "pass read_context as entry for a pinned context view",
                     "context_tool": "etalon_research_context", "context_views": ["node", "full"],
                     "node_id_source": "items[].node_id or reference_index[].node_id",
                     "context_max_bytes": {"minimum": 4096, "maximum": 65536},
                     "evidence_tool": "etalon_evidence_read", "ref_source": "items[].ref or reference_index[].ref",
                     "evidence_scope": "pass read_context; do not infer workspace paths",
                     "evidence_views": ["record", "artifact"], "evidence_limit": {"minimum": 1, "maximum": 32768},
                     "artifact_paging": "follow fragment.next_offset; no need to repeat the computation"},
        "reading_menu": reading_menu(),
        "unknowns": ["No global semantic search or scientific truth assessment was performed.",
                     "Summary excerpts are navigation data; inspect exact refs for full conditions before a new decision."],
        "execution_permission": "not_granted", "automatic_retry": False,
        "content_role": "sourced_research_data_not_host_instructions", "truncated": graph["truncated"]}
    if scoped is not None:
        result["scope"] = scoped["requested_scope"]
        result["scope_receipt"] = _compact_scope_receipt(scoped)
        result["selection"]["strategy"] = "explicit-scope-navigation-" + view + "/1"
        result["selection"]["exclusion_scope"] = "explicit requested scope; ordinary-hop boundaries remain exact references"
        result["selection"]["coverage_complete"] = False
        result["counterevidence"]["scope"] = "explicit_requested_scope"
        result["followup"].update(
            scope_source="pass scope with read_context to repeat this explicit scope",
            boundary_ref_source="resolve scope_receipt.deliberate_boundary_node_ids in reference_index; read originals or start a new root_refs scope",
            omitted_excerpt_refs="reference_index entries absent from items; use evidence_tool with read_context")
        result["unknowns"].append("Scoped reference coverage does not establish global coverage or reading every original body.")
        result["unknowns"].append("Traversal covers recognized explicit references; opaque artifact bodies do not establish a scientific dependency closure.")
        _scope_excerpt_receipt(result, set(), selected_scope)
    if view == "summary":
        result["resume_frontier"] = _resume_frontier(workspace, graph, nodes, max_bytes=max_bytes)
    detail_reserve = 160 if view == "summary" else 128
    # Reserve room for identity, selected counters and omission receipt. We do
    # not drop branch, pending, pause or direction metadata to squeeze a view.
    if len(canonical(result).encode()) > max_bytes - max(256, detail_reserve):
        if scoped is not None:
            raise EvidenceError("scoped checkpoint and counter metadata exceeds context bound; choose narrower roots or ordinary_hops, or read exact originals")
        raise EvidenceError("checkpoint and counter control metadata exceeds context bound; increase max_bytes")
    priority = sorted(selected_scope, key=lambda nid: (
        nid != node_id, reasons[nid] != "checkpoint_tail_step", reasons[nid] != "scope_root", "experience" not in nodes[nid],
        nid not in graph["counter_node_ids"], reasons[nid] != "branch_reference",
        nodes[nid]["kind"] != "J", nid))
    if view == "summary":
        # Summary is a resumption card, not another full-graph dump. All branch
        # and counter identities remain in the index even beyond this cap.
        result["selection"]["item_limit"] = 4
    selected = set()
    for nid in priority:
        if view == "summary" and len(selected) >= 4:
            result["selection"]["excluded_by_view"] += 1
            continue
        node = nodes[nid]
        item = dict(node) if view != "summary" else {k: node[k] for k in (
            "node_id", "ref", "home", "kind", "kind_basis", "state", "branch_id", "content_type")}
        if view == "summary":
            brief = node["summary"]
            item["summary"] = {"text": brief["text"][:240], "truncated": brief["truncated"] or len(brief["text"]) > 240}
            # Do not abbreviate applicability, alternatives or claim scope.
            item.update({k: node[k] for k in ("experience", "decision", "claim_scope", "revision_note",
                                             "research_decision", "evidence_timing") if k in node})
        item["selection_reason"] = reasons[nid]
        result["items"].append(item)
        selected.add(nid)
        _counter_receipt(result, graph, nodes, selected, selected_scope, complete)
        if len(canonical(result).encode()) > max_bytes - detail_reserve:
            result["items"].pop(); selected.remove(nid)
            result["selection"]["excluded_by_budget"] += 1
            if nid == node_id:
                if scoped is not None:
                    raise EvidenceError("selected node and scoped controls exceed context bound; read its exact original with etalon_evidence_read")
                raise EvidenceError("selected node and control metadata exceed context bound; increase max_bytes")
            if reasons[nid] == "checkpoint_tail_step":
                # A growing checkpoint must not make a bounded resumption card
                # unusable just because all tail step bodies no longer fit.
                # Their exact references remain mandatory in checkpoint_progress
                # and reference_index; only optional excerpts are omitted.
                result["selection"]["tail_details_omitted"] = result["selection"].get("tail_details_omitted", 0) + 1
    _counter_receipt(result, graph, nodes, selected, selected_scope, complete)
    if view != "summary":
        for edge in graph["edges"]:
            if edge["kind"] != "refutes" and edge["from"] in selected and edge["to"] in selected:
                result["relations"].append(_compact_relation(edge) if scoped is not None else edge)
                if len(canonical(result).encode()) > max_bytes - 128:
                    result["relations"].pop()
                    result["selection"]["omitted_optional_relations"] = result["selection"].get("omitted_optional_relations", 0) + 1
    result["selection"]["selected_count"] = len(selected)
    result["truncated"] = graph["truncated"] or len(selected) != len(selected_scope)
    if graph["issues"]:
        result["unknowns"].append("Some explicit evidence references were unavailable or changed.")
    result["context_id"] = "research-context-" + digest(result)
    if len(canonical(result).encode()) > max_bytes:
        if scoped is not None:
            raise EvidenceError("scoped context receipt exceeds byte bound; choose narrower roots or ordinary_hops, or read exact originals")
        raise EvidenceError("context receipt exceeds byte bound; increase max_bytes")
    return result


def _counter_receipt(result, graph, nodes, selected, selected_scope, complete):
    counters = set(graph["counter_node_ids"]) & selected_scope
    result["counterevidence"].update(
        selected_refs=[nodes[nid]["ref"] for nid in sorted(counters & selected)],
        declared_counter_refs=[nodes[nid]["ref"] for nid in graph["counter_evidence_node_ids"] if nid in selected],
        status="found" if counters & selected else "none_in_checked_scope" if complete and not graph["truncated"] and not counters else "coverage_insufficient",
        coverage_complete=complete and not graph["truncated"] and graph["counter_context_complete"] and counters <= selected)
    if "scope_receipt" in result:
        # Preserve the old conservative flag: only the explicitly named scope
        # receipt can claim scoped success. A small excerpt cap says nothing
        # about whether all declared counter pointers have been delivered.
        result["counterevidence"]["coverage_complete"] = False
        _scope_excerpt_receipt(result, selected, selected_scope)


def _compact_relation(edge):
    return {**{key: value for key, value in edge.items() if key != "basis_ref"},
            "basis_node_id": node_identity(edge["basis_ref"])}


def _compact_scope_receipt(scoped):
    """Keep exact identities once in reference_index, preserving every relation."""
    ref_fields = ("root_refs", "control_refs", "counter_endpoint_refs", "deliberate_boundary_refs")
    receipt = {key: value for key, value in scoped.items()
               if key not in {*ref_fields, "counter_relations", "boundary_relations", "requested_scope", "schema"}}
    receipt.update(schema="etalon-research-context-scope/1", graph_scope_schema=scoped["schema"],
                   reference_table="reference_index; node_id resolves to the exact original ref")
    for key in ref_fields:
        receipt[key.replace("_refs", "_node_ids")] = [node_identity(ref) for ref in scoped[key]]
    requested = scoped["requested_scope"]
    receipt["requested_scope"] = ({"root_node_ids": [node_identity(ref) for ref in requested["root_refs"]],
        "ordinary_hops": requested["ordinary_hops"]} if "root_refs" in requested else requested)
    groups = {}
    for edge in scoped["boundary_relations"]:
        groups.setdefault((edge["from"], edge["relation"]), []).append(edge["to"])
    receipt["boundary_relation_groups"] = [{"from": source, "relation": relation, "to_node_ids": targets}
        for (source, relation), targets in sorted(groups.items())]
    receipt["boundary_relation_count"] = len(scoped["boundary_relations"])
    # All counter directions are mandatory top-level relations. Point to that
    # single lossless representation rather than delivering every edge twice.
    receipt["counter_relation_source"] = "relations where kind=refutes; basis_node_id resolves in reference_index"
    receipt["counter_relation_count"] = len(scoped["counter_relations"])
    return receipt


def _scope_excerpt_receipt(result, selected, selected_scope):
    receipt = result["scope_receipt"]
    counters = set(receipt["counter_endpoint_node_ids"])
    result["excerpt_coverage"] = {
        "eligible_count": len(selected_scope), "selected_count": len(selected & selected_scope),
        "omitted_count": len(selected_scope - selected), "complete": selected_scope <= selected,
        "counter_endpoint_count": len(counters), "selected_counter_endpoint_count": len(counters & selected),
        "omitted_counter_endpoint_count": len(counters - selected), "counter_complete": counters <= selected,
        "meaning": "node excerpts only; original-byte verification is separate from reading full original content"}
    receipt["excerpt_coverage_complete"] = selected_scope <= selected
    result["counterevidence"]["reference_coverage_complete"] = receipt["counter_reference_coverage_complete"]
    result["counterevidence"]["excerpt_coverage_complete"] = counters <= selected
    result["counterevidence"]["status"] = ("found" if counters else "none_in_checked_scope"
        if receipt["counter_reference_coverage_complete"] else "coverage_insufficient")


def _publication_context(entry):
    """Expose indexed, unfinished archival work separately from science jobs."""
    if not isinstance(entry, (str, Path)):
        return {"status": "not_inspected", "reason": "entry_path_required_for_publication_index",
                "incomplete_count": None, "operations": []}
    from .publication import discover_publications
    try:
        discovery = discover_publications(entry)
    except (EvidenceError, OSError, ValueError) as exc:
        return {"status": "unknown", "reason": type(exc).__name__,
                "incomplete_count": None, "operations": []}
    fields = ("operation_id", "intent_path", "status", "phase", "needs_recovery", "checkpoint_ref", "entry_conflict", "reason")
    return {"status": "observed", "incomplete_count": discovery["incomplete_count"],
            "operations": [{k: item[k] for k in fields if k in item} for item in discovery["operations"]],
            "inspect": {"api": "etalon.research.publication.inspect_publication", "argument": "operations[].intent_path"},
            "meaning": "publication recovery is metadata work, not a scientific execution or retry",
            "automatic_retry": False}


def load_entry(entry):
    """Read the existing {workspace, checkpoint_ref} entry, preserving relocation."""
    return resolve_entry(entry)[:2]


@paged_read
def build_research_context(entry, *, node_id=None, question=None, purpose="research",
                           policy=None, max_bytes=16384, as_of=None, home_overrides=None,
                           conditions=None, view="full", scope=None, section="references",
                           cursor=None, limit=20, branch_id=None, selectors=None, offset=0,
                           definitions=None):
    """Read local/adjacent/counterevidence context and report selection coverage.

    question records the host's purpose; it does not rewrite the research task
    or silently impose a semantic relevance filter. No embedding model or global
    search is invoked. A missing counter is reported, never filled to a quota.
    Optional scope selects exact roots or a branch before bounded graph traversal;
    scoped reference coverage and excerpt delivery never imply global coverage.
    """
    from .notes import ENTRY as NOTE_ENTRY, entry_value, read_entry as read_note_entry, read_current
    candidate = entry_value(entry)
    if candidate.get("schema") == NOTE_ENTRY:
        if scope is not None or node_id is not None or cursor is not None or policy is not None or view not in {"summary", "card", "full", "knowledge", "index", "selected"}:
            raise EvidenceError("note entry selects one task branch; graph scope applies to legacy graph entries")
        if view in {"summary", "card"}:
            recipe = ({"view": "selected", "selectors": selectors, "offset": offset, "limit": limit}
                      if selectors is not None else None)
            result = read_current(entry, recipe=recipe, conditions=conditions,
                definitions=definitions, branch_id=branch_id, max_bytes=max_bytes)
        else:
            result = read_note_entry(entry, conditions=conditions, definitions=definitions, branch_id=branch_id,
                view=view, selectors=selectors, offset=offset, limit=limit, max_bytes=max_bytes)
        if len(canonical(result).encode("utf-8")) > max_bytes:
            raise EvidenceError("note exceeds delivery budget; keep arrays in original artifacts")
        return result
    if branch_id is not None or selectors is not None or offset:
        raise EvidenceError("branch_id/selectors/offset apply to note entries; graph branches use scope")
    if view not in {"summary", "node", "full", "card", "manifest"}:
        raise EvidenceError("context view must be summary, node, full, card or manifest")
    if view in {"card", "manifest"} and scope is not None:
        raise EvidenceError("card and manifest use the pinned checkpoint; graph scope belongs to summary, node or full")
    if view != "manifest" and (section != "references" or scope is None and (cursor is not None or limit != 20)):
        raise EvidenceError("section/cursor/limit require manifest or scoped context view")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise EvidenceError("context page limit must be 1..50")
    if view == "manifest" and (question is not None or conditions is not None or definitions is not None):
        raise EvidenceError("manifest uses exact navigation, without question or conditions selection")
    if view == "node" and scope is None and (not isinstance(node_id, str) or not node_id):
        raise EvidenceError("node context requires node_id")
    if type(max_bytes) is not int or not 4096 <= max_bytes <= 65536:
        raise EvidenceError("context max_bytes must be 4096..65536")
    if question is not None and (not isinstance(question, str) or len(question) > 4096):
        raise EvidenceError("context question must be text of at most 4096 characters")
    if conditions is not None and (not isinstance(conditions, dict) or not conditions or
            any(not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in conditions.items())):
        raise EvidenceError("conditions must contain explicit nonempty string values")
    workspace, checkpoint_ref, entry_path, pinned = resolve_entry(entry)
    if pinned is not None:
        if policy is not None and policy != pinned["policy"]:
            raise EvidenceError("pinned context policy cannot change; resolve the entry explicitly")
        policy, purpose = pinned["policy"], pinned["purpose"]
    read_context = pinned or make_read_context(workspace, checkpoint_ref,
        entry_path=entry_path, policy=policy, purpose=purpose)
    handoff = {"read_context": read_context, "entry_status": continuation_status(read_context)}
    publications = _publication_context(entry_path or entry)
    if read_context is not None:
        # Use the exact verified definition for this assembly, even if a publisher
        # atomically advances a path while source records are being collected.
        workspace = workspace_from_context(read_context, apply_policy=False)
    if view == "manifest" and section == "closure":
        if node_id is not None or as_of is not None or home_overrides is not None:
            raise EvidenceError("closure pages use the pinned declared closure without node/as_of/home selection")
        from .continuation_pages import closure_page
        return closure_page(workspace, checkpoint_ref, handoff, policy=policy, purpose=purpose,
                            cursor=cursor, limit=limit, max_bytes=max_bytes)
    graph_options = {} if scope is None else {"scope": scope}
    graph, metadata_overflow = None, None
    # Explicit cards already use the checked continuation inventory below.
    # Building a full display graph first repeats resume/original reads merely
    # to discard its nodes. Summary fallback retains its existing graph path.
    if view not in {"manifest", "card"}:
        try:
            graph = build_research_graph(workspace, checkpoint_ref=checkpoint_ref, policy=policy,
                purpose=purpose, home_overrides=home_overrides, max_nodes=128, max_bytes=131072, as_of=as_of,
                **graph_options)
        except EvidenceError as exc:
            # Only a transport metadata overflow can select the alternate
            # presentation. Missing, corrupt or forbidden originals still fail.
            if view not in {"summary", "card"} or scope is not None or str(exc) not in {
                    "checkpoint metadata exceeds graph byte bound; use a smaller published checkpoint",
                    "graph identity exceeds byte bound"}:
                raise
            metadata_overflow = str(exc)
    from .meta_context import workset_summary
    from .graph_memory import restrict_workspace
    try:
        visible_workspace = restrict_workspace(workspace, policy=policy, purpose=purpose)[0]
        handoff["task_workset"] = workset_summary(visible_workspace, checkpoint_ref, conditions=conditions,
                                                definitions=definitions)
    except (ValueError, OSError, KeyError) as exc:
        handoff["task_workset"] = {"status": "unavailable", "reason": str(exc)[:500]}
        if graph is not None and scope is None:
            graph["issues"].append({"code": "task_workset_unavailable", "message": str(exc)[:500]})
    def card_or_manifest(reason=None):
        from .continuation_card import _inventory, continuation_card, manifest_page
        if view == "card":
            from .graph_memory import HOMES
            if home_overrides is not None and (not isinstance(home_overrides, dict)
                    or any(not isinstance(k, str) or v not in HOMES for k, v in home_overrides.items())):
                raise EvidenceError("home overrides require node identities and Search/Judge/Quick/Deep")
        data = _inventory(workspace, checkpoint_ref, policy=policy, purpose=purpose,
            publications=publications, handoff=handoff, graph=graph, as_of=as_of, home_overrides=home_overrides,
            neutral_workset=handoff["task_workset"] if conditions is None and definitions is None else None)
        if view == "manifest":
            return manifest_page(data, checkpoint_ref, handoff, section=section, cursor=cursor,
                                 limit=limit, max_bytes=max_bytes, node_id=node_id)
        return continuation_card(data, checkpoint_ref, handoff, publications=publications, purpose=purpose,
            max_bytes=max_bytes, view=view, question=question, node_id=node_id, fallback_reason=reason)
    if view in {"card", "manifest"} or metadata_overflow is not None:
        return card_or_manifest(metadata_overflow)
    # Applicability annotations belong to this context, not the immutable
    # projection whose identity binds a possible lossless continuation.
    nodes = {n["node_id"]: n for n in (json.loads(canonical(graph["nodes"])) if scope is not None else graph["nodes"])}
    if node_id is not None and node_id not in nodes:
        # Accept a unique original workflow-node ID for convenience, but never
        # silently choose among repeated executions of the same logical node.
        matches = [n["node_id"] for n in nodes.values() if n["logical_node_id"] == node_id]
        if len(matches) != 1:
            raise EvidenceError("context node is unavailable or logical node identity is ambiguous")
        node_id = matches[0]
    def scoped_page():
        from .graph_scope import graph_page
        return graph_page(graph, cursor=cursor, limit=limit, max_bytes=max_bytes, handoff=handoff,
            context={"requested_view": view, "node_id": node_id, "question": question,
                "purpose": purpose, "conditions": conditions, "scope": scope,
                **({"definitions": definitions} if definitions is not None else {}),
                "publications": publications,
                "handoff": {k: v for k, v in handoff.items() if k != "read_context"}})
    if scope is not None and cursor is not None:
        return scoped_page()
    priority = []
    def add(nid, reason):
        if nid in nodes and all(item[0] != nid for item in priority):
            priority.append((nid, reason))
    if node_id:
        add(node_id, "selected_node")
    for nid, node in nodes.items():
        if "experience" in node:
            unmet = [] if conditions is None else [k for k, v in node["experience"]["conditions"].items() if conditions.get(k) != v]
            node["experience"]["applicability"] = "not_checked_for_new_branch" if conditions is None else "conditions_mismatch" if unmet else "conditions_match_host_review_required"
            node["experience"]["unmatched_conditions"] = unmet
            add(nid, "current_task_experience" if conditions is None else "matched_experience" if not unmet else "experience_counterexample_to_transfer")
    if view != "full" or scope is not None:
        try:
            result = _focused_context(graph, nodes, workspace=visible_workspace, node_id=node_id, question=question,
                                      purpose=purpose, max_bytes=max_bytes, view=view, publications=publications, handoff=handoff)
            if scope is not None and len(canonical(result).encode()) > max_bytes - 16:
                return scoped_page()
            return result
        except EvidenceError as exc:
            if scope is not None and str(exc) in {
                    "scoped checkpoint and counter metadata exceeds context bound; choose narrower roots or ordinary_hops, or read exact originals",
                    "selected node and scoped controls exceed context bound; read its exact original with etalon_evidence_read",
                    "scoped context receipt exceeds byte bound; choose narrower roots or ordinary_hops, or read exact originals"}:
                return scoped_page()
            if view != "summary" or scope is not None or str(exc) not in {
                    "checkpoint and counter control metadata exceeds context bound; increase max_bytes",
                    "context receipt exceeds byte bound; increase max_bytes"}:
                raise
            return card_or_manifest(str(exc))
    for nid in graph["counter_node_ids"]:
        add(nid, "declared_counter_relationship_context")
    if node_id:
        for edge in graph["edges"]:
            if node_id in (edge["from"], edge["to"]):
                add(edge["to"] if edge["from"] == node_id else edge["from"], "adjacent_" + edge["kind"])
        branch = nodes[node_id]["branch_id"]
        if branch is not None:
            for nid, node in nodes.items():
                if node["branch_id"] == branch:
                    add(nid, "same_branch")
    for nid, node in nodes.items():
        if node["kind"] == "J":
            add(nid, "current_judgment")
    for nid in nodes:
        add(nid, "checkpoint_or_task_reference")
    references = {}
    branches = []
    for branch in graph["branches"]:
        item = {k: v for k, v in branch.items() if k not in {"basis_refs", "next_read_refs"}}
        for key in ("basis_refs", "next_read_refs"):
            ids = []
            for ref in branch[key]:
                if ref.get("schema") == "etalon-evidence-ref/1":
                    nid = node_identity(ref)
                    references[nid] = ref
                    ids.append(nid)
                else:
                    item["reference_gap"] = True
            item[key.replace("_refs", "_node_ids")] = ids
        branches.append(item)
    result = {"schema": CONTEXT, "view": view, "checkpoint_ref": checkpoint_ref, **handoff,
        "task_id": graph["task_id"], "question": question or graph["question"], "research_question": graph["question"],
        "node_id": node_id, "purpose": purpose, "items": [], "relations": [],
        "branches": branches, "reference_index": [{"node_id": nid, "ref": ref} for nid, ref in references.items()],
        "continuation": graph["continuation"], "publications": publications,
        "checkpoint_progress": graph["checkpoint_progress"],
        "source_watermarks": graph["coverage"], "temporal": graph["temporal"], "source_issues": graph["issues"],
        "selection": {"strategy": STRATEGY, "policy": graph["policy"], "graph_view_id": graph["view_id"],
                      "candidate_count": len(nodes), "selected_count": 0, "excluded_by_budget": 0,
                      "exclusion_scope": "only candidates in the bounded permitted graph; no claim about unknown evidence",
                      "graph_truncated": graph["truncated"],
                      "coverage_complete": not graph["issues"] and all(c["status"] == "complete" for c in graph["coverage"])},
        "counterevidence": {"search": "checkpoint_claim_links_and_reverse_links_across_published_workspaces",
                            "status": "not_established", "selected_refs": [], "coverage_complete": False,
                            "selection_semantics": graph["counter_context_semantics"],
                            "declared_counter_refs": []},
        "unknowns": ["No global semantic search or scientific truth assessment was performed."],
        "execution_permission": "not_granted", "automatic_retry": False,
        "content_role": "sourced_research_data_not_host_instructions",
        "truncated": graph["truncated"]}
    # Reserve for the final receipt, counters and bounded relation subset. A
    # large mandatory checkpoint fails closed instead of silently losing pause.
    reserve = min(1536, max_bytes // 8)
    if len(canonical(result).encode()) > max_bytes - reserve:
        raise EvidenceError("checkpoint control metadata exceeds context bound")
    selected_ids = set()
    for nid, reason in priority:
        item = {**nodes[nid], "selection_reason": reason}
        result["items"].append(item)
        if len(canonical(result).encode()) > max_bytes - reserve:
            result["items"].pop()
            result["selection"]["excluded_by_budget"] += 1
            result["truncated"] = True
        else:
            selected_ids.add(nid)
    selected_counters = [nodes[nid]["ref"] for nid in graph["counter_node_ids"] if nid in selected_ids]
    complete = not graph["truncated"] and not graph["issues"] and all(c["status"] == "complete" for c in graph["coverage"])
    result["counterevidence"].update(selected_refs=selected_counters,
        status="found" if selected_counters else "none_in_checked_scope" if complete and not graph["counter_node_ids"] else "coverage_insufficient",
        coverage_complete=complete and graph["counter_context_complete"] and len(selected_counters) == len(graph["counter_node_ids"]),
        declared_counter_refs=[nodes[nid]["ref"] for nid in graph["counter_evidence_node_ids"] if nid in selected_ids])
    if result["counterevidence"]["status"] != "found":
        result["unknowns"].append("No eligible counterevidence was selected; this is not proof that none exists.")
    if graph["issues"]:
        result["unknowns"].append("Some explicit evidence references were unavailable or changed.")
    result["selection"]["selected_count"] = len(result["items"])
    # Directional counter relationships precede optional provenance edges.
    for edge in sorted(graph["edges"], key=lambda e: e["kind"] != "refutes"):
        if edge["from"] in selected_ids and edge["to"] in selected_ids:
            result["relations"].append(edge)
            if len(canonical(result).encode()) > max_bytes - 192:
                result["relations"].pop()
                result["truncated"] = True
    while len(canonical(result).encode()) > max_bytes - 256 and result["items"]:
        removed = result["items"].pop()["node_id"]
        selected_ids.remove(removed)
        result["relations"] = [e for e in result["relations"] if removed not in (e["from"], e["to"])]
        result["counterevidence"]["selected_refs"] = [r for r in result["counterevidence"]["selected_refs"] if node_identity(r) != removed]
        result["counterevidence"]["declared_counter_refs"] = [r for r in result["counterevidence"]["declared_counter_refs"] if node_identity(r) != removed]
        result["selection"]["excluded_by_budget"] += 1
        result["selection"]["selected_count"] -= 1
        result["truncated"] = True
        if removed in graph["counter_node_ids"]:
            result["counterevidence"]["coverage_complete"] = False
            if not result["counterevidence"]["selected_refs"]:
                result["counterevidence"]["status"] = "coverage_insufficient"
    expected_counter_relations = [e for e in graph["edges"] if e["kind"] == "refutes"]
    selected_counter_relations = [e for e in result["relations"] if e["kind"] == "refutes"]
    if len(expected_counter_relations) != len(selected_counter_relations):
        result["counterevidence"]["coverage_complete"] = False
    result["counterevidence"]["relationship_count"] = len(expected_counter_relations)
    result["counterevidence"]["selected_relationship_count"] = len(selected_counter_relations)
    result["context_id"] = "research-context-" + digest(result)
    if len(canonical(result).encode()) > max_bytes:
        raise EvidenceError("context receipt exceeds byte bound; increase the bound")
    return result
