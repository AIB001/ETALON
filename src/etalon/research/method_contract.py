"""Explicit tunable methods and comparison gates over existing Screen/Journal APIs.

These helpers compile, inspect and compare; they never execute a screen, choose
a winner, modify defaults or allocate resources. Payloads are ordinary Journal
Artifacts via continuation.record_content. Required checks describe semantic
obligations, not a requirement to repeat identical tool calls.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
from dataclasses import fields, is_dataclass
from pathlib import Path

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, validate_ref, _sha

CONTRACT = "etalon-method-contract/1"
SCREEN_PLAN = "etalon-screen-compilation/1"
COMPARISON = "etalon-method-comparability/1"
SCHEMAS = {CONTRACT, SCREEN_PLAN, COMPARISON}
CATEGORIES = {"execution", "method", "evaluation"}
MODES = {"same_protocol", "method_change", "workflow_change"}
_MISSING = object()


def _fields(obj, required, optional=()):
    if not isinstance(obj, dict) or set(required) - obj.keys() or obj.keys() - set(required) - set(optional):
        raise EvidenceError("missing or unsupported method contract fields")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError("expected nonempty text")
    return value


def _refs(value):
    if not isinstance(value, list) or len(value) > 64:
        raise EvidenceError("expected bounded reference list")
    for ref in value:
        validate_ref(ref)


def _strings(value, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value) or len(value) > 128:
        raise EvidenceError("expected bounded explicit strings")
    for item in value:
        _text(item)
    if len(set(value)) != len(value):
        raise EvidenceError("duplicate entries")


def _identity(value):
    if not isinstance(value, dict) or not value or any(not isinstance(v, str) or not v.strip() for v in value.values()):
        raise EvidenceError("explicit identity/conditions values required; unknown is not a match")
    for key in value:
        _text(key)


def _file_identity(value):
    _fields(value, ("sha256", "bytes"))
    _sha(value["sha256"])
    if type(value["bytes"]) is not int or value["bytes"] < 0:
        raise EvidenceError("exact file byte count required")


def _pointer(value, path):
    """JSON Pointer, including list indices; absence is distinct from null."""
    if not isinstance(path, str) or not path.startswith("/") or path == "/":
        raise EvidenceError("parameter path must be an explicit JSON pointer")
    current = value
    for token in path[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(token, _MISSING)
        elif isinstance(current, list) and token.isdigit() and str(int(token)) == token and int(token) < len(current):
            current = current[int(token)]
        else:
            return _MISSING
    return current


def _value(value, spec):
    kind = spec["type"]
    valid = {"integer": type(value) is int, "number": type(value) in (int, float),
             "boolean": type(value) is bool, "string": isinstance(value, str),
             "array": isinstance(value, list), "object": isinstance(value, dict)}
    if not valid.get(kind, False):
        raise EvidenceError("parameter type mismatch: " + spec["path"])
    if kind in {"integer", "number"}:
        if not math.isfinite(value):
            raise EvidenceError("parameter must be finite")
        if spec["minimum"] is not None and value < spec["minimum"]:
            raise EvidenceError("parameter below minimum: " + spec["path"])
        if spec["maximum"] is not None and value > spec["maximum"]:
            raise EvidenceError("parameter above maximum: " + spec["path"])
    if spec["choices"] and not any(canonical(value) == canonical(v) for v in spec["choices"]):
        raise EvidenceError("parameter outside choices: " + spec["path"])


def validate_contract(payload):
    _fields(payload, ("schema", "name", "parameters", "required_checks", "evaluation", "supersedes"))
    if payload["schema"] != CONTRACT:
        raise EvidenceError("wrong method contract schema")
    _text(payload["name"])
    _strings(payload["required_checks"], nonempty=True)
    _refs(payload["supersedes"])
    if not isinstance(payload["evaluation"], dict) or not payload["evaluation"]:
        raise EvidenceError("fixed external evaluation contract required")
    params = payload["parameters"]
    if not isinstance(params, list) or len(params) > 128:
        raise EvidenceError("bounded parameter declarations required")
    paths = set()
    for spec in params:
        _fields(spec, ("path", "category", "type", "unit", "default", "minimum", "maximum", "choices",
                       "required", "immutable", "depends_on", "expected_effect"))
        _pointer({}, spec["path"])
        path = spec["path"]
        if path in paths or any(path.startswith(p + "/") or p.startswith(path + "/") for p in paths):
            raise EvidenceError("duplicate or overlapping parameter paths")
        paths.add(path)
        if spec["category"] not in CATEGORIES or spec["type"] not in {"integer", "number", "boolean", "string", "array", "object"}:
            raise EvidenceError("unknown parameter category/type")
        for key in ("unit", "expected_effect"):
            _text(spec[key])
        if any(type(spec[k]) is not bool for k in ("required", "immutable")):
            raise EvidenceError("required/immutable must be booleans")
        for key in ("minimum", "maximum"):
            bound = spec[key]
            if bound is not None and (spec["type"] not in {"integer", "number"} or type(bound) not in (int, float) or not math.isfinite(bound)):
                raise EvidenceError("numeric finite bounds required")
        if spec["minimum"] is not None and spec["maximum"] is not None and spec["minimum"] > spec["maximum"]:
            raise EvidenceError("reversed parameter range")
        if not isinstance(spec["choices"], list):
            raise EvidenceError("choices must be explicit")
        _value(spec["default"], spec)
        if not isinstance(spec["depends_on"], list):
            raise EvidenceError("dependencies must be explicit")
        for dep in spec["depends_on"]:
            _fields(dep, ("path", "equals"))
            _pointer({}, dep["path"])
    for spec in params:
        for dep in spec["depends_on"]:
            if dep["path"] not in paths or dep["path"] == spec["path"]:
                raise EvidenceError("dependency must name another contracted parameter")
    return json.loads(canonical(payload))


def parameter_values(contract, configuration):
    """Inspect declarations/defaults, not a claim that a backend consumed them."""
    contract = validate_contract(contract)
    if not isinstance(configuration, dict):
        raise EvidenceError("configuration must be an object")
    resolved = {}
    for spec in contract["parameters"]:
        value = _pointer(configuration, spec["path"])
        if value is _MISSING:
            if spec["required"]:
                raise EvidenceError("missing required parameter: " + spec["path"])
            value = spec["default"]
        _value(value, spec)
        resolved[spec["path"]] = {"value": value, "origin": "default" if _pointer(configuration, spec["path"]) is _MISSING else "declared",
                                  "category": spec["category"], "unit": spec["unit"]}
    for spec in contract["parameters"]:
        for dep in spec["depends_on"]:
            if canonical(resolved[dep["path"]]["value"]) != canonical(dep["equals"]):
                raise EvidenceError("parameter dependency unsatisfied: " + spec["path"])
    return json.loads(canonical(resolved))


def _differences(before, after, path=""):
    if type(before) is type(after) and isinstance(before, dict):
        for key in sorted(set(before) | set(after)):
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from _differences(before.get(key, _MISSING), after.get(key, _MISSING), path + "/" + escaped)
    elif type(before) is type(after) and isinstance(before, list) and len(before) == len(after):
        for index, (left, right) in enumerate(zip(before, after)):
            yield from _differences(left, right, path + "/" + str(index))
    elif before is _MISSING or after is _MISSING or canonical(before) != canonical(after):
        yield path


def compare_parameters(contract, base_config, candidate_config):
    """Unknown changes fail closed; changing a method is legal, changing an
    evaluator prevents benefit attribution under the old comparison contract.
    """
    contract = validate_contract(contract)
    base, candidate = parameter_values(contract, base_config), parameter_values(contract, candidate_config)
    differences = list(_differences(base_config, candidate_config))
    specs = {p["path"]: p for p in contract["parameters"]}
    unknown = [path for path in differences if not any(path == p or path.startswith(p + "/") for p in specs)]
    changes, origin_changes = [], []
    for path, spec in specs.items():
        if base[path]["origin"] != candidate[path]["origin"]:
            origin_changes.append({"path": path, "before": base[path]["origin"], "after": candidate[path]["origin"]})
        if canonical(base[path]["value"]) != canonical(candidate[path]["value"]):
            if spec["immutable"]:
                raise EvidenceError("user hard constraint cannot change: " + path)
            changes.append({"path": path, "category": spec["category"], "before": base[path], "after": candidate[path]})
    return {"changes": changes, "origin_changes": origin_changes, "uncontracted_changes": unknown,
            "evaluation_changed": any(c["category"] == "evaluation" for c in changes),
            "contract_digest": digest(contract)}


def _file(path):
    raw = Path(path).read_bytes()
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _json(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {field.name: _json(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(v) for v in value]
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


def compile_screen_revision(screen, config_path, *, contract, input_identities, conditions,
                            stage_checks, library=None, target=None, evidence_bindings=(), parameter_bindings=()):
    """Use the real Screen.plan compiler, without screen execution or mutation.

    stage_checks explicitly maps lowered stage IDs to semantic obligations.
    Unknown/missing IDs remain gaps; compilation never certifies executed checks.
    Both exact authored bytes and semantic/effective configuration are retained.
    parameter_bindings explicitly maps authored pointers to compiled stage
    config pointers. Unmapped/transformed values are disclosed, never silently
    claimed to have been consumed by the component.
    """
    import yaml
    contract = validate_contract(contract)
    _identity(input_identities)
    _identity(conditions)
    raw = Path(config_path).read_bytes()
    authored = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    configuration = yaml.safe_load(raw.decode("utf-8-sig"))
    parameters = parameter_values(contract, configuration)
    if not isinstance(stage_checks, dict):
        raise EvidenceError("explicit stage-to-check map required")
    for stage, checks in stage_checks.items():
        _text(stage)
        _strings(checks, nonempty=True)
        if not set(checks) <= set(contract["required_checks"]):
            raise EvidenceError("stage map names an uncontracted check")
    bindings = list(evidence_bindings)
    for binding in bindings:
        _fields(binding, ("parameter", "basis_refs", "scientific_input_ref"))
        if binding["parameter"] not in parameters:
            raise EvidenceError("binding names uncontracted parameter")
        _refs(binding["basis_refs"])
        if not binding["basis_refs"]:
            raise EvidenceError("parameter binding needs evidence")
        if binding["scientific_input_ref"] is not None:
            validate_ref(binding["scientific_input_ref"])
    lib_identity = _file(library) if library is not None else None
    plan = screen.plan(config_path, library=library, target=target)
    if authored != _file(config_path) or (library is not None and lib_identity != _file(library)):
        raise EvidenceError("configuration or library changed during compilation")
    compiled = plan._internal["compiled"]
    stages = [{"stage_id": s.stage_id, "plugin": s.plugin_key, "slot": s.slot, "config": _json(dict(s.config)),
               "input_contract": s.input_contract, "output_contract": s.output_contract,
               "inputs": [{"port": b.request_port, "stage_id": b.source_stage_id,
                           "source_port": b.source_port, "contract": b.contract_id} for b in s.input_bindings]}
              for s in compiled.stages]
    ids = {s["stage_id"] for s in stages}
    effective = _effective_parameters(parameters, stages, list(parameter_bindings))
    mapping = [{"stage_id": sid, "checks": checks, "compiled": sid in ids} for sid, checks in sorted(stage_checks.items())]
    covered = set().union(*(set(m["checks"]) for m in mapping if m["compiled"])) if mapping else set()
    backends = _json(plan.backends)
    assets = _json(plan.assets)
    from molcascade.pipeline import PipelineCompiler
    modules = {}
    for cls in (type(screen), PipelineCompiler):
        path = inspect.getfile(cls)
        modules[cls.__module__] = {"path": str(Path(path).absolute()), **_file(path)}
    payload = {"schema": SCREEN_PLAN, "supersedes": [], "contract": contract,
               "contract_digest": digest(contract), "authored_file": authored,
               "declaration": configuration, "declaration_digest": digest(configuration),
               "parameters": parameters, "input_identities": input_identities, "conditions": conditions,
               "effective_parameters": effective,
               "unbound_parameters": sorted(set(parameters) - {p["parameter"] for p in effective}),
               "library_identity": lib_identity, "target": target, "evidence_bindings": bindings,
               "revision_id": plan.revision_id, "compiled_stages": stages, "compiled_digest": digest(stages),
               "stage_checks": mapping, "missing_checks": sorted(set(contract["required_checks"]) - covered),
               "backends": backends, "assets": assets, "advisories": _json(plan.advisories), "modules": modules,
               "status": "compiled_not_executed"}
    return validate_content(payload)


def _effective_parameters(parameters, stages, bindings):
    if not isinstance(bindings, list) or len(bindings) > 256:
        raise EvidenceError("bounded explicit parameter bindings required")
    indexed = {stage["stage_id"]: stage for stage in stages}
    result, seen = [], set()
    for binding in bindings:
        _fields(binding, ("parameter", "stage_id", "path"))
        if binding["parameter"] not in parameters:
            raise EvidenceError("compiled binding names uncontracted parameter")
        _text(binding["stage_id"])
        _pointer({}, binding["path"])
        key = canonical(binding)
        if key in seen:
            raise EvidenceError("duplicate compiled parameter binding")
        seen.add(key)
        stage = indexed.get(binding["stage_id"])
        effective = _MISSING if stage is None else _pointer(stage["config"], binding["path"])
        expected = parameters[binding["parameter"]]["value"]
        result.append({**binding, "value": None if effective is _MISSING else effective,
            "status": "missing" if effective is _MISSING else "matches" if canonical(effective) == canonical(expected) else "transformed"})
    return result


def validate_content(payload):
    if not isinstance(payload, dict):
        raise EvidenceError("method content must be an object")
    if payload.get("schema") == CONTRACT:
        return validate_contract(payload)
    if payload.get("schema") == SCREEN_PLAN:
        _fields(payload, ("schema", "supersedes", "contract", "contract_digest", "authored_file", "declaration",
                         "declaration_digest", "parameters", "input_identities", "conditions", "library_identity", "target",
                         "effective_parameters", "unbound_parameters",
                         "evidence_bindings", "revision_id", "compiled_stages", "compiled_digest", "stage_checks",
                         "missing_checks", "backends", "assets", "advisories", "modules", "status"))
        contract = validate_contract(payload["contract"])
        _file_identity(payload["authored_file"])
        if payload["library_identity"] is not None:
            _file_identity(payload["library_identity"])
        if not isinstance(payload["modules"], dict) or not payload["modules"]:
            raise EvidenceError("loaded compiler module identities required")
        for name, module in payload["modules"].items():
            _text(name)
            _fields(module, ("path", "sha256", "bytes"))
            _text(module["path"])
            _file_identity({k: module[k] for k in ("sha256", "bytes")})
        for stage in payload["compiled_stages"]:
            _fields(stage, ("stage_id", "plugin", "slot", "config", "input_contract", "output_contract", "inputs"))
            for key in ("stage_id", "plugin", "slot", "output_contract"):
                _text(stage[key])
            if stage["input_contract"] is not None:
                _text(stage["input_contract"])
            if not isinstance(stage["config"], dict) or not isinstance(stage["inputs"], list):
                raise EvidenceError("compiled stage configuration/inputs invalid")
            for binding in stage["inputs"]:
                _fields(binding, ("port", "stage_id", "source_port", "contract"))
                for value in binding.values():
                    _text(value)
        for field, value in (("contract_digest", contract), ("declaration_digest", payload["declaration"]),
                             ("compiled_digest", payload["compiled_stages"])):
            if payload[field] != digest(value):
                raise EvidenceError("method content identity mismatch: " + field)
        if payload["parameters"] != parameter_values(contract, payload["declaration"]):
            raise EvidenceError("parameter declarations differ")
        effective = payload["effective_parameters"]
        bindings = [{k: row[k] for k in ("parameter", "stage_id", "path")} for row in effective]
        if effective != _effective_parameters(payload["parameters"], payload["compiled_stages"], bindings):
            raise EvidenceError("declared-to-effective parameter binding differs")
        if payload["unbound_parameters"] != sorted(set(payload["parameters"]) - {p["parameter"] for p in effective}):
            raise EvidenceError("unbound parameter gaps concealed")
        for binding in payload["evidence_bindings"]:
            _fields(binding, ("parameter", "basis_refs", "scientific_input_ref"))
            if binding["parameter"] not in payload["parameters"] or not binding["basis_refs"]:
                raise EvidenceError("evidence binding needs a contracted parameter and evidence")
            _refs(binding["basis_refs"])
            if binding["scientific_input_ref"] is not None:
                validate_ref(binding["scientific_input_ref"])
        _identity(payload["input_identities"])
        _identity(payload["conditions"])
        _text(payload["revision_id"])
        if payload["status"] != "compiled_not_executed":
            raise EvidenceError("compilation is not execution")
        ids = [s["stage_id"] for s in payload["compiled_stages"]]
        _strings(ids, nonempty=True)
        covered = set()
        seen = set()
        for mapping in payload["stage_checks"]:
            _fields(mapping, ("stage_id", "checks", "compiled"))
            if mapping["stage_id"] in seen or type(mapping["compiled"]) is not bool or mapping["compiled"] != (mapping["stage_id"] in ids):
                raise EvidenceError("incorrect declared-to-compiled map")
            seen.add(mapping["stage_id"])
            _strings(mapping["checks"], nonempty=True)
            if not set(mapping["checks"]) <= set(contract["required_checks"]):
                raise EvidenceError("uncontracted check")
            if mapping["compiled"]:
                covered.update(mapping["checks"])
        if payload["missing_checks"] != sorted(set(contract["required_checks"]) - covered):
            raise EvidenceError("missing compiled checks concealed")
    elif payload.get("schema") == COMPARISON:
        _fields(payload, ("schema", "supersedes", "mode", "base_digest", "candidate_digest", "differences", "missing",
                         "status", "required_checks", "coverage", "benefit_established", "comparison_contexts",
                         "receipt_verification", "verifier_used", "verification_scope"))
        for key in ("base_digest", "candidate_digest"):
            _sha(payload[key])
        if payload["mode"] not in MODES or payload["status"] != ("awaiting_evidence" if payload["missing"] else "ready_for_evaluation"):
            raise EvidenceError("invalid comparability status")
        if payload["benefit_established"] is not False:
            raise EvidenceError("comparability cannot establish benefit")
        _strings(payload["required_checks"], nonempty=True)
        _strings(payload["missing"])
        _strings(payload["differences"])
        if type(payload["verifier_used"]) is not bool or payload["verification_scope"] != "caller_attested_not_kernel_certified":
            raise EvidenceError("comparison receipt verification must declare its attestation boundary")
        for key in ("coverage", "comparison_contexts", "receipt_verification"):
            _fields(payload[key], ("base", "candidate"))
        gaps = []
        for side in ("base", "candidate"):
            context = payload["comparison_contexts"][side]
            _fields(context, ("input_digest", "conditions_digest"))
            for value in context.values():
                _sha(value)
            verdicts = payload["receipt_verification"][side]
            _fields(verdicts, payload["required_checks"])
            for verdict in verdicts.values():
                if verdict not in {"verified", "rejected", "not_checked"}:
                    raise EvidenceError("unknown receipt verification verdict")
                if verdict != "not_checked" and not payload["verifier_used"]:
                    raise EvidenceError("receipt verdict cannot exist without a verifier")
            rows = _coverage_rows(payload["coverage"][side], payload["required_checks"])
            for check in payload["required_checks"]:
                row = rows.get(check)
                valid = (row is not None and row["state"] in {"passed", "reused"} and row["receipt_ref"] is not None
                         and (row["state"] != "reused" or row["reuse_basis_ref"] is not None)
                         and row["input_digest"] == context["input_digest"] and row["conditions_digest"] == context["conditions_digest"])
                if not valid and verdicts[check] != "not_checked":
                    raise EvidenceError("ineligible receipt cannot have a verification verdict")
                if not valid or verdicts[check] != "verified":
                    gaps.append(side + ": unverified or unsatisfied check " + check)
        if payload["comparison_contexts"]["base"] != payload["comparison_contexts"]["candidate"]:
            gaps.append("input or condition context mismatch")
        if gaps and payload["status"] == "ready_for_evaluation":
            raise EvidenceError("ready comparison requires complete verified applicable coverage")
    else:
        raise EvidenceError("unknown method content schema")
    _refs(payload["supersedes"])
    return json.loads(canonical(payload))


def _coverage_rows(rows, required):
    if not isinstance(rows, (tuple, list)):
        raise EvidenceError("explicit executed coverage required")
    by_check = {}
    for row in rows:
        _fields(row, ("check_id", "state", "receipt_ref", "reuse_basis_ref", "input_digest", "conditions_digest"))
        if row["check_id"] in by_check or row["check_id"] not in required:
            raise EvidenceError("duplicate or unknown coverage check")
        if row["state"] not in {"passed", "reused", "failed", "missing"}:
            raise EvidenceError("unknown coverage state")
        for key in ("receipt_ref", "reuse_basis_ref"):
            if row[key] is not None:
                validate_ref(row[key])
        for key in ("input_digest", "conditions_digest"):
            _sha(row[key])
        if row["state"] != "reused" and row["reuse_basis_ref"] is not None:
            raise EvidenceError("only reused coverage can carry a reuse basis")
        by_check[row["check_id"]] = row
    return by_check


def assess_comparability(base, candidate, *, mode, base_coverage, candidate_coverage, verify_receipt=None):
    """Check prerequisites, never a scientific success score.

    Coverage rows: check_id, state=passed/reused/failed/missing, receipt_ref,
    reuse_basis_ref, input_digest, conditions_digest. The verifier must inspect
    the actual receipt against (row, side); omitted verifier leaves evidence
    awaiting verification. A reuse basis explains semantic equivalence, and
    never creates an independent repeat. Quality/outcome metrics remain in the
    existing MethodEvaluation pipeline.
    """
    base, candidate = validate_content(base), validate_content(candidate)
    if base["schema"] != SCREEN_PLAN or candidate["schema"] != SCREEN_PLAN or mode not in MODES:
        raise EvidenceError("comparison requires compiled plans and an explicit comparison mode")
    missing, differences = [], []
    if base["contract_digest"] != candidate["contract_digest"]:
        missing.append("method contract changed; explicitly re-establish comparison")
    for key in ("input_identities", "conditions", "library_identity", "target"):
        if canonical(base[key]) != canonical(candidate[key]):
            differences.append(key)
            missing.append(key + " differ; matched comparison required")
    parameters = compare_parameters(base["contract"], base["declaration"], candidate["declaration"])
    differences.extend("parameter:" + c["path"] for c in parameters["changes"])
    differences.extend("declaration_origin:" + c["path"] for c in parameters["origin_changes"])
    # A matching authored file is insufficient: compiler defaults, plugin
    # implementations, stage wiring or the actual loaded compiler can drift.
    base_stages = {s["stage_id"]: s for s in base["compiled_stages"]}
    candidate_stages = {s["stage_id"]: s for s in candidate["compiled_stages"]}
    if list(base_stages) != list(candidate_stages):
        differences.append("compiled:stage-order-or-membership")
        if mode != "workflow_change":
            missing.append("effective stage order/membership changed; workflow comparison required")
    allowed_execution_paths = {}
    execution_changes = {c["path"] for c in parameters["changes"] if c["category"] == "execution"}
    for bound in candidate["effective_parameters"]:
        if bound["parameter"] in execution_changes and bound["status"] == "matches":
            # Both sides must bind the same semantic parameter to this stage
            # slot. A changed binding is itself an effective-program change.
            if any(all(old[k] == bound[k] for k in ("parameter", "stage_id", "path")) and old["status"] == "matches"
                   for old in base["effective_parameters"]):
                allowed_execution_paths.setdefault(bound["stage_id"], []).append("/config" + bound["path"])
    for stage_id in sorted(set(base_stages) & set(candidate_stages)):
        for path in _differences(base_stages[stage_id], candidate_stages[stage_id]):
            differences.append("compiled:" + stage_id + path)
            explained_execution_change = any(path == p or path.startswith(p + "/") for p in allowed_execution_paths.get(stage_id, []))
            if not explained_execution_change and mode == "same_protocol":
                missing.append("effective program changed outside execution bindings; method/workflow comparison required")
    # File relocation alone is not changed code. Hash/size/module changes are
    # scientific execution-condition changes and cannot be silently normalized.
    source_identity = lambda plan: {name: {k: value[k] for k in ("sha256", "bytes")} for name, value in plan["modules"].items()}
    if source_identity(base) != source_identity(candidate):
        differences.append("compiler_module_identity")
        missing.append("loaded compiler/module bytes changed; establish matched execution conditions")
    for key in ("backends", "assets"):
        if canonical(base[key]) != canonical(candidate[key]):
            differences.append("compiled:" + key)
            if mode == "same_protocol":
                missing.append(key + " changed; method/workflow comparison required")
    for side, plan in (("base", base), ("candidate", candidate)):
        for change in parameters["changes"]:
            if change["category"] == "evaluation":
                continue
            bindings = [row for row in plan["effective_parameters"] if row["parameter"] == change["path"]]
            if not bindings or any(row["status"] != "matches" for row in bindings):
                missing.append(side + ": changed parameter consumption not established " + change["path"])
    if parameters["uncontracted_changes"]:
        # Structural changes are legitimate workflow candidates, not silently
        # treated as the same protocol. Their obligations still need receipts.
        differences.extend("structure:" + p for p in parameters["uncontracted_changes"])
        if mode != "workflow_change":
            missing.append("uncontracted configuration changes require workflow comparison")
    if parameters["evaluation_changed"] or base["contract"]["evaluation"] != candidate["contract"]["evaluation"]:
        missing.append("evaluation standard changed; cannot claim benefit under the old evaluation")
    if mode == "same_protocol" and any(c["category"] == "method" for c in parameters["changes"]):
        missing.append("scientific method changed; same_protocol attribution unavailable")
    required = sorted(set(base["contract"]["required_checks"]) | set(candidate["contract"]["required_checks"]))
    coverage, receipt_verification, comparison_contexts = {}, {}, {}
    for side, plan, rows in (("base", base, base_coverage), ("candidate", candidate, candidate_coverage)):
        by_check = _coverage_rows(rows, required)
        comparison_contexts[side] = {"input_digest": digest(plan["input_identities"]), "conditions_digest": digest(plan["conditions"])}
        receipt_verification[side] = {check: "not_checked" for check in required}
        for check in required:
            row = by_check.get(check)
            if row is None or row["state"] not in {"passed", "reused"}:
                missing.append(side + ": unsatisfied check " + check)
                continue
            if check in plan["missing_checks"] and row["state"] != "reused":
                missing.append(side + ": no compiled implementation for " + check)
            if row["receipt_ref"] is None or (row["state"] == "reused" and row["reuse_basis_ref"] is None):
                missing.append(side + ": missing receipt/reuse basis " + check)
            elif row["input_digest"] != digest(plan["input_identities"]) or row["conditions_digest"] != digest(plan["conditions"]):
                missing.append(side + ": receipt applicability mismatch " + check)
            elif verify_receipt is None:
                missing.append(side + ": receipt not verified " + check)
            else:
                verified = verify_receipt(row, side) is True
                receipt_verification[side][check] = "verified" if verified else "rejected"
                if not verified:
                    missing.append(side + ": receipt not verified " + check)
        coverage[side] = list(rows)
    return validate_content({"schema": COMPARISON, "supersedes": [], "mode": mode,
        "base_digest": digest(base), "candidate_digest": digest(candidate), "differences": differences,
        "missing": sorted(set(missing)), "status": "awaiting_evidence" if missing else "ready_for_evaluation",
        "required_checks": required, "coverage": coverage, "benefit_established": False,
        "comparison_contexts": comparison_contexts, "receipt_verification": receipt_verification,
        "verifier_used": verify_receipt is not None, "verification_scope": "caller_attested_not_kernel_certified"})
