"""Optional, attributable research decisions in the existing Artifact journal.

These contracts verify identities and declared links, not scientific truth,
historical knowledge, execution permission, or adoption. No scheduler or store.
"""
from __future__ import annotations

import json
from pathlib import Path

from etalon.active.schema import canonical, digest
from .continuation import MAX_CONTENT_BYTES, _exact_refs, _fields, _list, _refs, _text
from .evidence_refs import EvidenceError, validate_claim, validate_ref
from .improvement import read_material
from .read_scope import bounded_read

DECISION = "etalon-research-decision/1"
UPDATE = "etalon-research-decision-update/1"
SCHEMAS = {DECISION, UPDATE}
MAX_CLOSURE = 128
DIMENSIONS = ("task_family", "objective_kind", "subject", "intervention", "readout")


def _key(ref):
    # Aliases do not change immutable record identity, but every alias is read.
    validate_ref(ref)
    return canonical({k: ref[k] for k in ("record_id", "record_digest", "member") if k in ref})


def _keys(refs):
    return {_key(ref) for ref in refs}


def _unique(refs):
    return list({canonical(validate_ref(r)): r for r in refs}.values())


def _strings(values):
    for value in _list(values):
        _text(value)
    if len(set(values)) != len(values):
        raise EvidenceError("duplicate research decision text")


def _input_refs(p):
    refs = p["problem_refs"] + p["support_refs"] + p["counter_refs"]
    refs += [i["ref"] for i in p["scientific_inputs"] if i["use"] != "evaluation_only"]
    refs += [r for o in p["options"] for r in o["claim_refs"]]
    refs += [r for prediction in p["predictions"] for r in prediction["basis_refs"]]
    return _unique(refs)


def _outcome(judgments, gaps):
    states = {j["status"] for j in judgments}
    if not states or states == {"not_tested"}:
        return "pending"
    if gaps or states & {"unresolved", "not_tested"}:
        return "unresolved"
    return next(iter(states)) if len(states) == 1 else "mixed"


