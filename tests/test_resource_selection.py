"""Condition checks and feedback are declared-use evidence, never efficacy tests."""
from copy import deepcopy

import pytest

from etalon.active import CampaignStore, CampaignSpec, Endpoint
from etalon.active.schema import digest
from etalon.research import ResearchJournal
from etalon.research.meta_context import (
    compare_part_conditions, make_task_profile, validate_resource_parts,
)
from etalon.research.notes import patch_progress, read_entry, save_progress
from etalon.research.resource_selection import (
    feedback_for_query, normalize_query, record_resource_feedback, resource_applicability,
)
from etalon.research.saved_methods import publish_method, resolve_saved_method


def part(name, conditions=None, *, irrelevant=(), definitions=None):
    return {"part_id": name, "conditions": conditions or {}, "requires": [],
            "irrelevant": list(irrelevant), "available_at": "planning", "dependencies": [],
            **({"definitions": definitions} if definitions is not None else {})}


def test_parts_share_workset_semantics_without_requiring_every_target_to_match():
    query = {"conditions": {"target": "PB", "format": "coordinates"},
             "available_stages": ["planning"], "definitions": {"distance": "a" * 64}}
    portable = part("kernel", {"format": "coordinates"}, irrelevant=["target"],
                    definitions={"distance": "a" * 64})
    fixed = part("residues", {"target": "SND1", "format": "coordinates"},
                 definitions={"distance": "a" * 64})
    source = {"kind": "method", "parts": [portable, fixed]}
    unchanged = deepcopy(source)
    selected = resource_applicability(source, query)
    assert source == unchanged
    assert selected["status"] == "mixed" and selected["disposition"] == "adapt"
    assert [p["disposition"] for p in selected["parts"]] == ["direct-use", "adapt"]
    assert selected["parts"][0]["checks"]["target"] == "not_applicable"
    assert selected["parts"][1]["checks"]["target"] == "conflict"
    assert selected["coverage"] == {"part_count": 2, "match": 1, "conflict": 1, "unknown": 0}
    profile = make_task_profile(task_id="fixture", question="Software conditions only",
        conditions=query["conditions"], critical_conditions=list(query["conditions"]),
        definitions=query["definitions"], available_stages=query["available_stages"])
    assert selected == resource_applicability(source, profile)
    assert selected["parts"][0]["checks"] == compare_part_conditions(profile, portable)


def test_missing_dimensions_and_definitions_remain_unknown_and_countercases_visible():
    query = {"conditions": {"target": "PB"}, "available_stages": ["planning"],
             "definitions": {"success": "a" * 64}}
    legacy = resource_applicability({"kind": "method", "conditions": {"target": "PB"}}, query)
    assert legacy["status"] == "unknown" and legacy["disposition"] == "adapt"
    assert legacy["parts"][0]["checks"]["definition:success"] == "unknown"
    assert legacy["parts"][0]["checks"]["available_at"] == "unknown"
    contrary = resource_applicability({"kind": "experience", "outcome": "negative",
        "parts": [part("case", {"target": "SND1"}, definitions={"success": "b" * 64})]}, query)
    assert contrary["status"] == "conflict" and contrary["disposition"] == "reference"
    assert contrary["coverage"]["conflict"] == 1
    explicit_unknown = normalize_query({"critical_conditions": ["stereo"]})
    assert explicit_unknown["conditions"] == {"stereo": None}
    assert normalize_query(None) is None
    with pytest.raises(ValueError):
        normalize_query({"conditions": {"target": "PB"}, "automatic_adoption": True})


