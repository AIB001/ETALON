"""Optional research-model manifests and bounded representation-change reviews.

Values are ordinary Artifacts. These helpers verify existing originals and
normal-use receipts; they execute no checker, predict no scientific result, and
never create/reopen an opportunity or adopt a method. A model is an attributed
research representation, not a world model certified correct by this module.
"""
from __future__ import annotations

import json

from etalon.active.schema import canonical, digest
from .continuation import _fields, _list, _refs, _text
from .evidence_refs import EvidenceError, validate_ref
from .improvement import read_material
from .read_scope import bounded_read
from .revision_guidance import _pointer
from .schema import method_record_from_dict
from . import research_decision as rd

MODEL = "etalon-research-model/1"
REVIEW = "etalon-model-revision-review/1"
MAX_BYTES = 32768
VERDICTS = {"TRUE", "FALSE", "NA", "UNKNOWN", "ERR", "NOT_RUN"}
OPERATIONS = {"unchanged", "rename", "split", "merge", "generalize", "specialize", "withdraw", "add"}
BOUNDARY = {"scientific_truth": "not_certified", "scientific_benefit": "not_established",
            "native_execution": "not_certified", "adoption": "not_performed",
            "opportunity_reopen": "not_performed", "execution_permission": "not_granted"}


def _seal(value):
    if len(canonical(value).encode("utf-8")) > MAX_BYTES:
        raise EvidenceError("research model/review exceeds 32 KiB; select a smaller explicit scope")
    return json.loads(canonical(value))


def _ids(values, *, nonempty=False):
    for value in _list(values, nonempty=nonempty):
        _text(value)
    if len(values) != len(set(values)):
        raise EvidenceError("duplicate stable identity")


def _conditions(value):
    if not isinstance(value, dict) or not value:
        raise EvidenceError("explicit model applicability conditions required")
    for key, item in value.items():
        _text(key)
        if item is not None:
            _text(item)


def rule_digest(rule):
    """Identity of rule semantics/executable references, independent of its label.

    Equal digests establish equal declared content, not semantic equivalence.
    Changing a rule's ID or evidence annotations alone cannot change this digest.
    """
    return digest({key: value for key, value in rule.items()
                   if key not in {"rule_id", "support_refs", "counter_refs", "issue_ids"}})