def validate_content(payload):
    """Validate a detached <=16 KiB declaration; no source verification here."""
    if not isinstance(payload, dict) or len(canonical(payload).encode("utf-8")) > MAX_CONTENT_BYTES:
        raise EvidenceError("research decision requires an object within 16 KiB")
    schema = payload.get("schema")
    common = ("schema", "task_id", "scope", "branch_id", "method_refs", "support_refs", "counter_refs",
              "gaps", "supersedes", "scientific_truth", "adoption")
    if schema == DECISION:
        fields = ("question", "workset_ref", "problem_refs", "structure", "attribution", "options",
                  "predictions", "actions", "scientific_inputs", "input_refs", "evaluation_refs", "evidence_window_ref")
    elif schema == UPDATE:
        fields = ("decision_ref", "previous_update_ref", "execution_refs", "evaluation_refs", "experience_refs",
                  "judgments", "disposition", "summary", "outcome", "gap_resolutions")
    else:
        raise EvidenceError("unsupported research decision schema")
    _fields(payload, common + fields)
    for key in ("task_id", "scope", "branch_id"):
        _text(payload[key])
    for key, value in payload.items():
        if key.endswith("_refs") or key == "supersedes":
            _refs(value)
        elif key.endswith("_ref") and value is not None:
            validate_ref(value)
    _strings(payload["gaps"])
    if (payload["scientific_truth"], payload["adoption"]) != ("not_certified", "not_performed"):
        raise EvidenceError("research decision is host judgment, not truth or adoption")
    if schema == DECISION:
        _text(payload["question"])
        _refs(payload["problem_refs"], nonempty=True)
        structure = payload["structure"]
        _fields(structure, (*DIMENSIONS, "constraints", "unknowns"))
        _strings(structure["constraints"])
        _strings(structure["unknowns"])
        for name in DIMENSIONS:
            if structure[name] is None:
                if name not in structure["unknowns"]:
                    raise EvidenceError("unknown task dimensions must be explicit")
            else:
                _text(structure[name])
        attr = payload["attribution"]
        _fields(attr, ("actor", "basis", "model_ref", "skill_refs", "verifier_refs"))
        _text(attr["actor"])
        if attr["basis"] != "host_judgment":
            raise EvidenceError("predictions must remain attributable host judgment")
        if attr["model_ref"] is not None:
            validate_ref(attr["model_ref"])
        _refs(attr["skill_refs"])
        _refs(attr["verifier_refs"])
        options, actions, predictions = set(), set(), set()
        for option in _list(payload["options"], nonempty=True):
            _fields(option, ("option_id", "kind", "statement", "claim_refs"))
            _text(option["option_id"])
            _text(option["statement"])
            _refs(option["claim_refs"])
            if option["option_id"] in options or option["kind"] not in {"hypothesis", "option", "combined", "unknown"}:
                raise EvidenceError("duplicate option or unsupported explanation kind")
            options.add(option["option_id"])
        for action in _list(payload["actions"], nonempty=True):
            _fields(action, ("action_id", "description", "disposition", "reason", "execution_ref", "prediction_ids"))
            for key in ("action_id", "description", "reason"):
                _text(action[key])
            _strings(action["prediction_ids"])
            if action["execution_ref"] is not None:
                validate_ref(action["execution_ref"])
            if action["action_id"] in actions or action["disposition"] not in {"selected", "skipped", "deferred"}:
                raise EvidenceError("duplicate action or unsupported action disposition")
            actions.add(action["action_id"])
        for pred in _list(payload["predictions"]):
            _fields(pred, ("prediction_id", "action_id", "option_ids", "observable", "expected", "distinguishes",
                           "status", "basis_refs", "unknown_reason"))
            _text(pred["prediction_id"])
            _strings(pred["option_ids"])
            if (pred["prediction_id"] in predictions or pred["action_id"] not in actions
                    or not pred["option_ids"] or not set(pred["option_ids"]) <= options):
                raise EvidenceError("prediction must bind distinct known option and action identities")
            predictions.add(pred["prediction_id"])
            _refs(pred["basis_refs"])
            if pred["status"] == "testable":
                for key in ("observable", "expected", "distinguishes"):
                    _text(pred[key])
                if pred["unknown_reason"] is not None:
                    raise EvidenceError("testable prediction cannot hide an unknown expected difference")
            elif pred["status"] == "unknown":
                _text(pred["unknown_reason"])
                for key in ("observable", "expected", "distinguishes"):
                    if pred[key] is not None:
                        _text(pred[key])
            else:
                raise EvidenceError("unsupported prediction status")
        for action in payload["actions"]:
            expected = {p["prediction_id"] for p in payload["predictions"] if p["action_id"] == action["action_id"]}
            if set(action["prediction_ids"]) != expected:
                raise EvidenceError("action prediction links must match exactly")
        seen = set()
        for item in _list(payload["scientific_inputs"]):
            _fields(item, ("ref", "use", "reason"))
            validate_ref(item["ref"])
            _text(item["reason"])
            if item["use"] not in {"conditioning", "context", "evaluation_only"} or _key(item["ref"]) in seen:
                raise EvidenceError("scientific input needs a unique explicit visibility role")
            seen.add(_key(item["ref"]))
        if _keys(payload["input_refs"]) != _keys(_input_refs(payload)):
            raise EvidenceError("input_refs must account for all declared decision evidence")
        if _keys(payload["input_refs"]) & _keys([i["ref"] for i in payload["scientific_inputs"] if i["use"] == "evaluation_only"]):
            raise EvidenceError("evaluation-only evidence cannot condition a decision")
    else:
        _text(payload["summary"])
        if payload["disposition"] not in {"retain", "revise", "reject", "defer", "no_change"}:
            raise EvidenceError("unsupported judgment disposition")
        seen = set()
        for judgment in _list(payload["judgments"]):
            _fields(judgment, ("prediction_id", "status", "observation_refs", "evaluation_refs", "reason"))
            _text(judgment["prediction_id"])
            _text(judgment["reason"])
            if judgment["prediction_id"] in seen or judgment["status"] not in {"supported", "contradicted", "unresolved", "not_tested"}:
                raise EvidenceError("invalid or duplicate prediction judgment")
            seen.add(judgment["prediction_id"])
            for key in ("observation_refs", "evaluation_refs"):
                _refs(judgment[key], nonempty=judgment["status"] in {"supported", "contradicted"})
            if not _keys(judgment["evaluation_refs"]) <= _keys(payload["evaluation_refs"]):
                raise EvidenceError("judgment must use the update's exact evaluations")
            if judgment["status"] == "not_tested" and judgment["observation_refs"]:
                raise EvidenceError("not-tested judgment cannot contain observations")
        for resolution in _list(payload["gap_resolutions"]):
            _fields(resolution, ("gap", "basis_refs", "reason"))
            _text(resolution["gap"])
            _text(resolution["reason"])
            _refs(resolution["basis_refs"], nonempty=True)
        if payload["outcome"] != _outcome(payload["judgments"], payload["gaps"]):
            raise EvidenceError("outcome cannot turn unknown or unsupported predictions into success")
        expected = [] if payload["previous_update_ref"] is None else [payload["previous_update_ref"]]
        if payload["supersedes"] != expected:
            raise EvidenceError("update supersedes must identify its exact previous update")
    return json.loads(canonical(payload))


