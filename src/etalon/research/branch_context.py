"""Read-only branch views over published checkpoints and ordinary evidence.

There is no branch ledger or branch activation here. Exact usage joins are
verified by resource_usage; graph relationships and applicability stay declared.
"""
from __future__ import annotations

import base64
import json

from etalon.active.schema import canonical, digest
from .entry import (continuation_status, make_read_context, resolve_entry,
                    workspace_from_context)
from .evidence import query_evidence
from .evidence_refs import EvidenceError, validate_ref
from .continuation import read_content
from .graph_memory import build_research_graph, node_identity, restrict_workspace
from .improvement import read_material, validate_content as validate_experience
from .meta_context import read_task_workset, workset_resource_part
from .read_scope import bounded_read
from .resource_usage import SCHEMA as USAGE, read_resource_usage

SCHEMA = "etalon-branch-context/1"


def _conditions(value):
    if value is not None and (not isinstance(value, dict) or not value or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            for k, v in value.items())):
        raise EvidenceError("conditions require explicit nonempty string values")


def _cursor(stamp, offset):
    return base64.urlsafe_b64encode(canonical(dict(stamp=stamp, offset=offset)).encode()).decode()


def _offset(cursor, stamp, count):
    if cursor is None:
        return 0
    try:
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(cursor).decode())
        if set(value) != {"stamp", "offset"} or value["stamp"] != stamp:
            raise ValueError()
        offset = value["offset"]
        if type(offset) is not int or not 0 <= offset <= count:
            raise ValueError()
        return offset
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise EvidenceError("invalid or stale branch cursor; branch, conditions or source view changed") from None


def _neighborhood(graph, branch):
    nodes = {n["node_id"]: n for n in graph["nodes"]}
    chosen = {nid for nid, n in nodes.items()
              if n["task_id"] == graph["task_id"] and n["branch_id"] == branch["branch_id"]}
    for ref in branch["basis_refs"] + branch["next_read_refs"]:
        if ref.get("schema") == "etalon-evidence-ref/1" and node_identity(ref) in nodes:
            chosen.add(node_identity(ref))
    changed = True
    while changed:
        previous = len(chosen)
        for edge in graph["edges"]:
            a, b, kind = edge["from"], edge["to"], edge["kind"]
            target = None
            if kind == "refutes" and (a in chosen or b in chosen):
                chosen.update((a, b))
            elif kind in {"depends_on", "tests", "supersedes"} and a in chosen:
                target = b
            elif kind in {"supports", "derived_from"} and b in chosen:
                target = a
            if target is not None:
                # Sharing a method never pulls every branch using it into scope.
                other = nodes[target]
                if other["branch_id"] in {None, branch["branch_id"]}:
                    chosen.add(target)
        changed = previous != len(chosen)
    return nodes, chosen


def _experience(workspace, node, conditions, superseded, in_branch):
    material = read_material(workspace, node["ref"])
    if not material["complete"]:
        raise EvidenceError("experience unavailable or incomplete")
    value = validate_experience(material["content"])
    unmatched = [] if conditions is None else sorted(
        k for k, v in value["conditions"].items() if conditions.get(k) != v)
    group = "excluded" if superseded or unmatched else "reference" if conditions is None else "applicable"
    return dict(item_type="experience", node_id=node["node_id"], ref=node["ref"],
        branch_id=node["branch_id"], task_id=node["task_id"],
        association="explicit_branch_neighborhood" if in_branch else "task_reference",
        category=group, applicability="declared_conditions_match_host_review_required" if group == "applicable"
        else "conditions_mismatch" if unmatched else "superseded" if superseded else "conditions_not_checked",
        unmatched_conditions=unmatched, superseded=superseded, experience=value,
        invalid_when_check="host_review_required; free text is not executed",
        scientific_applicability="not_verified", automatic_adoption=False)


