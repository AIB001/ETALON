"""Optional source-backed proposal Artifacts, never an executor or adoption gate.

A candidate identifies intended content relative to an exact base and scope.
An episode identifies why that same candidate was considered on this occasion.
Shape validation is deliberately separate from verifying the referenced originals.
"""
from __future__ import annotations

import json

from etalon.active.schema import canonical, digest
from .continuation import _fields, _list, _refs, _text
from .evidence_refs import EvidenceError, _sha, validate_ref
from .improvement import CONTEXT, OPPORTUNITY, PATCH, EXPERIENCE, _conditions, read_material
from .read_scope import bounded_read

CANDIDATE_BRIEF = "etalon-candidate-brief/1"
SCHEMAS = {CANDIDATE_BRIEF}
MAX_REVIEWED = 32
MAX_CLOSURE = 128


def _ref_identity(ref):
    """A verified source alias/bytes selector does not create a new record."""
    validate_ref(ref)
    return {k: ref[k] for k in ("record_id", "record_digest", "member") if k in ref}


def _same(left, right):
    return _ref_identity(left) == _ref_identity(right)


def _unique(refs):
    # Preserve full original refs, but collapse aliases after original verification.
    result = {}
    for ref in refs:
        result.setdefault(canonical(_ref_identity(ref)), validate_ref(ref))
    return list(result.values())


def _strings(values, *, nonempty=False):
    for value in _list(values, nonempty=nonempty):
        _text(value)


def _candidate_key(payload):
    return digest({"base": _ref_identity(payload["base_method_ref"]),
                   **{k: payload[k] for k in ("scope", "conditions", "target_part", "change_identity")}})


def _episode_key(payload):
    return digest({"candidate_key": payload["candidate_key"],
                   **{k: _ref_identity(payload[k]) for k in ("opportunity_ref", "context_ref")}})