class _Reader:
    def __init__(self, read, workspace, *, local_journal=None, source_id=None, preflight=False):
        self.read, self.workspace = read, workspace
        self.local_journal, self.source_id, self.preflight = local_journal, source_id, preflight
        self.cache, self.active, self.checked = {}, set(), set()
        self.original_budget = {"bytes": 0}

    def material(self, ref):
        key = canonical(validate_ref(ref))
        if key not in self.cache:
            if len(self.cache) >= MAX_CLOSURE:
                raise EvidenceError("research decision reference closure exceeds bounded budget")
            material = self.read(ref)
            original = material.get("ref", ref) if "member" in ref else material["record"]
            if "sha256" in original:
                from .opportunity_state import _original_size, _paged_original, _original_json
                identity = {k: original[k] for k in ("sha256", "bytes")}
                _original_size(identity, self.original_budget)
                if not material.get("complete") or material.get("content") is None and material.get("fragment"):
                    selected = material.get("ref", ref)
                    raw = _paged_original(self.workspace, selected, identity, material.get("fragment"))
                    material = {**material, "content": _original_json(raw), "complete": True}
            self.cache[key] = material
        return self.cache[key]

    def body(self, ref, schema=None):
        material = self.material(ref)
        body = material.get("content")
        if not material.get("complete") or not isinstance(body, dict):
            raise EvidenceError("structured decision reference unavailable within bounded read")
        if schema is not None and body.get("schema") != schema:
            raise EvidenceError("wrong research decision reference schema: " + schema)
        return body

    def decision(self, ref):
        key = _key(ref)
        if key in self.active:
            raise EvidenceError("cyclic decision update ancestry")
        content = validate_content(self.body(ref))
        if key not in self.checked:
            self.active.add(key)
            _verify(self, content)
            self.active.remove(key)
            self.checked.add(key)
        return content


def _scope(value, payload):
    for key in ("task_id", "scope"):
        if key in value and value[key] != payload[key]:
            raise EvidenceError("research decision task/scope mismatch: " + key)


