"""Optional host-reviewed defect groups, stored as ordinary immutable Artifacts.

Exact opportunities remain unchanged. Shared bytes are an evidence identity, not
a semantic clustering rule; only an explicit same_issue review accepts a member.
No helper proposes, evaluates, closes another task, or creates a journal.
"""
from __future__ import annotations

import json

from etalon.active.schema import canonical, digest
from .continuation import _fields, _list, _refs
from .evidence_refs import EvidenceError, _sha, _text, validate_ref
from .improvement import CONTEXT, OPPORTUNITY, _conditions, read_material
from .improvement import validate_content as validate_improvement
from .read_scope import bounded_read

DEFECT_FAMILY = "etalon-defect-family/1"
SCHEMAS = {DEFECT_FAMILY}
_UNKNOWN = {"unknown", "unspecified", "not_checked", "not checked", "*", "?"}
_BOUNDARY = ("task_id", "scope", "conditions", "capability_id", "target_identity")
_MEMBER_FIELDS = (
    "context_ref", "opportunity_ref", "task_id", "scope", "conditions", "target_ref",
    "target_identity", "method_refs", "problem_refs", "evidence_refs", "evidence_identities",
    "issue_key", "relation", "accepted", "grouping_reason", "review_refs", "counter_refs",
)


def _unique(values):
    return [v for _, v in sorted({canonical(v): v for v in values}.items())]


def _known(value):
    _text(value)
    if value.strip().casefold() in _UNKNOWN:
        raise EvidenceError("unknown scope/conditions cannot establish a same-issue match")


def _boundary(value):
    _text(value["task_id"])
    _known(value["scope"])
    _conditions(value["conditions"])
    for condition in value["conditions"].values():
        _known(condition)


def _issue_key(member):
    return digest({k: member[k] for k in (
        "scope", "conditions", "target_identity", "evidence_identities")})


def _family_key(value):
    return digest({k: value[k] for k in (*_BOUNDARY, "anchor_issue_key")})


def _validate_member(member):
    _fields(member, _MEMBER_FIELDS)
    for key in ("context_ref", "opportunity_ref", "target_ref"):
        validate_ref(member[key])
    _text(member["task_id"])
    _text(member["scope"])
    _conditions(member["conditions"])
    for key in ("method_refs", "problem_refs", "evidence_refs", "review_refs", "counter_refs"):
        _refs(member[key], nonempty=key in {"method_refs", "problem_refs", "review_refs"})
    _sha(member["target_identity"])
    for identity in _list(member["evidence_identities"], nonempty=True):
        _sha(identity)
    if member["evidence_identities"] != sorted(set(member["evidence_identities"])):
        raise EvidenceError("evidence identities must be unique and sorted")
    if member["issue_key"] != _issue_key(member):
        raise EvidenceError("defect issue identity mismatch")
    if member["relation"] not in {"same_issue", "reference_only", "rejected"}:
        raise EvidenceError("member needs an explicit host review relation")
    if type(member["accepted"]) is not bool or member["accepted"] != (member["relation"] == "same_issue"):
        raise EvidenceError("only same_issue is an accepted member")
    if member["accepted"]:
        _boundary(member)
    _text(member["grouping_reason"])


