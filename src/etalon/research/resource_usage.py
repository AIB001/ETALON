"""Task workset attribution over existing MethodUse/step/evaluation records.

Ordinary immutable Artifacts, no new ledger or execution policy. A verified join
is not proof of consumption: host reports and retrospective gaps stay explicit.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, file_identity, validate_ref
from .meta_context import WORKSET, _dependencies, _text, workset_resource_part
from .read_scope import bounded_read, verified_read

SCHEMA = "etalon-resource-usage/1"
PHASES = ("read", "consumed", "executed")
REF_FIELDS = ("workset_ref", "checkpoint_ref", "method_ref", "use_ref", "node_ref",
              "evaluation_ref", "selection_ref")
RELATIONS = dict(workset_ref="selected_workset", checkpoint_ref="selection_checkpoint",
                 method_ref="resource_method", use_ref="attributed_use", node_ref="associated_node",
                 evaluation_ref="associated_evaluation", selection_ref="subsequent_selection")
RELATIONS = {key: "declared_" + value for key, value in RELATIONS.items()}


def build_resource_usage(*, task_id, workset_ref, resource_id, part_id, provenance,
                         checkpoint_ref=None, method_ref=None, use_ref=None, node_ref=None,
                         evaluation_ref=None, selection_ref=None, observations=None):
    """Construct a bounded declaration. record/read perform the evidence joins.

    provenance = {mode: contemporaneous|retrospective, note, source_refs: [ref]}.
    Each phase is {status: unknown|host_reported, evidence_refs: [ref], note}.
    Retrospective declarations cannot upgrade historical phases from unknown.
    evaluation_ref identifies the attribution-bearing research evaluation step.
    """
    for value in (task_id, resource_id, part_id):
        _text(value)
    if not isinstance(provenance, dict) or set(provenance) != {"mode", "note", "source_refs"}:
        raise EvidenceError("explicit attribution provenance required")
    if provenance["mode"] not in {"contemporaneous", "retrospective"}:
        raise EvidenceError("unknown attribution mode")
    _text(provenance["note"])
    _refs(provenance["source_refs"], nonempty=True)
    refs = dict(workset_ref=workset_ref, checkpoint_ref=checkpoint_ref, method_ref=method_ref,
                use_ref=use_ref, node_ref=node_ref, evaluation_ref=evaluation_ref, selection_ref=selection_ref)
    validate_ref(workset_ref)
    for value in refs.values():
        if value is not None:
            validate_ref(value)
    if use_ref is not None and method_ref is None or node_ref is not None and use_ref is None:
        raise EvidenceError("node/use attribution requires its explicit use/method")
    if evaluation_ref is not None and node_ref is None or selection_ref is not None and evaluation_ref is None:
        raise EvidenceError("evaluation/selection requires its explicit preceding node/evaluation")
    observations = observations if observations is not None else {
        phase: dict(status="unknown", evidence_refs=[], note="No phase observation supplied") for phase in PHASES}
    if not isinstance(observations, dict) or set(observations) != set(PHASES):
        raise EvidenceError("read, consumed and executed must be recorded separately")
    for phase, value in observations.items():
        if not isinstance(value, dict) or set(value) != {"status", "evidence_refs", "note"}:
            raise EvidenceError("phase needs status, evidence_refs and note")
        if value["status"] not in {"unknown", "host_reported"}:
            raise EvidenceError("no mechanical consumption telemetry is available; use unknown or host_reported")
        _text(value["note"])
        _refs(value["evidence_refs"], nonempty=value["status"] == "host_reported")
        if provenance["mode"] == "retrospective" and value["status"] != "unknown":
            raise EvidenceError("retrospective links cannot backfill phase observations")
    payload = dict(schema=SCHEMA, task_id=task_id, resource_id=resource_id, part_id=part_id,
                   **refs, observations=observations, provenance=provenance)
    if len(canonical(payload).encode("utf-8")) > 32768:
        raise EvidenceError("resource usage exceeds 32 KiB")
    return json.loads(canonical(payload))


def _refs(refs, *, nonempty=False):
    if not isinstance(refs, list) or len(refs) > 64 or nonempty and not refs:
        raise EvidenceError("bounded explicit evidence references required")
    for ref in refs:
        validate_ref(ref)
    if len({canonical(r) for r in refs}) != len(refs):
        raise EvidenceError("duplicate evidence references")


def validate_resource_usage(payload):
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise EvidenceError("expected resource usage schema")
    try:
        return build_resource_usage(**{k: v for k, v in payload.items() if k != "schema"})
    except TypeError as exc:
        raise EvidenceError("unsupported resource usage fields") from exc


def usage_dependencies(payload):
    refs = [payload[k] for k in REF_FIELDS if payload[k] is not None]
    refs += payload["provenance"]["source_refs"]
    refs += [r for value in payload["observations"].values() for r in value["evidence_refs"]]
    return [r for _, r in sorted({canonical(r): r for r in refs}.items())]


def _links(payload):
    return [{"ref": payload[key], "relation": relation} for key, relation in RELATIONS.items()
            if payload[key] is not None]


def _material_reader(workspace=None, journal=None, source_id=None):
    """One call-local cache; ordinary reader still checks original bytes/scope."""
    from .improvement import read_material
    from .schema import Artifact
    cache = {}

    def read(ref):
        key = canonical(validate_ref(ref))
        if key not in cache:
            if journal is not None and ref["source_id"] == source_id:
                if "member" in ref:
                    raise EvidenceError("local usage bindings require whole records")
                record = journal.get(ref["record_id"])
                if digest(record) != ref["record_digest"]:
                    raise EvidenceError("record digest mismatch")
                content = record
                if {"path", "sha256", "bytes", "source_kind", "role"} <= set(record):
                    artifact = Artifact(**record)
                    path = journal.artifact_path(artifact)
                    raw = path.read_bytes()
                    if len(raw) != record["bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
                        raise EvidenceError("artifact changed during usage verification")
                    if len(raw) > 32768:
                        content = None
                    else:
                        try:
                            content = json.loads(raw.decode("utf-8-sig"))
                        except (ValueError, UnicodeError):
                            content = None
                    if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (record["sha256"], record["bytes"]):
                        raise EvidenceError("artifact identity mismatch")
                cache[key] = dict(ref=ref, record=record, content=content, complete=content is not None)
            else:
                if workspace is None:
                    raise EvidenceError("external references require an explicit evidence workspace")
                cache[key] = read_material(workspace, ref)
            if cache[key]["record"].get("role") == "sealed_holdout":
                raise EvidenceError("sealed material cannot enter resource usage")
        return cache[key]
    return read


def _verify(payload, read, workspace=None):
    from .continuation import validate_content
    from .meta_context import read_task_workset, verify_resource

    def content(ref, schema=None):
        result = read(ref)
        value = result["content"]
        if not result["complete"] or not isinstance(value, dict) or schema and value.get("schema") != schema:
            raise EvidenceError("usage binding unavailable, incomplete or wrong schema")
        return value

    workset = content(payload["workset_ref"], WORKSET)
    if workset["task_id"] != payload["task_id"]:
        raise EvidenceError("workset belongs to another task")
    item = workset_resource_part(workset, payload["resource_id"], payload["part_id"])
    if read(payload["workset_ref"])["record"].get("bindings", {}).get("evidence_refs") != _dependencies(workset):
        raise EvidenceError("workset must bind complete evidence dependencies")
    resource = item["resource"]
    # Only the named resource is read; unrelated changed skills are not silently
    # treated as verified. The returned scope is explicit about this boundary.
    if resource["kind"] == "skill":
        verify_resource(resource, workspace)
    else:
        read(resource["content_ref"])
        for ref in [*resource["support_refs"], *resource["counter_refs"]]:
            read(ref)
    activation = "unknown"
    if payload["checkpoint_ref"] is not None:
        if workspace is None:
            raise EvidenceError("checkpoint activation requires a published evidence workspace")
        active = read_task_workset(workspace, payload["checkpoint_ref"], verify=False)
        if active.get("ref") != payload["workset_ref"] or active["task_id"] != payload["task_id"]:
            raise EvidenceError("workset is not active at the given checkpoint")
        activation = "verified_at_supplied_checkpoint"
    values = {}
    for key in REF_FIELDS[2:]:
        if payload[key] is not None:
            values[key] = content(payload[key])
    method, use, node, evaluation, selection = [values.get(k) for k in REF_FIELDS[2:]]
    method_selection = None
    if method is not None:
        if read(payload["method_ref"])["record"].get("schema") != "etalon-method-version/1":
            raise EvidenceError("resource method requires MethodVersion")
        from .schema import method_record_from_dict
        method_record_from_dict(method)
        if resource["kind"] == "skill":
            if method["content_ref"].get("manifest_sha256") != resource["content_ref"]["manifest_sha256"]:
                raise EvidenceError("method does not identify the selected skill version")
        elif resource["content_ref"] != payload["method_ref"]:
            raise EvidenceError("resource does not identify this exact method")
    if use is not None:
        if read(payload["use_ref"])["record"].get("schema") != "etalon-method-use/1" or not any(
            s["kind"] == "task" and s["id"] == payload["task_id"] for s in use["subject_refs"]):
            raise EvidenceError("MethodUse must name the same task")
        method_record_from_dict(use)
        if payload["use_ref"]["source_id"] != payload["method_ref"]["source_id"]:
            raise EvidenceError("bare MethodUse method IDs resolve only within the same source")
        method_selection = next((m for m in use["selected_methods"]
                                 if m["method_version_ref"] == payload["method_ref"]["record_id"]), None)
        if method_selection is None:
            raise EvidenceError("MethodUse did not select the resource method")
    for key, op in (("node_ref", "node"), ("evaluation_ref", "evaluation"), ("selection_ref", "decision")):
        if key in values:
            value = validate_content(values[key])
            if value.get("operation") != op or value["task_id"] != payload["task_id"]:
                raise EvidenceError("usage step has wrong operation or task")
    if node is not None:
        if payload["use_ref"] not in node["detail"]["use_refs"] or payload["method_ref"] not in node["method_refs"]:
            raise EvidenceError("node does not explicitly bind this use and method")
        if node["detail"]["state"] in {"planned", "skipped"}:
            raise EvidenceError("unexecuted node cannot establish a usage chain")
        for ref in node["detail"]["receipt_refs"]:
            read(ref)
    evaluated = None
    if evaluation is not None:
        detail = evaluation["detail"]
        if payload["use_ref"] not in detail.get("use_refs", []) or payload["node_ref"] not in detail.get("node_refs", []):
            raise EvidenceError("evaluation does not explicitly attribute this node and use")
        evaluated = content(detail["evaluation_ref"], "etalon-method-evaluation/1")
        if read(detail["evaluation_ref"])["record"].get("schema") != "etalon-method-evaluation/1":
            raise EvidenceError("evaluation must identify a real MethodEvaluation record")
        method_record_from_dict(evaluated)
        plan = content(detail["plan_ref"], "etalon-research-step/1")
        if plan.get("operation") != "evaluation_plan" or plan["task_id"] != payload["task_id"]:
            raise EvidenceError("evaluation plan belongs to another task")
        for key in ("subjects", "comparators"):
            if evaluated[key] != [r["record_id"] for r in plan["detail"][key]]:
                raise EvidenceError("evaluation differs from its plan")
        if payload["method_ref"] not in plan["detail"]["subjects"] + plan["detail"]["comparators"]:
            raise EvidenceError("resource method is outside the evaluation comparison")
        for key in ("protocol_ref", "task_split_ref", "initial_conditions_ref"):
            if evaluated[key] != plan["detail"][key]["record_id"]:
                raise EvidenceError("evaluation conditions differ from its plan")
            read(plan["detail"][key])
        if detail["evaluation_ref"]["source_id"] != payload["method_ref"]["source_id"]:
            raise EvidenceError("bare evaluation method IDs require the same source")
    selection_via = []
    if selection is not None:
        basis = selection["detail"]["basis_refs"]
        if payload["evaluation_ref"] not in basis and evaluation["detail"]["evaluation_ref"] not in basis:
            # Existing improvement experiences preserve a precise evaluation ref.
            # Follow that one explicit hop, never fuzzy names or arbitrary prose.
            for ref in basis:
                experience = read(ref)["content"]
                if isinstance(experience, dict) and experience.get("schema") == "etalon-method-experience/1":
                    validate_content(experience)
                    if (experience["task_id"] == payload["task_id"]
                            and experience["evaluation_ref"] in [payload["evaluation_ref"], evaluation["detail"]["evaluation_ref"]]
                            and payload["method_ref"] in experience["method_refs"]):
                        selection_via.append(ref)
            if not selection_via:
                raise EvidenceError("subsequent selection does not cite this evaluation")
        for ref in selection["output_refs"] + selection["method_refs"]:
            read(ref)
    for ref in usage_dependencies(payload):
        read(ref)
    for phase, observation in payload["observations"].items():
        if observation["status"] == "unknown":
            continue
        if not item["selected"]:
            raise EvidenceError("unselected parts cannot claim workset use")
        if use is None or use["host_conditions"].get("task_workset_ref") != payload["workset_ref"]:
            raise EvidenceError("phase report requires MethodUse bound to this exact workset")
        if payload["use_ref"] not in observation["evidence_refs"]:
            raise EvidenceError("phase report must cite its MethodUse")
        if payload["part_id"] not in method_selection.get("used_parts", []):
            raise EvidenceError("phase report requires the explicitly used part")
        if phase == "executed" and (node is None or payload["node_ref"] not in observation["evidence_refs"]):
            raise EvidenceError("executed report requires its explicit node")
        if phase == "executed" and not node["detail"]["receipt_refs"]:
            raise EvidenceError("executed report requires an existing execution receipt")
    return dict(selected=item["selected"], disposition=item["decision"]["disposition"],
        activation=activation, applicability=next(c["applicability"] for c in workset["review"]["candidates"]
            if (c["resource_id"], c["part_id"]) == (payload["resource_id"], payload["part_id"])),
        verification_scope="named resource and explicit association chain; not every indexed resource",
        resource_read="verified_bytes_or_record_now; historical reading and host comprehension are not inferred",
        method_use_basis=use["basis"] if use else "unknown",
        part_attribution="explicit" if method_selection and payload["part_id"] in method_selection.get("used_parts", []) else "unknown",
        node_state=node["detail"]["state"] if node else "unknown",
        evaluation_conclusion=evaluated["conclusion"] if evaluated else None,
        selection_choice=selection["detail"]["choice"] if selection else None,
        selection_via_experience_refs=selection_via,
        chain_complete=all(payload[k] is not None for k in REF_FIELDS[2:]),
        observations=payload["observations"], provenance=payload["provenance"],
        causal_benefit="not_established", automatic_activation=False, execution_permission="not_granted")


def record_resource_usage(journal, payload, directory, *, source_id, source_kind, workspace=None):
    """Append a validated ordinary Artifact. Replay does not mint a MethodUse."""
    from .publication import _save
    from .schema import Artifact
    _text(source_id); _text(source_kind)
    payload = validate_resource_usage(payload)
    with verified_read():
        _verify(payload, _material_reader(workspace, journal, source_id), workspace)
    path = Path(directory).absolute() / (digest(payload) + ".json")
    _save(path, payload)
    artifact = Artifact(str(path), **file_identity(path), source_kind=source_kind, role="diagnostic",
        bindings=dict(schema=SCHEMA, task_id=payload["task_id"], source_id=source_id,
                      evidence_refs=usage_dependencies(payload), resource_usage_links=_links(payload)))
    return journal.record(artifact)


@bounded_read
def read_resource_usage(workspace, ref):
    """Verify exact refs and phase limits; never infer a missing link by name."""
    read = _material_reader(workspace)
    material = read(ref)
    payload = validate_resource_usage(material["content"])
    bindings = material["record"].get("bindings", {})
    if bindings.get("evidence_refs") != usage_dependencies(payload) or bindings.get("resource_usage_links") != _links(payload):
        raise EvidenceError("resource usage Artifact must bind its complete dependencies and links")
    return dict(ref=ref, payload=payload, **_verify(payload, read, workspace))
