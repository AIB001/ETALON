"""Real immutable storage, bounded reads and relocation of note resources."""
import json
from pathlib import Path

import pytest

from etalon.active.schema import canonical
from etalon.research import note_resources as resources
from etalon.research.handoff import export_research_handoff, plan_research_handoff, relocate_research_handoff
from etalon.research.notes import (entry_journal, entry_value, original, patch_progress,
    open_original, read_entry, read_note, resolve_method, save_progress)
from etalon.research.schema import Artifact
from etalon.runtime.files import _save, file_identity
from test_research_notes import case


def _compact(entry):
    return read_entry(entry, verify_originals=False, expand_resources=False)["note"]


def _counts(journal):
    with journal.store.connection() as db:
        return dict(db.execute("SELECT json_extract(body,'$.bindings.schema'), count(*) "
            "FROM metadata WHERE key LIKE 'research:%' GROUP BY json_extract(body,'$.bindings.schema')"))


def test_progress_reuses_archived_resources_and_stable_identity(case, tmp_path, monkeypatch):
    journal, note, _ = case
    entry = tmp_path / "entry.json"
    descriptions = {f"method-{i:03}": {"kind": "method", "summary": f"method {i}",
        "body": "x" * 16000, "conditions": {"domain": "fixture"}} for i in range(130)}
    first = save_progress(journal, entry, {**note, "resources": descriptions})
    stored = _compact(entry)
    reference = stored["resources_ref"]
    assert "resources" not in stored and len(canonical(stored)) < 3000
    identity = resources.resource_identity(journal, stored, "method-072")
    counts = _counts(journal)
    assert counts[resources.RESOURCE] == 130 and counts[resources.SHARD] == 3
    loaded = []
    ordinary = resources._load

    def track(journal, ref, schema):
        loaded.append(schema)
        return ordinary(journal, ref, schema)

    monkeypatch.setattr(resources, "_load", track)
    for i in range(3):
        patch_progress(entry, {"summary": f"Progress {i}"})
    assert loaded == []  # No index or descriptor loading to update unrelated progress.
    assert _compact(entry)["resources_ref"] == reference
    assert _counts(journal)[resources.RESOURCE] == counts[resources.RESOURCE]
    assert resources.resource_identity(journal, _compact(entry), "method-072") == identity
    assert read_note(journal, first, verify_originals=False)["note"]["resources"] == descriptions
    assert read_entry(entry, verify_originals=False)["note"]["resources"] == descriptions


