"""Four-stage research trace, projected from ordinary Journal content.

Observable receipts and agent-declared decisions remain distinct. A rejected
result is retained alongside successful results; reuse is not a new experiment.
No hidden model reasoning is requested, no scientific work is dispatched, and
no second event store is created by this module.
"""
from __future__ import annotations

import html
import json
import math

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, validate_ref

TRACE = "etalon-research-trace/1"
SCHEMAS = {TRACE}
PHASES = {"declaration", "compiled", "execution", "admission"}
WORKSPACES = {"Search", "Judge", "Quick", "Deep"}
STATES = {
    "declaration": {"planned", "skipped", "deferred", "selected"},
    "compiled": {"ready", "blocked", "partial"},
    "execution": {"running", "completed", "failed", "partial", "reused", "cancelled"},
    "admission": {"accepted", "rejected", "partial", "awaiting"},
}


def _fields(obj, required, optional=()):
    if not isinstance(obj, dict) or set(required) - obj.keys() or obj.keys() - set(required) - set(optional):
        raise EvidenceError("missing or unsupported trace fields")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError("nonempty trace text required")


def _refs(value):
    if not isinstance(value, list) or len(value) > 64:
        raise EvidenceError("bounded reference list required")
    for item in value:
        validate_ref(item)


def _strings(value):
    if not isinstance(value, list) or len(value) > 128:
        raise EvidenceError("bounded trace list required")
    for item in value:
        _text(item)
    if len(set(value)) != len(value):
        raise EvidenceError("duplicate trace entries")


def validate_content(payload):
    _fields(payload, ("schema", "event_key", "sequence", "task_id", "branch_id", "node_id", "workspace", "phase",
                     "origin", "state", "reason", "attempt_id", "workflow_ref", "node_ref", "config_ref", "previous_refs",
                     "method_refs", "receipt_refs", "evidence_refs", "detail", "supersedes"))
    if payload["schema"] != TRACE or payload["phase"] not in PHASES or payload["workspace"] not in WORKSPACES:
        raise EvidenceError("unknown trace schema/phase/workspace")
    for key in ("event_key", "task_id", "branch_id", "node_id", "reason"):
        _text(payload[key])
    if type(payload["sequence"]) is not int or payload["sequence"] < 0:
        raise EvidenceError("explicit nonnegative event sequence required")
    if payload["origin"] not in {"observed", "declared"} or payload["state"] not in STATES[payload["phase"]]:
        raise EvidenceError("trace state or provenance invalid")
    if payload["attempt_id"] is not None:
        _text(payload["attempt_id"])
    for key in ("workflow_ref", "node_ref", "config_ref"):
        if payload[key] is not None:
            validate_ref(payload[key])
    for key in ("previous_refs", "method_refs", "receipt_refs", "evidence_refs", "supersedes"):
        _refs(payload[key])
    d, phase, state = payload["detail"], payload["phase"], payload["state"]
    if phase == "declaration":
        _fields(d, ("planned_checks", "alternatives", "choice", "reconsider_when"))
        _strings(d["planned_checks"])
        _strings(d["alternatives"])
        _text(d["choice"])
        _text(d["reconsider_when"])
        if payload["origin"] != "declared" or payload["attempt_id"] is not None or payload["receipt_refs"]:
            raise EvidenceError("a plan/skip is declared, not an observed execution")
        if state in {"skipped", "deferred"} and not payload["evidence_refs"]:
            raise EvidenceError("skipped/deferred branch needs explicit basis")
    elif phase == "compiled":
        _fields(d, ("revision_id", "declared_checks", "compiled_checks", "missing_checks"))
        _text(d["revision_id"])
        for key in ("declared_checks", "compiled_checks", "missing_checks"):
            _strings(d[key])
        if set(d["missing_checks"]) != set(d["declared_checks"]) - set(d["compiled_checks"]):
            raise EvidenceError("compiled check coverage inconsistent")
        if state == "ready" and d["missing_checks"]:
            raise EvidenceError("missing checks cannot claim compile-ready")
        if payload["origin"] != "observed" or payload["config_ref"] is None or not payload["receipt_refs"]:
            raise EvidenceError("compilation needs an observed report and configuration identity")
    elif phase == "execution":
        _fields(d, ("tool_name", "call_id", "input_digest", "output_digest", "duration_seconds", "cost", "reuse_of", "error", "completed_checks"))
        for key in ("tool_name", "call_id", "input_digest"):
            _text(d[key])
        if d["output_digest"] is not None:
            _text(d["output_digest"])
        if payload["origin"] != "observed" or not payload["receipt_refs"]:
            raise EvidenceError("execution needs observed receipts, not agent assertions")
        if payload["attempt_id"] is None:
            raise EvidenceError("execution needs explicit attempt identity")
        _strings(d["completed_checks"])
        duration = d["duration_seconds"]
        if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
            raise EvidenceError("duration must be null or finite nonnegative seconds")
        cost = d["cost"]
        if cost is not None:
            _fields(cost, ("amount", "unit", "accounting_ref"))
            _text(cost["unit"])
            validate_ref(cost["accounting_ref"])
            if type(cost["amount"]) not in (int, float) or not math.isfinite(cost["amount"]) or cost["amount"] < 0:
                raise EvidenceError("cost must be finite nonnegative")
        if state == "reused":
            if d["reuse_of"] is None:
                raise EvidenceError("reuse must link the original execution")
            validate_ref(d["reuse_of"])
        elif d["reuse_of"] is not None:
            raise EvidenceError("reanalysis/repeat must be a new attempt, not disguised reuse")
        if state == "failed" and (not isinstance(d["error"], str) or not d["error"].strip()):
            raise EvidenceError("failure must preserve the error")
        if state == "completed" and d["output_digest"] is None:
            raise EvidenceError("completed execution must identify its output")
    else:
        _fields(d, ("accepted_refs", "rejected_refs", "criteria_ref"))
        _refs(d["accepted_refs"])
        _refs(d["rejected_refs"])
        validate_ref(d["criteria_ref"])
        if payload["attempt_id"] is None or not payload["previous_refs"]:
            raise EvidenceError("admission must link an execution attempt")
        if state == "accepted" and (not d["accepted_refs"] or d["rejected_refs"]):
            raise EvidenceError("accepted admission needs accepted evidence only")
        if state == "rejected" and (not d["rejected_refs"] or d["accepted_refs"]):
            raise EvidenceError("rejected evidence must remain referenced")
        if state == "partial" and (not d["accepted_refs"] or not d["rejected_refs"]):
            raise EvidenceError("partial admission must preserve both portions")
        if state == "awaiting" and (d["accepted_refs"] or d["rejected_refs"]):
            raise EvidenceError("awaiting is not an admission decision")
        if payload["origin"] == "observed" and not payload["receipt_refs"]:
            raise EvidenceError("mechanical admission needs its validator receipt")
    return json.loads(canonical(payload))


