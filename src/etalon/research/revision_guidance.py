"""Read-only decision-to-candidate bridge over existing evidence contracts.

This is a derived view, not a new stored schema, opportunity or scientific judge.
Differences are exact JSON observations; their relevance remains host judgment.
"""
from __future__ import annotations

import json
import re

from etalon.active.schema import canonical, digest
from .continuation import _fields, _list, _text
from .evidence_refs import EvidenceError
from .improvement import CONTEXT, read_material, validate_content as validate_improvement
from .read_scope import bounded_read
from . import research_decision as rd


def _pointer(value, pointer):
    """Keep absent, explicit null and false/zero distinct; never impute an answer."""
    if not isinstance(pointer, str) or pointer and not pointer.startswith("/"):
        raise EvidenceError("observation pointer must be an RFC 6901 JSON pointer")
    for token in pointer.split("/")[1:] if pointer else ():
        # Reject invalid escaping rather than silently selecting another field.
        if re.search(r"~(?![01])", token):
            raise EvidenceError("invalid JSON pointer escape")
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isascii() and token.isdecimal() and str(int(token)) == token and int(token) < len(value):
            value = value[int(token)]
        else:
            return {"presence": "missing", "value": None}
    if len(canonical(value).encode("utf-8")) > 4096:
        raise EvidenceError("observation exceeds 4 KiB; select a narrower pointer")
    return {"presence": "null" if value is None else "observed", "value": value}


def _observation(reader, selector, decision):
    _fields(selector, ("ref", "pointer"))
    if rd._key(selector["ref"]) not in rd._keys(decision["input_refs"]):
        raise EvidenceError("diagnostic difference must use declared decision inputs, not evaluation answers")
    # An excluded answer is rejected before reading it. Every accepted alias
    # still goes through original identity and byte verification.
    body = reader.body(selector["ref"])
    rd._scope(body, decision)
    return {**selector, **_pointer(body, selector["pointer"])}