def test_exact_selection_and_summary_page_read_only_required_resource_files(case, tmp_path, monkeypatch):
    journal, note, _ = case
    entry = tmp_path / "entry.json"
    descriptions = {f"method-{i:03}": {"kind": "method", "summary": f"method {i}",
        "body": "x" * 10000, "requires": {"target": "fixture"}, "irrelevant": ["hostname"],
        **({"family": "group-" + str(i // 64)} if i < 129 else {})}
        for i in range(130)}
    save_progress(journal, entry, {**note, "resources": descriptions})
    stored = _compact(entry)
    ordinary = resources._load
    loaded = []

    def track(journal, ref, schema):
        loaded.append((schema, ref["record_id"]))
        return ordinary(journal, ref, schema)

    monkeypatch.setattr(resources, "_load", track)
    page = resources.resource_page(journal, stored, offset=70, limit=2)
    assert [row["name"] for row in page["items"]] == ["method-070", "method-071"]
    assert page["items"][0]["requires"] == {"target": "fixture"}
    assert page["items"][0]["irrelevant"] == ["hostname"]
    assert [kind for kind, _ in loaded] == [resources.INDEX, resources.SHARD]
    loaded.clear()
    families = resources.resource_families(journal, stored)
    assert families == {"families": [{"name": "group-0", "count": 64},
        {"name": "group-1", "count": 64}, {"name": "group-2", "count": 1}],
        "total_items": 130, "unclassified": 1}
    assert [kind for kind, _ in loaded] == [resources.INDEX]
    loaded.clear()
    family_page = resources.resource_page(journal, stored, families=["group-2"])
    assert family_page["total_items"] == 1 and family_page["items"][0]["name"] == "method-128"
    assert [kind for kind, _ in loaded] == [resources.INDEX, resources.SHARD]
    loaded.clear()
    family_union = resources.resource_page(journal, stored, families=["group-0", "group-2"], offset=63, limit=2)
    assert family_union["total_items"] == 65 and family_union["next_offset"] is None
    assert [row["name"] for row in family_union["items"]] == ["method-063", "method-128"]
    assert [kind for kind, _ in loaded] == [resources.INDEX, resources.SHARD, resources.SHARD]
    loaded.clear()
    found = resources.resource_page(journal, stored, names=["method-072", "feedback:method-072"])
    assert found["total_items"] == 1 and found["missing_names"] == ["feedback:method-072"]
    assert not any(kind == resources.RESOURCE for kind, _ in loaded)
    loaded.clear()
    selected = read_entry(entry, view="selected", selectors=["/resources/method-072/summary"])
    assert selected["selected"][0]["value"] == "method 72"
    assert sum(kind == resources.RESOURCE for kind, _ in loaded) == 1
    loaded.clear()
    collection = read_entry(entry, view="selected", selectors=["/resources"], offset=70, limit=2,
                            max_bytes=65536)
    assert collection["selected"][0]["total_items"] == 130
    assert sum(kind == resources.RESOURCE for kind, _ in loaded) == 2
    # A corrupt unselected descriptor cannot silently contaminate exact selection.
    unrelated = resources.resource_row(journal, stored, "method-001")
    Path(journal.get(unrelated["record_id"])["path"]).write_text("changed", encoding="utf-8")
    assert read_entry(entry, view="brief")["resource_navigation"]["declared_in_this_note"] == 130
    assert read_entry(entry, view="selected", selectors=["/resources/method-072/summary"])["selected"][0]["value"] == "method 72"
    with pytest.raises((ValueError, RuntimeError)):
        read_entry(entry, verify_originals=False)


def test_resource_merge_and_publication_replay_preserve_other_identities(case, tmp_path):
    journal, note, _ = case
    entry = tmp_path / "entry.json"
    save_progress(journal, entry, {**note, "resources": {
        "one": {"kind": "method", "summary": "first"},
        "two": {"kind": "method", "summary": "second"}}})
    before = resources.resource_identity(journal, _compact(entry), "two")
    change = {"one": {"kind": "method", "summary": "revised"}}
    result = patch_progress(entry, {}, resources=change, publication_id="resource-revision")
    assert _counts(journal)[resources.RESOURCE] == 3
    assert resources.resource_identity(journal, _compact(entry), "two") == before
    patch_progress(entry, {"summary": "later host interpretation"})
    head = entry_value(entry)["record_id"]
    replay = patch_progress(entry, {}, resources=change, publication_id="resource-revision")
    assert replay["record_id"] == result["record_id"] and entry_value(entry)["record_id"] == head
    # A complete caller-supplied note reuses content even in another directory.
    full = read_entry(entry, verify_originals=False)["note"]
    assert save_progress(journal, entry, full, directory=tmp_path / "elsewhere") == head
    assert not (tmp_path / "elsewhere" / "resources").exists()


def test_resource_archive_and_originals_survive_two_handoff_locations(case, tmp_path):
    journal, note, _ = case
    source = tmp_path / "method.py"
    source.write_text("def method(): return 7\n", encoding="utf-8")
    observation = tmp_path / "failed-use.json"
    observation.write_text('{"outcome":"negative"}', encoding="utf-8")
    entry = tmp_path / "entry.json"
    description = {"kind": "method", "summary": "fixture callable", "source": original(source)}
    receipt = {"evidence": [], "counterevidence": [original(observation)]}
    feedback = {"kind": "experience", "form": "resource-feedback", "last_receipt": receipt,
        "cases": {"condition-one": {"latest_outcomes": {"negative": receipt}}}}
    save_progress(journal, entry, {**note, "resources": {"callable": description, "feedback:callable": feedback}})
    stable = resources.resource_identity(journal, _compact(entry), "callable")
    first, moved = tmp_path / "bundle", tmp_path / "moved"
    export_research_handoff(plan_research_handoff(entry), first)
    relocate_research_handoff(first, moved)
    source.rename(tmp_path / "unavailable-method.py")
    observation.rename(tmp_path / "unavailable-observation.json")
    (tmp_path / "memory").rename(tmp_path / "unavailable-memory")
    first.rename(tmp_path / "unavailable-bundle")
    relocated = moved / "entry.json"
    resolved = resolve_method(relocated, "/resources/callable")
    assert resolved["description"] == description
    assert Path(resolved["source"]["path"]).read_text() == "def method(): return 7\n"
    active = entry_journal(entry_value(relocated))
    assert resources.resource_identity(active, _compact(relocated), "callable") == stable
    with open_original(relocated, "/resources/feedback:callable/cases/condition-one/latest_outcomes/negative/counterevidence/0") as saved:
        assert json.loads(saved.path.read_text())["outcome"] == "negative"
    patch_progress(relocated, {"summary": "Continued after relocation"})
    assert resolve_method(relocated, "/resources/callable")["description"] == description


def test_legacy_inline_resources_read_and_migrate_without_rewriting_original(case, tmp_path):
    journal, note, _ = case
    from etalon.research.notes import SCHEMA, publish_note
    body = {**note, "schema": SCHEMA, "resources": {"old/name": {"kind": "method", "summary": "legacy"}}}
    path = tmp_path / "legacy-note.json"
    _save(path, body)
    before = path.read_bytes()
    rid = journal.record(Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic",
        bindings={"schema": SCHEMA, "task_id": note["task_id"], "branch_id": note["branch_id"]}))
    entry = tmp_path / "entry.json"
    publish_note(entry, journal, rid)
    assert read_entry(entry)["note"] == body
    assert read_entry(entry, view="selected", selectors=["/resources/old~1name/summary"])["selected"][0]["value"] == "legacy"
    patch_progress(entry, {"summary": "New progress"})
    assert "resources_ref" in _compact(entry) and path.read_bytes() == before
    assert read_entry(entry)["note"]["resources"] == body["resources"]