def trace_event(*, event_key, sequence, task_id, branch_id, node_id, workspace, phase, origin, state, reason, detail,
                attempt_id=None, workflow_ref=None, node_ref=None, config_ref=None, previous_refs=(), method_refs=(),
                receipt_refs=(), evidence_refs=(), supersedes=()):
    """Build a validated record; persist with continuation.record_content."""
    return validate_content(dict(schema=TRACE, event_key=event_key, sequence=sequence, task_id=task_id,
        branch_id=branch_id, node_id=node_id, workspace=workspace, phase=phase, origin=origin, state=state,
        reason=reason, detail=detail, attempt_id=attempt_id, workflow_ref=workflow_ref, node_ref=node_ref,
        config_ref=config_ref, previous_refs=list(previous_refs), method_refs=list(method_refs),
        receipt_refs=list(receipt_refs), evidence_refs=list(evidence_refs), supersedes=list(supersedes)))


def validate_local_links(journal, content, source_id):
    """Typed previous-stage links checked on journal publication by the caller.

    External links require import first; artifact identity is still checked by
    the existing record_content path. Phase links are allowed to branch, but an
    admission must refer to the same node and attempt's actual execution.
    """
    from .improvement import _local
    phases = []
    for ref in content["previous_refs"]:
        previous = _local(journal, ref, source_id, TRACE)
        validate_content(previous)
        if (previous["task_id"], previous["branch_id"], previous["node_id"]) != (content["task_id"], content["branch_id"], content["node_id"]):
            raise EvidenceError("trace stage link must belong to the same task/branch/node")
        if previous["sequence"] >= content["sequence"]:
            raise EvidenceError("trace predecessors must have an earlier sequence")
        phases.append(previous["phase"])
        if content["phase"] == "admission" and previous["phase"] == "execution" and previous["attempt_id"] != content["attempt_id"]:
            raise EvidenceError("admission belongs to a different attempt")
    if content["phase"] == "admission" and "execution" not in phases:
        raise EvidenceError("admission must link an execution trace")