def _conditioning(reader, ref, seen=None):
    """Check only exposed declared lineage; not a host filesystem sandbox."""
    seen = set() if seen is None else seen
    key = _key(ref)
    if key in seen:
        return
    seen.add(key)
    material = reader.material(ref)
    record, body = material["record"], material.get("content")
    if record.get("role") in {"independent_validation", "sealed_holdout", "evaluation_only"} or material.get("protected_original"):
        raise EvidenceError("evaluation-only original or ancestor cannot condition a decision")
    if isinstance(body, dict):
        if body.get("role") == "evaluation_only":
            raise EvidenceError("evaluation-only scientific input cannot condition a decision")
        if body.get("schema") == "etalon-scientific-input/1":
            from .scientific_input import validate_input
            validation_copy = json.loads(canonical(body))
            unpublished_local = (reader.preflight and ref["source_id"] == reader.source_id and
                (reader.workspace is None or not any(s["source_id"] == ref["source_id"] for s in reader.workspace.manifest["sources"])))
            if reader.workspace is not None and not unpublished_local:
                from .evidence_refs import EvidenceResolver
                resolver = EvidenceResolver(reader.workspace, ref["source_id"])
                for source in validation_copy["sources"]:
                    source["path"] = str(resolver.resolve(source["path"], sha256=source["sha256"], bytes=source["bytes"]))
            validate_input(validation_copy, verify_files=True)
            if any(row.get("role") == "evaluation_only" for layer in ("sources", "observations", "interpretations")
                   for row in body[layer]):
                raise EvidenceError("evaluation-only data exposed in a conditioning envelope; publish a clean view")
    if isinstance(body, (dict, list)):
        for parent in _exact_refs(body):
            _conditioning(reader, parent, seen)
    for parent in _exact_refs(record.get("bindings", {})):
        _conditioning(reader, parent, seen)
    # Parent IDs are source-local; recover exact originals before following them.
    parents = record.get("parents", [])
    if parents:
        if reader.local_journal is not None and ref["source_id"] == reader.source_id:
            from .evidence_refs import make_ref
            for rid in parents:
                parent = make_ref(reader.source_id, rid, reader.local_journal.get(rid))
                _conditioning(reader, parent, seen)
            return
        from .evidence import read_evidence
        lineage = read_evidence(reader.workspace, ref, view="lineage", limit=50)
        if lineage.get("missing") or lineage.get("next_offset") is not None:
            raise EvidenceError("declared conditioning ancestors unavailable within bounded read")
        resolved = {r["record_id"]: r for r in lineage.get("items", []) if r["source_id"] == ref["source_id"]}
        for rid in parents:
            if rid not in resolved:
                raise EvidenceError("declared conditioning ancestor unavailable")
            _conditioning(reader, resolved[rid], seen)


def _step(reader, ref, operation, payload):
    from .continuation import validate_content as validate_step
    value = validate_step(reader.body(ref, "etalon-research-step/1"))
    _scope(value, payload)
    if value["operation"] != operation:
        raise EvidenceError("wrong ordinary execution/evaluation operation")
    for link in _exact_refs(value):
        reader.material(link)
    if operation == "node":
        from .schema import method_record_from_dict
        for link in value["detail"]["use_refs"]:
            use = reader.body(link, "etalon-method-use/1")
            method_record_from_dict(use)
            if not any(s["kind"] == "task" and s["id"] == value["task_id"] for s in use["subject_refs"]):
                raise EvidenceError("executed method use belongs to another task")
            if not {(link["source_id"], m["method_version_ref"]) for m in use["selected_methods"]} <= {
                    (m["source_id"], m["record_id"]) for m in value["method_refs"]}:
                raise EvidenceError("executed method use differs from node methods")
    return value


