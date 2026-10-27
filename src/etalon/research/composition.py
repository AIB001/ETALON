"""Optional workflow descriptions, node memory and comparison entry points.

No scheduler, automatic optimization, default-method mutation or new ledger.
MethodUse still attributes task/request/action/result; nodes refer to that use.
"""
from __future__ import annotations

import json

from etalon.active.schema import canonical
from .evidence_refs import EvidenceError, validate_ref

WORKFLOW = "etalon-research-workflow/1"
TARGETS = {"method", "branch_selection", "workflow", "workflow_composition"}
NODE_STATES = {"planned", "completed", "reused", "skipped", "failed", "waiting", "partial"}


def validate_workflow(payload):
    from .continuation import _fields, _list, _refs, _text
    _fields(payload, ("schema", "name", "scope", "nodes", "edges", "method_refs", "supersedes"))
    for key in ("name", "scope"):
        _text(payload[key])
    _refs(payload["method_refs"])
    _refs(payload["supersedes"])
    ids = set()
    for node in _list(payload["nodes"], nonempty=True):
        _fields(node, ("node_id", "capability_id", "method_refs", "views", "purpose", "skip_when"))
        nid = _text(node["node_id"])
        if nid in ids:
            raise EvidenceError("duplicate workflow node")
        ids.add(nid)
        if node["capability_id"] is not None:
            _text(node["capability_id"])
        _refs(node["method_refs"])
        _text(node["purpose"])
        _text(node["skip_when"])
        if not _list(node["views"], nonempty=True) or not set(node["views"]) <= {"E", "J", "X", "R"}:
            raise EvidenceError("workflow views must be E, J, X or R")
    seen = set()
    for edge in _list(payload["edges"]):
        _fields(edge, ("from", "to", "kind", "condition"))
        if edge["from"] not in ids or edge["to"] not in ids:
            raise EvidenceError("workflow edge must name existing nodes")
        if edge["kind"] not in {"data", "support", "control"}:
            raise EvidenceError("unknown workflow relationship")
        _text(edge["condition"])
        if canonical(edge) in seen:
            raise EvidenceError("duplicate workflow edge")
        seen.add(canonical(edge))
    return json.loads(canonical(payload))


def validate_step_extension(payload):
    from .continuation import _fields, _handle, _list, _refs, _text
    detail = payload["detail"]
    if payload["operation"] == "node":
        _fields(detail, ("node_id", "workflow_ref", "capability_id", "state", "use_refs",
                         "receipt_refs", "identities", "action_handles"))
        _text(detail["node_id"])
        if detail["workflow_ref"] is not None:
            validate_ref(detail["workflow_ref"])
        if detail["capability_id"] is not None:
            _text(detail["capability_id"])
        _refs(detail["use_refs"])
        _refs(detail["receipt_refs"])
        if detail["state"] not in NODE_STATES:
            raise EvidenceError("unknown node state")
        if detail["capability_id"] == "prism.general_fep_lab" and detail["state"] not in {"planned", "skipped"}:
            raise EvidenceError("not-connected capability can only be proposed or skipped")
        for handle in _list(detail["action_handles"]):
            _handle(handle)
        for identity in _list(detail["identities"]):
            _fields(identity, ("kind", "value", "basis_ref"))
            if identity["kind"] not in {"source", "candidate", "molecule", "chemical_state", "pose",
                                         "prepared_system", "attempt", "observation", "protocol"}:
                raise EvidenceError("unknown scientific identity kind")
            _text(identity["value"])
            validate_ref(identity["basis_ref"])
        state = detail["state"]
        if state in {"planned", "skipped"} and (payload["output_refs"] or detail["receipt_refs"] or detail["action_handles"]):
            raise EvidenceError("unexecuted node cannot manufacture outputs, receipts or actions")
        if state in {"completed", "reused"} and (not payload["output_refs"] or not detail["receipt_refs"]):
            raise EvidenceError("completed/reused node requires existing outputs and receipts")
        if state in {"failed", "waiting", "partial"} and not detail["receipt_refs"] and not detail["action_handles"]:
            raise EvidenceError("failed/waiting node needs an actual receipt or handle")
        if state in {"planned", "skipped"} and detail["use_refs"]:
            raise EvidenceError("unexecuted node has no applied method use; record selection in a decision")
    elif payload["operation"] == "evaluation_plan":
        _fields(detail, ("target", "subjects", "comparators", "protocol_ref", "task_split_ref",
                         "initial_conditions_ref", "use_refs", "metrics", "trigger_refs"))
        if detail["target"] not in TARGETS:
            raise EvidenceError("unknown evaluation target")
        for key in ("subjects", "comparators", "trigger_refs"):
            _refs(detail[key], nonempty=True)
        if {canonical(r) for r in detail["subjects"]} & {canonical(r) for r in detail["comparators"]}:
            raise EvidenceError("evaluation needs distinct methods and comparators")
        for key in ("protocol_ref", "task_split_ref", "initial_conditions_ref"):
            validate_ref(detail[key])
        _refs(detail["use_refs"])
        names = set()
        for metric in _list(detail["metrics"], nonempty=True):
            _fields(metric, ("name", "family", "unit", "denominator", "missing_policy"))
            _text(metric["name"])
            if metric["name"] in names or metric["family"] not in {"regression", "retention", "funnel", "cost"}:
                raise EvidenceError("distinct metrics with separate regression/retention/funnel/cost families required")
            names.add(metric["name"])
            for key in ("unit", "denominator", "missing_policy"):
                _text(metric[key])
        if payload["output_refs"]:
            raise EvidenceError("evaluation plan does not contain measured results")
    elif payload["operation"] == "evaluation":
        _fields(detail, ("plan_ref", "evaluation_ref"), ("use_refs", "node_refs"))
        validate_ref(detail["plan_ref"])
        validate_ref(detail["evaluation_ref"])
        _refs(detail.get("use_refs", []))
        _refs(detail.get("node_refs", []))
        if bool(detail.get("use_refs")) != bool(detail.get("node_refs")):
            raise EvidenceError("evaluated use attribution needs both use_refs and node_refs")
    else:
        raise EvidenceError("unknown composition step")