def validate_content(payload):
    """Validate a detached JSON value; this alone never verifies source evidence."""
    _fields(payload, ("schema", "task_id", "scope", "conditions", "method_refs", "supersedes",
        "opportunity_ref", "context_ref", "base_method_ref", "family_ref", "title", "rationale",
        "target_part", "change", "change_identity", "expected_effect", "frozen_invariants",
        "quality_criteria", "normal_execution", "applicability", "missing_evidence", "missing",
        "support_refs", "counter_refs", "revision_ref", "patch_ref", "evaluation_refs", "experience_refs",
        "reviewed_candidate_refs", "same_candidate_refs", "candidate_key", "episode_key", "status",
        "claim_status", "independent_benefit", "adoption"))
    if payload["schema"] != CANDIDATE_BRIEF:
        raise EvidenceError("unknown candidate brief schema")
    for key in ("task_id", "scope", "title", "rationale", "target_part"):
        _text(payload[key])
    _conditions(payload["conditions"])
    for key, value in payload.items():
        if key.endswith("_refs") or key == "supersedes":
            _refs(value)
        elif key.endswith("_ref") and value is not None:
            validate_ref(value)
    if payload["method_refs"] != [payload["base_method_ref"]]:
        raise EvidenceError("brief method_refs must identify its exact base")
    change = payload["change"]
    _fields(change, ("kind", "before_ref", "after_ref"))
    if change["kind"] not in {"method_content", "workflow"}:
        raise EvidenceError("candidate change must replace method content or a workflow")
    for key in ("before_ref", "after_ref"):
        validate_ref(change[key])
    identity = payload["change_identity"]
    _fields(identity, ("kind", "before_digest", "after_digest"))
    for key in ("before_digest", "after_digest"):
        _sha(identity[key])
    if identity["kind"] != change["kind"] or identity["before_digest"] == identity["after_digest"]:
        raise EvidenceError("no content change: retain/no-change has no candidate")
    effect = payload["expected_effect"]
    _fields(effect, ("metric", "unit", "direction", "hypothesis"))
    for value in effect.values():
        _text(value)
    if effect["direction"] not in {"increase", "decrease", "preserve"}:
        raise EvidenceError("expected effect direction is not measured improvement")
    for key in ("frozen_invariants", "quality_criteria"):
        _strings(payload[key], nonempty=True)
    execution = payload["normal_execution"]
    _fields(execution, ("execution_ref", "description", "discriminator", "metric", "unit", "success_criterion"))
    for key in ("description", "discriminator", "metric", "unit", "success_criterion"):
        _text(execution[key])
    if execution["execution_ref"] is not None:
        validate_ref(execution["execution_ref"])
    if (execution["metric"], execution["unit"]) != (effect["metric"], effect["unit"]):
        raise EvidenceError("normal execution must discriminate the expected measurable effect")
    applicability = payload["applicability"]
    _fields(applicability, ("applies_when", "does_not_apply_when"))
    for values in applicability.values():
        _strings(values, nonempty=True)
    for key in ("missing_evidence", "missing"):
        _strings(payload[key])
    if not set(payload["missing_evidence"]) <= set(payload["missing"]):
        raise EvidenceError("declared missing evidence cannot disappear")
    if execution["execution_ref"] is None and not payload["missing"]:
        raise EvidenceError("an executable proposal needs an existing normal execution reference")
    if payload["status"] != ("incomplete" if payload["missing"] else "ready_for_normal_execution"):
        raise EvidenceError("candidate readiness conflicts with missing evidence")
    if (payload["claim_status"], payload["independent_benefit"], payload["adoption"]) != (
            "hypothesis", "not_established", "not_performed"):
        raise EvidenceError("candidate brief is a hypothesis, never evaluation or adoption")
    if len(payload["reviewed_candidate_refs"]) > MAX_REVIEWED:
        raise EvidenceError("duplicate detection needs an explicit bounded list")
    reviewed = {canonical(_ref_identity(r)) for r in payload["reviewed_candidate_refs"] + payload["supersedes"]}
    if any(canonical(_ref_identity(r)) not in reviewed for r in payload["same_candidate_refs"]):
        raise EvidenceError("same-candidate refs must come from the explicit reviewed list")
    if payload["candidate_key"] != _candidate_key(payload) or payload["episode_key"] != _episode_key(payload):
        raise EvidenceError("candidate or episode identity mismatch")
    return json.loads(canonical(payload))


class _Reader:
    def __init__(self, read):
        self.read = read
        self.cache = {}
        self.checked_briefs = set()
        self.active_briefs = set()

    def material(self, ref):
        validate_ref(ref)
        key = canonical(ref)
        if key not in self.cache:
            if len(self.cache) >= MAX_CLOSURE:
                raise EvidenceError("candidate reference closure exceeds bounded read budget")
            material = self.read(ref)
            self.cache[key] = material
        return self.cache[key]

    def body(self, ref, schema=None):
        material = self.material(ref)
        if not material.get("complete"):
            raise EvidenceError("structured candidate reference is incomplete; narrow the artifact")
        content = material["content"]
        if not isinstance(content, dict) or schema is not None and content.get("schema") != schema:
            raise EvidenceError("wrong candidate reference schema: " + str(schema))
        return content

    def brief(self, ref):
        key = canonical(ref)
        if key in self.active_briefs:
            raise EvidenceError("cyclic candidate ancestry")
        payload = validate_content(self.body(ref, CANDIDATE_BRIEF))
        if key not in self.checked_briefs:
            self.active_briefs.add(key)
            _verify(self, payload)
            self.active_briefs.remove(key)
            self.checked_briefs.add(key)
        return payload


def _content_digest(reader, ref):
    material = reader.material(ref)
    record = material["record"]
    if not {"sha256", "bytes", "path"} <= set(record):
        raise EvidenceError("intended content must be a verified Artifact")
    # Ignore JSON whitespace and object-key ordering, not actual content edits.
    return digest(material["content"]) if material["content"] is not None else digest(
        {"sha256": record["sha256"], "bytes": record["bytes"]})