def _evaluation(reader, ref, decision):
    """Return exact evaluated outputs, attached nodes, and replay status if any."""
    value = reader.body(ref)
    _scope(value, decision)
    if value.get("schema") == "etalon-evaluation-report/1":
        from .evaluation_contract import evaluate_contract, evaluation_rules_digest
        contract = value["contract"]
        frozen = [reader.body(r) for r in decision["evaluation_refs"]]
        if not any(c.get("schema") == "etalon-evaluation-contract/1"
                   and evaluation_rules_digest(c) == evaluation_rules_digest(contract) for c in frozen):
            raise EvidenceError("evaluation report differs from the decision's exact frozen rules")
        unpublished = reader.preflight and any(r["source_id"] == reader.source_id for r in _exact_refs(value))
        if unpublished:
            # Publication's exact in-memory adapter has no durable local source
            # yet. The authoritative real-journal commit always replays below.
            replay = value
        else:
            replay = evaluate_contract(reader.workspace, contract if value["contract_ref"] is None else None,
                                       contract_ref=value["contract_ref"])
            if canonical(replay) != canonical(value):
                raise EvidenceError("evaluation report differs from verified replay")
        return [i["ref"] for i in contract["inputs"] if i["kind"] == "result"], None, replay["status"]
    step = _step(reader, ref, "evaluation", decision)
    detail = step["detail"]
    if _key(detail["plan_ref"]) not in _keys(decision["evaluation_refs"]):
        raise EvidenceError("evaluation must bind the decision's exact evaluation plan")
    plan = _step(reader, detail["plan_ref"], "evaluation_plan", decision)
    evaluation = reader.body(detail["evaluation_ref"], "etalon-method-evaluation/1")
    from .schema import method_record_from_dict
    method_record_from_dict(evaluation)
    _scope(evaluation, decision)
    raw_source = detail["evaluation_ref"]["source_id"]
    plan_originals = plan["detail"]["subjects"] + plan["detail"]["comparators"] + [
        plan["detail"][k] for k in ("protocol_ref", "task_split_ref", "initial_conditions_ref")]
    if any(r["source_id"] != raw_source for r in plan_originals):
        raise EvidenceError("raw MethodEvaluation IDs require the plan's exact local source")
    for key in ("subjects", "comparators"):
        if evaluation[key] != [r["record_id"] for r in plan["detail"][key]]:
            raise EvidenceError("evaluation methods differ from the exact plan")
    for key in ("protocol_ref", "task_split_ref", "initial_conditions_ref"):
        if evaluation[key] != plan["detail"][key]["record_id"]:
            raise EvidenceError("evaluation conditions differ from the exact plan")
    # Result IDs are backed by explicit observation refs checked below; retain
    # source identity so an equal ID from an unrelated source cannot substitute.
    return [(detail["evaluation_ref"]["source_id"], rid) for rid in evaluation["result_refs"]], detail.get("node_refs", []), None