def validate_content(payload):
    """Validate the declaration; original evidence is rechecked by verified readers."""
    _fields(payload, ("schema", "task_id", "scope", "conditions", "capability_id", "target_ref",
        "target_identity", "mechanism_hypothesis", "anchor_issue_key", "family_key", "members",
        "accepted_issue_keys", "method_refs", "supersedes"))
    if payload["schema"] not in SCHEMAS:
        raise EvidenceError("unknown defect family content")
    _boundary(payload)
    _text(payload["capability_id"])
    _text(payload["mechanism_hypothesis"])
    if payload["target_ref"] is not None:
        validate_ref(payload["target_ref"])
        _sha(payload["target_identity"])
    elif payload["target_identity"] is not None:
        raise EvidenceError("target identity requires its exact target ref")
    _refs(payload["method_refs"], nonempty=True)
    _refs(payload["supersedes"])
    accepted = []
    for member in _list(payload["members"], nonempty=True):
        _validate_member(member)
        if member["accepted"]:
            if any(member[k] != payload[k] for k in ("task_id", "scope", "conditions")):
                raise EvidenceError("same_issue member task/scope/conditions mismatch")
            if payload["target_ref"] is not None and member["target_ref"] != payload["target_ref"]:
                raise EvidenceError("same_issue member target mismatch")
            accepted.append(member["issue_key"])
    if payload["members"] != _unique(payload["members"]):
        raise EvidenceError("duplicate or unordered member reviews")
    if not accepted or payload["accepted_issue_keys"] != sorted(set(accepted)):
        raise EvidenceError("family requires explicit accepted issue identities")
    if payload["anchor_issue_key"] not in accepted or payload["family_key"] != _family_key(payload):
        raise EvidenceError("defect family identity mismatch")
    expected_methods = _unique([r for m in payload["members"] for r in m["method_refs"]])
    if payload["method_refs"] != expected_methods:
        raise EvidenceError("family method refs must retain every reviewed member")
    return json.loads(canonical(payload))


def _identity(material):
    record = material["record"]
    if "member" in material["ref"]:
        # read_material returns the verified selected member's byte identity;
        # the envelope digest would incorrectly collapse all sibling members.
        ref = material["ref"]
        return digest({"kind": "artifact_bytes", "sha256": ref["sha256"], "bytes": ref["bytes"]})
    if {"path", "sha256", "bytes", "source_kind", "role"} <= set(record):
        # Paths, display labels, source aliases and Artifact IDs are not new bytes.
        return digest({"kind": "artifact_bytes", "sha256": record["sha256"], "bytes": record["bytes"]})
    return digest({"kind": "record", "content": record})


def _content(read, ref, schema):
    material = read(ref)
    value = material["content"]
    if not material["complete"] or not isinstance(value, dict) or value.get("schema") != schema:
        raise EvidenceError("complete verified " + schema + " content required")
    return value


def _review(read, *, context_ref, opportunity_ref, relation, grouping_reason, review_refs, counter_refs=()):
    context = validate_improvement(_content(read, context_ref, CONTEXT))
    opportunity = validate_improvement(_content(read, opportunity_ref, OPPORTUNITY))
    original = validate_improvement(_content(read, opportunity["context_ref"], CONTEXT))
    if context["status"] != "ready_for_revision" or original["status"] != "ready_for_revision" or (
            context["opportunity_key"] != opportunity["opportunity_key"] or
            original["opportunity_key"] != opportunity["opportunity_key"]):
        raise EvidenceError("opportunity requires its exact complete context identity")
    for ref in [*context["problem_refs"], *context["evidence_refs"], *context["method_refs"],
                *review_refs, *counter_refs]:
        read(ref)
    member = {k: context[k] for k in ("task_id", "scope", "conditions", "target_ref", "method_refs",
                                      "problem_refs", "evidence_refs")}
    # Explicit supporting evidence is the anchor when present. Relabelled problem
    # descriptions cannot manufacture new evidence; all problem refs stay retained.
    basis = context["evidence_refs"] or context["problem_refs"]
    member.update(context_ref=validate_ref(context_ref), opportunity_ref=validate_ref(opportunity_ref),
        target_identity=_identity(read(context["target_ref"])),
        evidence_identities=sorted({_identity(read(ref)) for ref in basis}), relation=relation,
        accepted=relation == "same_issue", grouping_reason=grouping_reason,
        review_refs=_unique(list(review_refs)), counter_refs=_unique(list(counter_refs)))
    member["issue_key"] = _issue_key(member)
    _validate_member(member)
    return json.loads(canonical(member))


@bounded_read
def review_defect_member(workspace, *, context_ref, opportunity_ref, relation, grouping_reason,
                         review_refs, counter_refs=()):
    """Bind a host's explicit review to verified originals; never infer a relation."""
    return _review(lambda ref: read_material(workspace, ref), context_ref=context_ref,
        opportunity_ref=opportunity_ref, relation=relation, grouping_reason=grouping_reason,
        review_refs=review_refs, counter_refs=counter_refs)


