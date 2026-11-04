"""Selected reads and edits scale with the requested resource, using real files."""
from collections import Counter
from pathlib import Path

import pytest

from etalon.research import advice as advice_module
from etalon.research import note_resources as resources
from etalon.research.notes import patch_progress, read_entry, save_progress
from test_research_notes import case


def library(case, tmp_path):
    journal, note, _ = case
    entry = tmp_path / "ENTRY.json"
    descriptions = {f"method-{i:03}": {"kind": "method", "summary": f"Fixture {i}",
        "fields": {str(j): f"value {j}" for j in range(20)}, "family": "initial"} for i in range(130)}
    save_progress(journal, entry, {**note, "resources": descriptions})
    return entry, descriptions


def track_loads(monkeypatch):
    loaded = []
    original = resources._load

    def read(journal, ref, schema):
        loaded.append((schema, ref["record_id"]))
        return original(journal, ref, schema)

    monkeypatch.setattr(resources, "_load", read)
    return loaded


def test_selected_fields_share_one_read_but_new_call_rechecks_original(case, tmp_path, monkeypatch):
    entry, _ = library(case, tmp_path)
    loaded = track_loads(monkeypatch)
    selectors = [f"/resources/method-072/fields/{i}" for i in range(20)]
    result = read_entry(entry, view="selected", selectors=selectors, max_bytes=65536)
    assert [item["value"] for item in result["selected"]] == [f"value {i}" for i in range(20)]
    assert Counter(schema for schema, _ in loaded) == {resources.INDEX: 1, resources.SHARD: 1, resources.RESOURCE: 1}
    journal, _, _ = case
    selected_id = next(rid for schema, rid in loaded if schema == resources.RESOURCE)
    Path(journal.get(selected_id)["path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        read_entry(entry, view="selected", selectors=selectors[:1])


def test_advice_control_read_leaves_resource_bodies_unloaded(case, tmp_path, monkeypatch):
    entry, _ = library(case, tmp_path)
    loaded = track_loads(monkeypatch)
    result = advice_module._read(entry)
    assert result["note"]["question"] == case[1]["question"]
    assert result["note"]["resources_ref"]["count"] == 130
    assert not loaded


def test_resource_update_reuses_untouched_pages_and_updates_family_counts(case, tmp_path, monkeypatch):
    entry, descriptions = library(case, tmp_path)
    journal, _, _ = case
    before = read_entry(entry, expand_resources=False, verify_originals=False)["note"]
    original = resources._manifest(journal, before)
    loaded = track_loads(monkeypatch)
    changed = {**descriptions["method-072"], "family": ["revised", "initial"], "summary": "Revised fixture"}
    patch_progress(entry, {}, resources={"method-072": changed})
    assert Counter(schema for schema, _ in loaded) == {resources.INDEX: 1, resources.SHARD: 1}
    after = read_entry(entry, expand_resources=False, verify_originals=False)["note"]
    revised = resources._manifest(journal, after)
    assert revised["pages"][0] == original["pages"][0]
    assert revised["pages"][2] == original["pages"][2]
    assert revised["families"] == {"initial": 130, "revised": 1}
    assert read_entry(entry, verify_originals=False)["note"]["resources"] == {**descriptions, "method-072": changed}


def test_resource_insertions_keep_sorted_pages_and_offsets(case, tmp_path):
    entry, descriptions = library(case, tmp_path)
    additions = {name: {"kind": "experience", "summary": name}
                 for name in ("000-before", "method-063.5", "zzz-after")}
    patch_progress(entry, {}, resources=additions)
    journal, _, _ = case
    stored = read_entry(entry, expand_resources=False, verify_originals=False)["note"]
    page = resources.resource_page(journal, stored, limit=1000)
    assert [row["name"] for row in page["items"]] == sorted({**descriptions, **additions})
    assert page["total_items"] == 133 and page["next_offset"] is None
    assert read_entry(entry, verify_originals=False)["note"]["resources"] == {**descriptions, **additions}