def _verify(reader, p):
    for ref in _exact_refs(p):
        reader.material(ref)
    from .schema import method_record_from_dict
    for ref in p["method_refs"]:
        method_record_from_dict(reader.body(ref, "etalon-method-version/1"))
    if p["schema"] == DECISION:
        excluded = set()
        for item in p["scientific_inputs"]:
            if item["use"] == "evaluation_only":
                original = reader.material(item["ref"])["record"]
                if "sha256" in original:
                    excluded.add((original["sha256"], original["bytes"]))
        for ref in p["input_refs"]:
            _conditioning(reader, ref)
            original = reader.material(ref)["record"]
            if "sha256" in original and (original["sha256"], original["bytes"]) in excluded:
                raise EvidenceError("evaluation-only bytes cannot be relabelled as decision inputs")
        for option in p["options"]:
            for ref in option["claim_refs"]:
                claim = validate_claim(reader.body(ref, "etalon-research-claim/1"))
                for link in _exact_refs(claim):
                    reader.material(link)
        for item in p["scientific_inputs"]:
            material = reader.material(item["ref"])
            if isinstance(material.get("content"), dict):
                _scope(material["content"], p)
        for action in p["actions"]:
            if action["execution_ref"] is not None:
                node = _step(reader, action["execution_ref"], "node", p)
                if node["branch_id"] != p["branch_id"] or not _keys(node["method_refs"]) <= _keys(p["method_refs"]):
                    raise EvidenceError("selected execution differs from decision branch/method identities")
                if not _keys(node["input_refs"]) <= _keys(p["input_refs"]):
                    raise EvidenceError("execution inputs must appear in declared decision input_refs")
        for ref in p["evaluation_refs"]:
            value = reader.body(ref)
            _scope(value, p)
            if value.get("schema") == "etalon-evaluation-contract/1":
                from .evaluation_contract import validate_evaluation_contract
                validate_evaluation_contract(value)
            else:
                _step(reader, ref, "evaluation_plan", p)
        if p["evidence_window_ref"] is not None:
            # One timing implementation owns controlled-window semantics.
            from .evidence_window import _decision, _Reader as WindowReader
            _decision(WindowReader(reader.material), p)
        for ref in p["supersedes"]:
            old = reader.decision(ref)
            if old["schema"] != DECISION:
                raise EvidenceError("decision supersedes must reference a decision")
            _scope(old, p)
            if not _keys(old["counter_refs"]) <= _keys(p["counter_refs"]):
                raise EvidenceError("decision must retain prior counterevidence")
            if not set(old["gaps"]) <= set(p["gaps"]):
                raise EvidenceError("superseding decision must retain prior unresolved gaps")
    else:
        decision = reader.decision(p["decision_ref"])
        if decision["schema"] != DECISION:
            raise EvidenceError("update must identify an exact original decision")
        for key in ("task_id", "scope", "branch_id", "method_refs"):
            if p[key] != decision[key]:
                raise EvidenceError("update task/scope/branch/method differs from exact decision")
        prior = decision
        if p["previous_update_ref"] is not None:
            prior = reader.decision(p["previous_update_ref"])
            if prior["schema"] != UPDATE or _key(prior["decision_ref"]) != _key(p["decision_ref"]):
                raise EvidenceError("previous update belongs to a different exact decision")
        retained = decision["counter_refs"] + prior["counter_refs"]
        resolved = {r["gap"] for r in p["gap_resolutions"]}
        if not resolved <= set(prior["gaps"]) or not set(prior["gaps"]) <= set(p["gaps"]) | resolved:
            raise EvidenceError("prior gaps must remain or have explicit evidence-backed resolutions")
        if resolved & set(p["gaps"]):
            raise EvidenceError("resolved gap cannot also remain pending")
        predictions = {x["prediction_id"]: x for x in decision["predictions"]}
        if {j["prediction_id"] for j in p["judgments"]} != predictions.keys():
            raise EvidenceError("update must account for every original prediction, including unknowns")
        selected = {a["action_id"]: a for a in decision["actions"] if a["disposition"] == "selected"}
        nodes = []
        for ref in p["execution_refs"]:
            node = _step(reader, ref, "node", p)
            if node["branch_id"] != p["branch_id"] or not _keys(node["method_refs"]) <= _keys(p["method_refs"]):
                raise EvidenceError("update execution branch/method differs from decision")
            bound = {a["action_id"] for a in selected.values() if a["execution_ref"] is not None
                     and _key(a["execution_ref"]) in _keys([ref] + node["supersedes"])}
            if not bound:
                raise EvidenceError("execution must exactly bind a selected action's original node")
            for aid in bound:
                planned = _step(reader, selected[aid]["execution_ref"], "node", p)
                if (any(node["detail"][k] != planned["detail"][k] for k in ("node_id", "capability_id", "workflow_ref"))
                        or any(_keys(node[k]) != _keys(planned[k]) for k in ("input_refs", "method_refs"))):
                    raise EvidenceError("executed node changed selected action identity, inputs or methods")
            nodes.append((ref, node, bound))
        evaluations = {_key(ref): _evaluation(reader, ref, decision) for ref in p["evaluation_refs"]}
        for ref in p["experience_refs"]:
            from .improvement import validate_content as validate_experience
            experience = validate_experience(reader.body(ref, "etalon-method-experience/1"))
            _scope(experience, p)
            if not _keys(experience["method_refs"]) <= _keys(p["method_refs"]):
                raise EvidenceError("experience method differs from decision")
            if experience["evaluation_ref"] is not None:
                available = list(p["evaluation_refs"])
                for evaluation_ref in p["evaluation_refs"]:
                    evaluation = reader.body(evaluation_ref)
                    if evaluation.get("operation") == "evaluation":
                        available.append(evaluation["detail"]["evaluation_ref"])
                if _key(experience["evaluation_ref"]) not in _keys(available):
                    raise EvidenceError("experience must bind the update's exact evaluation")
            for original in _exact_refs(experience):
                reader.material(original)
            retained += experience["counter_refs"]
        for judgment in p["judgments"]:
            if judgment["status"] not in {"supported", "contradicted"}:
                continue
            prediction = predictions[judgment["prediction_id"]]
            if prediction["status"] != "testable" or prediction["action_id"] not in selected:
                raise EvidenceError("unknown or skipped predictions cannot become supported/contradicted")
            matching = [(r, n) for r, n, ids in nodes if prediction["action_id"] in ids
                        and n["detail"]["state"] in {"completed", "reused", "partial"}]
            outputs = [r for _, n in matching for r in n["output_refs"]]
            if not matching or not _keys(judgment["observation_refs"]) <= _keys(outputs):
                raise EvidenceError("judgment observations must be exact outputs of its executed action")
            for ref in judgment["evaluation_refs"]:
                observed, attributed, replay_status = evaluations[_key(ref)]
                if attributed is not None:
                    if not _keys(attributed) & _keys([r for r, _ in matching]):
                        raise EvidenceError("evaluation must attribute the exact executed node")
                    if not {(r["source_id"], r["record_id"]) for r in judgment["observation_refs"]} <= set(observed):
                        raise EvidenceError("judgment observation absent from exact evaluation results")
                elif not _keys(judgment["observation_refs"]) <= _keys(observed):
                    raise EvidenceError("judgment observation absent from evaluation contract inputs")
                if replay_status is not None and replay_status != ("criteria_passed" if judgment["status"] == "supported" else "criteria_failed"):
                    raise EvidenceError("unsupported judgment conflicts with verified evaluation status")
            if judgment["status"] == "contradicted":
                retained += judgment["observation_refs"]
        if not _keys(retained) <= _keys(p["counter_refs"]):
            raise EvidenceError("update must retain prior and newly contradicted counterevidence")
    return p


