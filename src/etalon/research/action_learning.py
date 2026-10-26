"""Small, optional action briefs and feedback from existing normal-use evidence.

The host selects originals, authors the missing mechanism and conditional next
choice, and calls its existing executor. These helpers only read and compare;
their dictionaries can be saved with ordinary ``record_json``. They neither run
a checker nor turn an exact JSON match into a scientific success or adoption.
"""
from __future__ import annotations

import json

from etalon.active.schema import canonical
from . import research_decision as rd
from .continuation import _fields, _list, _text
from .evidence_refs import EvidenceError
from .improvement import EXPERIENCE, read_material, validate_content as validate_experience
from .read_scope import bounded_read
from .revision_guidance import _pointer


def _reader(workspace):
    return rd._Reader(lambda ref: read_material(workspace, ref), workspace)


def _conditions(required, actual):
    """Unknown dimensions cannot match; extra actual dimensions are retained."""
    if not required:
        return dict(status="not_declared", unknown=[], different=[])
    if not isinstance(required, dict):
        raise EvidenceError("declared applicability conditions must be a mapping")
    unknown = [k for k, v in required.items() if v is None or actual.get(k) is None]
    different = [k for k, v in required.items() if k not in unknown and actual[k] != v]
    return dict(status="different" if different else "unknown" if unknown else "match",
                unknown=unknown, different=different)


def _rule_contexts(source, pointer, actual):
    """Keep the context along one selected path, without reading linked history."""
    contexts, comparisons = [], []
    value, path = source, ""
    tokens = pointer.split("/")[1:] if pointer else []
    for index in range(len(tokens) + 1):
        if isinstance(value, dict):
            context = {k: value[k] for k in ("task_id", "scope", "conditions", "unknowns", "counter_refs",
                        "applicability", "invalid_when", "status", "dependencies", "issue_ids") if k in value}
            if context:
                contexts.append(dict(pointer=path, context=context))
            for field, required in (("conditions", value.get("conditions", {})),
                    ("applicability", value.get("applicability", {}).get("conditions", {})
                     if isinstance(value.get("applicability", {}), dict) else None)):
                if field == "applicability" and required is None:
                    raise EvidenceError("declared rule applicability must be a mapping")
                if required:
                    comparisons.append(dict(pointer=path, field=field, **_conditions(required, actual)))
        if index == len(tokens):
            break
        token = tokens[index].replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isascii() and token.isdecimal() and str(int(token)) == token and int(token) < len(value):
            value = value[int(token)]
        else:
            break
        path += "/" + tokens[index]
    unknown = sorted({k for row in comparisons for k in row["unknown"]})
    different = sorted({k for row in comparisons for k in row["different"]})
    return contexts, dict(status="different" if different else "unknown" if unknown else "match" if comparisons else "not_declared",
                          unknown=unknown, different=different, by_context=comparisons)


def _expectations(reader, decision, action, selectors):
    predictions = {p["prediction_id"]: p for p in decision["predictions"] if p["action_id"] == action["action_id"]}
    result, seen = [], set()
    for selector in _list(list(selectors), nonempty=True, limit=16):
        _fields(selector, ("prediction_id", "ref", "pointer"))
        pid = selector["prediction_id"]
        if pid not in predictions or pid in seen:
            raise EvidenceError("expectation must name a distinct prediction of the selected action")
        seen.add(pid)
        prediction = predictions[pid]
        if rd._key(selector["ref"]) not in rd._keys(prediction["basis_refs"]):
            raise EvidenceError("structured expectation must already be a declared prediction basis")
        selected = _pointer(reader.body(selector["ref"]), selector["pointer"])
        value = selected["value"]
        if selected["presence"] != "observed" or not isinstance(value, dict):
            raise EvidenceError("selected expectation must be an existing structured object")
        _fields(value, ("observable", "expected_statement", "checks", "next_if_match", "next_if_different", "next_if_unknown"))
        if value["observable"] != prediction["observable"] or value["expected_statement"] != prediction["expected"]:
            raise EvidenceError("expectation observable/statement differs from the exact decision prediction")
        pointers = set()
        for check in _list(value["checks"], nonempty=True, limit=16):
            _fields(check, ("pointer", "expected"))
            _pointer({}, check["pointer"])
            _pointer({"expected": check["expected"]}, "/expected")
            if check["pointer"] in pointers:
                raise EvidenceError("duplicate expected result pointer")
            pointers.add(check["pointer"])
        for key in ("next_if_match", "next_if_different", "next_if_unknown"):
            _text(value[key])
        result.append({**selector, "prediction": prediction, "expectation": value})
    return result


