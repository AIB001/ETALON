"""Small, optional scientific-result descriptors over existing evidence objects.

A producer publishes a profile once (beside its result or as a Journal Artifact)
and binds runs to its digest. Definitions stay in the profile, actual conditions
and observations in each run. Domain-specific content is ordinary JSON: this
module neither executes methods nor evaluates, ranks, adopts or stores results.
Existing method/evaluation contracts may be linked through evidence references.
"""
from __future__ import annotations

import json

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, _sha, validate_ref

PROFILE_SCHEMA = "etalon-result-profile/1"
RESULT_SCHEMA = "etalon-scientific-result/1"


def _object(value, name, *, nonempty=False):
    if not isinstance(value, dict) or (nonempty and not value):
        raise EvidenceError(f"{name} must be an explicit JSON object")
    if any(not isinstance(k, str) or not k.strip() for k in value):
        raise EvidenceError(f"{name} requires nonempty string keys")


def _copy(value):
    # Reject coercion of numeric dictionary keys and non-JSON values instead of
    # allowing two distinct declarations to acquire the same serialized digest.
    def walk(item):
        if isinstance(item, dict):
            _object(item, "content")
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise EvidenceError("result content must be JSON data")
    walk(value)
    try:
        return json.loads(canonical(value))
    except (ValueError, TypeError) as exc:
        raise EvidenceError("result content must be finite JSON data") from exc


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{name} requires nonempty text")


def _references(value):
    if not isinstance(value, list):
        raise EvidenceError("references must be a list of existing evidence refs")
    for ref in value:
        validate_ref(ref)


def build_result_profile(*, profile_id, method, metrics, conditions=None,
                         references=(), extensions=None):
    """Describe a method and named metrics once; only ``definition`` is required
    per metric. Units, denominator, applicability, QC and other domain content
    belong in those definitions or extensions, without a global field catalog.

    ``conditions`` describes named run conditions; each must be supplied when
    binding (explicit null is allowed). Additional actual conditions are allowed.
    """
    profile = _copy(dict(schema=PROFILE_SCHEMA, profile_id=profile_id,
        method=method, metrics=metrics, conditions={} if conditions is None else conditions,
        references=list(references), extensions={} if extensions is None else extensions))
    profile["profile_digest"] = digest(profile)
    return validate_result_profile(profile)


def validate_result_profile(profile):
    profile = _copy(profile)
    _object(profile, "profile", nonempty=True)
    required = {"schema", "profile_id", "profile_digest", "method", "metrics",
                "conditions", "references", "extensions"}
    if set(profile) != required or profile["schema"] != PROFILE_SCHEMA:
        raise EvidenceError("unsupported or incomplete result profile")
    _text(profile["profile_id"], "profile_id")
    for key in ("method", "metrics", "conditions", "extensions"):
        _object(profile[key], key, nonempty=key in {"method", "metrics"})
    for metric, definition in profile["metrics"].items():
        _object(definition, "metric " + metric)
        _text(definition.get("definition"), "metric definition " + metric)
    _references(profile["references"])
    _sha(profile["profile_digest"])
    if profile["profile_digest"] != digest({k: v for k, v in profile.items()
                                           if k != "profile_digest"}):
        raise EvidenceError("result profile digest mismatch")
    return profile


def profile_definitions(profile):
    """Metric/condition identities independent of the method or profile name.

    Task profiles and changed methods can share these exact definitions without
    claiming that matching definitions alone establish a valid comparison.
    """
    profile = validate_result_profile(profile)
    return {f"{kind}:{name}": digest(definition)
            for kind, key in (("metric", "metrics"), ("condition", "conditions"))
            for name, definition in profile[key].items()}


def bind_scientific_result(profile, *, subjects, operation, conditions,
                           observations, assessment=None,
                           host_interpretation=None, references=()):
    """Bind actual producer values; preserve unknowns and assessment separately
    from a host's interpretation. Observation values may be scalars or domain
    objects (including explicit unknown/not-run statuses); no metric is computed.
    """
    profile = validate_result_profile(profile)
    return validate_result_binding(dict(schema=RESULT_SCHEMA,
        profile_id=profile["profile_id"], profile_digest=profile["profile_digest"],
        definitions=profile_definitions(profile),
        subjects=list(subjects), operation=operation, conditions=conditions,
        observations=observations, assessment=assessment,
        host_interpretation=host_interpretation, references=list(references)), profile)


