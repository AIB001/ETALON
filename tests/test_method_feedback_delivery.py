"""Native-note delivery regressions; all observations are software fixtures."""
from contextlib import closing
import hashlib
import sqlite3
import sys

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research import ResearchJournal, note_resources
from etalon.research.notes import byte_size, entry_journal, entry_value, read_entry, save_progress
from etalon.research.resource_catalog import build_resource_catalog
from etalon.research.resource_selection import record_resource_feedback, read_resource_feedback
from etalon.research.saved_methods import publish_method, resolve_saved_method


@pytest.fixture
def project(tmp_path):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
        [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    entry = tmp_path / "ENTRY.json"
    save_progress(ResearchJournal(store), entry, {"task_id": "delivery-fixture", "branch_id": "main",
        "question": "Does selecting a method expose its conditional experience?", "summary": "Software fixture"})
    script = tmp_path / "method.py"
    script.write_text("raise AssertionError('Reader must never execute the method')\n", encoding="utf-8")
    arguments = {"method_id": "fixture", "command": [sys.executable, str(script)],
                 "files": [str(script)], "inputs": {}}
    published = publish_method(entry, "rank", arguments, summary="Fixture ranker",
        conditions={"target": "A"}, family="ranking")
    return entry, arguments, published


def feedback(project, usage="negative", *, outcome="negative", conditions=None, cost=None, summary=None):
    entry, _, published = project
    return record_resource_feedback(entry, "rank", usage_id=usage,
        resource_version=published["resource_version"], part_ids=["whole"],
        conditions={"target": "A"} if conditions is None else conditions, outcome=outcome,
        summary=summary or "Fixture: lower prediction error did not improve final candidate retention",
        cost={"wall_second": 23.0} if cost is None else cost)


def snapshot(entry):
    database = entry_value(entry)["database"]
    with closing(sqlite3.connect(database)) as db:
        content = "\n".join(db.iterdump()).encode()
    return entry.read_bytes(), hashlib.sha256(content).hexdigest()


def test_catalog_and_selected_method_deliver_known_negative_without_writes(project):
    entry, _, published = project
    feedback(project)
    feedback(project, "positive", outcome="positive")
    before = snapshot(entry)
    query = {"conditions": {"target": "A"}}
    catalog = build_resource_catalog(entry=entry, resource_ids=["rank"], query=query)
    experience = next(row for row in catalog["items"] if row["resource_id"] == "feedback:rank")
    assert experience["selection"]["status"] == "match"
    assert experience["selection"]["outcome_counts_by_status"]["match"] == {"negative": 1, "positive": 1}
    methods = build_resource_catalog(entry=entry, kinds=["method"], resource_ids=["rank"], query=query)
    assert [row["resource_id"] for row in methods["items"]] == ["rank"]
    link = methods["items"][0]["related_feedback"]["next_read"]
    assert link["tool"] == "etalon_resource_feedback"
    explicit = read_resource_feedback(**link["arguments"])
    resolved = resolve_saved_method(**methods["items"][0]["resolve"]["arguments"])
    case = resolved["related_feedback"]["cases"][0]
    assert case["subject"]["version"] == published["resource_version"]
    assert case["version_relation"] == "same_version" and case["status"] == "match"
    assert set(case["latest_outcomes"]) == {"negative", "positive"}
    assert "candidate retention" in case["latest_outcomes"]["negative"]["summary"]
    assert case["latest_outcomes"]["negative"]["cost"] == {"wall_second": 23.0}
    assert explicit["cases"][0] == case
    assert snapshot(entry) == before


def test_pinned_method_keeps_old_note_and_distinguishes_old_method_feedback(project):
    entry, arguments, first = project
    feedback(project)
    original = build_resource_catalog(entry=entry, kinds=["method"])["items"][0]
    second = publish_method(entry, "rank", arguments, summary="New fixture version",
        conditions={"target": "A"}, family="ranking")
    feedback((entry, arguments, second), "second-use", outcome="unknown")
    query = {"conditions": {"target": "A"}}
    current = resolve_saved_method(entry, "/resources/rank", query=query)["related_feedback"]
    assert current["version_counts"] == {"same_version": 1, "other_version": 1}
    assert current["cases"][0]["subject"]["version"] == second["resource_version"]
    old = resolve_saved_method(original["content_ref"], original["pointer"], query=query)["related_feedback"]
    assert old["case_count"] == 1 and old["cases"][0]["subject"]["version"] == first["resource_version"]
    assert old["record_id"] == original["content_ref"]["record_id"]
    assert old["version_counts"] == {"same_version": 1, "other_version": 0}


def test_mixed_conditions_keep_conflict_unknown_and_negative(project):
    entry, _, _ = project
    feedback(project, "matched")
    feedback(project, "other-target", outcome="positive", conditions={"target": "B"})
    feedback(project, "unknown-target", outcome="failed", conditions={"target": None})
    result = read_resource_feedback(entry, "rank", query={"conditions": {"target": "A"}})
    assert result["status_counts"] == {"match": 1, "conflict": 1, "unknown": 1}
    assert [case["status"] for case in result["cases"]] == ["match", "unknown", "conflict"]
    assert result["outcome_counts_by_status"]["match"] == {"negative": 1}
    assert result["scientific_suitability"] == "not_inferred"


def test_case_pages_are_pinned_complete_without_duplicates_and_read_only(project, monkeypatch):
    entry, _, _ = project
    monkeypatch.setattr(note_resources, "FEEDBACK_INLINE_CASES", 2)
    for number in range(9):
        feedback(project, f"use-{number}", conditions={"target": str(number)}, summary="Fixture " + "x" * 1600)
    calls = []
    original_load = note_resources._load
    def measured_load(journal, ref, schema):
        calls.append(schema)
        return original_load(journal, ref, schema)
    monkeypatch.setattr(note_resources, "_load", measured_load)
    first = read_resource_feedback(entry, "rank", limit=3, max_bytes=4096)
    assert calls.count(note_resources.FEEDBACK_CASE) <= 3
    assert first["coverage"]["verification"] == "selected_case_artifacts_and_index"
    feedback(project, "later", conditions={"target": "new-after-first-page"})
    before = snapshot(entry)
    current, seen = first, []
    while True:
        assert byte_size(current) <= 4096
        assert current["record_id"] == first["record_id"] and current["case_count"] == 9
        seen.extend(case["case_id"] for case in current["cases"])
        if current["next_read"] is None:
            break
        current = read_resource_feedback(**current["next_read"]["arguments"])
    assert len(seen) == len(set(seen)) == 9
    assert snapshot(entry) == before


def test_large_feedback_cannot_prevent_valid_method_preflight(project):
    entry, _, _ = project
    invocation = {"request_id": "prepare-only", "limits": {"wall_second": 30}}
    baseline = resolve_saved_method(entry, "/resources/rank", invocation=invocation)
    assert baseline["related_feedback"]["status"] == "absent"
    feedback(project, cost={f"measured-unit-{number}": number for number in range(800)})
    result = resolve_saved_method(entry, "/resources/rank", invocation=invocation, max_bytes=4096)
    assert result["request"]["arguments"] == baseline["request"]["arguments"]
    assert result["plan_id"] and not result["execution_performed"]
    assert byte_size(result) <= 4096
    page = read_resource_feedback(entry, "rank", max_bytes=2048)
    assert byte_size(page) <= 2048 and page["cases"][0]["body_omitted"]
    assert page["next_offset"] is None


def test_page_verification_does_not_read_or_hide_corrupt_selected_case(project, monkeypatch):
    entry, _, _ = project
    monkeypatch.setattr(note_resources, "FEEDBACK_INLINE_CASES", 1)
    feedback(project, "one")
    feedback(project, "two", conditions={"target": "B"})
    journal = entry_journal(entry_value(entry))
    note = read_entry(entry, verify_originals=False, expand_resources=False)["note"]
    row = note_resources.resource_row(journal, note, "feedback:rank")
    body = note_resources._load(journal, row, note_resources.RESOURCE_V2)
    case_row = note_resources.resource_page(journal, {"resources_ref": body["cases_ref"]}, limit=1)["items"][0]
    from pathlib import Path
    Path(journal.get(case_row["record_id"])["path"]).write_text("corrupted fixture", encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        read_resource_feedback(entry, "rank", limit=1)
    selected = resolve_saved_method(entry, "/resources/rank")
    assert selected["related_feedback"]["status"] == "not_loaded"
    assert selected["related_feedback"]["reason"] == "feedback_read_unavailable"
    assert selected["source"]["verification"] == "verified"


def test_direct_call_and_collect_replay_deliver_original_experience_without_new_feedback(project, tmp_path, monkeypatch):
    from etalon.runtime import service
    from etalon.research.saved_methods import call_saved_method, collect_saved_method
    entry, arguments, first = project
    feedback(project)
    launches = []
    monkeypatch.setattr(service, "_launch", lambda runtime, job: launches.append(job))
    invocation = {"request_id": "one-call", "limits": {"wall_second": 30}}
    called = call_saved_method(entry, "/resources/rank", invocation=invocation, workspace=tmp_path / "runtime")
    captured = called["related_feedback"]
    assert captured["cases"][0]["subject"]["version"] == first["resource_version"]
    second = publish_method(entry, "rank", arguments, summary="Changed after submission", conditions={"target": "A"})
    feedback((entry, arguments, second), "after-call", outcome="positive")
    before = snapshot(entry)
    collected = collect_saved_method(called["invocation_path"])
    replayed = call_saved_method(entry, "/resources/rank", invocation=invocation, workspace=tmp_path / "runtime")
    assert collected["related_feedback"] == replayed["related_feedback"] == captured
    assert replayed["resource_version"] == first["resource_version"] and replayed["submission"] == "existing"
    assert launches == ["one-call"] and snapshot(entry) == before
    current = read_resource_feedback(entry, "rank")
    assert current["case_count"] == 2  # No automatic use/outcome receipt is fabricated.


def test_legacy_invocation_does_not_backfill_from_later_head(project, tmp_path, monkeypatch):
    from pathlib import Path
    from etalon.runtime import service
    from etalon.research import method_invocations
    from etalon.research.saved_methods import call_saved_method, collect_saved_method
    entry, _, _ = project
    original_save = method_invocations._save
    def old_writer(path, value, **kwargs):
        if Path(path).name == "invocation.json":
            value = {key: item for key, item in value.items() if key not in {"method_entry", "related_feedback"}}
        return original_save(path, value, **kwargs)
    monkeypatch.setattr(method_invocations, "_save", old_writer)
    launches = []
    monkeypatch.setattr(service, "_launch", lambda runtime, job: launches.append(job))
    result = call_saved_method(entry, "/resources/rank", invocation={"request_id": "legacy", "limits": {"wall_second": 30}},
                               workspace=tmp_path / "legacy-runtime")
    feedback(project, "new-experience")
    before = snapshot(entry)
    collected = collect_saved_method(result["invocation_path"])
    assert collected["related_feedback"] == {"status": "not_captured", "reason": "legacy_invocation"}
    assert snapshot(entry) == before and launches == ["legacy"]


def test_direct_collect_preserves_outputs_when_captured_experience_exceeds_spare_budget(project):
    from etalon.research.method_invocations import _with_captured_feedback
    entry, _, published = project
    feedback(project)
    captured = read_resource_feedback(entry, "rank")
    intent = {"method_entry": captured["content_ref"], "pointer": "/resources/rank",
              "selection": {"query": None}, "resource_version": published["resource_version"],
              "related_feedback": {**captured, "fixture_large_receipt": "x" * 8000}}
    response = {"handle": {"request_id": "valid-runtime-handle"}, "outputs": "x" * 10000}
    delivered = _with_captured_feedback(response, intent)
    assert delivered["outputs"] == response["outputs"] and delivered["handle"] == response["handle"]
    assert byte_size(delivered) <= 16384
    link = delivered["related_feedback"]["next_read"]["arguments"]
    assert link["entry"]["record_id"] == captured["record_id"]
    assert read_resource_feedback(**link)["cases"] == captured["cases"]