def validate_research_model(value):
    _fields(value, ("schema", "model_id", "task_id", "scope", "conditions", "unknowns", "rules", "issues",
                    "method_refs", "prediction_bindings", "support_refs", "counter_refs", "supersedes", "boundary"))
    if value["schema"] != MODEL or value["boundary"] != BOUNDARY:
        raise EvidenceError("wrong research model schema or authority boundary")
    for key in ("model_id", "task_id", "scope"):
        _text(value[key])
    _conditions(value["conditions"])
    _ids(value["unknowns"])
    if not {k for k, v in value["conditions"].items() if v is None} <= set(value["unknowns"]):
        raise EvidenceError("unknown conditions must remain explicit")
    for key in ("method_refs", "support_refs", "counter_refs", "supersedes"):
        _refs(value[key])
    issue_ids = set()
    for issue in _list(value["issues"]):
        _fields(issue, ("issue_id", "statement", "witness_refs", "status", "resolution_refs"))
        _text(issue["issue_id"]); _text(issue["statement"])
        if issue["issue_id"] in issue_ids or issue["status"] not in {"unresolved", "resolved"}:
            raise EvidenceError("duplicate issue or unsupported issue status")
        issue_ids.add(issue["issue_id"])
        _refs(issue["witness_refs"], nonempty=True)
        _refs(issue["resolution_refs"], nonempty=issue["status"] == "resolved")
        if issue["status"] == "unresolved" and issue["resolution_refs"]:
            raise EvidenceError("unresolved issue cannot declare a resolution")
    rules = {}
    for rule in _list(value["rules"], nonempty=True):
        _fields(rule, ("rule_id", "statement", "judgment_kind", "status", "applicability", "checker_ref",
                       "predictor_ref", "verdict_map", "dependencies", "support_refs", "counter_refs", "issue_ids"))
        _text(rule["rule_id"]); _text(rule["statement"])
        if rule["rule_id"] in rules or rule["status"] not in {"active", "withdrawn"}:
            raise EvidenceError("duplicate rule or unsupported rule status")
        if rule["judgment_kind"] not in {"integrity", "diagnosis_prediction", "method_quality"}:
            raise EvidenceError("diagnostic prediction and method quality require distinct judgment kinds")
        rules[rule["rule_id"]] = rule
        _fields(rule["applicability"], ("conditions", "unknowns"))
        _conditions(rule["applicability"]["conditions"])
        _ids(rule["applicability"]["unknowns"])
        if not {k for k, v in rule["applicability"]["conditions"].items() if v is None} <= set(rule["applicability"]["unknowns"]):
            raise EvidenceError("unknown rule applicability must remain explicit")
        for key in ("checker_ref", "predictor_ref"):
            if rule[key] is not None:
                validate_ref(rule[key])
        _ids(rule["dependencies"]); _ids(rule["issue_ids"])
        if not set(rule["issue_ids"]) <= issue_ids:
            raise EvidenceError("rule names an unknown stable issue")
        for key in ("support_refs", "counter_refs"):
            _refs(rule[key])
        seen = set()
        for row in _list(rule["verdict_map"]):
            _fields(row, ("value", "verdict"))
            if not isinstance(row["value"], (str, bool)) or row["verdict"] not in VERDICTS - {"NOT_RUN"}:
                raise EvidenceError("verdict mappings require explicit string/bool observations")
            key = canonical(row["value"])
            if key in seen:
                raise EvidenceError("ambiguous recorded verdict mapping")
            seen.add(key)
    active, done = set(), set()
    def visit(rid):
        if rid not in rules:
            raise EvidenceError("rule dependency is unavailable")
        if rid in active:
            raise EvidenceError("cyclic rule dependencies")
        if rid in done:
            return
        active.add(rid)
        for dep in rules[rid]["dependencies"]:
            if rules[rid]["status"] == "active" and rules.get(dep, {}).get("status") == "withdrawn":
                raise EvidenceError("active rule depends on a withdrawn rule")
            visit(dep)
        active.remove(rid); done.add(rid)
    for rid in rules:
        visit(rid)
    seen = set()
    for binding in _list(value["prediction_bindings"]):
        _fields(binding, ("decision_ref", "prediction_id", "rule_ids"))
        validate_ref(binding["decision_ref"]); _text(binding["prediction_id"])
        _ids(binding["rule_ids"], nonempty=True)
        if not set(binding["rule_ids"]) <= set(rules):
            raise EvidenceError("prediction depends on an unknown rule")
        key = (rd._key(binding["decision_ref"]), binding["prediction_id"])
        if key in seen:
            raise EvidenceError("duplicate prediction binding")
        seen.add(key)
    return _seal(value)


class _Reader(rd._Reader):
    """Reuse the existing paged, original-verified and budgeted decision reader."""
    def __init__(self, workspace):
        super().__init__(lambda ref: read_material(workspace, ref), workspace)
        self.models, self.model_active = {}, set()

    def model(self, ref):
        key = rd._key(ref)
        if key in self.model_active:
            raise EvidenceError("cyclic research model ancestry")
        if key not in self.models:
            self.model_active.add(key)
            self.models[key] = _verify_model(self, self.body(ref, MODEL))
            self.model_active.remove(key)
        return self.models[key]

    def identity(self, ref):
        material = self.material(ref)
        original = material["ref"] if "member" in ref else material["record"]
        return ("bytes", original["sha256"], original["bytes"]) if "sha256" in original else (
            "record", digest(material["record"]))


