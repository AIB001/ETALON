"""Meaning identity survives workset changes; synthetic fixtures, not science."""
import copy

import pytest

from etalon.active.schema import digest
from etalon.research.evidence_refs import EvidenceError
from etalon.research.meta_context import (
    compare_definitions, make_task_profile, describe_resource, index_resources,
    prepare_adaptation_context, review_adaptation, build_task_workset,
    validate_workset, workset_summary,
    note_workset_summary,
)
from test_meta_context import setup, profile, part, review
from test_research_publication import case


def defined_profile(definitions, target="SND1"):
    old = profile(target)
    return make_task_profile(**{k: v for k, v in old.items() if k not in {"schema", "digest"}},
                             definitions=definitions)


def resource(base, *, definitions=None, irrelevant=()):
    p = part("method", {"target": "SND1", "objective": "pose"}, irrelevant=irrelevant)
    if definitions is not None:
        p["definitions"] = definitions
    return describe_resource(resource_id="local", kind="experience", content_ref=base["content_ref"],
                             summary="synthetic scoped interpretation", parts=[p])


def test_same_label_or_target_does_not_match_different_metric_meaning(case):
    ws, resources = setup(case)
    current = {"metric:success": digest({"unit": "count", "population": "all poses"})}
    other = {"metric:success": digest({"unit": "count", "population": "QC passed poses"})}
    idx = index_resources([resource(resources[0], definitions=other)], workspace=ws)
    candidate = prepare_adaptation_context(defined_profile(current), idx)["candidates"][0]
    assert candidate["applicability"] == "conflict"
    assert candidate["checks"]["definition:metric:success"] == "conflict"
    with pytest.raises(EvidenceError, match="unknown/conflicting"):
        review(defined_profile(current), idx, {("local", "method")})


def test_legacy_missing_definition_is_unknown_and_explicit_independence_is_allowed(case):
    ws, resources = setup(case)
    defs = {"metric:success": "a" * 64}
    old = resource(resources[0])
    idx = index_resources([old], workspace=ws)
    assert prepare_adaptation_context(defined_profile(defs), idx)["candidates"][0]["applicability"] == "unknown"
    independent = resource(resources[0], irrelevant=("definition:metric:success",))
    idx = index_resources([independent], workspace=ws)
    c = prepare_adaptation_context(defined_profile(defs), idx)["candidates"][0]
    assert c["applicability"] == "match"
    assert c["checks"]["definition:metric:success"] == "not_applicable"
    with pytest.raises(EvidenceError, match="cannot also be irrelevant"):
        resource(resources[0], definitions=defs, irrelevant=("definition:metric:success",))


def test_known_unknown_and_mismatched_definitions():
    got = compare_definitions({"a": "a" * 64, "b": "b" * 64, "c": None},
                              {"a": "a" * 64, "b": "c" * 64, "c": "c" * 64})
    assert got == {"missing": ["c"], "mismatched": ["b"], "matched": ["a"]}
    assert compare_definitions({"missing": "a" * 64}, {})["missing"] == ["missing"]
    with pytest.raises(EvidenceError, match="definition identity"):
        compare_definitions({"metric": "same-name-is-not-a-definition"}, {})


def test_workset_reentry_with_changed_definition_stops_old_activation(case, monkeypatch):
    import etalon.research.meta_context as meta
    ws, resources = setup(case)
    defs = {"metric:success": "a" * 64}
    # Fixture template expressly does not rely on this scientific endpoint.
    template = copy.deepcopy(resources[0])
    template = describe_resource(resource_id="base", kind="template", content_ref=template["content_ref"],
        summary="generic fixture", parts=[part(irrelevant=("target", "objective", "definition:metric:success"))])
    idx = index_resources([template, resource(resources[0], definitions=defs)], workspace=ws)
    p = defined_profile(defs)
    rv = review(p, idx, {("base", "base"), ("local", "method")})
    work = build_task_workset(p, idx, rv, template={"resource_id": "base", "part_id": "base"})
    assert validate_workset(work) == work
    monkeypatch.setattr(meta, "read_task_workset", lambda *a, **kw: {
        "status": "active", "workset": work, "ref": {}, "activation_ref": {}})
    same = workset_summary(ws, {}, definitions=defs)
    assert same["status"] == "active" and same["enabled"]
    changed = workset_summary(ws, {}, definitions={"metric:success": "b" * 64})
    assert changed["status"] == "review_required"
    assert changed["enabled"] == changed["selected_skill_sources"] == []
    assert changed["changed_definitions"] == ["metric:success"]
    # This is a resource-activation boundary, not a prohibition on exploration.
    fallback_review = review(p, idx, {("base", "base")})
    fallback = build_task_workset(p, idx, fallback_review, template={"resource_id": "base", "part_id": "base"},
                                 mode="template", fallback_reason="Use generic fixture without old endpoint assumptions")
    assert fallback["enabled"] == [{"resource_id": "base", "part_id": "base"}]


def test_note_binding_uses_existing_workset_and_rejects_another_task(case, monkeypatch):
    import etalon.research.meta_context as meta
    from etalon.research.evidence_refs import validate_ref
    import json
    entry = json.loads(case["parent"].read_text())
    binding = {"workspace": entry["workspace"], "checkpoint_ref": entry["checkpoint_ref"]}
    validate_ref(binding["checkpoint_ref"])
    seen = []
    def summary(workspace, checkpoint_ref, **kw):
        seen.append((workspace, checkpoint_ref, kw))
        return {"status": "active", "task_id": "expected", "digest": "fixture"}
    monkeypatch.setattr(meta, "workset_summary", summary)
    assert note_workset_summary({}, task_id="expected") is None
    assert note_workset_summary({"task_workset": binding}, task_id="expected", definitions={"metric": "a"*64})["status"] == "active"
    assert seen[0][2]["definitions"] == {"metric": "a"*64}
    with pytest.raises(EvidenceError, match="another task"):
        note_workset_summary({"task_workset": binding}, task_id="other")
    with pytest.raises(EvidenceError, match="published workspace"):
        note_workset_summary({"task_workset": {"status": "active"}}, task_id="expected")
