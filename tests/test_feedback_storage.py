"""Incremental feedback storage, legacy reads and interrupted publication."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from etalon.active import CampaignStore, CampaignSpec, Endpoint
from etalon.active.schema import digest
from etalon.research import ResearchJournal, note_resources as resources, notes
from etalon.research.handoff import (
    export_research_handoff, plan_research_handoff, relocate_research_handoff,
)
from etalon.research.resource_selection import _feedback_description, record_resource_feedback


def project(directory):
    directory.mkdir(parents=True, exist_ok=True)
    store = CampaignStore(directory / "research.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
        [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    entry = directory / "ENTRY.json"
    method = {"kind": "method", "summary": "Synthetic storage fixture", "family": "fixture"}
    notes.save_progress(ResearchJournal(store), entry,
        {"task_id": "feedback-storage", "branch_id": "main", "question": "Storage fixture",
         "summary": "Before feedback", "conditions": {}, "resources": {"method": method}})
    return entry, digest(method)


def append(entry, version, number, *, outcome=None, conditions=None, **kwargs):
    return record_resource_feedback(entry, "method", usage_id=f"use-{number:03}",
        resource_version=version, part_ids=["whole"],
        conditions=conditions or {"target": f"fixture-{number:03}"},
        outcome=outcome or ("negative", "unknown", "positive", "failed", "no_change")[number % 5],
        summary="Synthetic feedback for storage equivalence", **kwargs)


def description(entry):
    return notes.read_entry(entry, verify_originals=False)["note"]["resources"]["feedback:method"]


def storage(entry):
    journal = notes.entry_journal(notes.entry_value(entry))
    note = notes.read_entry(entry, verify_originals=False, expand_resources=False)["note"]
    row = resources.resource_row(journal, note, "feedback:method")
    body = resources._load(journal, row, row.get("storage_schema", resources.RESOURCE))
    return journal, row, body


def legacy_store(journal, descriptor, directory, previous_row=None, **kwargs):
    return resources._artifact(journal, {"schema": resources.RESOURCE, "description": descriptor}, directory)


def artifact_files(directory):
    return {str(path.absolute()): path.stat().st_size
            for path in sorted((directory / "memory").rglob("*.json"))}


def measure_storage_growth(directory, checkpoints=(1, 8, 32, 128), *, distinct_conditions=True):
    """Measure actual immutable artifact files; SQLite footprint is separate."""
    directory = Path(directory).absolute()
    directory.mkdir(parents=True, exist_ok=False)
    reports, snapshots = {}, {}
    for mode in ("full_snapshot_reference", "incremental"):
        root = directory / mode
        entry, version = project(root)
        initial = artifact_files(root)
        database_files = lambda: {str(path.absolute()): path.stat().st_size for path in root.glob("research.sqlite*") if path.is_file()}
        initial_database = database_files()
        previous_database_bytes = sum(initial_database.values())
        previous_files, previous_checkpoint_bytes = initial, 0
        milestones, logical = [], {}
        writer = legacy_store if mode == "full_snapshot_reference" else resources._store_description
        with patch.object(resources, "_store_description", writer):
            for number in range(max(checkpoints)):
                append(entry, version, number, conditions=None if distinct_conditions else {"target": "shared-fixture"})
                current = artifact_files(root)
                added = {path: size for path, size in current.items() if path not in previous_files}
                databases = database_files()
                database_bytes = sum(databases.values())
                count = number + 1
                if count in checkpoints:
                    body = description(entry)
                    logical[count] = deepcopy(body)
                    journal, row, physical = storage(entry)
                    assert row["descriptor_digest"] == digest(body)
                    assert len(body["cases"]) == (count if distinct_conditions else 1)
                    assert ("cases_ref" in physical) == (mode == "incremental" and len(body["cases"]) > resources.FEEDBACK_INLINE_CASES)
                    cumulative = sum(size for path, size in current.items() if path not in initial)
                    database_growth = database_bytes - sum(initial_database.values())
                    milestones.append({"feedback_count": count, "last_append_artifact_bytes": sum(added.values()),
                        "last_append_artifact_files": len(added),
                        "last_append_database_footprint_growth_bytes": database_bytes - previous_database_bytes,
                        "last_append_combined_footprint_growth_bytes": sum(added.values()) + database_bytes - previous_database_bytes,
                        "since_previous_checkpoint_artifact_bytes": cumulative - previous_checkpoint_bytes,
                        "cumulative_artifact_bytes": cumulative,
                        "cumulative_artifact_files": len(current) - len(initial),
                        "database_file_bytes": journal.store.path.stat().st_size,
                        "database_files": databases, "database_footprint_growth_bytes": database_growth,
                        "combined_additional_footprint_bytes": cumulative + database_growth,
                        "logical_descriptor_digest": row["descriptor_digest"],
                        "logical_descriptor_bytes": len(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()),
                        "case_count": len(body["cases"])})
                    previous_checkpoint_bytes = cumulative
                previous_files = current
                previous_database_bytes = database_bytes
        snapshots[mode] = logical
        reports[mode] = {"measured_directory": str(root / "memory"), "file_pattern": "**/*.json",
            "initial_artifact_bytes_excluded": sum(initial.values()), "milestones": milestones,
            "initial_database_files": initial_database,
            "measured_files": [{"path": path, "bytes": size,
                                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                               for path, size in current.items() if path not in initial]}
    assert snapshots["full_snapshot_reference"] == snapshots["incremental"]
    return {"scales": list(checkpoints), "distinct_conditions": distinct_conditions,
        "inline_case_threshold": resources.FEEDBACK_INLINE_CASES,
        "readback_equal_at_every_checkpoint": True, "measurements": reports,
        "byte_scope": "new immutable JSON artifacts in memory, including notes, descriptors, pages and indexes; initial state excluded",
        "reference_scope": "same current publication code with the original full-description storage function",
        "database_scope": "physical SQLite main/sidecar footprint growth, not total write IO; combined footprint adds it to immutable artifact bytes",
        "growth_boundary": "changed case plus COW page, compact descriptor and indexes; top-level page index grows with page count; logical digest/readback still visits all cases"}


def test_legacy_feedback_migrates_without_rewriting_history(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "FEEDBACK_INLINE_CASES", 1)
    entry, version = project(tmp_path / "project")
    with patch.object(resources, "_store_description", legacy_store):
        old = append(entry, version, 0, outcome="negative")
        expected = description(entry)
    old_files = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in artifact_files(entry.parent)}
    append(entry, version, 1, outcome="unknown")
    current = description(entry)
    assert current["feedback"]["outcome_counts"] == {"negative": 1, "unknown": 1}
    journal, row, body = storage(entry)
    assert "cases_ref" in body and "cases" not in body["description"]
    assert body["schema"] == row["storage_schema"] == resources.RESOURCE_V2
    assert row["descriptor_digest"] == digest(current)
    assert notes.read_note(journal, old["record_id"], verify_originals=False)["note"]["resources"]["feedback:method"] == expected
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == identity for path, identity in old_files.items())
    head = notes.entry_value(entry)["record_id"]
    full = notes.read_entry(entry, verify_originals=False)["note"]
    writable = notes.entry_journal(notes.entry_value(entry), read_only=False)
    assert notes.save_progress(writable, entry, full, directory=tmp_path / "different-directory",
                               publication_id=full["publication_id"]) == head


def test_legacy_usage_publication_remains_idempotent(tmp_path):
    entry, version = project(tmp_path / "project")
    receipt = {"usage_id": "use-000", "subject": {"resource_id": "method", "version": version, "parts": ["whole"]},
               "conditions": {"target": "fixture-000"}, "definitions": {}, "outcome": "negative",
               "summary": "Synthetic feedback for storage equivalence", "evidence": [], "counterevidence": [], "cost": None}
    descriptor = {**_feedback_description(None, receipt), "family": "fixture"}
    with patch.object(resources, "_store_description", legacy_store):
        original = notes.patch_progress(entry, {}, publication_id="resource-use:use-000",
                                        resources={"feedback:method": descriptor})
    before = artifact_files(entry.parent)
    replay = append(entry, version, 0, outcome="negative")
    assert replay["replayed"] and replay["record_id"] == original["record_id"]
    assert description(entry) == descriptor
    assert artifact_files(entry.parent) == before


def test_changed_case_reuses_other_pages_and_detects_corruption(tmp_path):
    entry, version = project(tmp_path / "project")
    for number in range(40):
        append(entry, version, number)
    journal, _, before = storage(entry)
    old_index = resources._manifest(journal, {"resources_ref": before["cases_ref"]})
    append(entry, version, 40, outcome="positive", conditions={"target": "fixture-000"})
    journal, _, after = storage(entry)
    new_index = resources._manifest(journal, {"resources_ref": after["cases_ref"]})
    assert len({p["record_id"] for p in old_index["pages"]} - {p["record_id"] for p in new_index["pages"]}) == 1
    assert after["cases_ref"]["count"] == 40
    target = next(case for case in description(entry)["cases"].values() if case["conditions"] == {"target": "fixture-000"})
    assert target["outcome_counts"] == {"negative": 1, "positive": 1}
    page = resources._load(journal, new_index["pages"][0], resources.SHARD)
    artifact = journal.get(page["items"][0]["record_id"])
    Path(artifact["path"]).write_text("corrupted case", encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        description(entry)


@pytest.mark.parametrize("advance", [False, True])
@pytest.mark.parametrize("locator", [False, True])
def test_feedback_recovers_unpublished_record_without_overwriting_host_changes(tmp_path, advance, locator):
    entry, version = project(tmp_path / "project")
    target = entry
    if locator:
        entry = tmp_path / "locator.json"
        entry.write_text(json.dumps({"entry": "project/ENTRY.json"}), encoding="utf-8")
        locator_bytes = entry.read_bytes()
    with patch.object(notes, "publish_note", side_effect=InterruptedError("fixture after journal commit")):
        with pytest.raises(InterruptedError):
            append(entry, version, 0, outcome="negative")
    if advance:
        notes.patch_progress(entry, {"summary": "Host continued", "knowledge": {"new": "host fact"}},
            resources={"other": {"kind": "experience", "summary": "Other resource"}})
        append(entry, version, 1, outcome="unknown", conditions={"target": "fixture-000"})
    recovered = append(entry, version, 0, outcome="negative")
    assert recovered["replayed"] and recovered["recovered"]
    current = notes.read_entry(entry, verify_originals=False)
    feedback = current["note"]["resources"]["feedback:method"]
    assert feedback["feedback"]["outcome_counts"] == ({"negative": 1, "unknown": 1} if advance else {"negative": 1})
    if advance:
        assert current["note"]["summary"] == "Host continued"
        assert current["note"]["knowledge"] == {"new": "host fact"}
        assert current["note"]["resources"]["other"]["summary"] == "Other resource"
        assert append(entry, version, 1, outcome="unknown", conditions={"target": "fixture-000"})["replayed"]
    assert append(entry, version, 0, outcome="negative")["record_id"] == recovered["record_id"]
    assert notes.entry_value(entry)["record_id"] == current["record_id"]
    assert notes.entry_value(target)["record_id"] == current["record_id"]
    if locator:
        assert entry.read_bytes() == locator_bytes
    with pytest.raises(ValueError, match="already records different"):
        append(entry, version, 0, outcome="positive")


def test_feedback_cases_and_originals_continue_after_two_handoff_locations(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "FEEDBACK_INLINE_CASES", 1)
    entry, version = project(tmp_path / "project")
    negative = tmp_path / "negative.json"
    negative.write_text('{"fixture":"negative"}', encoding="utf-8")
    append(entry, version, 0, outcome="negative", counterevidence=[negative])
    append(entry, version, 1, outcome="unknown")
    expected = description(entry)
    identity = storage(entry)[1]["descriptor_digest"]
    first, moved = tmp_path / "bundle", tmp_path / "moved"
    export_research_handoff(plan_research_handoff(entry), first)
    relocate_research_handoff(first, moved)
    entry.parent.rename(tmp_path / "original-project-unavailable")
    negative.rename(tmp_path / "negative-unavailable.json")
    first.rename(tmp_path / "first-bundle-unavailable")
    relocated = moved / "entry.json"
    assert description(relocated) == expected
    assert storage(relocated)[1]["descriptor_digest"] == identity
    case_id = next(key for key, value in expected["cases"].items() if "negative" in value["latest_outcomes"])
    pointer = f"/resources/feedback:method/cases/{case_id}/latest_outcomes/negative/counterevidence/0"
    with notes.open_original(relocated, pointer) as saved:
        assert json.loads(saved.path.read_text()) == {"fixture": "negative"}
    assert append(relocated, version, 1, outcome="unknown")["replayed"]
    append(relocated, version, 2, outcome="positive")
    assert description(relocated)["feedback"]["outcome_counts"] == {"negative": 1, "unknown": 1, "positive": 1}


def test_feedback_storage_growth_reads_same_values(tmp_path):
    report = measure_storage_growth(tmp_path / "measurement", checkpoints=(1, 8, 32, 48))
    for before, after in zip(report["measurements"]["full_snapshot_reference"]["milestones"][:-1],
                             report["measurements"]["incremental"]["milestones"][:-1]):
        assert before["cumulative_artifact_bytes"] == after["cumulative_artifact_bytes"]
    reference = report["measurements"]["full_snapshot_reference"]["milestones"][-1]
    incremental = report["measurements"]["incremental"]["milestones"][-1]
    assert incremental["cumulative_artifact_bytes"] < reference["cumulative_artifact_bytes"]