def validate_local_links(journal, content, source_id, local_records, *, external_workspace=None):
    """Check local typed references and node/method bindings before publication."""
    from .continuation import validate_content
    from .schema import Artifact

    def body(ref, schema=None):
        if ref["source_id"] != source_id:
            if external_workspace is None:
                raise EvidenceError("composition bindings require local records or an explicit published external workspace")
            from .evidence import read_evidence
            result = read_evidence(external_workspace, ref)
            value = result.get("record")
            if value is None or result.get("availability") != "available":
                raise EvidenceError("external composition reference unavailable")
        else:
            value = local_records[ref["record_id"]]
        if schema and value.get("schema", value.get("bindings", {}).get("schema")) != schema:
            raise EvidenceError("composition reference has wrong record type")
        return value

    def content_body(ref):
        if ref["source_id"] != source_id and external_workspace is not None:
            from .continuation import read_content
            return read_content(external_workspace, ref)
        value = body(ref)
        path = journal.artifact_path(Artifact(**value))
        return validate_content(json.loads(path.read_text("utf-8-sig")))

    def methods(refs, role=None):
        for ref in refs:
            value = body(ref, "etalon-method-version/1")
            if role and role not in value["roles"]:
                raise EvidenceError("evaluation method role differs from its target")

    if content["schema"] == WORKFLOW:
        methods(content["method_refs"])
        for node in content["nodes"]:
            methods(node["method_refs"])
        return
    detail = content["detail"]
    op = content["operation"]
    if op == "node":
        methods(content["method_refs"])
        if detail["workflow_ref"]:
            workflow = content_body(detail["workflow_ref"])
            if workflow["schema"] != WORKFLOW:
                raise EvidenceError("node requires a workflow definition")
            node = next((n for n in workflow["nodes"] if n["node_id"] == detail["node_id"]), None)
            if node is None or node["capability_id"] != detail["capability_id"]:
                raise EvidenceError("node capability differs from workflow definition")
            if not {canonical(r) for r in content["method_refs"]} <= {canonical(r) for r in node["method_refs"]}:
                raise EvidenceError("method switch needs a new workflow version or a standalone node")
        declared = {r["record_id"] for r in content["method_refs"]}
        for ref in detail["use_refs"]:
            use = body(ref, "etalon-method-use/1")
            if not any(s["kind"] == "task" and s["id"] == content["task_id"] for s in use["subject_refs"]):
                raise EvidenceError("node method use must include the same task subject")
            if not {r["method_version_ref"] for r in use["selected_methods"]} <= declared:
                raise EvidenceError("node must declare every applied method version")
    elif op == "evaluation_plan":
        role = ("usage_method" if detail["target"] in {"branch_selection", "workflow_composition"}
                else "domain_skill" if detail["target"] == "method" else None)
        methods(detail["subjects"] + detail["comparators"], role)
        for ref in detail["use_refs"]:
            use = body(ref, "etalon-method-use/1")
            if not any(s["kind"] == "task" and s["id"] == content["task_id"] for s in use["subject_refs"]):
                raise EvidenceError("evaluation use must include the same task subject")
            compared = {r["record_id"] for r in detail["subjects"] + detail["comparators"]}
            if not {m["method_version_ref"] for m in use["selected_methods"]} <= compared:
                raise EvidenceError("evaluation use selects a method outside its comparison")
        for key in ("protocol_ref", "initial_conditions_ref"):
            value = body(detail[key])
            if "sha256" not in value or "path" not in value:
                raise EvidenceError("comparison protocol and initial conditions require Artifacts")
        body(detail["task_split_ref"])
        if not detail["task_split_ref"]["record_id"].startswith("tasksplit-"):
            raise EvidenceError("comparison requires an existing TaskSplit")
    elif op == "evaluation":
        plan = content_body(detail["plan_ref"])
        if plan.get("operation") != "evaluation_plan" or plan["task_id"] != content["task_id"]:
            raise EvidenceError("evaluation must bind a plan for the same task")
        evaluation = body(detail["evaluation_ref"], "etalon-method-evaluation/1")
        d = plan["detail"]
        for key in ("subjects", "comparators"):
            if evaluation[key] != [r["record_id"] for r in d[key]]:
                raise EvidenceError("evaluation subjects differ from plan")
        for key in ("protocol_ref", "task_split_ref", "initial_conditions_ref"):
            if evaluation[key] != d[key]["record_id"]:
                raise EvidenceError("evaluation conditions differ from plan")
        attributed = {canonical(r) for r in detail.get("use_refs", [])}
        node_uses = set()
        for ref in detail.get("node_refs", []):
            node = content_body(ref)
            if node.get("operation") != "node" or node["task_id"] != content["task_id"]:
                raise EvidenceError("evaluation requires executed nodes of the same task")
            if node["detail"]["state"] in {"planned", "skipped"}:
                raise EvidenceError("unexecuted node cannot prove evaluated use")
            node_uses.update(canonical(r) for r in node["detail"]["use_refs"])
        if attributed != node_uses:
            raise EvidenceError("evaluated uses must exactly match the attributed nodes")
        compared = set(evaluation["subjects"] + evaluation["comparators"])
        for ref in detail.get("use_refs", []):
            use = body(ref, "etalon-method-use/1")
            if not any(s["kind"] == "task" and s["id"] == content["task_id"] for s in use["subject_refs"]):
                raise EvidenceError("evaluated use must include the same task subject")
            if not {m["method_version_ref"] for m in use["selected_methods"]} <= compared:
                raise EvidenceError("evaluated use selects a method outside its comparison")