def _evidence(value):
    refs = value["support_refs"] + value["counter_refs"]
    refs += [ref for r in value["rules"] for key in ("support_refs", "counter_refs") for ref in r[key]]
    refs += [ref for i in value["issues"] for key in ("witness_refs", "resolution_refs") for ref in i[key]]
    return rd._unique(refs)


def _counters(value):
    return rd._unique(value["counter_refs"] + [ref for rule in value["rules"] for ref in rule["counter_refs"]])


def _verify_model(reader, value):
    value = validate_research_model(value)
    for ref in _evidence(value):
        reader.material(ref)
    for ref in value["method_refs"]:
        method_record_from_dict(reader.body(ref, "etalon-method-version/1"))
    for rule in value["rules"]:
        for field in ("checker_ref", "predictor_ref"):
            if rule[field] is not None:
                material = reader.material(rule[field])
                if not material["complete"]:
                    raise EvidenceError("checker/predictor content is incomplete")
                content = material.get("content")
                if isinstance(content, dict) and content.get("schema") == "etalon-method-version/1":
                    method_record_from_dict(content)
                    fixed = content["content_ref"]
                    if set(fixed) != {"artifact_ref"} or not isinstance(fixed["artifact_ref"], str) or not fixed["artifact_ref"].startswith("artifact-"):
                        raise EvidenceError("checker/predictor MethodVersion needs an immutable Artifact content snapshot")
                    rid = fixed["artifact_ref"]
                    reader.material(dict(schema="etalon-evidence-ref/1", source_id=rule[field]["source_id"],
                                         record_id=rid, record_digest=rid.removeprefix("artifact-")))
                elif "sha256" not in material["record"]:
                    raise EvidenceError("checker/predictor must identify an immutable Artifact or MethodVersion")
    for binding in value["prediction_bindings"]:
        decision = reader.decision(binding["decision_ref"])
        if decision["schema"] != rd.DECISION or any(decision[k] != value[k] for k in ("task_id", "scope")):
            raise EvidenceError("prediction binding belongs to another task/scope or an update")
        if binding["prediction_id"] not in {p["prediction_id"] for p in decision["predictions"]}:
            raise EvidenceError("prediction binding names an unknown decision prediction")
    issues = {i["issue_id"]: i for i in value["issues"]}
    for ref in value["supersedes"]:
        old = reader.model(ref)
        if any(old[k] != value[k] for k in ("model_id", "task_id", "scope")):
            raise EvidenceError("model successor must retain model/task/scope identity")
        if not {reader.identity(r) for r in _counters(old)} <= {reader.identity(r) for r in _counters(value)}:
            raise EvidenceError("model successor must retain prior counterevidence")
        for issue in old["issues"]:
            later = issues.get(issue["issue_id"])
            if later is None or not {reader.identity(r) for r in issue["witness_refs"]} <= {
                    reader.identity(r) for r in later["witness_refs"]}:
                raise EvidenceError("model successor cannot rename/drop an issue or its witnesses")
            if issue["status"] == "unresolved" and later["status"] == "resolved":
                prior_witnesses = {reader.identity(r) for r in issue["witness_refs"]}
                if {reader.identity(r) for r in later["resolution_refs"]} <= prior_witnesses:
                    raise EvidenceError("relabelled original witnesses alone cannot resolve their own issue")
    return value


@bounded_read
def build_research_model(workspace, *, model_id, task_id, scope, conditions, rules, issues=(), method_refs=(),
                         prediction_bindings=(), support_refs=(), counter_refs=(), supersedes=(), unknowns=()):
    """Build an optional immutable manifest; no recording, checker execution or adoption."""
    value = dict(schema=MODEL, model_id=model_id, task_id=task_id, scope=scope, conditions=conditions,
        unknowns=list(unknowns), rules=list(rules), issues=list(issues), method_refs=list(method_refs),
        prediction_bindings=list(prediction_bindings), support_refs=list(support_refs), counter_refs=list(counter_refs),
        supersedes=list(supersedes), boundary=BOUNDARY)
    return _verify_model(_Reader(workspace), value)