def validate_result_binding(binding, profile=None):
    """Structural validation alone does not resolve definitions or verify files.
    Supplying the profile additionally checks exact binding and declared metrics.
    """
    binding = _copy(binding)
    _object(binding, "result", nonempty=True)
    required = {"schema", "profile_id", "profile_digest", "definitions", "subjects", "operation",
                "conditions", "observations", "assessment", "host_interpretation",
                "references"}
    if set(binding) != required or binding["schema"] != RESULT_SCHEMA:
        raise EvidenceError("unsupported or incomplete scientific result")
    _text(binding["profile_id"], "profile_id")
    _sha(binding["profile_digest"])
    _object(binding["definitions"], "definitions", nonempty=True)
    for key, value in binding["definitions"].items():
        if not key.startswith(("metric:", "condition:")):
            raise EvidenceError("definition key requires metric: or condition: prefix")
        _sha(value)
    if not isinstance(binding["subjects"], list) or not binding["subjects"]:
        raise EvidenceError("result requires explicit subjects")
    for subject in binding["subjects"]:
        _object(subject, "subject", nonempty=True)
    for key in ("operation", "conditions", "observations"):
        _object(binding[key], key, nonempty=key == "operation")
    if any("metric:" + name not in binding["definitions"] for name in binding["observations"]):
        raise EvidenceError("observation has no metric definition binding")
    for key in ("assessment", "host_interpretation"):
        if binding[key] is not None:
            _object(binding[key], key)
    _references(binding["references"])
    if profile is not None:
        profile = validate_result_profile(profile)
        if any(binding[k] != profile[k] for k in ("profile_id", "profile_digest")):
            raise EvidenceError("scientific result profile binding mismatch")
        if binding["definitions"] != profile_definitions(profile):
            raise EvidenceError("scientific result definition binding mismatch")
        if binding["observations"].keys() - profile["metrics"].keys():
            raise EvidenceError("observation has no metric definition in profile")
        if profile["conditions"].keys() - binding["conditions"].keys():
            raise EvidenceError("declared run conditions missing; use explicit null for unknown")
    return binding


def summarize_result_binding(binding, profile=None):
    """Lossless small reading context; callers page large domain values normally.
    Missing profiles stay unresolved; a digest or a label is not a definition.
    This is not a scientific validity or adoption assessment.
    """
    result = validate_result_binding(binding, profile)
    result["definition_status"] = "resolved" if profile is not None else "unresolved"
    if profile is not None:
        profile = validate_result_profile(profile)
        result["method"] = profile["method"]
        result["metric_definitions"] = {name: profile["metrics"][name]
                                        for name in result["observations"]}
        result["condition_definitions"] = profile["conditions"]
        result["profile_extensions"] = profile["extensions"]
        result["profile_references"] = profile["references"]
    return result


def compare_result_context(left, right):
    """Report exact declared-context equality, never scientific comparability.
    Null conditions remain unknown even when both results contain null. Different
    subjects may share a context; this does not imply independent observations.
    """
    left, right = validate_result_binding(left), validate_result_binding(right)
    def unknown(value):
        if isinstance(value, dict):
            return any(unknown(v) for v in value.values())
        if isinstance(value, list):
            return any(unknown(v) for v in value)
        return value is None
    differences = [key for key in ("profile_id", "profile_digest", "operation", "conditions")
                   if canonical(left[key]) != canonical(right[key])]
    has_unknown = any(unknown(row[key]) for row in (left, right)
                      for key in ("operation", "conditions"))
    names = left["definitions"].keys() | right["definitions"].keys()
    definition_differences = sorted(name for name in names
        if left["definitions"].get(name) != right["definitions"].get(name))
    return dict(same_declared_context=not differences and not definition_differences and not has_unknown,
                differences=differences, unknown_context=has_unknown,
                same_definitions=not definition_differences,
                definition_differences=definition_differences,
                scientific_comparability="not_assessed")