def _derive(reader, payload):
    from .improvement import validate_content as validate_improvement
    from .composition import WORKFLOW, validate_workflow
    from .schema import method_record_from_dict

    context = validate_improvement(reader.body(payload["context_ref"], CONTEXT))
    opportunity = validate_improvement(reader.body(payload["opportunity_ref"], OPPORTUNITY))
    base = reader.body(payload["base_method_ref"], "etalon-method-version/1")
    method_record_from_dict(base)
    if (not _same(opportunity["context_ref"], payload["context_ref"])
            or opportunity["opportunity_key"] != context["opportunity_key"]
            or not _same(context["target_ref"], payload["base_method_ref"])):
        raise EvidenceError("candidate must bind the exact opportunity, context and base")
    for key in ("task_id", "scope", "conditions"):
        if payload[key] != context[key] or opportunity[key] != context[key]:
            raise EvidenceError("candidate scope/conditions differ from its exact context")
    # Verify context originals; its declared ready status is not source proof.
    for ref in context["problem_refs"] + context["evidence_refs"]:
        reader.material(ref)
    change = payload["change"]
    before_digest = _content_digest(reader, change["before_ref"])
    after_digest = _content_digest(reader, change["after_ref"])
    if change["kind"] == "method_content":
        if base["content_ref"] != {"artifact_ref": change["before_ref"]["record_id"]}:
            raise EvidenceError("method content replacement must identify the exact base content Artifact")
    else:
        before = validate_workflow(reader.body(change["before_ref"], WORKFLOW))
        after = validate_workflow(reader.body(change["after_ref"], WORKFLOW))
        if not any(_same(ref, payload["base_method_ref"]) for ref in before["method_refs"]):
            raise EvidenceError("workflow change must bind the exact base method")
        # A display rename or another publication link is not a workflow edit.
        before_digest, after_digest = (digest({k: v for k, v in flow.items() if k not in {"name", "supersedes"}})
                                       for flow in (before, after))
    identity = {"kind": change["kind"], "before_digest": before_digest, "after_digest": after_digest}
    if before_digest == after_digest:
        raise EvidenceError("no content change: retain/no-change has no candidate")
    missing = list(dict.fromkeys(payload["missing_evidence"] + context["missing"]))
    normal_ref = payload["normal_execution"]["execution_ref"]
    if normal_ref is None:
        missing.append("normal_execution.execution_ref is not supplied")
    else:
        from .continuation import validate_content as validate_step
        step = validate_step(reader.body(normal_ref, "etalon-research-step/1"))
        if step["operation"] not in {"node", "evaluation_plan"}:
            raise EvidenceError("smallest discriminating execution must be an existing normal node or evaluation plan")
        if not any(_same(ref, payload["base_method_ref"]) for ref in step["method_refs"]):
            raise EvidenceError("normal execution must include the exact comparator/base method")
    for ref in payload["support_refs"] + payload["counter_refs"]:
        reader.material(ref)
    if payload["family_ref"] is not None:
        from .defect_family import _check as check_family
        family = check_family(reader.material, reader.body(payload["family_ref"], "etalon-defect-family/1"))
        if not any(m["accepted"] and _same(m["opportunity_ref"], payload["opportunity_ref"])
                   for m in family["members"]):
            raise EvidenceError("family must include the exact opportunity as an accepted reviewed member")
    revision = None
    if payload["revision_ref"] is not None:
        from .method_revision import build_method_revision
        revision = reader.body(payload["revision_ref"], "etalon-method-revision/1")
        build_method_revision(**{k: v for k, v in revision.items() if k != "schema"})
        if not _same(revision["base_method_ref"], payload["base_method_ref"]):
            raise EvidenceError("revision refers to a different candidate base")
        if not revision["candidate_version_refs"]:
            raise EvidenceError("no-change revision has no candidate; keep it as counterevidence")
        for key in ("problem_refs", "candidate_version_refs", "trial_refs", "accounting_refs"):
            for original in revision[key]:
                reader.material(original)
        for key in ("producer_method_ref", "producer_use_ref"):
            reader.material(revision[key])
        if change["kind"] == "method_content":
            candidates = [reader.body(r, "etalon-method-version/1") for r in revision["candidate_version_refs"]]
            if not any(m["content_ref"] == {"artifact_ref": change["after_ref"]["record_id"]}
                       and payload["base_method_ref"]["record_id"] in m["parents"] for m in candidates):
                raise EvidenceError("revision does not identify the intended candidate content")
        elif payload["patch_ref"] is None:
            raise EvidenceError("workflow revision needs its exact patch binding")
    patch = None
    if payload["patch_ref"] is not None:
        patch = validate_improvement(reader.body(payload["patch_ref"], PATCH))
        for key in ("base_method_ref", "context_ref", "opportunity_ref"):
            if not _same(patch[key], payload[key]):
                raise EvidenceError("patch differs from candidate brief bindings")
        if any(patch[k] != payload[k] for k in ("task_id", "scope", "conditions")):
            raise EvidenceError("patch conditions differ from candidate brief")
        candidate = reader.body(patch["candidate_method_ref"], "etalon-method-version/1")
        method_record_from_dict(candidate)
        if payload["base_method_ref"]["record_id"] not in candidate["parents"]:
            raise EvidenceError("patch candidate must descend from the exact base")
        if revision is not None and not any(_same(r, patch["candidate_method_ref"])
                                            for r in revision["candidate_version_refs"]):
            raise EvidenceError("revision does not include the linked patch candidate")
        if change["kind"] == "method_content":
            if candidate["content_ref"] != {"artifact_ref": change["after_ref"]["record_id"]}:
                raise EvidenceError("patch candidate content differs from intended replacement")
        elif not (_same(patch["workflow_before_ref"], change["before_ref"])
                  and _same(patch["workflow_after_ref"], change["after_ref"])):
            raise EvidenceError("patch workflow differs from intended replacement")
    for ref in payload["evaluation_refs"]:
        if patch is None:
            raise EvidenceError("candidate evaluation requires its exact patch; unrelated results are evidence refs")
        evaluation = reader.body(ref, "etalon-method-evaluation/1")
        method_record_from_dict(evaluation)
        if payload["base_method_ref"]["record_id"] not in evaluation["comparators"]:
            raise EvidenceError("evaluation lacks the exact base comparator")
        if patch and patch["candidate_method_ref"]["record_id"] not in evaluation["subjects"]:
            raise EvidenceError("evaluation lacks the patch candidate")
    for ref in payload["experience_refs"]:
        experience = validate_improvement(reader.body(ref, EXPERIENCE))
        if not patch or not _same(experience["patch_ref"], payload["patch_ref"]):
            raise EvidenceError("experience requires this exact candidate patch")
        for original in experience["support_refs"] + experience["counter_refs"]:
            reader.material(original)
        for key in ("evaluation_ref", "resolution_ref"):
            if experience[key] is not None:
                reader.material(experience[key])
    return identity, list(dict.fromkeys(missing))