@bounded_read
def build_research_decision(workspace, *, task_id, scope, question, branch_id, problem_refs, structure,
        attribution, options, predictions, actions, workset_ref=None, method_refs=(), scientific_inputs=(),
        evaluation_refs=(), support_refs=(), counter_refs=(), evidence_window_ref=None, gaps=(), supersedes=()):
    p = dict(schema=DECISION, task_id=task_id, scope=scope, question=question, branch_id=branch_id,
             workset_ref=workset_ref, problem_refs=list(problem_refs), structure=structure, attribution=attribution,
             options=list(options), predictions=list(predictions), actions=list(actions), method_refs=list(method_refs),
             scientific_inputs=list(scientific_inputs), evaluation_refs=list(evaluation_refs), support_refs=list(support_refs),
             counter_refs=list(counter_refs), evidence_window_ref=evidence_window_ref, gaps=list(gaps),
             supersedes=list(supersedes), scientific_truth="not_certified", adoption="not_performed")
    p["input_refs"] = _input_refs(p)
    return validate_links(workspace, p)


@bounded_read
def build_decision_update(workspace, *, decision_ref, judgments, disposition, summary, previous_update_ref=None,
        execution_refs=(), evaluation_refs=(), experience_refs=(), gaps=(), gap_resolutions=(), support_refs=(), counter_refs=()):
    reader = _Reader(lambda ref: read_material(workspace, ref), workspace)
    decision = reader.decision(decision_ref)
    prior = reader.decision(previous_update_ref) if previous_update_ref is not None else decision
    counter = list(counter_refs) + decision["counter_refs"] + prior["counter_refs"]
    for judgment in judgments:
        if judgment["status"] == "contradicted":
            counter += judgment["observation_refs"]
    for ref in experience_refs:
        counter += reader.body(ref, "etalon-method-experience/1")["counter_refs"]
    resolved = {r["gap"] for r in gap_resolutions}
    gaps = list(dict.fromkeys([g for g in prior["gaps"] if g not in resolved] + list(gaps)))
    p = {k: decision[k] for k in ("task_id", "scope", "branch_id", "method_refs")}
    p.update(schema=UPDATE, decision_ref=decision_ref, previous_update_ref=previous_update_ref,
             execution_refs=list(execution_refs), evaluation_refs=list(evaluation_refs), experience_refs=list(experience_refs),
             judgments=list(judgments), disposition=disposition, summary=summary, outcome=_outcome(judgments, gaps),
             support_refs=list(support_refs), counter_refs=_unique(counter), gaps=gaps, gap_resolutions=list(gap_resolutions),
             supersedes=[] if previous_update_ref is None else [previous_update_ref],
             scientific_truth="not_certified", adoption="not_performed")
    return _verify(reader, validate_content(p))


