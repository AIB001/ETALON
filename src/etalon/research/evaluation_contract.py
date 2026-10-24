"""Optional declarative checks over published, exact evidence references.

This is numeric/identity rule replay, not a scientific oracle, scheduler, adoption
policy, or execution gate. Rules use JSON pointers and a small fixed operation
set; they cannot import code or execute a supplied expression.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import operator

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, validate_ref
from .method_contract import _fields, _pointer, _text, _MISSING
from .read_scope import bounded_read

CONTRACT = "etalon-evaluation-contract/1"
REPORT = "etalon-evaluation-report/1"
FAMILIES = ("regression", "retention", "funnel", "cost")
STATUSES = {"criteria_passed", "criteria_failed", "insufficient", "incomparable"}
_OPS = {"le": operator.le, "lt": operator.lt, "ge": operator.ge,
        "gt": operator.gt, "eq": operator.eq}
_REF_FIELDS = ("protocol_ref", "initial_conditions_ref", "method_contract_ref", "plan_ref", "evaluation_ref")
MAX_INPUT_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


def _list(value, *, nonempty=False):
    if not isinstance(value, list) or len(value) > 64 or nonempty and not value:
        raise EvidenceError("expected bounded explicit evaluation list")
    return value


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _refs(value, *, nonempty=False):
    for ref in _list(value, nonempty=nonempty):
        validate_ref(ref)
    if len({canonical(r) for r in value}) != len(value):
        raise EvidenceError("duplicate evaluation reference")


def _selector(value, names):
    _fields(value, ("input", "pointer"), ("reducer",))
    if value["input"] not in names:
        raise EvidenceError("selector names an undeclared input")
    _pointer({}, value["pointer"])
    if "reducer" in value and value["reducer"] != "count":
        raise EvidenceError("only explicit array count is a supported selector reducer")


def _measure(value, names):
    _fields(value, ("operation", "left", "right"))
    if value["operation"] not in {"value", "difference", "abs_difference", "ratio"}:
        raise EvidenceError("unsupported declarative measure operation")
    _selector(value["left"], names)
    if value["operation"] == "value":
        if value["right"] is not None:
            raise EvidenceError("value measure has no right operand")
    else:
        _selector(value["right"], names)


def build_evaluation_contract(*, task_id, scope, conditions, protocol_ref,
        initial_conditions_ref, inputs, metrics, comparability, required_checks,
        provenance, subjects=(), comparators=(), method_contract_ref=None,
        plan_ref=None, evaluation_ref=None):
    """Build a declaration without reading, freezing, executing or adopting.

    Inputs: {name, ref, kind=result|accounting|context}. Checks:
    {check_id, selector: {input, pointer}, expected}. Every result/accounting
    input must have an explicit required check (e.g. execution completeness).
    Metrics: {name, family, measure, operator, threshold, unit, denominator,
    denominator_selector, missing_policy='insufficient'}. A denominator selector
    is required and must resolve to a positive finite count/exposure. It records
    the cohort/exposure; it does not silently divide the metric.
    """
    return validate_evaluation_contract(dict(schema=CONTRACT, task_id=task_id,
        scope=scope, conditions=conditions, protocol_ref=protocol_ref,
        initial_conditions_ref=initial_conditions_ref, subjects=list(subjects),
        comparators=list(comparators), inputs=inputs, metrics=metrics,
        comparability=comparability, required_checks=required_checks,
        provenance=provenance, method_contract_ref=method_contract_ref,
        plan_ref=plan_ref, evaluation_ref=evaluation_ref))


def validate_evaluation_contract(payload):
    _fields(payload, ("schema", "task_id", "scope", "conditions", "subjects", "comparators",
        "inputs", "metrics", "comparability", "required_checks", "provenance", *_REF_FIELDS))
    if payload["schema"] != CONTRACT:
        raise EvidenceError("wrong evaluation contract schema")
    for key in ("task_id", "scope"):
        _text(payload[key])
    if not isinstance(payload["conditions"], dict) or not payload["conditions"]:
        raise EvidenceError("explicit evaluation conditions required")
    for key, value in payload["conditions"].items():
        _text(key)
        _text(value)
    for key in _REF_FIELDS:
        if payload[key] is not None:
            validate_ref(payload[key])
        elif key in {"protocol_ref", "initial_conditions_ref"}:
            raise EvidenceError("exact protocol and initial conditions required")
    for key in ("subjects", "comparators"):
        _refs(payload[key])
    if bool(payload["subjects"]) != bool(payload["comparators"]):
        raise EvidenceError("method comparison needs subjects and comparators")
    if {canonical(r) for r in payload["subjects"]} & {canonical(r) for r in payload["comparators"]}:
        raise EvidenceError("subjects and comparators must differ")
    provenance = payload["provenance"]
    _fields(provenance, ("mode", "note", "basis_refs"))
    if provenance["mode"] not in {"retrospective", "predeclared"}:
        raise EvidenceError("explicit retrospective or predeclared provenance required")
    _text(provenance["note"])
    _refs(provenance["basis_refs"], nonempty=provenance["mode"] == "predeclared")
    names = set()
    for item in _list(payload["inputs"], nonempty=True):
        _fields(item, ("name", "ref", "kind"))
        _text(item["name"])
        if item["name"] in names or item["kind"] not in {"result", "accounting", "context"}:
            raise EvidenceError("duplicate input or unknown input kind")
        names.add(item["name"])
        validate_ref(item["ref"])
    checks = set()
    for group in ("comparability", "required_checks"):
        for check in _list(payload[group], nonempty=True):
            _fields(check, ("check_id", "selector", "expected"))
            _text(check["check_id"])
            if check["check_id"] in checks or check["expected"] is None:
                raise EvidenceError("duplicate check or unknown expected check value")
            checks.add(check["check_id"])
            _selector(check["selector"], names)
    covered = {c["selector"]["input"] for c in payload["required_checks"]}
    if any(i["kind"] != "context" and i["name"] not in covered for i in payload["inputs"]):
        raise EvidenceError("each result/accounting input needs an explicit required check")
    metric_names = set()
    for metric in _list(payload["metrics"], nonempty=True):
        _fields(metric, ("name", "family", "measure", "operator", "threshold", "unit",
                         "denominator", "denominator_selector", "missing_policy"))
        for key in ("name", "unit", "denominator"):
            _text(metric[key])
        if metric["name"] in metric_names or metric["family"] not in FAMILIES:
            raise EvidenceError("duplicate metric or unknown metric family")
        metric_names.add(metric["name"])
        if metric["operator"] not in _OPS or not _number(metric["threshold"]):
            raise EvidenceError("finite numeric threshold and fixed comparator required")
        if metric["missing_policy"] != "insufficient":
            raise EvidenceError("missing values cannot be dropped, imputed or passed")
        _measure(metric["measure"], names)
        _selector(metric["denominator_selector"], names)
    if len(canonical(payload).encode("utf-8")) > 65536:
        raise EvidenceError("evaluation contract exceeds 64 KiB")
    return json.loads(canonical(payload))


def _aggregate(statuses):
    # All reasons remain in the report even when one status takes precedence.
    for status in ("incomparable", "criteria_failed", "insufficient"):
        if status in statuses:
            return status
    return "criteria_passed" if statuses else "not_declared"


def evaluation_rules_digest(contract):
    # Future panels can retain the exact rules while naming new observations.
    value = {k: v for k, v in contract.items() if k not in {
        "inputs", "provenance", "evaluation_ref", "method_contract_ref", "plan_ref"}}
    value["inputs"] = [{"name": i["name"], "kind": i["kind"]} for i in contract["inputs"]]
    return digest(value)


@bounded_read
def evaluate_contract(workspace=None, contract=None, *, contract_ref=None, entry=None, read_context=None):
    """Read exact published originals and replay fixed rules, without writes.

    Exactly one of contract (inline declaration) or contract_ref (ordinary JSON
    Artifact) is required. Inline identity is a digest, not a prospective freeze.
    Visibility/purpose and original-byte checks use the existing evidence reader.
    """
    from .entry import resolve_read_workspace
    from .improvement import read_material
    from .evidence import read_evidence
    from .schema import method_record_from_dict
    from .continuation import validate_content
    from .method_contract import validate_contract

    workspace = resolve_read_workspace(workspace, entry=entry, read_context=read_context)
    if (contract is None) == (contract_ref is None):
        raise EvidenceError("supply exactly one contract or contract_ref")
    cache, verified, issues = {}, [], []
    total_bytes = 0

    def read(ref):
        nonlocal total_bytes
        key = canonical(validate_ref(ref))
        if key not in cache:
            try:
                material = read_material(workspace, ref)
                fragment = material.get("fragment") or {}
                size = fragment.get("total_bytes", material["record"].get("bytes", 0))
                total_bytes += size
                if size > MAX_INPUT_BYTES or total_bytes > MAX_TOTAL_BYTES:
                    raise EvidenceError("bounded material incomplete; input exceeds byte budget")
                if not material["complete"] and fragment.get("encoding") == "base64":
                    # Page only this already selected, verified published object.
                    # Every page reuses the evidence reader's visibility and byte
                    # identity checks; no resolver/database bypass is introduced.
                    chunks = [base64.b64decode(fragment["content"])]
                    offset = fragment["next_offset"]
                    while offset is not None:
                        page = read_evidence(workspace, material["ref"], view="artifact", offset=offset, limit=32768)
                        part = page.get("fragment", {})
                        if page.get("verification", {}).get("artifact") != "verified" or part.get("offset") != offset:
                            raise EvidenceError("artifact page unavailable or unverified")
                        chunks.append(base64.b64decode(part["content"]))
                        following = part["next_offset"]
                        if following is not None and following <= offset:
                            raise EvidenceError("artifact pagination did not advance")
                        offset = following
                    raw = b"".join(chunks)
                    if len(raw) != size or hashlib.sha256(raw).hexdigest() != material["ref"]["sha256"]:
                        raise EvidenceError("paged artifact identity mismatch")
                    material["content"] = json.loads(raw.decode("utf-8-sig"))
                    material["complete"] = True
                if not material["complete"]:
                    raise EvidenceError("bounded material incomplete; supply a scoped summary")
                cache[key] = material
                verified.append(material["ref"])
            except (ValueError, OSError, KeyError) as exc:
                cache[key] = None
                issues.append(dict(ref=ref, status="insufficient", reason=str(exc)[:300]))
        return cache[key]

    if contract_ref is not None:
        material = read(contract_ref)
        if material is None:
            raise EvidenceError("evaluation contract unavailable; rules cannot be recovered")
        contract = material["content"]
    contract = validate_evaluation_contract(contract)
    data = {}
    for item in contract["inputs"]:
        material = read(item["ref"])
        data[item["name"]] = material["content"] if material else None
        if material and not isinstance(material["content"], (dict, list)):
            issues.append(dict(ref=item["ref"], status="insufficient", reason="input is not complete JSON data"))
    for ref in [contract[k] for k in _REF_FIELDS if contract[k] is not None] + contract["subjects"] + contract["comparators"] + contract["provenance"]["basis_refs"]:
        read(ref)

    def body(ref):
        material = read(ref)
        return material["content"] if material else None

    bindings = []
    for key, validator in (("method_contract_ref", validate_contract), ("plan_ref", validate_content),
                           ("evaluation_ref", method_record_from_dict)):
        ref = contract[key]
        if ref is None:
            continue
        value = body(ref)
        status, reason = "criteria_passed", "Existing typed binding verified"
        try:
            if value is None:
                raise EvidenceError("typed binding unavailable")
            if key == "plan_ref":
                if value.get("operation") != "evaluation_plan" or value.get("task_id") != contract["task_id"]:
                    raise EvidenceError("evaluation plan belongs to another task or operation")
                validator(value)
                plan = value["detail"]
                read(plan["task_split_ref"])
                for field in ("subjects", "comparators", "protocol_ref", "initial_conditions_ref"):
                    if plan[field] != contract[field]:
                        raise EvidenceError("evaluation plan differs: " + field)
                expected_metrics = [{k: m[k] for k in ("name", "family", "unit", "denominator", "missing_policy")} for m in contract["metrics"]]
                if plan["metrics"] != expected_metrics:
                    raise EvidenceError("evaluation plan metric declarations differ")
            elif key == "evaluation_ref":
                record = read(ref)["record"]
                if record.get("schema") != "etalon-method-evaluation/1":
                    raise EvidenceError("expected actual MethodEvaluation record")
                validator(value)
                for field in ("subjects", "comparators"):
                    refs = contract[field]
                    if any(r["source_id"] != ref["source_id"] for r in refs) or value[field] != [r["record_id"] for r in refs]:
                        raise EvidenceError("MethodEvaluation methods differ from contract")
                for field in ("protocol_ref", "initial_conditions_ref"):
                    if contract[field]["source_id"] != ref["source_id"] or value[field] != contract[field]["record_id"]:
                        raise EvidenceError("MethodEvaluation conditions differ from contract")
                for kind, field in (("result", "result_refs"), ("accounting", "accounting_refs")):
                    named = [i["ref"] for i in contract["inputs"] if i["kind"] == kind]
                    if any(r["source_id"] != ref["source_id"] or r["record_id"] not in value[field] for r in named):
                        raise EvidenceError("observed input is outside MethodEvaluation " + field)
                if contract["plan_ref"]:
                    split = body(contract["plan_ref"])["detail"]["task_split_ref"]
                    if split["source_id"] != ref["source_id"] or split["record_id"] != value["task_split_ref"]:
                        raise EvidenceError("MethodEvaluation task split differs from plan")
            else:
                validator(value)
                if not set(value["required_checks"]) <= {c["check_id"] for c in contract["required_checks"]}:
                    raise EvidenceError("method required checks omitted")
                if value["evaluation"] != {"schema": CONTRACT, "rules_digest": evaluation_rules_digest(contract)}:
                    raise EvidenceError("method external evaluation rules differ")
        except (ValueError, TypeError, KeyError) as exc:
            status = "insufficient" if value is None else "incomparable"
            reason = str(exc)[:300]
        bindings.append(dict(binding=key, ref=ref, status=status, reason=reason))
    for ref in contract["subjects"] + contract["comparators"]:
        value = body(ref)
        status = "criteria_passed"
        if (not isinstance(value, dict) or value.get("schema") != "etalon-method-version/1"
                or read(ref)["record"].get("schema") != "etalon-method-version/1"):
            status = "insufficient" if value is None else "incomparable"
        else:
            try:
                method_record_from_dict(value)
            except ValueError:
                status = "incomparable"
        bindings.append(dict(binding="method", ref=ref, status=status))

    def select(selector):
        value = _pointer(data.get(selector["input"]), selector["pointer"])
        if selector.get("reducer") == "count":
            return len(value) if isinstance(value, list) else _MISSING
        return value

    checks = []
    for group in ("comparability", "required_checks"):
        for check in contract[group]:
            actual = select(check["selector"])
            status = "insufficient" if actual is _MISSING or actual is None else (
                "criteria_passed" if canonical(actual) == canonical(check["expected"]) else
                "incomparable" if group == "comparability" else "criteria_failed")
            checks.append(dict(**check, group=group, actual=None if actual is _MISSING else actual, status=status))

    rows = []
    for metric in contract["metrics"]:
        measure = metric["measure"]
        left = select(measure["left"])
        right = select(measure["right"]) if measure["right"] else None
        denominator = select(metric["denominator_selector"])
        value, reason, status = None, None, "insufficient"
        if not _number(denominator) or denominator <= 0:
            reason = "missing, invalid or zero denominator"
        elif not _number(left) or measure["right"] and not _number(right):
            reason = "missing or non-finite numeric observation"
        elif measure["operation"] == "ratio" and right == 0:
            reason = "zero ratio divisor"
        else:
            operation = measure["operation"]
            value = left if operation == "value" else left / right if operation == "ratio" else left - right
            if operation == "abs_difference":
                value = abs(value)
            if not _number(value):
                value, reason = None, "non-finite derived measure"
            else:
                status = "criteria_passed" if _OPS[metric["operator"]](value, metric["threshold"]) else "criteria_failed"
        rows.append(dict(**metric, observed=value, denominator_observed=denominator if _number(denominator) else None,
                         status=status, reason=reason))
    prerequisite = [i["status"] for i in issues + bindings + checks]
    families = {family: dict(status=_aggregate(prerequisite + [r["status"] for r in rows if r["family"] == family])
                if any(r["family"] == family for r in rows) else "not_declared",
                metric_names=[r["name"] for r in rows if r["family"] == family]) for family in FAMILIES}
    return dict(schema=REPORT, task_id=contract["task_id"], scope=contract["scope"], conditions=contract["conditions"],
        contract=contract, contract_digest=digest(contract), rules_digest=evaluation_rules_digest(contract), contract_ref=contract_ref,
        input_refs=[i["ref"] for i in contract["inputs"]], verified_refs=verified,
        status=_aggregate(prerequisite + [r["status"] for r in rows]), metrics=rows, checks=checks,
        bindings=bindings, issues=issues, families=families, provenance=contract["provenance"],
        freeze_status="retrospective_not_prospective" if contract["provenance"]["mode"] == "retrospective" else "predeclaration_claim_chronology_not_verified",
        verification_scope="published identities and declared numeric checks; scientific applicability remains scoped",
        independent_validation="not_established_by_replay", scientific_truth="not_certified", adoption="not_performed",
        execution="not_performed", accounting="existing_refs_only_no_new_charge")


def record_evaluation_artifact(journal, payload, directory, *, source_kind, workspace=None,
                               entry=None, read_context=None):
    """Archive ordinary JSON. Reports are recomputed before archival.

    Contracts remain declarations. Reports require their original evidence
    workspace, and cannot be supplied with a forged status or changed rule.
    Existing composition.record_evaluation can explicitly reference the returned
    Artifact in its result_refs; no MethodEvaluation/adoption is auto-created.
    """
    from .components import record_json
    from .continuation import _exact_refs
    if isinstance(payload, dict) and payload.get("schema") == CONTRACT:
        content = validate_evaluation_contract(payload)
    elif isinstance(payload, dict) and payload.get("schema") == REPORT:
        ref = payload.get("contract_ref")
        content = evaluate_contract(workspace, payload.get("contract") if ref is None else None,
            contract_ref=ref, entry=entry, read_context=read_context)
        if canonical(content) != canonical(payload):
            raise EvidenceError("report differs from verified replay")
    else:
        raise EvidenceError("expected evaluation contract or report")
    refs = {canonical(r): r for r in _exact_refs(content)}
    return record_json(journal, content, directory, source_kind=source_kind,
        bindings=dict(schema=content["schema"], task_id=content["task_id"],
                      evidence_refs=list(refs.values()), evaluation_role="declared_rule_replay"))