def _complete(reader, payload):
    identity, missing = _derive(reader, payload)
    payload.update(change_identity=identity, missing=missing,
                   status="incomplete" if missing else "ready_for_normal_execution")
    payload["candidate_key"] = _candidate_key(payload)
    payload["episode_key"] = _episode_key(payload)
    same, counters = [], list(payload["counter_refs"])
    for ref in payload["experience_refs"]:
        counters.extend(reader.body(ref, EXPERIENCE)["counter_refs"])
    for ref in payload["reviewed_candidate_refs"] + payload["supersedes"]:
        previous = reader.brief(ref)
        if previous["candidate_key"] == payload["candidate_key"]:
            same.append(ref)
            counters.extend(previous["counter_refs"])
        elif ref in payload["supersedes"]:
            raise EvidenceError("a superseded brief must concern the same candidate content and scope")
    payload["same_candidate_refs"] = _unique(same)
    payload["counter_refs"] = _unique(counters)
    return validate_content(payload)


def _verify(reader, payload):
    rebuilt = _complete(reader, json.loads(canonical(payload)))
    if rebuilt != payload:
        raise EvidenceError("candidate brief conflicts with verified originals or retained counterevidence")
    return rebuilt


@bounded_read
def build_candidate_brief(workspace, *, opportunity_ref, context_ref, base_method_ref, title,
                          rationale, target_part, change, expected_effect, frozen_invariants,
                          quality_criteria, normal_execution, applicability, missing_evidence=(),
                          support_refs=(), counter_refs=(), family_ref=None, revision_ref=None,
                          patch_ref=None, evaluation_refs=(), experience_refs=(), duplicate_refs=(),
                          supersedes=()):
    """Read only explicit refs. Complete proposal metadata grants no execution.

    Intended before/after Artifacts need not have a candidate MethodVersion yet.
    No-change has no candidate; keep an existing retain/no-change record instead.
    """
    _list(list(duplicate_refs), limit=MAX_REVIEWED)
    reader = _Reader(lambda ref: read_material(workspace, ref))
    context = reader.body(context_ref, CONTEXT)
    payload = {k: context[k] for k in ("task_id", "scope", "conditions")}
    payload.update(schema=CANDIDATE_BRIEF, opportunity_ref=opportunity_ref, context_ref=context_ref,
        base_method_ref=base_method_ref, method_refs=[base_method_ref], title=title, rationale=rationale,
        target_part=target_part, change=change, expected_effect=expected_effect,
        frozen_invariants=list(frozen_invariants), quality_criteria=list(quality_criteria),
        normal_execution=normal_execution, applicability=applicability, missing_evidence=list(missing_evidence),
        support_refs=list(support_refs), counter_refs=list(counter_refs), family_ref=family_ref,
        revision_ref=revision_ref, patch_ref=patch_ref, evaluation_refs=list(evaluation_refs),
        experience_refs=list(experience_refs), reviewed_candidate_refs=list(duplicate_refs),
        supersedes=list(supersedes), claim_status="hypothesis", independent_benefit="not_established",
        adoption="not_performed")
    return _complete(reader, json.loads(canonical(payload)))