@bounded_read
def gather_action_basis(workspace, *, decision_ref, action_id, conditions, expectations,
                        rule_selectors=(), experience_refs=()):
    """Gather explicitly selected rules/experience beside one normal action.

``expectations`` selects objects via {prediction_id, ref, pointer}. Each object
has observable, expected_statement (identical to the decision), checks (a list
of {pointer, expected}), and next_if_match/different/unknown text. Its original
ref must already be in that prediction's basis_refs. All checks are conjunctive.
The host owns the semantic interpretation of this explicit measurement mapping.

``rule_selectors`` are {ref, pointer} into structured local rule originals.
Selected rule and ancestor scope/counters remain visible; unselected history is
not expanded. ``experience_refs`` are exact existing method-experience refs,
including conflicts. Null/missing query dimensions remain unknown. Free-text
invalid_when and undeclared selection omissions still require host judgment.
Save this returned brief before executing when prospective timing matters;
an immutable saved brief alone does not prove ignorance of future outcomes.
"""
    if not isinstance(conditions, dict) or not conditions:
        raise EvidenceError("explicit action conditions required")
    for key, value in conditions.items():
        _text(key)
        if value is not None:
            _text(value)
    reader = _reader(workspace)
    decision = reader.decision(decision_ref)
    if decision["schema"] != rd.DECISION:
        raise EvidenceError("action basis requires an original decision")
    action = next((a for a in decision["actions"] if a["action_id"] == action_id), None)
    if action is None or action["disposition"] != "selected" or action["execution_ref"] is None:
        raise EvidenceError("action basis needs a selected normal execution")
    expectations = _expectations(reader, decision, action, expectations)
    rules, experiences = [], []
    for selector in _list(list(rule_selectors), limit=16):
        _fields(selector, ("ref", "pointer"))
        source = reader.body(selector["ref"])
        selection = _pointer(source, selector["pointer"])
        contexts, comparison = _rule_contexts(source, selector["pointer"], conditions)
        if selection["presence"] != "observed":
            comparison = {**comparison, "status": "unknown"}
        rules.append({**selector, **selection,
            "source_context": {k: source[k] for k in ("task_id", "scope", "conditions", "unknowns", "counter_refs") if k in source},
            "context_chain": contexts, "condition_comparison": comparison})
    for ref in _list(list(experience_refs), limit=16):
        value = validate_experience(reader.body(ref, EXPERIENCE))
        experiences.append({"ref": ref, "experience": value,
                            "condition_comparison": _conditions(value["conditions"], conditions),
                            "invalid_when_check": "host_review_required"})
    superseded = rd._keys([ref for item in experiences for ref in item["experience"]["supersedes"]])
    for item in experiences:
        item["superseded_in_selected_set"] = rd._key(item["ref"]) in superseded
    return json.loads(canonical(dict(view="action_basis", decision_ref=decision_ref, action_id=action_id,
        task_id=decision["task_id"], scope=decision["scope"], conditions=conditions,
        action=action, expectations=expectations, rules=rules, experiences=experiences,
        counter_refs=decision["counter_refs"], gaps=decision["gaps"],
        selection_scope="explicit originals only; completeness not asserted",
        interpretation="host-authored measurement mapping and conditional next choices",
        prospective_ignorance="not_established", execution="not_performed", adoption="not_performed")))