@bounded_read
def validate_links(workspace, payload):
    return _verify(_Reader(lambda ref: read_material(workspace, ref), workspace), validate_content(payload))


@bounded_read
def read_research_decision(workspace, ref):
    """Read either decision schema, verifying the bounded exact update lineage."""
    return _Reader(lambda target: read_material(workspace, target), workspace).decision(ref)


@bounded_read
def validate_local_links(journal, content, source_id, external_workspace=None):
    """Publication adapter for record_content; no new persistent workspace/store."""
    from .evidence_refs import EvidenceWorkspace, file_identity, load_workspace
    from .schema import Artifact
    from .publication import _PreflightJournal
    preflight = isinstance(journal, _PreflightJournal)
    if not hasattr(journal, "store") and not preflight:
        raise EvidenceError("decision local validation requires a ResearchJournal or publication preflight adapter")
    manifest = {"schema": "etalon-evidence-workspace/1", "workspace_id": "decision-local-check", "sources": [], "location_map": []}
    base = None if preflight else Path(journal.store.path).absolute().parent
    if external_workspace is not None:
        external = load_workspace(external_workspace)
        base, manifest = external.base, external.manifest
        manifest["sources"] = [s for s in manifest["sources"] if s["source_id"] != source_id]
    if not preflight:
        manifest["sources"].append({"source_id": source_id, "kind": "journal", "location": str(Path(journal.store.path).absolute()),
            "identity": {"kind": "snapshot_sha256", "value": file_identity(journal.store.path)["sha256"]}})
    workspace = None if base is None else EvidenceWorkspace(base, canonical(manifest))

    def read(ref):
        validate_ref(ref)
        if ref["source_id"] != source_id or "member" in ref:
            if external_workspace is None:
                raise EvidenceError("decision external references need an explicit verification workspace")
            return read_material(external_workspace, ref)
        body = journal.get(ref["record_id"])
        if digest(body) != ref["record_digest"]:
            raise EvidenceError("decision record digest mismatch")
        if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (body.get("sha256"), body.get("bytes")):
            raise EvidenceError("decision artifact identity mismatch")
        if not {"path", "sha256", "bytes", "source_kind", "role"} <= set(body):
            return {"record": body, "content": body, "complete": True}
        path = journal.artifact_path(Artifact(**body))
        protected = False
        if not preflight:
            with journal.store.connection() as db:
                protected = db.execute("SELECT 1 FROM metadata WHERE key LIKE 'research:artifact-%' "
                    "AND json_extract(body,'$.sha256')=? AND json_extract(body,'$.bytes')=? "
                    "AND json_extract(body,'$.role') IN ('sealed_holdout','independent_validation') LIMIT 1",
                    (body["sha256"], body["bytes"])).fetchone() is not None
        if not preflight:
            from .opportunity_state import _material
            value, _, _ = _material(journal, ref, source_id)
        else:
            from .opportunity_state import _original_json, MAX_ORIGINAL_BYTES
            if body["bytes"] > MAX_ORIGINAL_BYTES:
                raise EvidenceError("original exceeds bounded 1 MiB inspection; publish narrower originals")
            value = _original_json(path.read_text(encoding="utf-8-sig").encode("utf-8"))
        return {"record": body, "content": value, "complete": True, "protected_original": protected}

    result = _verify(_Reader(read, workspace, local_journal=journal, source_id=source_id, preflight=preflight), validate_content(content))
    if result["schema"] == DECISION and result["evidence_window_ref"] is not None:
        from .evidence_window import validate_decision_window
        validate_decision_window(journal, result, source_id, external_workspace)
    return result