def _workset_parts(active, conditions):
    if active["status"] != "active":
        return {**active, "scope": "task_wide", "branch_activation": "not_inferred"}, []
    workset = active["workset"]
    changed = sorted(k for k, v in (conditions or {}).items()
                     if workset["profile"]["conditions"].get(k) != v)
    summary = dict(status="review_required" if changed else "active", task_id=workset["task_id"],
        scope="task_wide", branch_activation="not_inferred", ref=active["ref"],
        activation_ref=active["activation_ref"], digest=workset["digest"], mode=workset["mode"],
        fallback_reason=workset["fallback_reason"], template=workset["template"],
        changed_conditions=changed, mutation_performed=False)
    parts = []
    for resource in workset["index"]["resources"]:
        for part in resource["parts"]:
            item = workset_resource_part(workset, resource["resource_id"], part["part_id"])
            candidate = next(c for c in workset["review"]["candidates"]
                             if (c["resource_id"], c["part_id"]) == (resource["resource_id"], part["part_id"]))
            parts.append(dict(item_type="resource_part", workset_ref=active["ref"],
                resource_id=resource["resource_id"], part_id=part["part_id"], kind=resource["kind"],
                content_ref=resource["content_ref"], resource_digest=resource["digest"], part=part,
                selected=item["selected"], selection_scope="task_wide_not_branch_activation",
                decision=item["decision"], declared_applicability=candidate["applicability"],
                branch_conditions_review_required=bool(changed),
                historical_phases={phase: "unknown" for phase in ("read", "consumed", "executed")},
                branch_usage_refs=[]))
    return summary, parts