@bounded_read
def build_revision_input(workspace, *, decision_ref, prediction_id, context_ref,
                         differences=(), update_ref=None):
    """Inspect one declared prediction and explicit differences without mutation.

Each difference has difference_id, left/right {ref,pointer}, option_links
[{option_id,relation,reason}], and uncontrolled (explicit remaining confounders).
Relations supports/against/compatible/unknown are attributed host judgments.
Update observations remain a separate evaluation-followup, never diagnostic
inputs to the original decision. Missing/null measurements stay unavailable.
"""
    reader = rd._Reader(lambda ref: read_material(workspace, ref), workspace)
    decision = reader.decision(decision_ref)
    if decision["schema"] != rd.DECISION:
        raise EvidenceError("revision input requires the original decision")
    context = validate_improvement(reader.body(context_ref, CONTEXT))
    rd._scope(context, decision)
    if (rd._key(context["target_ref"]) not in rd._keys(decision["method_refs"])
            or not rd._keys(context["problem_refs"]) <= rd._keys(decision["input_refs"])):
        raise EvidenceError("revision context target/problems differ from exact decision")
    for ref in context["problem_refs"] + context["evidence_refs"]:
        reader.material(ref)
    prediction = next((p for p in decision["predictions"] if p["prediction_id"] == prediction_id), None)
    if prediction is None:
        raise EvidenceError("unknown decision prediction")
    action = next(a for a in decision["actions"] if a["action_id"] == prediction["action_id"])
    options = {o["option_id"]: o for o in decision["options"]}
    observed, ids = [], set()
    missing = list(context["missing"])
    for difference in _list(list(differences), limit=16):
        _fields(difference, ("difference_id", "left", "right", "option_links", "uncontrolled"))
        _text(difference["difference_id"])
        if difference["difference_id"] in ids:
            raise EvidenceError("duplicate difference identity")
        ids.add(difference["difference_id"])
        left, right = (_observation(reader, difference[k], decision) for k in ("left", "right"))
        linked = set()
        for link in _list(difference["option_links"], nonempty=True):
            _fields(link, ("option_id", "relation", "reason"))
            if link["option_id"] not in options or link["option_id"] in linked:
                raise EvidenceError("difference must identify distinct existing explanation options")
            linked.add(link["option_id"])
            if link["relation"] not in {"supports", "against", "compatible", "unknown"}:
                raise EvidenceError("unsupported explanation relation")
            _text(link["reason"])
        for value in _list(difference["uncontrolled"]):
            _text(value)
        comparison = "unknown"
        if left["presence"] == right["presence"] == "observed":
            comparison = "same" if canonical(left["value"]) == canonical(right["value"]) else "different"
        else:
            missing.append("difference " + difference["difference_id"] + " has missing/null observations")
        observed.append({**difference, "left": left, "right": right, "comparison": comparison,
                         "attribution": "host_judgment", "causal_identification": "not_established"})
    if prediction["status"] != "testable":
        missing.append("prediction: " + prediction["unknown_reason"])
    if action["disposition"] != "selected" or action["execution_ref"] is None:
        missing.append("no selected normal execution is bound")
    plans = []
    for ref in decision["evaluation_refs"]:
        plan = reader.body(ref)
        plans.append({"ref": ref, "content_digest": digest(plan), "content": plan})
    if not plans:
        missing.append("no fixed evaluation plan or contract is bound")
    window = None if decision["evidence_window_ref"] is None else reader.body(decision["evidence_window_ref"])
    followup = None
    counters = list(decision["counter_refs"])
    if update_ref is not None:
        update = reader.decision(update_ref)
        if update["schema"] != rd.UPDATE or rd._key(update["decision_ref"]) != rd._key(decision_ref):
            raise EvidenceError("evaluation followup belongs to a different exact decision")
        followup = {"ref": update_ref, **{k: update[k] for k in ("disposition", "outcome", "judgments",
                    "execution_refs", "evaluation_refs", "experience_refs", "gaps")}}
        counters += update["counter_refs"]
    result = dict(view="revision_input", decision_ref=decision_ref, context_ref=context_ref,
        task_id=decision["task_id"], scope=decision["scope"], conditions=context["conditions"],
        base_method_ref=context["target_ref"], question=decision["question"], prediction=prediction,
        action=action, alternatives=decision["options"], diagnostic_input_refs=decision["input_refs"], diagnostic_differences=observed,
        structure_unknowns=decision["structure"]["unknowns"], gaps=decision["gaps"],
        frozen_constraints=decision["structure"]["constraints"], fixed_evaluations=plans,
        evidence_timing={"window_ref": decision["evidence_window_ref"],
            "mode": None if window is None else window["timing_mode"],
            "prospective_ignorance": "not_established"}, evaluation_followup=followup,
        counter_refs=rd._unique(counters), missing=list(dict.fromkeys(missing)),
        execution="not_performed", candidate="not_created", adoption="not_performed")
    return json.loads(canonical(result))