def project_trace(records):
    """Two views from the same records; exact duplicate deliveries count once.

    The caller selects the journal scope and resolves refs using existing
    evidence APIs. No fabricated global coverage is claimed for a bounded set.
    """
    unique = {}
    for record in records:
        content = validate_content(record)
        key = (content["task_id"], content["event_key"])
        if key in unique and unique[key] != content:
            raise EvidenceError("same trace event identity has conflicting content")
        unique[key] = content
    rows = sorted(unique.values(), key=lambda r: (r["task_id"], r["sequence"], r["event_key"]))
    route = [{k: row[k] for k in ("event_key", "sequence", "task_id", "branch_id", "node_id", "workspace", "phase", "origin", "state", "reason", "attempt_id")}
             | {"receipt_refs": row["receipt_refs"], "evidence_refs": row["evidence_refs"]} for row in rows]
    execution = [row for row in rows if row["phase"] == "execution"]
    attempts = {}
    reused = set()
    attempt_identities = {}
    for row in execution:
        # Multiple state updates/call receipts for one attempt do not pretend
        # to be independent repetitions. Reuse retrieval has its own identity.
        key = (row["task_id"], row["node_id"], row["attempt_id"])
        identity = {"tool_name": row["detail"]["tool_name"], "input_digest": row["detail"]["input_digest"],
                    "config_ref": row["config_ref"], "method_refs": row["method_refs"]}
        if key in attempt_identities and attempt_identities[key] != identity:
            raise EvidenceError("attempt tool/input/config/method identity changed; use a new attempt identity")
        attempt_identities[key] = identity
        if row["state"] == "reused":
            reused.add(key)
        else:
            if key in reused:
                raise EvidenceError("attempt cannot be both a new execution and reused")
            earlier = attempts.get(key)
            if earlier and earlier["state"] in {"completed", "failed", "cancelled"} and earlier["state"] != row["state"]:
                raise EvidenceError("terminal attempt cannot change state; retry needs a new attempt identity")
            attempts[key] = row
    if reused & attempts.keys():
        raise EvidenceError("attempt cannot be both a new execution and reused")
    statuses = {s: sum(r["state"] == s for r in attempts.values()) for s in STATES["execution"] - {"reused"}}
    return {"schema": "etalon-trace-view/1", "scope": "provided_records_only", "record_digest": digest(rows),
            "receipt_verification": "not_verified_by_projection",
            "provenance_note": "origin records producer-declared provenance; authentic execution requires receipt verification",
            "route": route, "tools": execution, "summary": {"events": len(rows), "execution_attempts": len(attempts),
                "reuses": len(reused), "attempt_states": statuses,
                "admissions": sum(r["phase"] == "admission" for r in rows),
                "rejected_admissions": sum(r["phase"] == "admission" and r["state"] == "rejected" for r in rows)}}


def render_trace_html(records, *, title="ETALON research route"):
    """Standalone escaped HTML, no scripts/remote assets or second narrative."""
    view = project_trace(records)
    esc = lambda value: html.escape(str(value), quote=True)
    cards = []
    for row in view["route"]:
        cards.append('<article><small>' + esc(row["workspace"]) + ' · ' + esc(row["phase"]) + ' · ' + esc(row["origin"]) +
            '</small><h2>' + esc(row["node_id"]) + '</h2><strong>' + esc(row["state"]) + '</strong><p>' +
            esc(row["reason"]) + '</p><small>branch ' + esc(row["branch_id"]) + ' · event ' + esc(row["event_key"]) + '</small></article>')
    tools = []
    for row in view["tools"]:
        d = row["detail"]
        tools.append('<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in
            (row["sequence"], d["tool_name"], d["call_id"], row["attempt_id"], row["state"],
             "unknown" if d["duration_seconds"] is None else d["duration_seconds"])) + '</tr>')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' + \
        '<title>' + esc(title) + '</title><style>body{font:16px system-ui;margin:32px;color:#183145;background:#f4f7fa}' + \
        'main{display:flex;flex-wrap:wrap;gap:16px}article{background:white;border:1px solid #c7d4df;border-radius:12px;padding:18px;max-width:320px}' + \
        'h2{font-size:20px}small{color:#52677b}table{border-collapse:collapse;background:white}td,th{padding:10px;border:1px solid #c7d4df;text-align:left}' + \
        'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>' + esc(title) + '</h1><p>Observed is producer-declared provenance, not authentication. Receipt references are not verified by this projection. Scope: provided records only.</p><main>' + \
        ''.join(cards) + '</main><h2>Tool execution</h2><table><tr><th>Seq</th><th>Tool</th><th>Call</th><th>Attempt</th><th>State</th><th>Seconds</th></tr>' + \
        ''.join(tools) + '</table><h2>Accounting coverage</h2><pre>' + esc(json.dumps(view["summary"], ensure_ascii=False, indent=2)) + \
        '</pre><details><summary>Evidence references and full trace</summary><pre>' + esc(json.dumps(view, ensure_ascii=False, indent=2)) + '</pre></details></html>'
