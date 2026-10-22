"""Bounded decision context from a research graph, with selection receipts.

This is deterministic retrieval/assembly, not a claim-writing model. It never
executes tasks, changes methods, writes a journal, or grants permission to resume.
"""
from __future__ import annotations

import json
from pathlib import Path

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, validate_ref
from .graph_memory import build_research_graph, node_identity

CONTEXT = "etalon-research-context/1"
STRATEGY = "node-neighborhood-explicit-counterevidence/1"


def load_entry(entry):
    """Read the existing {workspace, checkpoint_ref} entry, preserving relocation."""
    base = None
    if isinstance(entry, (str, Path)):
        path = Path(entry).resolve(strict=True)
        if path.stat().st_size > 65536:
            raise EvidenceError("research entry exceeds 64 KiB")
        entry = json.loads(path.read_text(encoding="utf-8-sig"))
        base = path.parent
    if not isinstance(entry, dict) or "workspace" not in entry or "checkpoint_ref" not in entry:
        raise EvidenceError("research entry requires workspace and checkpoint_ref")
    workspace = entry["workspace"]
    if isinstance(workspace, str) and base is not None and not Path(workspace).is_absolute():
        workspace = base / workspace
    return workspace, validate_ref(entry["checkpoint_ref"])


def build_research_context(entry, *, node_id=None, question=None, purpose="research",
                           policy=None, max_bytes=16384, as_of=None, home_overrides=None,
                           conditions=None):
    """Read local/adjacent/counterevidence context and report selection coverage.

    question records the host's purpose; it does not rewrite the research task
    or silently impose a semantic relevance filter. No embedding model or global
    search is invoked. A missing counter is reported, never filled to a quota.
    """
    if type(max_bytes) is not int or not 4096 <= max_bytes <= 65536:
        raise EvidenceError("context max_bytes must be 4096..65536")
    if question is not None and (not isinstance(question, str) or len(question) > 4096):
        raise EvidenceError("context question must be text of at most 4096 characters")
    if conditions is not None and (not isinstance(conditions, dict) or not conditions or
            any(not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in conditions.items())):
        raise EvidenceError("conditions must contain explicit nonempty string values")
    workspace, checkpoint_ref = load_entry(entry)
    graph = build_research_graph(workspace, checkpoint_ref=checkpoint_ref, policy=policy,
        purpose=purpose, home_overrides=home_overrides, max_nodes=128, max_bytes=131072, as_of=as_of)
    nodes = {n["node_id"]: n for n in graph["nodes"]}
    if node_id is not None and node_id not in nodes:
        # Accept a unique original workflow-node ID for convenience, but never
        # silently choose among repeated executions of the same logical node.
        matches = [n["node_id"] for n in nodes.values() if n["logical_node_id"] == node_id]
        if len(matches) != 1:
            raise EvidenceError("context node is unavailable or logical node identity is ambiguous")
        node_id = matches[0]
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
    result = {"schema": CONTEXT, "checkpoint_ref": checkpoint_ref,
        "task_id": graph["task_id"], "question": question or graph["question"], "research_question": graph["question"],
        "node_id": node_id, "purpose": purpose, "items": [], "relations": [],
        "branches": branches, "reference_index": [{"node_id": nid, "ref": ref} for nid, ref in references.items()],
        "continuation": graph["continuation"],
        "source_watermarks": graph["coverage"], "temporal": graph["temporal"],
        "selection": {"strategy": STRATEGY, "policy": graph["policy"], "graph_view_id": graph["view_id"],
                      "candidate_count": len(nodes), "selected_count": 0, "excluded_by_budget": 0,
                      "exclusion_scope": "only candidates in the bounded permitted graph; no claim about unknown evidence",
                      "graph_truncated": graph["truncated"]},
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