def _candidate_execution_binding(workspace, guidance, change, patch_ref):
    """Require the actual edit in the selected action AND a fixed comparison.

    Sharing a baseline or an observable does not bind a new algorithm to an old
    MethodVersion. Workflow patches additionally bind the exact after-workflow.
    This adds readiness gaps to the existing brief, never a new approval stage.
    """
    from .schema import method_record_from_dict
    from .improvement import PATCH
    from .composition import WORKFLOW, validate_workflow
    from .candidate_brief import _content_digest

    _fields(change, ("kind", "before_ref", "after_ref"))
    reader = rd._Reader(lambda ref: read_material(workspace, ref), workspace)
    reader.material(change["before_ref"])
    after_digest = _content_digest(reader, change["after_ref"])
    action = guidance["action"]
    node = None
    if action["disposition"] == "selected" and action["execution_ref"] is not None:
        node = reader.body(action["execution_ref"], "etalon-research-step/1")
    plans = []
    for item in guidance["fixed_evaluations"]:
        value = item["content"]
        plan = value if value["schema"] == "etalon-evaluation-contract/1" else value["detail"]
        plans.append((item["ref"], plan))
    patch = None
    if patch_ref is not None:
        patch = validate_improvement(reader.body(patch_ref, PATCH))
    action_methods = [] if node is None else node["method_refs"]
    possible = rd._unique(action_methods + [ref for _, plan in plans for ref in plan["subjects"]])
    matches = []
    base = guidance["base_method_ref"]
    for ref in possible:
        method = reader.body(ref, "etalon-method-version/1")
        method_record_from_dict(method)
        if base["record_id"] not in method["parents"]:
            continue
        if change["kind"] == "method_content":
            content = method["content_ref"]
            if "member" in change["after_ref"] or set(content) != {"artifact_ref"}:
                continue
            # Read the method's source-local Artifact, then compare verified
            # content as candidate_brief does. A different locator containing
            # identical JSON is an alias, not a different algorithm.
            rid = content["artifact_ref"]
            if not isinstance(rid, str) or not rid.startswith("artifact-"):
                continue
            content_ref = {"schema": "etalon-evidence-ref/1", "source_id": ref["source_id"],
                           "record_id": rid, "record_digest": rid.removeprefix("artifact-")}
            if _content_digest(reader, content_ref) == after_digest:
                matches.append(ref)
        elif change["kind"] == "workflow" and patch is not None:
            if (rd._key(patch["candidate_method_ref"]) == rd._key(ref)
                    and rd._key(patch["base_method_ref"]) == rd._key(base)
                    and patch["workflow_before_ref"] is not None
                    and rd._key(patch["workflow_before_ref"]) == rd._key(change["before_ref"])
                    and rd._key(patch["workflow_after_ref"]) == rd._key(change["after_ref"])):
                matches.append(ref)
    action_matches = [ref for ref in matches if rd._key(ref) in rd._keys(action_methods)]
    missing = []
    if change["kind"] == "workflow":
        workflow = validate_workflow(reader.body(change["after_ref"], WORKFLOW))
        workflow_node = None if node is None else next(
            (item for item in workflow["nodes"] if item["node_id"] == node["detail"]["node_id"]), None)
        if (node is None or node["detail"]["workflow_ref"] is None
                or rd._key(node["detail"]["workflow_ref"]) != rd._key(change["after_ref"])
                or workflow_node is None or workflow_node["capability_id"] != node["detail"]["capability_id"]
                or not rd._keys(action_methods) <= rd._keys(workflow_node["method_refs"])):
            action_matches = []
        if patch is None:
            missing.append("candidate workflow needs an exact patch linking its after-workflow to a candidate MethodVersion")
    if not action_matches:
        missing.append("selected normal action does not bind the actual candidate after-content/workflow through an exact MethodVersion")
    applicable = [ref for ref, plan in plans
                  if rd._key(base) in rd._keys(plan["comparators"])
                  and rd._keys(action_matches) & rd._keys(plan["subjects"])]
    if not applicable:
        missing.append("no fixed evaluation binds the action's actual candidate as subject and the exact base as comparator")
    return {"status": "incomplete" if missing else "bound", "after_ref": change["after_ref"],
            "candidate_method_refs": matches, "action_candidate_method_refs": action_matches,
            "applicable_evaluation_refs": applicable,
            "normal_execution_ref": action["execution_ref"] if action_matches else None, "missing": missing}