@bounded_read
def compare_action_feedback(workspace, *, basis_ref, execution_ref, observation_ref=None):
    """Compare normal node output against the saved action brief, without writes.

Exact JSON equality distinguishes false, zero and empty values. Missing/null
expectations or observations remain unknown. Within a prediction, any measured
contradiction yields different; otherwise an unknown check prevents match.
Failed/waiting executions remain unknown even if they contain partial outputs.
Hints are the host's previously recorded alternatives, never executor commands.
The result is a compact reusable input to the host's *next* ordinary decision.
"""
    reader = _reader(workspace)
    basis = reader.body(basis_ref)
    if basis.get("view") != "action_basis":
        raise EvidenceError("feedback requires a saved action basis")
    decision = reader.decision(basis["decision_ref"])
    rd._scope(basis, decision)
    action = next((a for a in decision["actions"] if a["action_id"] == basis["action_id"]), None)
    if action is None or action != basis["action"] or action["disposition"] != "selected":
        raise EvidenceError("saved basis does not match the selected decision action")
    selectors = [{k: row[k] for k in ("prediction_id", "ref", "pointer")} for row in basis["expectations"]]
    expectations = _expectations(reader, decision, action, selectors)
    if expectations != basis["expectations"]:
        raise EvidenceError("saved expectations differ from their original prediction basis")
    node = rd._step(reader, execution_ref, "node", decision)
    planned = rd._step(reader, action["execution_ref"], "node", decision)
    if (rd._key(action["execution_ref"]) not in rd._keys([execution_ref] + node["supersedes"])
            or node["branch_id"] != decision["branch_id"]
            or any(node["detail"][k] != planned["detail"][k] for k in ("node_id", "capability_id", "workflow_ref"))
            or any(rd._keys(node[k]) != rd._keys(planned[k]) for k in ("input_refs", "method_refs"))):
        raise EvidenceError("feedback execution differs from the selected normal action")
    state = node["detail"]["state"]
    if state in {"planned", "skipped"}:
        raise EvidenceError("unexecuted action has no normal execution feedback")
    output = None
    if observation_ref is not None:
        if rd._key(observation_ref) not in rd._keys(node["output_refs"]):
            raise EvidenceError("feedback observation must be an exact normal node output")
        output = reader.body(observation_ref)
    comparisons = []
    for row in expectations:
        checks = []
        for check in row["expectation"]["checks"]:
            observation = _pointer(output, check["pointer"]) if output is not None else {"presence": "missing", "value": None}
            known = observation["presence"] == "observed" and check["expected"] is not None
            status = "unknown" if not known else "match" if canonical(observation["value"]) == canonical(check["expected"]) else "different"
            checks.append({**check, "observation": observation, "comparison": status})
        statuses = {check["comparison"] for check in checks}
        status = "different" if "different" in statuses else "unknown" if "unknown" in statuses else "match"
        if row["prediction"]["status"] != "testable" or state not in {"completed", "reused", "partial"}:
            status = "unknown"
        comparisons.append(dict(prediction_id=row["prediction_id"], observable=row["expectation"]["observable"],
            expected_statement=row["expectation"]["expected_statement"], checks=checks, comparison=status,
            next_choice_hint=row["expectation"]["next_if_" + status]))
    return dict(view="action_feedback", basis_ref=basis_ref, decision_ref=basis["decision_ref"],
        execution_ref=execution_ref, observation_ref=observation_ref, task_id=basis["task_id"], scope=basis["scope"],
        conditions=basis["conditions"], execution_state=state, receipt_refs=node["detail"]["receipt_refs"],
        comparisons=comparisons, unresolved_prediction_ids=[row["prediction_id"] for row in comparisons if row["comparison"] == "unknown"],
        next_choice_basis=dict(evidence_refs=[basis_ref, execution_ref] + ([] if observation_ref is None else [observation_ref]),
            hints=[dict(prediction_id=row["prediction_id"], when=row["comparison"], consider=row["next_choice_hint"]) for row in comparisons],
            choice="host_decides; conflicts are not resolved automatically"),
        scientific_success="not_inferred_from_value_match", adoption="not_performed")


