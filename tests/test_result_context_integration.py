"""Shared note reads and explicit meaning bindings, without science execution."""
import json

import pytest

from etalon.research.notes import read_current, save_progress
from etalon.research.result_semantics import build_result_profile, bind_scientific_result, profile_definitions
from etalon.research.research_context import build_research_context
from test_research_notes import case as notes_case
from test_research_publication import case as publication_case


@pytest.mark.parametrize("view", ["summary", "card"])
def test_sdk_current_views_use_same_recipe_and_scope(notes_case, tmp_path, view):
    journal, note, _ = notes_case
    note["definitions"] = {"metric:retained": "a" * 64}
    entry = tmp_path / "entry.json"
    save_progress(journal, entry, note)
    expected = read_current(entry, max_bytes=4096)
    assert build_research_context(entry, view=view, max_bytes=4096) == expected
    value = json.loads(entry.read_text())
    value["read_recipe"] = {"view": "selected", "selectors": ["/summary", "/definitions"]}
    value["definitions"] = note["definitions"]
    entry.write_text(json.dumps(value), encoding="utf-8")
    selected = build_research_context(entry, view=view, max_bytes=4096)
    assert selected == read_current(entry, max_bytes=4096)
    meanings = next(row for row in selected["selected"] if row["pointer"] == "/definitions")
    assert [item["value"] for item in meanings["items"]] == list(note["definitions"].values())
    with pytest.raises(ValueError, match="definition"):
        build_research_context(entry, view=view, max_bytes=4096,
                              definitions={"metric:retained": "b" * 64})


def test_typed_result_cannot_be_published_under_another_metric_meaning(notes_case, tmp_path):
    journal, note, _ = notes_case
    profile = build_result_profile(profile_id="fixture", method={"name": "counter"},
        metrics={"retained": {"definition": "All submitted candidates meeting the declared endpoint"}})
    note["scientific_result"] = bind_scientific_result(profile, subjects=[{"id": "fixture"}],
        operation={"name": "count"}, conditions={}, observations={"retained": 0})
    note["definitions"] = {"metric:retained": "b" * 64}
    entry = tmp_path / "entry.json"
    with pytest.raises(ValueError, match="note definitions differ"):
        save_progress(journal, entry, note)
    assert not entry.exists()
    note["definitions"] = profile_definitions(profile)
    save_progress(journal, entry, note)
    assert read_current(entry)["definitions"] == note["definitions"]


def test_published_workset_follows_note_but_old_meaning_does_not_activate(notes_case, publication_case, tmp_path):
    from etalon.research.meta_context import (
        describe_resource, index_resources, make_task_profile, build_task_workset, publish_task_workset)
    from etalon.research.publication import resume_publication
    from test_meta_context import setup, part, review
    workspace, resources = setup(publication_case)
    meanings = {"metric:retained": "a" * 64}
    base = describe_resource(resource_id="base", kind="template", content_ref=resources[0]["content_ref"],
        summary="Generic fixture reader", parts=[part(irrelevant=("target", "objective", "definition:metric:retained"))])
    method_part = part("method", {"target": "SND1", "objective": "pose"}, irrelevant=())
    method_part["definitions"] = meanings
    method = describe_resource(resource_id="local", kind="method", content_ref=resources[0]["content_ref"],
        summary="Scoped fixture method", parts=[method_part])
    profile = make_task_profile(task_id="fixture", question="Fixture next action",
        conditions={"target": "SND1", "objective": "pose"}, critical_conditions=["target", "objective"],
        available_stages=["input"], definitions=meanings)
    index = index_resources([base, method], workspace=workspace)
    decision = review(profile, index, {("base", "base"), ("local", "method")})
    workset = build_task_workset(profile, index, decision, template={"resource_id": "base", "part_id": "base"})
    intent = publish_task_workset(publication_case["entry"], workset, parent_entry=publication_case["parent"],
        operation_id="fixture-select", directory=tmp_path / "workset")
    resume_publication(intent["intent_path"])
    published = json.loads(publication_case["entry"].read_text())
    journal, note, _ = notes_case
    note.update(conditions=profile["conditions"], definitions=meanings)
    entry = tmp_path / "note-entry.json"
    save_progress(journal, entry, note)
    value = json.loads(entry.read_text())
    value["task_workset"] = {k: published[k] for k in ("workspace", "checkpoint_ref")}
    entry.write_text(json.dumps(value), encoding="utf-8")
    active = read_current(entry)["task_workset"]
    assert active["status"] == "active" and len(active["enabled"]) == 2
    save_progress(journal, entry, {**note, "branch_id": "new-endpoint",
                                  "definitions": {"metric:retained": "b" * 64}})
    changed = read_current(entry)["task_workset"]
    assert changed["status"] == "review_required" and changed["enabled"] == []
    assert changed["changed_definitions"] == ["metric:retained"]
    assert read_current(entry, branch_id="anchor")["task_workset"]["status"] == "active"