@bounded_read
def build_candidate_from_decision(workspace, *, decision_ref, prediction_id, context_ref,
        opportunity_ref, object_kind, falsifier, differences=(), update_ref=None, **brief_arguments):
    """Return {revision_input, candidate_brief}; store the existing brief if useful.

The host supplies the actual edit, effect metric/unit/direction and applicability.
Prediction, normal-use binding and fixed evaluation identities come from the
verified decision. No new opportunity, trial, evaluation or adoption is made.
No-change uses existing dispositions; this helper only describes real edits.
"""
    from .candidate_brief import build_candidate_brief
    guidance = build_revision_input(workspace, decision_ref=decision_ref, prediction_id=prediction_id,
        context_ref=context_ref, differences=differences, update_ref=update_ref)
    prediction, action = guidance["prediction"], guidance["action"]
    if prediction["status"] != "testable":
        raise EvidenceError("unknown prediction cannot manufacture a testable candidate")
    if object_kind not in {"method", "content", "selection", "composition"}:
        raise EvidenceError("editable object must be method, content, selection or composition")
    _text(falsifier)
    reserved = {"base_method_ref", "expected_effect", "normal_execution", "quality_criteria"}
    # expected_effect is accepted only with metric/unit/direction; hypothesis is
    # taken verbatim from the bound decision, never guessed from an outcome label.
    effect = brief_arguments.pop("expected_effect", None)
    _fields(effect, ("metric", "unit", "direction"))
    if effect["metric"] != prediction["observable"]:
        raise EvidenceError("candidate metric must preserve the exact decision observable")
    if reserved & brief_arguments.keys():
        raise EvidenceError("decision-derived execution and fixed checks cannot be overridden")
    if object_kind in {"selection", "composition"} and brief_arguments["change"]["kind"] != "workflow":
        raise EvidenceError("selection/composition change must identify the actual workflow edit")
    binding = _candidate_execution_binding(workspace, guidance, brief_arguments["change"], brief_arguments.get("patch_ref"))
    guidance["candidate_binding"] = binding
    guidance["missing"] = list(dict.fromkeys(guidance["missing"] + binding["missing"]))
    support = list(brief_arguments.pop("support_refs", ()))
    if not rd._keys(support) <= rd._keys(guidance["diagnostic_input_refs"]):
        raise EvidenceError("additional candidate support must already be declared decision inputs")
    support += [decision_ref] + prediction["basis_refs"]
    support += [row["ref"] for row in guidance["fixed_evaluations"]]
    support += [d[side]["ref"] for d in guidance["diagnostic_differences"] for side in ("left", "right")]
    counters = list(brief_arguments.pop("counter_refs", ())) + guidance["counter_refs"]
    missing = list(brief_arguments.pop("missing_evidence", ())) + guidance["missing"]
    invariants = list(brief_arguments.pop("frozen_invariants", ())) + guidance["frozen_constraints"]
    criteria = ["Preserve exact evaluation " + row["ref"]["record_id"] + " content " + row["content_digest"]
                for row in guidance["fixed_evaluations"]]
    if not criteria:
        criteria = ["Fixed evaluation criteria remain unspecified; no success claim is available"]
    # An existing update can document subsequent evaluation, but its IDs must
    # actually be the ones the caller associates with this candidate.
    followup = guidance["evaluation_followup"]
    evaluations = list(brief_arguments.get("evaluation_refs", ()))
    experiences = list(brief_arguments.get("experience_refs", ()))
    if evaluations or experiences:
        if followup is None:
            raise EvidenceError("candidate evaluation/experience needs the exact decision update")
        raw = []
        for ref in followup["evaluation_refs"]:
            value = read_material(workspace, ref)["content"]
            if value.get("operation") == "evaluation":
                raw.append(value["detail"]["evaluation_ref"])
        if not rd._keys(evaluations) <= rd._keys(raw) or not rd._keys(experiences) <= rd._keys(followup["experience_refs"]):
            raise EvidenceError("candidate evaluation/experience differs from exact attributed update")
        support.append(update_ref)
    brief = build_candidate_brief(workspace, context_ref=context_ref, opportunity_ref=opportunity_ref,
        base_method_ref=guidance["base_method_ref"], expected_effect={**effect, "hypothesis": prediction["expected"]},
        normal_execution={"execution_ref": binding["normal_execution_ref"],
            "description": action["description"], "discriminator": prediction["distinguishes"] + "; falsifier: " + falsifier,
            "metric": effect["metric"], "unit": effect["unit"], "success_criterion": prediction["expected"]},
        frozen_invariants=list(dict.fromkeys(invariants)), quality_criteria=criteria,
        support_refs=rd._unique(support), counter_refs=rd._unique(counters), missing_evidence=list(dict.fromkeys(missing)),
        **brief_arguments)
    guidance.update(editable_object=object_kind, falsifier=falsifier, candidate="existing_brief_built")
    return {"revision_input": guidance, "candidate_brief": brief}