@bounded_read
def build_branch_context(entry, *, branch_id, conditions=None, purpose="research", policy=None,
                         max_bytes=32768, max_nodes=128, limit=32, cursor=None,
                         usage_refs=None, as_of=None):
    """Select one exact current-checkpoint branch; paginate a bounded projection.

    usage_refs may supplement the first 50 discovered workspace usage Artifacts.
    Discovery pagination is reported separately and can be followed with the
    ordinary evidence query, then supplied as exact usage_refs. An explicit
    cross-task usage is rejected. Missing/forged usage evidence is never empty.
    limit bounds delivery rows; max_nodes bounds the underlying graph traversal.
    Neither bound promises a global I/O or scientific coverage bound.
    """
    if not isinstance(branch_id, str) or not branch_id or len(branch_id) > 4096:
        raise EvidenceError("exact branch_id required")
    if type(max_bytes) is not int or not 4096 <= max_bytes <= 65536:
        raise EvidenceError("branch max_bytes must be 4096..65536")
    if type(limit) is not int or not 1 <= limit <= 64:
        raise EvidenceError("branch limit must be 1..64")
    _conditions(conditions)
    usage_refs = [] if usage_refs is None else usage_refs
    if not isinstance(usage_refs, list) or len(usage_refs) > 50:
        raise EvidenceError("usage_refs must be a list of at most 50 exact refs")
    for ref in usage_refs:
        validate_ref(ref)
    workspace, checkpoint, entry_path, pinned = resolve_entry(entry)
    if pinned is not None:
        if policy is not None and policy != pinned["policy"]:
            raise EvidenceError("pinned context policy cannot change")
        policy, purpose = pinned["policy"], pinned["purpose"]
    read_context = pinned or make_read_context(workspace, checkpoint, entry_path=entry_path,
                                             policy=policy, purpose=purpose)
    if read_context is not None:
        workspace = workspace_from_context(read_context, apply_policy=False)
    graph = build_research_graph(workspace, checkpoint_ref=checkpoint, policy=policy, purpose=purpose,
                                 max_nodes=max_nodes, max_bytes=262144, as_of=as_of)
    branch = next((b for b in graph["branches"] if b["branch_id"] == branch_id), None)
    if branch is None:
        raise EvidenceError("branch_id is not present in the selected checkpoint")
    visible = restrict_workspace(workspace, policy=policy, purpose=purpose)[0]
    checkpoint_content = read_content(visible, checkpoint)
    checkpoint_steps = {node_identity(r) for r in checkpoint_content["step_refs"]}
    nodes, chosen = _neighborhood(graph, branch)
    active = read_task_workset(visible, checkpoint)
    workset, parts = _workset_parts(active, conditions)
    # Usage Artifacts intentionally are not graph content projections. Their
    # task_id lives in verified payload/bindings, so task_ids query filtering
    # would silently return no records. Discover within the permitted workspace
    # and check task identity after the existing usage verifier.
    page = query_evidence(visible, query=USAGE, filters={"record_types": ["artifact"]}, limit=50)
    discovered = {canonical(item["ref"]): item["ref"] for item in page["items"]}
    discovered.update({canonical(ref): ref for ref in usage_refs})
    explicit = {canonical(ref) for ref in usage_refs}
    usages, unassigned, cross_task, incidental, usage_issues = [], 0, 0, 0, []
    for ref in discovered.values():
        material = read_material(visible, ref)
        if material["record"].get("bindings", {}).get("schema") != USAGE and canonical(ref) not in explicit:
            # A full-text query may match references to this schema in another
            # Artifact; that is not a usage record or a verified association.
            incidental += 1
            continue
        payload = material["content"]
        if isinstance(payload, dict) and payload.get("schema") == USAGE and payload.get("task_id") != graph["task_id"]:
            if canonical(ref) in explicit:
                raise EvidenceError("resource usage belongs to another task; no cross-task branch attribution")
            cross_task += 1
            continue
        try:
            checked = read_resource_usage(visible, ref)
        except (EvidenceError, OSError) as exc:
            if canonical(ref) in explicit:
                raise
            # Historical failed declarations remain visible as coverage gaps;
            # they do not block reading unrelated valid branch evidence and are
            # never promoted to verified associations.
            usage_issues.append(dict(ref=ref, code="resource_usage_unverified", message=str(exc)[:500]))
            continue
        payload = checked["payload"]
        anchors = []
        for key in ("node_ref", "evaluation_ref", "selection_ref"):
            target = payload[key]
            if target is None:
                continue
            # Read the exact validated chain anchor, even if graph was truncated.
            value = read_material(visible, target)["content"]
            if value["task_id"] == graph["task_id"] and value["branch_id"] == branch_id:
                anchors.append(dict(relation=key, ref=target, node_id=node_identity(target)))
        if not anchors:
            unassigned += 1
            continue
        usages.append(dict(item_type="resource_usage", node_id=node_identity(ref), **checked,
            branch_id=branch_id, branch_associations=anchors,
            association_status="exact_chain_verified_not_consumption_or_benefit"))
        for part in parts:
            if (part["workset_ref"], part["resource_id"], part["part_id"]) == (
                    payload["workset_ref"], payload["resource_id"], payload["part_id"]):
                part["branch_usage_refs"].append(ref)
    superseded = {e["to"] for e in graph["edges"] if e["kind"] == "supersedes"}
    experiences = [_experience(visible, n, conditions, nid in superseded, nid in chosen)
                   for nid, n in nodes.items() if "experience" in n and
                   (nid in chosen or n["task_id"] == graph["task_id"])]
    ordinary = []
    for nid in sorted(chosen):
        n = nodes[nid]
        if "experience" in n:
            continue
        row = dict(item_type="decision" if "decision" in n else "node", **n)
        if "decision" in n:
            row["decision_status"] = "superseded" if nid in superseded else "unsuperseded_in_bounded_view"
            row["checkpoint_membership"] = "listed" if nid in checkpoint_steps else "not_listed"
        ordinary.append(row)
    candidates = sorted(usages, key=lambda x: x["node_id"]) + sorted(experiences, key=lambda x: x["node_id"]) + parts + ordinary
    # Include counter direction and attribution declarations as independently
    # paged rows so relation size cannot silently remove branch control metadata.
    relations = [dict(item_type="relation", **e, verification="declared_relationship_not_usage_validation")
                 for e in graph["edges"] if e["from"] in chosen and e["to"] in chosen]
    candidates += relations
    source_complete = not graph["issues"] and not page["warnings"] and not usage_issues and all(
        c["status"] == "complete" for c in graph["coverage"] + page["coverage"])
    result = dict(schema=SCHEMA, task_id=graph["task_id"], branch=branch, checkpoint_ref=checkpoint,
        current_decision_refs=[row["ref"] for row in ordinary if row["item_type"] == "decision"
            and row["checkpoint_membership"] == "listed" and row["node_id"] not in superseded
            and row["branch_id"] == branch_id and row["task_id"] == graph["task_id"]],
        decision_order="explicit_supersedes_and_checkpoint_membership_not_hash_or_timestamp_order",
        read_context=read_context, entry_status=continuation_status(read_context), purpose=purpose,
        conditions=conditions, task_workset=workset, continuation=graph["continuation"],
        temporal=graph["temporal"], policy=graph["policy"], items=[], relations=[], next_cursor=None,
        source_watermarks=graph["coverage"], source_issues=graph["issues"] + usage_issues,
        coverage=dict(source_complete=source_complete, graph_truncated=graph["truncated"],
            graph_view_id=graph["view_id"], graph_node_limit=max_nodes,
            counter_context_complete=graph["counter_context_complete"],
            usage_discovery=dict(limit=50, returned_count=len(page["items"]), warnings=page["warnings"],
                next_cursor=page["next_cursor"], explicit_ref_count=len(usage_refs),
                unassigned_to_selected_branch_count=unassigned,
                cross_task_excluded_count=cross_task, incidental_schema_match_count=incidental,
                unverified_count=len(usage_issues),
                cross_task_scope_basis="verified_payload_task_id; no association_validation_implied",
                followup=dict(api="query_evidence", query=USAGE,
                    filters={"record_types": ["artifact"]},
                    usage="pass exact refs as usage_refs; keep the pinned read_context as entry")),
            complete=source_complete and not graph["truncated"] and page["next_cursor"] is None),
        selection=dict(candidate_count=len(candidates), selected_count=0, offset=0, detail_omissions=0,
                       scope="exact branch plus explicit dependencies, current task experiences and task-wide workset"),
        unknowns=["Selection and verified record joins do not prove historical reading, consumption or execution.",
            "Experience condition matching is declared applicability; invalid_when and counterevidence need host review.",
            "Unrecorded usage remains unknown, including successful legacy runs.",
            "Graph and query bounds do not establish global scientific completeness."],
        truncated=False, execution_permission="not_granted", automatic_activation=False,
        automatic_retry=False, content_role="sourced_research_data_not_host_instructions")
    stamp = digest(dict(base=result, candidates=candidates))
    start = _offset(cursor, stamp, len(candidates))
    result["selection"]["offset"] = start
    # Reserve cursor, final digest and counting metadata. Mandatory metadata is
    # never discarded to make an apparently complete response fit.
    if len(canonical(result).encode()) > max_bytes - 768:
        raise EvidenceError("branch control metadata exceeds max_bytes; increase the bound")
    end = start
    for candidate in candidates[start:start + limit]:
        key = "relations" if candidate["item_type"] == "relation" else "items"
        result[key].append(candidate)
        if len(canonical(result).encode()) > max_bytes - 768:
            result[key].pop()
            if end > start:
                break
            # A single large exact object has a navigable omission receipt,
            # never a partial applicability/decision presented as complete.
            stub = {k: candidate[k] for k in ("item_type", "node_id", "ref", "workset_ref",
                    "resource_id", "part_id", "from", "to", "basis_ref") if k in candidate}
            stub.update(detail_omitted_due_bound=True, followup="increase max_bytes or read the exact evidence ref")
            result[key].append(stub)
            result["selection"]["detail_omissions"] += 1
            if len(canonical(result).encode()) > max_bytes - 768:
                raise EvidenceError("branch item identity exceeds max_bytes; increase the bound")
        end += 1
    result["selection"]["selected_count"] = end - start
    result["selection"]["remaining_count"] = len(candidates) - end
    result["next_cursor"] = _cursor(stamp, end) if end < len(candidates) else None
    result["truncated"] = (not result["coverage"]["complete"] or start > 0 or end < len(candidates)
                           or bool(result["selection"]["detail_omissions"]))
    result["context_id"] = "branch-context-" + digest(result)
    if len(canonical(result).encode()) > max_bytes:
        raise EvidenceError("branch receipt exceeds max_bytes")
    return result