def _check(read, payload):
    content = validate_content(payload)
    if content["target_ref"] is not None and content["target_identity"] != _identity(read(content["target_ref"])):
        raise EvidenceError("family target identity differs from verified evidence")
    for member in content["members"]:
        rebuilt = _review(read, **{k: member[k] for k in (
            "context_ref", "opportunity_ref", "relation", "grouping_reason", "review_refs", "counter_refs")})
        if rebuilt != member:
            raise EvidenceError("member declaration differs from verified context/evidence")
    for ref in content["supersedes"]:
        prior = validate_content(_content(read, ref, DEFECT_FAMILY))
        if prior["family_key"] != content["family_key"] or any(prior[k] != content[k] for k in _BOUNDARY):
            raise EvidenceError("successor cannot change its family identity or applicability")
        if not {canonical(m) for m in prior["members"]} <= {canonical(m) for m in content["members"]}:
            raise EvidenceError("successor must retain prior reviews, conflicts and counterevidence")
    return content


@bounded_read
def build_defect_family(workspace, *, task_id, scope, conditions, capability_id, mechanism_hypothesis,
                        members, target_ref=None, prior_family_ref=None, supersedes=()):
    """Build a declaration or append-only revision from explicitly reviewed members.

    Passing the prior family retains its key and every earlier review. Changing
    wording never changes the family key; repeated bytes add no accepted issue.
    This payload alone is not permission to suppress an exact opportunity.
    """
    read = lambda ref: read_material(workspace, ref)
    priors = _unique([*supersedes, *([prior_family_ref] if prior_family_ref is not None else [])])
    previous = [_check(read, _content(read, ref, DEFECT_FAMILY)) for ref in priors]
    rows = _unique([*members, *(m for p in previous for m in p["members"])])
    for row in rows:
        _validate_member(row)
    accepted = sorted({m["issue_key"] for m in rows if m["accepted"]})
    if not accepted:
        raise EvidenceError("a family requires an explicitly accepted same_issue member")
    value = dict(schema=DEFECT_FAMILY, task_id=task_id, scope=scope, conditions=conditions,
        capability_id=capability_id, target_ref=target_ref,
        target_identity=_identity(read(target_ref)) if target_ref is not None else None,
        mechanism_hypothesis=mechanism_hypothesis,
        anchor_issue_key=previous[0]["anchor_issue_key"] if previous else accepted[0],
        members=rows, accepted_issue_keys=accepted,
        method_refs=_unique([r for member in rows for r in member["method_refs"]]), supersedes=priors)
    value["family_key"] = _family_key(value)
    return _check(read, value)


@bounded_read
def read_defect_family(workspace, ref):
    """Recheck original bytes and exact bindings of an explicitly selected family."""
    read = lambda target: read_material(workspace, target)
    return _check(read, _content(read, ref, DEFECT_FAMILY))


@bounded_read
def validate_local_links(journal, payload, source_id, external_workspace=None):
    """Publication guard for hand-built payloads; external refs need a pinned workspace."""
    from .schema import Artifact

    def read(ref):
        validate_ref(ref)
        if ref["source_id"] != source_id or "member" in ref:
            if external_workspace is None:
                raise EvidenceError("family external refs require a verified evidence workspace")
            return read_material(external_workspace, ref)
        body = journal.get(ref["record_id"])
        if digest(body) != ref["record_digest"]:
            raise EvidenceError("record digest mismatch")
        content = body
        complete = True
        if ref["record_id"].startswith("artifact-"):
            path = journal.artifact_path(Artifact(**body))
            if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (body["sha256"], body["bytes"]):
                raise EvidenceError("artifact identity mismatch")
            complete = body["bytes"] <= 32768
            try:
                content = json.loads(path.read_text("utf-8-sig")) if complete else None
            except (ValueError, UnicodeError):
                content = None
        return dict(ref=ref, record=body, content=content, complete=complete)

    return _check(read, payload)