@pytest.fixture
def method(tmp_path):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
        [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    entry = tmp_path / "ENTRY.json"
    save_progress(ResearchJournal(store), entry, {"task_id": "selection", "branch_id": "main",
        "question": "Software fixture", "summary": "Scoped use", "conditions": {"target": "PB"}})
    script = tmp_path / "method.py"
    script.write_text("# Software fixture, never executed by these checks\n", encoding="utf-8")
    import sys
    arguments = {"method_id": "fixture/1", "command": [sys.executable, str(script)],
                 "files": [str(script)], "inputs": {}}
    published = publish_method(entry, "geometry", arguments, summary="Declared geometry fixture",
        conditions={"target": "SND1"}, family="geometry", parts=[
            part("kernel", irrelevant=["target"]), part("residues", {"target": "SND1"})])
    return entry, arguments, published


def feedback(entry, published, usage_id, outcome, *, conditions=None, **kwargs):
    return record_resource_feedback(entry, "geometry", usage_id=usage_id,
        resource_version=published["resource_version"], part_ids=["kernel"],
        conditions=conditions or {"target": "PB"}, outcome=outcome,
        summary="Fixture observation: " + outcome, **kwargs)


def test_feedback_is_idempotent_and_keeps_negative_conditions_after_later_success(method, tmp_path):
    entry, _, published = method
    original = tmp_path / "negative.json"
    original.write_text('{"result":"negative","scientific_claim":false}', encoding="utf-8")
    negative = feedback(entry, published, "negative", "negative", counterevidence=[original], cost={"wall_second": 2})
    feedback(entry, published, "unknown", "unknown", cost=None)
    feedback(entry, published, "positive", "positive", conditions={"target": "SND1"}, cost={"wall_second": 3})
    patch_progress(entry, {"summary": "Unrelated later continuation"})
    current = read_entry(entry, verify_originals=False)
    result = feedback(entry, published, "negative", "negative", counterevidence=[original], cost={"wall_second": 2})
    assert result["replayed"] and result["record_id"] == negative["record_id"]
    assert read_entry(entry, verify_originals=False)["record_id"] == current["record_id"]
    description = current["note"]["resources"]["feedback:geometry"]
    assert description["family"] == "geometry"
    assert description["feedback"]["outcome_counts"] == {"negative": 1, "unknown": 1, "positive": 1}
    selected = feedback_for_query(description, {"conditions": {"target": "PB"}})
    assert len(selected["cases"]) == 2
    matching = next(c for c in selected["cases"] if c["status"] == "match")
    assert matching["outcome_counts"] == {"negative": 1, "unknown": 1}
    assert matching["latest_outcomes"]["unknown"]["cost"] is None
    assert matching["latest_outcomes"]["negative"]["counterevidence"][0]["identity"]
    assert any(c["status"] == "conflict" for c in selected["cases"])
    assert not current["note"].get("counterevidence")  # receipts live in selected resource originals
    with pytest.raises(ValueError, match="already records different"):
        feedback(entry, published, "negative", "positive")


def test_exact_old_version_feedback_survives_method_replacement(method):
    entry, arguments, published = method
    resolved = resolve_saved_method(entry, "/resources/geometry")
    assert resolved["resource_version"] == published["resource_version"]
    replacement = publish_method(entry, "geometry", arguments, summary="Changed current description",
        conditions={"target": "PB"}, family="new-geometry", parts=[part("kernel", irrelevant=["target"])])
    with pytest.raises(ValueError, match="version changed"):
        feedback(entry, published, "old", "negative")
    feedback(entry, published, "old", "negative", resource_entry=resolved["details"])
    feedback(entry, replacement, "new", "positive")
    note = read_entry(entry, verify_originals=False)["note"]
    assert set(note["resources"]) == {"geometry", "feedback:geometry"}
    assert note["resources"]["feedback:geometry"]["family"] == ["geometry", "new-geometry"]
    cases = note["resources"]["feedback:geometry"]["cases"].values()
    assert {c["subject"]["version"] for c in cases} == {published["resource_version"], replacement["resource_version"]}
    assert digest(note["resources"]["geometry"]) == replacement["resource_version"]


def test_feedback_originals_are_sets_across_path_types_and_order(method, tmp_path):
    entry, _, published = method
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text('{"fixture":1}', encoding="utf-8")
    second.write_text('{"fixture":2}', encoding="utf-8")
    recorded = feedback(entry, published, "set-use", "unknown",
        evidence=[str(first), second, first], counterevidence=[second, str(second)])
    replay = feedback(entry, published, "set-use", "unknown",
        evidence=[second, first], counterevidence=[str(second)])
    assert replay["replayed"] and replay["record_id"] == recorded["record_id"]
    note = read_entry(entry, verify_originals=False)["note"]
    summary = note["resources"]["feedback:geometry"]
    assert summary["feedback"]["outcome_counts"] == {"unknown": 1}
    assert len(summary["last_receipt"]["evidence"]) == 2
    assert len(summary["last_receipt"]["counterevidence"]) == 1
    for invalid in (str(first), {"path": str(first)}, [None]):
        with pytest.raises(ValueError, match="must be a list of file paths"):
            feedback(entry, published, "invalid-input", "unknown", evidence=invalid)
    assert read_entry(entry, verify_originals=False)["record_id"] == recorded["record_id"]


def test_feedback_can_bind_exact_resource_in_a_different_journal(method, tmp_path):
    entry, _, published = method
    source = resolve_saved_method(entry, "/resources/geometry")["details"]
    target_store = CampaignStore(tmp_path / "next-task.sqlite")
    target_store.configure(CampaignSpec("next", 0, "wall_second", "fixture"),
        [Endpoint("next", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    target_entry = tmp_path / "NEXT.json"
    save_progress(ResearchJournal(target_store), target_entry, {"task_id": "different-task", "branch_id": "main",
        "question": "Cross-task software fixture", "summary": "Independent journal", "conditions": {"target": "PB"}})
    recorded = feedback(target_entry, published, "external-negative", "negative", resource_entry=source)
    assert recorded["ok"]
    description = read_entry(target_entry, verify_originals=False)["note"]["resources"]["feedback:geometry"]
    assert description["family"] == "geometry"
    assert description["subject"]["version"] == published["resource_version"]
    assert description["last_receipt"]["resource_entry"] == source
    assert "branches" not in description["last_receipt"]["resource_entry"]
    assert feedback(target_entry, published, "external-negative", "negative", resource_entry=source)["replayed"]


def test_optional_query_exposes_partial_reuse_without_invoking_whole_method(method):
    entry, _, _ = method
    query = {"conditions": {"target": "PB"}, "available_stages": ["planning"]}
    result = resolve_saved_method(entry, "/resources/geometry", query=query)
    assert result["selection"]["parts"][0]["disposition"] == "direct-use"
    assert not result["execution_performed"]
    with pytest.raises(ValueError, match="whole-method invocation"):
        resolve_saved_method(entry, "/resources/geometry", query=query,
            invocation={"request_id": "unsafe-whole", "limits": {"wall_second": 1}})
    matched = resolve_saved_method(entry, "/resources/geometry",
        query={"conditions": {"target": "SND1"}, "available_stages": ["planning"]},
        invocation={"request_id": "declared-match", "limits": {"wall_second": 1}})
    assert matched["selection"]["disposition"] == "direct-use" and matched["request"]
    with pytest.raises(ValueError, match="absent from the exact resource"):
        record_resource_feedback(entry, "geometry", usage_id="missing-part", resource_version=result["resource_version"],
            part_ids=["not-a-part"], conditions={"target": "PB"}, outcome="unknown", summary="Fixture")
    with pytest.raises(ValueError, match="both relevant and irrelevant"):
        validate_resource_parts([part("bad", {"target": "PB"}, irrelevant=["target"])])