@bounded_read
def validate_links(workspace, payload):
    """Verify the complete explicit, bounded provenance closure, read-only."""
    return _verify(_Reader(lambda ref: read_material(workspace, ref)), validate_content(payload))


@bounded_read
def read_candidate_brief(workspace, ref):
    return _Reader(lambda target: read_material(workspace, target)).brief(ref)


@bounded_read
def validate_local_links(journal, content, source_id, external_workspace=None):
    """Publication check for existing record_content, including external originals."""
    from .schema import Artifact

    def read(ref):
        validate_ref(ref)
        if ref["source_id"] != source_id or "member" in ref:
            if external_workspace is None:
                raise EvidenceError("candidate external references need an explicit verification workspace")
            return read_material(external_workspace, ref)
        body = journal.get(ref["record_id"])
        if digest(body) != ref["record_digest"]:
            raise EvidenceError("candidate record digest mismatch")
        if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (body.get("sha256"), body.get("bytes")):
            raise EvidenceError("candidate artifact identity mismatch")
        if not {"path", "sha256", "bytes", "source_kind", "role"} <= set(body):
            return {"record": body, "content": body, "complete": True}
        path = journal.artifact_path(Artifact(**body))
        if body["bytes"] > 32768:
            return {"record": body, "content": None, "complete": False}
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            value = None
        return {"record": body, "content": value, "complete": True}

    return _verify(_Reader(read), validate_content(content))