def node_step(*, task_id, question, branch_id, node_id, capability_id=None, workflow_ref=None,
              state, reason, input_refs=(), output_refs=(), method_refs=(), use_refs=(), receipt_refs=(),
              identities=(), action_handles=(), accounting_refs=(), source_watermarks=(), supersedes=()):
    from .continuation import STEP, validate_content
    return validate_content(dict(schema=STEP, task_id=task_id, question=question, branch_id=branch_id,
        operation="node", reason=reason, input_refs=list(input_refs), output_refs=list(output_refs),
        method_refs=list(method_refs), accounting_refs=list(accounting_refs), source_watermarks=list(source_watermarks),
        supersedes=list(supersedes), detail=dict(node_id=node_id, capability_id=capability_id,
        workflow_ref=workflow_ref, state=state, use_refs=list(use_refs), receipt_refs=list(receipt_refs),
        identities=list(identities), action_handles=list(action_handles))))


def record_evaluation(journal, plan_ref, *, directory, source_id, source_kind, result_refs=(),
                      accounting_refs=(), assessment_role="diagnostic", conclusion, scope,
                      use_refs=(), node_refs=(), external_workspace=None):
    """Reuse MethodEvaluation's existing diagnostic/independent validation checks.

    No auto-promotion or optimizer is called. A failed step append leaves the
    immutable evaluation available for idempotent retry.
    """
    from .continuation import record_content, validate_content
    from .method_revision import journal_ref
    from .schema import Artifact, MethodEvaluation
    if plan_ref["source_id"] != source_id:
        raise EvidenceError("evaluation plan must be local")
    record = journal.get(plan_ref["record_id"])
    from etalon.active.schema import digest
    if digest(record) != plan_ref["record_digest"]:
        raise EvidenceError("evaluation plan identity mismatch")
    plan = validate_content(json.loads(journal.artifact_path(Artifact(**record)).read_text("utf-8-sig")))
    if plan.get("operation") != "evaluation_plan":
        raise EvidenceError("expected evaluation plan step")
    d = plan["detail"]
    for ref in (*d["subjects"], *d["comparators"], d["protocol_ref"], d["task_split_ref"], d["initial_conditions_ref"]):
        if ref["source_id"] != source_id or digest(journal.get(ref["record_id"])) != ref["record_digest"]:
            raise EvidenceError("evaluation bindings must be exact local records")
    value = MethodEvaluation(tuple(r["record_id"] for r in d["subjects"]),
        tuple(r["record_id"] for r in d["comparators"]), d["protocol_ref"]["record_id"],
        d["task_split_ref"]["record_id"], d["initial_conditions_ref"]["record_id"], tuple(result_refs),
        tuple(accounting_refs), assessment_role, conclusion, scope)
    rid = journal.record(value)
    ref = journal_ref(journal, rid, source_id=source_id)
    step = {**plan, "operation": "evaluation", "reason": conclusion, "input_refs": [plan_ref],
            "output_refs": [ref], "accounting_refs": [], "supersedes": [],
            "detail": {"plan_ref": plan_ref, "evaluation_ref": ref}}
    if use_refs or node_refs:
        step["detail"].update(use_refs=list(use_refs), node_refs=list(node_refs))
    step_id = record_content(journal, step, directory, source_id=source_id, source_kind=source_kind,
                             external_workspace=external_workspace)
    return {"evaluation_ref": ref, "step_ref": journal_ref(journal, step_id, source_id=source_id),
            "promotion": "not_performed", "optimization": "not_triggered"}
