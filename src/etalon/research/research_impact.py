"""Read-only, bounded review impact of exact changed published references.

An impact path is declared dependency evidence, not causal attribution, automatic
invalidation or an instruction to execute. No comparison of opaque bodies or
semantic change detection is performed here.
"""
from __future__ import annotations

from collections import deque

from etalon.active.schema import canonical, digest
from .continuation import CHECKPOINT, read_content, resume_research
from .declared_relations import checked_row, relation_index, record_identity
from .entry import resolve_entry, make_read_context, workspace_from_context, continuation_status
from .evidence import _collect, _read_evidence
from .evidence_refs import EvidenceError, validate_ref
from .graph_memory import _ids, restrict_workspace
from .read_scope import bounded_read


@bounded_read
def build_research_impact(entry, *, changed_refs, policy=None, purpose="research",
                          max_nodes=64, max_bytes=16384):
    """Trace current exact references to potentially affected published records.

    All original envelopes on selected paths are verified. Omitted frontier
    endpoints remain explicit metadata-verified boundaries. Counter relations
    are traversed both ways for review, retaining their authoritative direction.
    """
    if type(max_nodes) is not int or not 1 <= max_nodes <= 128:
        raise EvidenceError("impact node bound must be 1..128")
    if type(max_bytes) is not int or not 4096 <= max_bytes <= 65536:
        raise EvidenceError("impact byte bound must be 4096..65536")
    if not isinstance(changed_refs, list) or not changed_refs or len(changed_refs) > max_nodes:
        raise EvidenceError("impact changed_refs must be nonempty within node bound")
    changed_refs = [ref for _, ref in sorted({canonical(validate_ref(ref)): validate_ref(ref)
                                            for ref in changed_refs}.items())]
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
    ws, policy_receipt = restrict_workspace(workspace, policy=policy, purpose=purpose)
    records, coverage, warnings = collection = _collect(ws)
    checkpoint = read_content(ws, checkpoint_ref)
    if checkpoint.get("schema") != CHECKPOINT:
        raise EvidenceError("impact requires a research checkpoint")
    resumed = resume_research(ws, checkpoint_ref, read_mode="frozen_view")
    universe = {(sid, rid): row for sid, rows in records.items() for rid, row in rows.items()}
    indexed = relation_index(ws, collection, dependencies=True)
    issues = [*warnings, *indexed["issues"]]
    suppressed = sum(len(_ids(ws, s) - records[s["source_id"]].keys())
        for s in ws.manifest["sources"] if s["source_id"] in records
        and s["kind"] in {"journal", "artifact_manifest"})
    if suppressed:
        issues.append({"reason": "published_records_hidden_or_dependency_suppressed", "count": suppressed})
    if (policy_receipt["excluded_source_count"] or policy_receipt["restricted_record_rule_count"]
            or (policy or {}).get("record_ids")):
        issues.append({"reason": "policy_narrowed_impact_universe"})
    if any("record_ids" in s for s in ws.manifest["sources"]):
        issues.append({"reason": "published_source_record_allowlist_limits_impact_universe"})
    if not indexed["source_complete"]:
        issues.append({"reason": "impact_source_coverage_incomplete"})

    verified = set()
    def verify(ref):
        row = checked_row(universe, ref)
        key = canonical(ref)
        if key not in verified:
            artifact = row["record_type"] == "artifact" or "member" in ref
            read = _read_evidence(ws, ref, view="artifact" if artifact else "record", limit=1,
                                  collection=collection)
            if (read.get("availability") != "available" or read.get("missing")
                    or artifact and read.get("verification", {}).get("artifact") != "verified"):
                raise EvidenceError("impact original unavailable or identity changed")
            verified.add(key)
        return row

    roots = {record_identity(ref) for ref in changed_refs}
    selected, paths, queue = {}, {}, deque()
    for ref in changed_refs:
        verify(ref)
        key = record_identity(ref)
        if key not in selected:
            selected[key], paths[key] = ref, []
            queue.append(key)
    boundaries = {}
    while queue:
        identity = queue.popleft()
        keys = set(indexed["forward"].get(identity, []))
        keys.update(key for key in indexed["incidence"].get(identity, [])
                    if indexed["relations"][key]["kind"] in {"refutes", "supersedes"})
        for key in sorted(keys):
            relation = indexed["relations"][key]
            forward = record_identity(relation["from_ref"]) == identity
            target = relation["to_ref"] if forward else relation["from_ref"]
            target_id = record_identity(target)
            hop = {**relation, "traversal": "forward" if forward else "reverse_review"}
            if target_id not in selected and len(selected) >= max_nodes:
                boundary = {"from_ref": selected[identity], "ref": target,
                            "relation": hop, "reason": "node_bound",
                            "original_verification": "metadata_identity_only"}
                boundaries[canonical(boundary)] = boundary
                continue
            # Verify both exact envelopes even if their record was already
            # visited via another member/qualifier or another path.
            verify(relation["from_ref"]); verify(relation["to_ref"])
            if target_id not in selected:
                selected[target_id], paths[target_id] = target, paths[identity] + [hop]
                queue.append(target_id)
    affected = []
    for identity in sorted(set(selected) - roots):
        ref = selected[identity]
        row = checked_row(universe, ref)
        content = row.get("projected_content", row["record"])
        affected.append({"ref": ref, "path": paths[identity], "reason": "declared_dependency_review",
                         "task_id": content.get("task_id"), "branch_id": content.get("branch_id"),
                         "record_schema": content.get("schema")})
    result = {"schema": "etalon-research-impact/1", "read_context": read_context,
        "checkpoint_ref": checkpoint_ref, "entry_status": continuation_status(read_context),
        "task_id": checkpoint["task_id"], "changed_refs": changed_refs, "affected": affected,
        "boundaries": [boundaries[k] for k in sorted(boundaries)], "coverage": coverage,
        "source_issues": issues, "policy": policy_receipt,
        "continuation": {"review_required": resumed["review_required"],
            "authority_state": resumed["authority"]["state"],
            "issue_codes": sorted({i["reason"] for i in resumed["issues"]})},
        "declared_scope_complete": not boundaries and not issues,
        "global_scientific_coverage": "not_established", "truncated": bool(boundaries),
        "original_verification": "selected_paths_and_all_supplied_changed_envelopes",
        "verified_envelope_count": len(verified), "scientific_status": "not_inferred",
        "semantics": "declared_reference_dependents_and_bidirectional_counter_or_supersession_review; paths_are_not_causal_proofs",
        "execution_permission": "not_granted", "automatic_retry": False,
        "automatic_invalidation": False, "rsi_triggered": False}
    result["impact_digest"] = digest(result)
    if len(canonical(result).encode("utf-8")) > max_bytes:
        raise EvidenceError("impact paths and explicit boundaries exceed byte bound; choose fewer changed_refs or a smaller node bound")
    return result