@bounded_read
def gather_rule_revision_context(workspace, *, feedback_ref, model_ref, prediction_ids=None):
    """Connect normal feedback to the local assumptions that produced it.

    Uses the model's existing prediction bindings and dependency edges. The
    default selects different/unknown predictions; a host may explicitly select
    a match when investigating a missed opportunity. This is an optional local
    read, not a rule gate, causal diagnosis, new proposal or command dispatcher.
    Save the result as an ordinary Artifact beside a host-authored next decision.
    """
    from . import research_model as rm

    reader = rm._Reader(workspace)
    saved = reader.body(feedback_ref)
    if saved.get("view") != "action_feedback":
        raise EvidenceError("rule revision needs normal action feedback")
    feedback = compare_action_feedback(workspace, **{k: saved[k] for k in (
        "basis_ref", "execution_ref", "observation_ref")})
    if canonical(saved) != canonical(feedback):
        raise EvidenceError("saved feedback differs from the original execution")
    model = reader.model(model_ref)
    rd._scope(model, feedback)
    comparisons = {row["prediction_id"]: row for row in feedback["comparisons"]}
    selected = list(prediction_ids) if prediction_ids is not None else [
        key for key, row in comparisons.items() if row["comparison"] != "match"]
    rm._ids(selected)
    if not set(selected) <= set(comparisons):
        raise EvidenceError("selected prediction is absent from normal feedback")
    bindings = {item["prediction_id"]: item["rule_ids"] for item in model["prediction_bindings"]
                if rd._key(item["decision_ref"]) == rd._key(feedback["decision_ref"])}
    rules = {rule["rule_id"]: rule for rule in model["rules"]}
    needed, paths, unbound = set(), [], []
    for pid in selected:
        roots = bindings.get(pid, [])
        if not roots:
            unbound.append(pid)
        closure = set()
        def visit(rid):
            if rid in closure:
                return
            closure.add(rid)
            for dep in rules[rid]["dependencies"]:
                visit(dep)
        for rid in roots:
            visit(rid)
        needed.update(closure)
        paths.append(dict(prediction_id=pid, comparison=comparisons[pid]["comparison"],
            root_rule_ids=roots, dependency_rule_ids=sorted(closure - set(roots)),
            observations=comparisons[pid]["checks"],
            prior_next_choice=comparisons[pid]["next_choice_hint"]))
    # Keep shared rules once; readers can expand cited evidence when it affects
    # the next choice instead of reinjecting all historical rule bodies.
    local = [dict(rule=rules[rid], condition_comparison=_conditions(
        rules[rid]["applicability"]["conditions"], feedback["conditions"])) for rid in sorted(needed)]
    downstream, frontier = set(), set(needed)
    while frontier:
        layer = {rid for rid, rule in rules.items() if set(rule["dependencies"]) & frontier}
        frontier = layer - needed - downstream
        downstream.update(frontier)
    issue_ids = {iid for rid in needed for iid in rules[rid]["issue_ids"]}
    return rm._seal(dict(view="rule_revision_context", task_id=model["task_id"], scope=model["scope"],
        model_ref=model_ref, feedback_ref=feedback_ref, decision_ref=feedback["decision_ref"],
        conditions=feedback["conditions"], model_condition_comparison=_conditions(
            model["conditions"], feedback["conditions"]), discrepancies=paths, rules=local,
        relevant_issues=[issue for issue in model["issues"] if issue["issue_id"] in issue_ids],
        counter_refs=rd._unique(model["counter_refs"] + [ref for rid in needed for ref in rules[rid]["counter_refs"]]),
        unbound_prediction_ids=unbound, potentially_affected_rule_ids=sorted(downstream),
        unselected_rule_ids=sorted(set(rules) - needed),
        basis_refs=rd._unique([model_ref, feedback_ref, feedback["execution_ref"]] +
            ([] if feedback["observation_ref"] is None else [feedback["observation_ref"]])),
        next_choice="host may revise an assumption, action representation or improvement procedure; continue unrelated work",
        interpretation="declared dependency paths, not proof that any listed rule caused the discrepancy",
        scientific_benefit="not_inferred", execution="not_performed", rsi_triggered=False))