@bounded_read
def read_research_model(workspace, ref):
    return _Reader(workspace).model(ref)


def _assessment(reader, row, rules, case_ref, model):
    _fields(row, ("rule_id", "verdict", "observation_ref", "pointer", "execution_ref"))
    if row["rule_id"] not in rules or row["verdict"] not in VERDICTS:
        raise EvidenceError("assessment needs a known rule and explicit verdict")
    if row["verdict"] == "NOT_RUN":
        if any(row[k] is not None for k in ("observation_ref", "pointer", "execution_ref")):
            raise EvidenceError("NOT_RUN cannot claim an observation or execution")
        return row
    for key in ("observation_ref", "execution_ref"):
        validate_ref(row[key])
    rule = rules[row["rule_id"]]
    if rule["checker_ref"] is None or rule["status"] == "withdrawn":
        raise EvidenceError("withdrawn or unchecked rule cannot claim a measured verdict")
    node = rd._step(reader, row["execution_ref"], "node", model)
    if node["task_id"] != model["task_id"] or node["operation"] != "node" or node["detail"]["state"] not in {"completed", "reused", "partial"}:
        raise EvidenceError("assessment requires an existing terminal/partial normal-use node")
    if reader.identity(case_ref) not in {reader.identity(r) for r in node["input_refs"]}:
        raise EvidenceError("assessment case is absent from execution inputs")
    if rd._key(row["observation_ref"]) not in rd._keys(node["output_refs"]):
        raise EvidenceError("assessment observation is absent from execution outputs")
    if not node["detail"]["receipt_refs"]:
        raise EvidenceError("assessment execution has no receipt")
    if rd._key(rule["checker_ref"]) not in rd._keys(node["method_refs"] + node["input_refs"]):
        raise EvidenceError("assessment execution does not bind the frozen checker")
    if row["verdict"] in {"TRUE", "FALSE"} and any(v is None for v in rule["applicability"]["conditions"].values()):
        raise EvidenceError("unknown applicability cannot become a true/false scientific judgment")
    for ref in node["detail"]["receipt_refs"]:
        reader.material(ref)
    observed = _pointer(reader.body(row["observation_ref"]), row["pointer"])
    if observed["presence"] != "observed":
        raise EvidenceError("missing/null observations are not recorded verdicts")
    mapped = {canonical(r["value"]): r["verdict"] for r in rule["verdict_map"]}.get(canonical(observed["value"]))
    if mapped != row["verdict"]:
        raise EvidenceError("verdict differs from the frozen mapping of exact observed bytes")
    return row


@bounded_read
def recorded_rule_assessment(workspace, *, model_ref, rule_id, case_ref, observation_ref, pointer, execution_ref):
    """Derive a verdict from an already recorded output, never accept a claimed pass.

    The frozen model must map the exact selected value. Missing values, absent
    execution attribution or unknown applicability never manufacture TRUE/FALSE.
    """
    reader = _Reader(workspace)
    model = reader.model(model_ref)
    rules = {r["rule_id"]: r for r in model["rules"]}
    if rule_id not in rules:
        raise EvidenceError("unknown rule identity")
    value = _pointer(reader.body(observation_ref), pointer)
    verdict = {canonical(r["value"]): r["verdict"] for r in rules[rule_id]["verdict_map"]}.get(canonical(value["value"]))
    if value["presence"] != "observed" or verdict is None:
        raise EvidenceError("selected original has no declared verdict mapping; keep the comparison unresolved")
    return _assessment(reader, dict(rule_id=rule_id, verdict=verdict, observation_ref=observation_ref,
                                   pointer=pointer, execution_ref=execution_ref), rules, case_ref, model)


