"""Exact conditions reach the first page without expanding unrelated originals."""
import json

import pytest

from etalon.research import notes
from etalon.research.resource_selection import _feedback_description, feedback_for_query
from etalon.research.schema import Artifact
from etalon.runtime.files import _save, file_identity
from test_research_notes import case


def test_entry_metadata_accepts_only_declared_path_fields(tmp_path):
    declaration = {"workspace": tmp_path, "database": tmp_path / "research.sqlite",
                   "task_workset": {"workspace": tmp_path}, "checkpoint_ref": {"record_id": "fixture"}}
    value = notes.entry_value(declaration)
    assert value["workspace"] == str(tmp_path)
    assert value["database"] == str(tmp_path / "research.sqlite")
    assert value["task_workset"]["workspace"] == str(tmp_path)
    assert declaration["workspace"] == tmp_path
    with pytest.raises(TypeError):
        notes.entry_value({"workspace": tmp_path, "opaque": tmp_path})


def test_condition_matching_precedes_page_limit_and_reads_only_delivered_notes(case, tmp_path, monkeypatch):
    journal, note, _ = case
    declared = {}
    for i in range(25):
        conditions = {"target": str(i)}
        rid = notes.record_note(journal, {**note, "conditions": conditions}, tmp_path / "notes")
        declared[rid] = conditions
    target = max(declared)
    loaded = []
    original = notes.read_note

    def read(journal, rid, **kwargs):
        loaded.append(rid)
        return original(journal, rid, **kwargs)

    monkeypatch.setattr(notes, "read_note", read)
    result = notes.find_notes(journal, task_id=note["task_id"], conditions=declared[target], limit=1)
    assert [row["record_id"] for row in result["matches"]] == [target]
    assert loaded == [target]
    assert result["coverage"] == {"matches": 1, "related": 24}
    assert result["next_offset"] == 1
    following = notes.find_notes(journal, task_id=note["task_id"], conditions=declared[target], limit=1, offset=1)
    assert not following["matches"] and len(following["related"]) == 1


def test_legacy_lookup_and_optional_current_view_preserve_historical_negatives(case, tmp_path):
    journal, note, _ = case
    body = {**note, "schema": notes.SCHEMA, "outcome": "negative"}
    path = tmp_path / "legacy.json"
    _save(path, body)
    old = journal.record(Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic",
        bindings={"schema": notes.SCHEMA, "task_id": note["task_id"], "branch_id": note["branch_id"]}))
    original_bytes = path.read_bytes()
    new = notes.record_note(journal, {**body, "summary": "Retained after later evidence", "supersedes": [old]}, tmp_path / "notes")
    full = notes.find_notes(journal, task_id=note["task_id"], conditions=note["conditions"])
    assert {row["record_id"] for row in full["matches"]} == {old, new}
    current = notes.find_notes(journal, task_id=note["task_id"], conditions=note["conditions"], current_only=True)
    assert [row["record_id"] for row in current["matches"]] == [new]
    assert current["matches"][0]["note"]["outcome"] == "negative"
    assert path.read_bytes() == original_bytes


def test_feedback_pages_prioritize_match_and_keep_negative_and_unknown_totals():
    description = None
    for i in range(32):
        receipt = {"subject": {"resource_id": "fixture", "version": "a" * 64, "parts": ["whole"]},
                   "conditions": {"target": str(i)}, "definitions": {},
                   "usage_id": str(i), "outcome": "negative" if i % 2 else "unknown"}
        description = _feedback_description(description, receipt)
    before = json.dumps(description, sort_keys=True)
    query = {"conditions": {"target": "17"}}
    first = feedback_for_query(description, query, limit=3)
    assert len(first["cases"]) == 3 and first["cases"][0]["conditions"] == {"target": "17"}
    assert first["status_counts"] == {"match": 1, "conflict": 31, "unknown": 0}
    assert first["outcome_counts_by_status"]["match"] == {"negative": 1}
    assert first["outcome_counts_by_status"]["conflict"] == {"unknown": 16, "negative": 15}
    seen, offset = [], 0
    while True:
        page = feedback_for_query(description, query, limit=3, offset=offset)
        seen.extend(c["case_id"] for c in page["cases"])
        offset = page["next_offset"]
        if offset is None:
            break
    assert len(seen) == len(set(seen)) == 32
    assert feedback_for_query(description, query, status="match")["selected_count"] == 1
    assert feedback_for_query(description, query, status="conflict")["selected_count"] == 31
    assert json.dumps(description, sort_keys=True) == before
    for kwargs in ({"limit": 0}, {"offset": -1}, {"status": "positive"}):
        with pytest.raises(ValueError):
            feedback_for_query(description, query, **kwargs)
