"""Read-only research graph projections over existing Journal records.

The graph is an index/view, not a new ledger, execution engine or permission
system. A published workspace remains the outer authority boundary. Policy can
narrow it before content projection; it cannot grant access to unpublished data.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3

from etalon.active.schema import canonical, digest
from .continuation import CHECKPOINT, STEP, _exact_refs, resume_research
from .evidence import _collect, _read_evidence
from .evidence_refs import (EvidenceError, EvidenceWorkspace, _local, file_identity,
                            load_workspace, make_ref, validate_ref)

GRAPH = "etalon-research-graph/1"
HOMES = {"Search", "Judge", "Quick", "Deep"}
EDGES = {"flow", "depends_on", "derived_from", "supports", "refutes", "tests",
         "supersedes", "skipped_because"}


def node_identity(ref):
    """One immutable record identity, independently of the referring workspace."""
    ref = validate_ref(ref)
    return "research-node-" + digest({k: ref[k] for k in
        ("source_id", "record_id", "record_digest", "member") if k in ref})[:24]


def validate_research_graph(value):
    """Check a generated/stored projection, not its scientific conclusions."""
    if not isinstance(value, dict) or value.get("schema") != GRAPH:
        raise EvidenceError("unsupported research graph")
    validate_ref(value["checkpoint_ref"])
    ids = set()
    for node in value["nodes"]:
        nid = node_identity(node["ref"])
        if nid != node["node_id"] or nid in ids or node["home"] not in HOMES or node["kind"] not in {"E", "J", "X", "R"}:
            raise EvidenceError("invalid research node identity, home or kind")
        ids.add(nid)
        if node["scope"] == "task" and node["task_id"] != value["task_id"]:
            raise EvidenceError("cross-task node cannot claim task scope")
    for edge in value["edges"]:
        if edge["from"] not in ids or edge["to"] not in ids or edge["kind"] not in EDGES:
            raise EvidenceError("graph relationship has unknown endpoint or kind")
        validate_ref(edge["basis_ref"])
    if value.get("execution_permission") != "not_granted" or value.get("automatic_retry") is not False:
        raise EvidenceError("a research projection cannot grant execution")
    if "view_id" in value and value["view_id"] != "research-graph-" + digest({k: v for k, v in value.items() if k != "view_id"}):
        raise EvidenceError("research graph projection digest mismatch")
    return json.loads(canonical(value))


def _ids(ws, source):
    """Metadata-only ID enumeration to apply deny rules before projection."""
    if "record_ids" in source:
        return set(source["record_ids"])
    path = _local(ws.base, source["location"])
    if source["kind"] == "journal":
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            return {key[9:] if key.startswith("research:") else key for (key,) in
                    db.execute("SELECT key FROM metadata WHERE key LIKE 'research:%' OR key LIKE 'budget:execution:%'")}
        finally:
            db.close()
    if path.stat().st_size > 32 * 1024 * 1024:
        raise EvidenceError("source exceeds policy metadata bound")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if source["kind"] != "artifact_manifest":
        raise EvidenceError("record restrictions on data snapshots require explicit record_ids")
    return {r["record_id"] for r in data.get("records", [])}


def restrict_workspace(workspace, *, policy=None, purpose="research"):
    """Narrow published sources/records before any summary or artifact projection.

    supported policy: source_ids; record_ids {source:[ids]}; source_purposes
    {source:[purposes]}; record_rules [{source_id, record_id, allowed_purposes,
    seen_by?}]. seen_by is descriptive and NEVER grants permission.
    """
    ws = load_workspace(workspace)
    if not isinstance(purpose, str) or not purpose:
        raise EvidenceError("purpose must be explicit text")
    policy = {} if policy is None else policy
    if not isinstance(policy, dict) or set(policy) - {"source_ids", "record_ids", "source_purposes", "record_rules", "version"}:
        raise EvidenceError("unsupported research context policy")
    known = {s["source_id"] for s in ws.manifest["sources"]}
    selected = policy.get("source_ids", sorted(known))
    if not isinstance(selected, list) or any(not isinstance(s, str) for s in selected) or set(selected) - known:
        raise EvidenceError("policy source_ids must name published sources")
    allow = policy.get("record_ids", {})
    purposes = policy.get("source_purposes", {})
    for mapping in (allow, purposes):
        if not isinstance(mapping, dict) or set(mapping) - known or any(
                not isinstance(v, list) or any(not isinstance(x, str) for x in v) for v in mapping.values()):
            raise EvidenceError("policy allowlists must name published sources and text values")
    rules = policy.get("record_rules", [])
    if not isinstance(rules, list) or len(rules) > 1024:
        raise EvidenceError("record_rules must be bounded")
    denied = {}
    rule_ids = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"source_id", "record_id", "allowed_purposes", "seen_by"}:
            raise EvidenceError("unsupported record rule")
        sid, rid, permitted = rule.get("source_id"), rule.get("record_id"), rule.get("allowed_purposes")
        if sid not in known or not isinstance(rid, str) or not rid or not isinstance(permitted, list) or any(not isinstance(p, str) for p in permitted):
            raise EvidenceError("record rule requires identity and allowed purposes")
        if (sid, rid) in rule_ids:
            raise EvidenceError("duplicate record policy rule")
        rule_ids.add((sid, rid))
        if purpose not in permitted:
            denied.setdefault(sid, set()).add(rid)
    manifest = ws.manifest
    sources = []
    for source in manifest["sources"]:
        sid = source["source_id"]
        if sid not in selected or (sid in purposes and purpose not in purposes[sid]):
            continue
        source = dict(source)
        if sid in allow or sid in denied:
            permitted = _ids(ws, source)
            if sid in allow:
                permitted &= set(allow[sid])
            permitted -= denied.get(sid, set())
            source["record_ids"] = sorted(permitted)
            source["content_projections"] = [p for p in source.get("content_projections", []) if p["record_id"] in permitted]
        sources.append(source)
    manifest["sources"] = sources
    kept = {s["source_id"] for s in sources}
    manifest["location_map"] = [m for m in manifest.get("location_map", []) if m["source_id"] in kept]
    narrowed = load_workspace(EvidenceWorkspace(ws.base, canonical(manifest)))
    return narrowed, {"version": policy.get("version", "published-workspace/1"),
                      "digest": digest(policy), "purpose": purpose,
                      "boundary": "explicit_published_workspace_narrowed_before_projection",
                      "seen_is_permission": False,
                      "excluded_source_count": len(known - kept),
                      "restricted_record_rule_count": sum(map(len, denied.values()))}


def _historical(ws, as_of):
    if as_of is None:
        return {"mode": "current_published_view", "historical_decision_replay": False}
    if not isinstance(as_of, dict) or set(as_of) != {"source_snapshots"} or not isinstance(as_of["source_snapshots"], dict):
        raise EvidenceError("as_of requires exact source_snapshots; timestamps alone cannot reconstruct history")
    snapshots = {s["source_id"]: s["identity"].get("value") for s in ws.manifest["sources"]}
    if any(s["identity"]["kind"] != "snapshot_sha256" for s in ws.manifest["sources"]) or snapshots != as_of["source_snapshots"]:
        raise EvidenceError("as_of requires a matching fully frozen published workspace")
    return {"mode": "explicit_frozen_snapshot", "source_snapshots": snapshots,
            "historical_decision_replay": False,
            "limitation": "snapshot is not proof of what a previous host knew"}


def _brief(value, limit=700):
    text = value if isinstance(value, str) else canonical(value)
    return {"text": text[:limit], "truncated": len(text) > limit}


def _project_node(ref, row, *, task_id, home_overrides):
    content = row.get("projected_content", row["record"])
    operation, detail = content.get("operation"), content.get("detail", {})
    schema = content.get("schema", "")
    claim = schema == "etalon-research-claim/1"
    experience = schema == "etalon-method-experience/1"
    trace = schema == "etalon-research-trace/1"
    home = "Judge" if claim or operation in {"judgment", "decision", "evaluation", "evaluation_plan"} else "Quick" if operation in {"plan", "node", "result"} or row["record_type"] in {"experiment", "observation"} else "Search"
    if experience or schema in {"etalon-method-contract/1", "etalon-method-comparability/1"}:
        home = "Judge"
    elif schema == "etalon-screen-compilation/1":
        home = "Quick"
    elif trace:
        home = content["workspace"]
    override = home_overrides.get(node_identity(ref), home_overrides.get(detail.get("node_id")))
    if override is not None:
        home = override
    # Work area is not epistemic type. Moving an observation into Judge must
    # not turn a measured result into an interpretation (or vice versa).
    judgment = claim or experience or operation in {"judgment", "decision", "evaluation", "evaluation_plan"} or schema in {
        "etalon-method-contract/1", "etalon-method-comparability/1"}
    kind = "R" if operation == "result" or row["record_type"] == "observation" else "J" if judgment else "X" if operation in {"node", "plan"} or row["record_type"] == "experiment" else "E"
    if trace:
        kind = "R" if content["phase"] in {"execution", "admission"} else "X"
    elif schema == "etalon-screen-compilation/1":
        kind = "X"
    summary_text = next((content[k] for k in ("statement", "lesson", "reason", "question", "name") if k in content), row["record_type"])
    node = {"node_id": node_identity(ref), "ref": ref, "home": home, "kind": kind,
        "home_basis": "explicit_projection_override" if override else "explicit_trace_workspace" if trace else "legacy_record_projection",
        "kind_basis": "explicit_trace_phase" if trace else "legacy_record_semantics_inferred",
        "task_id": content.get("task_id"), "branch_id": content.get("branch_id"),
        "logical_node_id": content.get("node_id", detail.get("node_id")), "operation": operation,
        "state": detail.get("state", content.get("status", content.get("state", "recorded"))),
        "content_type": "method_experience" if experience else "trace." + content["phase"] if trace else
                        content.get("epistemic_status", operation or schema or row["record_type"]),
        "evidence_status": "identity_verified_not_scientific_truth",
        "scope": "task" if content.get("task_id") == task_id else "explicit_shared_reference",
        "summary": _brief(summary_text),
        "conditions": _brief(content.get("conditions", [])),
        "limitations": _brief(content.get("limitations", []))}
    if operation == "decision":
        node["decision"] = {"choice": detail["choice"], "alternatives": detail["alternatives"],
                            "reconsider_when": detail["reconsider_when"], "basis_refs": detail["basis_refs"]}
    if claim:
        node["claim_scope"] = {"subjects": content["subjects"], "conditions": content["conditions"], "limitations": content["limitations"]}
    if experience:
        node["experience"] = {key: content[key] for key in ("outcome", "invalid_when", "capability_id", "conditions", "scope",
                                                            "support_refs", "counter_refs", "supersedes")}
        node["experience"]["lesson"] = _brief(content["lesson"], 1200)
    if trace:
        node["trace"] = {key: content[key] for key in ("phase", "origin", "sequence", "attempt_id", "state")}
    if schema == "etalon-method-use/1":
        node["method_use"] = {key: content[key] for key in (
            "selected_methods", "subject_refs", "basis", "reason", "host_conditions")}
        node["method_use"]["meaning"] = "attribution_only; actual execution requires linked node receipts"
    if schema == "etalon-method-evaluation/1":
        node["evaluation"] = {key: content[key] for key in (
            "subjects", "comparators", "assessment_role", "conclusion", "scope")}
    if operation == "node":
        node["usage"] = {key: detail[key] for key in ("use_refs", "receipt_refs", "workflow_ref", "capability_id")}
        node["usage"]["input_refs"] = content["input_refs"]
        node["usage"]["output_refs"] = content["output_refs"]
        node["usage"]["attribution_status"] = "linked" if detail["use_refs"] else "not_recorded"
    return node


def build_research_graph(workspace, *, checkpoint_ref, policy=None, purpose="research",
                         home_overrides=None, max_nodes=64, max_bytes=65536, as_of=None):
    """Project reachable records without changing a journal or executing work.

    Legacy nodes default to Quick when resource mode was never recorded. Deep
    requires an explicit projection override; component identity is not a cost
    classifier. No historical record is rewritten to fit the new view.
    """
    if type(max_nodes) is not int or not 1 <= max_nodes <= 128 or type(max_bytes) is not int or not 4096 <= max_bytes <= 262144:
        raise EvidenceError("graph bounds must be 1..128 nodes and 4096..262144 bytes")
    home_overrides = {} if home_overrides is None else home_overrides
    if not isinstance(home_overrides, dict) or any(not isinstance(k, str) or v not in HOMES for k, v in home_overrides.items()):
        raise EvidenceError("home overrides require node identities and Search/Judge/Quick/Deep")
    ws, policy_receipt = restrict_workspace(workspace, policy=policy, purpose=purpose)
    temporal = _historical(ws, as_of)
    checkpoint_ref = validate_ref(checkpoint_ref)
    if checkpoint_ref["source_id"] not in {s["source_id"] for s in ws.manifest["sources"]}:
        raise EvidenceError("checkpoint is outside permitted scope")
    records, coverage, warnings = _collect(ws)
    checkpoint_row = records.get(checkpoint_ref["source_id"], {}).get(checkpoint_ref["record_id"])
    if not checkpoint_row or digest(checkpoint_row["record"]) != checkpoint_ref["record_digest"]:
        raise EvidenceError("checkpoint unavailable, restricted or identity changed")
    checkpoint = checkpoint_row.get("projected_content", {})
    if checkpoint.get("schema") != CHECKPOINT:
        raise EvidenceError("verified checkpoint projection required")
    # Existing continuation checks authority bytes, pending handles and task
    # successors. Its potentially path-bearing details are not injected below.
    resumed = resume_research(ws, checkpoint_ref, read_mode="frozen_view")
    task_id = checkpoint["task_id"]
    universe = {(sid, rid): row for sid, rows in records.items() for rid, row in rows.items()}
    from .method_links import method_links
    attribution = {key: method_links(row.get("projected_content", row["record"]), key[0], universe)
                   for key, row in universe.items()}
    selected, queue, issues = {}, [], []
    counter_ids, counter_context_ids = set(), set()
    def enqueue(ref):
        validate_ref(ref)
        key = node_identity(ref)
        if key not in selected and all(node_identity(r) != key for r in queue):
            queue.append(ref)
    # Explicit counterevidence is prioritized before the node bound.
    for ref in checkpoint["counter_refs"]:
        counter_ids.add(node_identity(ref)); counter_context_ids.add(node_identity(ref)); enqueue(ref)
    # Preserve the newest declared checkpoint progress before older steps can
    # consume the bounded projection. This is list order, not an execution
    # chronology inference; explicit counters still take priority.
    for ref in checkpoint["step_refs"][-1:]:
        enqueue(ref)
    for ref in checkpoint["claim_refs"] + checkpoint["step_refs"] + checkpoint["method_refs"]:
        enqueue(ref)
    for branch in checkpoint["branches"]:
        for ref in branch["basis_refs"] + branch["next_read_refs"]:
            enqueue(ref)
    # Match current task steps only. Cross-task records enter through explicit
    # relationships, never by a text similarity heuristic.
    for (sid, rid), row in universe.items():
        content = row.get("projected_content", row["record"])
        if content.get("schema") in {STEP, "etalon-method-experience/1", "etalon-research-trace/1"} and content.get("task_id") == task_id:
            enqueue(make_ref(sid, rid, row["record"]))
    edges = []
    def edge(a, b, kind, basis, relation=None):
        value = {"from": a, "to": b, "kind": kind, "basis_ref": basis}
        if relation is not None:
            value["relation"] = relation
        if kind == "refutes":
            # Both ends are required context. Only the `from` end is declared
            # counterevidence; following a reverse link never reverses its claim.
            counter_context_ids.update((a, b))
        if value not in edges:
            edges.append(value)
    while queue and len(selected) < max_nodes:
        ref = queue.pop(0)
        nid = node_identity(ref)
        row = universe.get((ref["source_id"], ref["record_id"]))
        if row and digest(row["record"]) != ref["record_digest"]:
            raise EvidenceError("referenced record identity changed")
        if not row:
            issues.append({"reason": "referenced_record_unavailable_or_changed"})
            continue
        if row.get("projection", {}).get("status") == "unavailable":
            issues.append({"reason": "referenced_content_unavailable"}); continue
        # Verify actual bytes even for ordinary raw artifacts. No scientific
        # interpretation is inferred from a successful read.
        read = _read_evidence(ws, ref, view="artifact" if row["record_type"] == "artifact" else "record", limit=512,
                              collection=(records, coverage, warnings))
        if read.get("availability") != "available" or (row["record_type"] == "artifact" and read.get("verification", {}).get("artifact") != "verified"):
            raise EvidenceError("referenced artifact unavailable or identity changed")
        selected[nid] = _project_node(ref, row, task_id=task_id, home_overrides=home_overrides)
        if row["record_type"] == "artifact" and "projected_content" not in row:
            fragment = read.get("fragment", {})
            try:
                excerpt = base64.b64decode(fragment.get("content", "")).decode("utf-8")
                if excerpt and all(c.isprintable() or c in "\r\n\t" for c in excerpt):
                    selected[nid]["summary"] = {"text": excerpt, "truncated": fragment.get("next_offset") is not None}
            except (ValueError, UnicodeDecodeError):
                pass  # Binary artifacts remain exact references, not guessed text.
        content = row.get("projected_content", row["record"])
        detail = content.get("detail", {})
        for target, relation in attribution[(ref["source_id"], ref["record_id"])]:
            if target is None:
                issues.append({"reason": "method_link_unavailable_in_source", "node_id": nid, "relation": relation})
            else:
                enqueue(target)
                edge(nid, node_identity(target), "depends_on", ref, relation)
        for key, kind in (("input_refs", "depends_on"), ("output_refs", "derived_from"),
                          ("support_refs", "supports"), ("counter_refs", "refutes"), ("supersedes", "supersedes")):
            for target in content.get(key, []):
                enqueue(target)
                tid = node_identity(target)
                if key in {"support_refs", "counter_refs", "output_refs"}:
                    edge(tid, nid, kind, ref)
                else:
                    edge(nid, tid, kind, ref)
                if key == "counter_refs":
                    counter_ids.add(tid)
        for target in detail.get("hypothesis_refs", []):
            enqueue(target); edge(nid, node_identity(target), "tests", ref)
        for target in _exact_refs(content):
            enqueue(target)
        if content.get("operation") == "decision":
            for target in detail["basis_refs"]:
                edge(nid, node_identity(target), "depends_on", ref)
        # Include cross-home/cross-task explicit counterclaims of this record.
        # This bounded universe is the authorized workspace, not a global search.
        for (sid, rid), candidate in universe.items():
            payload = candidate.get("projected_content", candidate["record"])
            if any(node_identity(r) == nid for r in payload.get("counter_refs", []) if isinstance(r, dict)):
                other = make_ref(sid, rid, candidate["record"])
                enqueue(other)
                edge(nid, node_identity(other), "refutes", other)
    # Workflow template edges are attached only to instances with the same exact
    # workflow ref. Do not infer chronological order from artifact hashes.
    workflow_nodes = {}
    for nid, node in selected.items():
        row = universe[(node["ref"]["source_id"], node["ref"]["record_id"])]
        payload = row.get("projected_content", {})
        detail = payload.get("detail", {})
        if detail.get("workflow_ref") and detail.get("node_id"):
            workflow_nodes.setdefault(node_identity(detail["workflow_ref"]), {}).setdefault(detail["node_id"], []).append(nid)
    for wid, instances in workflow_nodes.items():
        if wid not in selected:
            continue
        ref = selected[wid]["ref"]
        template = universe[(ref["source_id"], ref["record_id"])].get("projected_content", {})
        for link in template.get("edges", []):
            for a in instances.get(link["from"], []):
                for b in instances.get(link["to"], []):
                    edge(a, b, "flow" if link["kind"] == "control" else "depends_on", ref)
    def safe_metadata(value):
        if isinstance(value, dict):
            if value.get("schema") == "etalon-evidence-ref/1":
                row = universe.get((value.get("source_id"), value.get("record_id")))
                if row is None or digest(row["record"]) != value.get("record_digest"):
                    return {"unavailable_reference": True}
            return {k: safe_metadata(v) for k, v in value.items()}
        if isinstance(value, list):
            return [safe_metadata(v) for v in value]
        return value
    result = {"schema": GRAPH, "workspace_id": ws.manifest["workspace_id"], "checkpoint_ref": checkpoint_ref,
        "task_id": task_id, "question": checkpoint["question"], "nodes": safe_metadata(list(selected.values())),
        "edges": [e for e in edges if e["from"] in selected and e["to"] in selected],
        "branches": safe_metadata(checkpoint["branches"]),
        "checkpoint_progress": {"step_count": len(checkpoint["step_refs"]),
            "tail_step_refs": safe_metadata(checkpoint["step_refs"][-1:]),
            "order_semantics": "checkpoint_list_order_not_execution_order"},
        "counter_node_ids": sorted(counter_context_ids & selected.keys()),
        "counter_evidence_node_ids": sorted(counter_ids & selected.keys()),
        "counter_context_semantics": "both_endpoints_of_declared_counter_relations; refutes_edge_direction_is_authoritative",
        "counter_context_complete": counter_context_ids <= selected.keys(),
        "coverage": [{k: c[k] for k in ("source_id", "status", "snapshot", "read_watermark", "visible_records") if k in c} for c in coverage],
        "policy": policy_receipt, "temporal": temporal,
        "continuation": {"authority_state": resumed["authority"]["state"], "review_required": resumed["review_required"],
            "pending": [{"request_id": p["handle"]["request_id"], "execution_state": p["execution_state"]} for p in resumed["pending"]],
            "issue_codes": sorted({i["reason"] for i in resumed["issues"]})},
        "issues": issues, "truncated": bool(queue), "execution_permission": "not_granted", "automatic_retry": False,
        "snapshot_consistency": "per_source_reads_not_global_atomic_snapshot"}
    while len(canonical(result).encode()) > max_bytes - 128 and result["nodes"]:
        removed = result["nodes"].pop()["node_id"]
        result["edges"] = [e for e in result["edges"] if removed not in (e["from"], e["to"])]
        result["counter_node_ids"] = [n for n in result["counter_node_ids"] if n != removed]
        result["counter_evidence_node_ids"] = [n for n in result["counter_evidence_node_ids"] if n != removed]
        if removed in counter_context_ids:
            result["counter_context_complete"] = False
        result["truncated"] = True
    if len(canonical(result).encode()) > max_bytes:
        raise EvidenceError("checkpoint metadata exceeds graph byte bound; use a smaller published checkpoint")
    result["view_id"] = "research-graph-" + digest(result)
    if len(canonical(result).encode()) > max_bytes:
        raise EvidenceError("graph identity exceeds byte bound")
    return validate_research_graph(result)