def _mapping(before, after, mappings, revision_kind):
    seen_before, seen_after = set(), set()
    for row in _list(mappings, nonempty=True):
        _fields(row, ("before_rule_ids", "after_rule_ids", "operation", "reason"))
        _ids(row["before_rule_ids"]); _ids(row["after_rule_ids"]); _text(row["reason"])
        old, new, op = row["before_rule_ids"], row["after_rule_ids"], row["operation"]
        if op not in OPERATIONS or not old and not new:
            raise EvidenceError("unknown or empty representation mapping")
        if not set(old) <= set(before) or not set(new) <= set(after) or seen_before & set(old) or seen_after & set(new):
            raise EvidenceError("representation mapping has unknown or duplicated rules")
        if (op == "add" and (old or not new) or op == "withdraw" and (not old or new)
                or op not in {"add", "withdraw"} and (not old or not new)):
            raise EvidenceError("representation operation has inconsistent sides")
        if op == "split" and (len(old) != 1 or len(new) < 2) or op == "merge" and (len(old) < 2 or len(new) != 1):
            raise EvidenceError("split/merge requires its explicit many-to-one mapping")
        if op in {"unchanged", "rename"}:
            if len(old) != 1 or len(new) != 1 or rule_digest(before[old[0]]) != rule_digest(after[new[0]]):
                raise EvidenceError("unchanged/rename cannot hide changed rule content")
            if op == "unchanged" and old != new or op == "rename" and old == new:
                raise EvidenceError("rename and unchanged have distinct stable-ID meanings")
        if revision_kind == "refactor":
            if op in {"add", "withdraw", "generalize", "specialize"}:
                raise EvidenceError("changed rule coverage is a knowledge revision, not a refactor")
            for a in old:
                for b in new:
                    if any(before[a][k] != after[b][k] for k in ("judgment_kind", "applicability", "verdict_map", "status")):
                        raise EvidenceError("refactor cannot silently change applicability or judgment standards")
        seen_before.update(old); seen_after.update(new)
    if seen_before != set(before) or seen_after != set(after):
        raise EvidenceError("mapping must account for every old and new rule")


@bounded_read
def review_model_revision(workspace, *, before_ref, after_ref, revision_kind, mappings, assessments=(),
                          evidence_refs=(), decision_refs=()):
    """Review preserved witnesses and explicit changes without opening an RSI episode.

    An assessment uses a frozen rule verdict_map and exact existing node outputs.
    It certifies recorded attribution only, never independently certifies native
    execution or scientific truth. Absence of execution remains NOT_RUN.
    Same-evidence reinterpretation is permitted; a new model's bytes are not new
    scientific evidence and this report cannot satisfy an opportunity reopen.
    """
    if revision_kind not in {"refactor", "knowledge_revision"}:
        raise EvidenceError("explicit refactor or knowledge_revision required")
    reader = _Reader(workspace)
    old, new = reader.model(before_ref), reader.model(after_ref)
    # No mechanical issue-resolution certifier exists here. A host declaration
    # cannot erase the obligation by passing through an intermediate manifest.
    historical_unresolved = {i["issue_id"] for model in reader.models.values()
                             for i in model["issues"] if i["status"] == "unresolved"}
    if any(old[k] != new[k] for k in ("model_id", "task_id", "scope")):
        raise EvidenceError("representation comparison requires the same model/task/scope")
    if rd._key(before_ref) not in rd._keys(new["supersedes"]):
        raise EvidenceError("after model must explicitly supersede the exact before model")
    if revision_kind == "refactor" and (old["conditions"], old["unknowns"]) != (new["conditions"], new["unknowns"]):
        raise EvidenceError("refactor cannot silently change model applicability")
    before, after = ({r["rule_id"]: r for r in model["rules"]} for model in (old, new))
    _mapping(before, after, list(mappings), revision_kind)
    rows, gaps, losses, obligations = [], [], [], []
    assessed = {"before": set(), "after": set()}
    seen, cases, witnessed_pairs = set(), set(), set()
    for item in _list(list(assessments)):
        _fields(item, ("case_id", "case_ref", "role", "before", "after"))
        _text(item["case_id"]); validate_ref(item["case_ref"])
        if item["role"] not in {"positive", "counterexample"} or item["case_id"] in cases:
            raise EvidenceError("duplicate witness case or unknown role")
        cases.add(item["case_id"])
        identity = reader.identity(item["case_ref"])
        if identity in seen:
            raise EvidenceError("renamed/aliased witness cannot inflate coverage")
        seen.add(identity)
        sides = {}
        for side, rules, model in (("before", before, old), ("after", after, new)):
            side_rows = _list(item[side])
            _ids([r["rule_id"] for r in side_rows])
            sides[side] = [_assessment(reader, r, rules, item["case_ref"], model) for r in side_rows]
            assessed[side].update(r["rule_id"] for r in side_rows)
        selected_old = {r["rule_id"] for r in sides["before"]}
        selected_new = {r["rule_id"] for r in sides["after"]}
        touched = [m for m in mappings if set(m["before_rule_ids"]) & selected_old or set(m["after_rule_ids"]) & selected_new]
        if selected_old != {r for m in touched for r in m["before_rule_ids"]} or selected_new != {
                r for m in touched for r in m["after_rule_ids"]}:
            raise EvidenceError("witness comparison must cover complete mapped rule groups")
        witnessed_pairs.update((rid, identity) for rid in selected_old)
        # Different mapping groups cannot lend each other rejections or positives.
        for group in touched:
            old_verdicts = {r["verdict"] for r in sides["before"] if r["rule_id"] in group["before_rule_ids"]}
            new_verdicts = {r["verdict"] for r in sides["after"] if r["rule_id"] in group["after_rule_ids"]}
            label = item["case_id"] + ": " + ",".join(group["before_rule_ids"])
            if old_verdicts & {"NOT_RUN", "UNKNOWN", "ERR"} or new_verdicts & {"NOT_RUN", "UNKNOWN", "ERR"}:
                gaps.append(label + ": incomplete recorded comparison")
            if "FALSE" in old_verdicts:
                associated = {iid for rid in group["before_rule_ids"] for iid in before[rid]["issue_ids"]}
                related = [i["issue_id"] for i in old["issues"] if i["issue_id"] in associated
                    and identity in {reader.identity(r) for r in i["witness_refs"]}]
                obligations.append(dict(case_id=item["case_id"], case_ref=item["case_ref"], issue_ids=related,
                    before_rule_ids=group["before_rule_ids"], after_rule_ids=group["after_rule_ids"],
                    status="retained_counterexample" if "FALSE" in new_verdicts else "review_required",
                    reason="Historical false verdict remains evidence; NA, withdrawal or a changed rule is not a resolved cause"))
                if "FALSE" not in new_verdicts:
                    losses.append(label + ": prior rejection not preserved")
            if "TRUE" in old_verdicts and "TRUE" not in new_verdicts:
                losses.append(label + ": prior positive coverage not preserved")
        rows.append({**item, **sides})
    witness_refs = rd._unique([r["case_ref"] for r in rows])
    required = rd._unique(_counters(old) + [r for i in old["issues"] if i["issue_id"] in historical_unresolved for r in i["witness_refs"]])
    missing_counters = [ref for ref in required if reader.identity(ref) not in seen]
    if missing_counters:
        gaps.append("prior counterevidence/unresolved witnesses lack an explicit assessment")
    if not rows:
        gaps.append("no recorded witness comparisons")
    missing_rules = {side: sorted(set(rules) - assessed[side]) for side, rules in (("before", before), ("after", after))}
    if any(missing_rules.values()):
        gaps.append("some mapped rules lack an explicit witness comparison")
    old_issues = {i["issue_id"]: i for i in old["issues"]}
    missing_pairs, attributed_counters = [], set()
    for rule in old["rules"]:
        attributed_counters.update(reader.identity(r) for r in rule["counter_refs"])
        required_pairs = [("support", r) for r in rule["support_refs"]] + [("counterexample", r) for r in rule["counter_refs"]]
        required_pairs += [("unresolved_issue", r) for iid in rule["issue_ids"] if iid in historical_unresolved
                           for r in old_issues[iid]["witness_refs"]]
        for kind, ref in required_pairs:
            if (rule["rule_id"], reader.identity(ref)) not in witnessed_pairs:
                missing_pairs.append(dict(rule_id=rule["rule_id"], witness_ref=ref, obligation=kind))
    unassigned = [r for r in old["counter_refs"] if reader.identity(r) not in attributed_counters]
    if missing_pairs:
        gaps.append("declared rule/witness pairs lack an explicit comparison")
    if unassigned:
        gaps.append("model-level counterevidence lacks explicit rule attribution")
    for ref in decision_refs:
        decision = reader.decision(ref)
        if any(decision[k] != new[k] for k in ("task_id", "scope")):
            raise EvidenceError("affected decision belongs to another task/scope")
    _refs(list(evidence_refs)); _refs(list(decision_refs))
    for ref in evidence_refs:
        content = reader.material(ref).get("content")
        if isinstance(content, dict) and content.get("schema") in {MODEL, REVIEW, "etalon-method-version/1"}:
            raise EvidenceError("model/method/review content is not new observed evidence")
    from .opportunity_state import _origins
    origins_before = _origins(None, _evidence(old), None, workspace)
    origins_after = _origins(None, rd._unique(_evidence(new) + list(evidence_refs) + witness_refs), None, workspace)
    old_keys = {canonical(i) for i in origins_before}
    novel = [i for i in origins_after if canonical(i) not in old_keys]
    changed = sorted(rule_digest(r) for r in before.values()) != sorted(rule_digest(r) for r in after.values())
    status = "coverage_changed" if losses else "incomplete" if gaps else "preserved_on_declared_witnesses"
    return _seal(dict(schema=REVIEW, task_id=new["task_id"], scope=new["scope"], model_id=new["model_id"],
        before_ref=before_ref, after_ref=after_ref, revision_kind=revision_kind, mappings=list(mappings), assessments=rows,
        evidence_refs=list(evidence_refs), decision_refs=list(decision_refs), retained_counter_refs=_counters(new),
        retained_issues=new["issues"], counterexample_obligations=obligations, gaps=gaps, coverage_losses=losses,
        unresolved_issue_ids=sorted(historical_unresolved | {i for item in obligations for i in item["issue_ids"]}),
        issue_resolution_claims=[dict(issue_id=i["issue_id"], resolution_refs=i["resolution_refs"],
            verification="host_declared_not_certified", closes_counterexample_obligation=False)
            for i in new["issues"] if i["status"] == "resolved"],
        coverage=dict(scope="explicit unique witnesses only", case_count=len(rows), missing_counter_refs=missing_counters,
                      missing_rule_ids=missing_rules, missing_rule_witnesses=missing_pairs, unassigned_model_counter_refs=unassigned,
                      status=status, completeness="not_a_global_coverage_claim"),
        declared_rule_content_changed=changed, evidence_novelty="new_originals_not_scientific_novelty" if novel else "none",
        new_original_identities=novel, reinterpretation="same_evidence_allowed_without_reopening",
        reopen_permission="not_granted; existing terminal and new-original rules remain authoritative", boundary=BOUNDARY))


@bounded_read
def read_model_revision(workspace, ref):
    """Rebuild a saved review from exact original arguments; reject edited claims."""
    value = _Reader(workspace).body(ref, REVIEW)
    rebuilt = review_model_revision(workspace, **{k: value[k] for k in (
        "before_ref", "after_ref", "revision_kind", "mappings", "assessments", "evidence_refs", "decision_refs")})
    if value != rebuilt:
        raise EvidenceError("model revision review differs from verified originals")
    return rebuilt
