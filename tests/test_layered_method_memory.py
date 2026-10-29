"""Independent working memory and useful forms share existing notes, not a new ledger."""
import json
import subprocess
import sys

import pytest

from etalon.active import CampaignStore, CampaignSpec, Endpoint
from etalon.research import ResearchJournal
from etalon.research.notes import (entry_value, find_notes, fork_memory, original,
    patch_progress, read_current, read_entry, save_progress, resolve_method)
from etalon.research.resource_catalog import build_resource_catalog
from etalon import research_hooks, skills


@pytest.fixture
def project(tmp_path):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
        [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    source = tmp_path / "method.py"
    source.write_text("print('fixture')")
    note = {"task_id": "fixture", "branch_id": "working", "question": "Useful change?",
        "summary": "Measured failure retained", "conditions": {"target": "fixture"},
        "evidence": [original(source)], "counterevidence": [original(source)],
        "pending_handles": ["running-fixture-handle"], "other_pauses": ["R4b"],
        "resources": {"compute": {"kind": "method", "form": "executable", "summary": "Fixture method",
            "source": original(source), "links": ["usage", "lesson"]},
            "usage": {"kind": "skill", "form": "guidance", "summary": "Select the method"},
            "lesson": {"kind": "experience", "form": "conditional_judgment", "summary": "Negative scope"}}}
    entry = tmp_path / "MAIN.json"
    save_progress(journal, entry, note, publication_id="first")
    return journal, entry, note, source


def test_advisor_search_and_publication_do_not_pollute_shared_memory(project, tmp_path):
    journal, main, note, _ = project
    advisor = tmp_path / "advisor" / "ENTRY.json"
    initial = fork_memory(main, advisor, memory_scope="advisor", question="Which bottleneck?")
    private = read_entry(advisor)["note"]
    assert private["knowledge"] == {} and "resources" not in private
    assert private["pending_handles"] == note["pending_handles"] and private["other_pauses"] == ["R4b"]
    patch_progress(advisor, {"summary": "Unvalidated advisor hypothesis"}, publication_id="first")
    shared = find_notes(journal, task_id="fixture", conditions=note["conditions"])
    assert len(shared["matches"]) == 1
    assert all(row["note"].get("memory_scope") == "advisor" for row in
        find_notes(journal, task_id="fixture", conditions=note["conditions"], memory_scope="advisor")["matches"])
    assert read_entry(main)["note"]["summary"] == note["summary"]
    repeated = fork_memory(main, advisor, memory_scope="advisor", question="Which bottleneck?")
    assert repeated["reused"] and repeated["record_id"] != initial["record_id"]
    with pytest.raises(ValueError):
        save_progress(journal, main, read_entry(advisor)["note"])
    pin = read_current(advisor)["details"]
    assert read_current(pin)["memory_scope"] == "advisor"
    forged = {**pin, "memory_scope": "shared"}
    with pytest.raises(ValueError, match="memory_scope"):
        read_entry(forged)


def test_private_hook_has_own_cursor_even_with_same_native_session(project, tmp_path):
    _, main, _, _ = project
    advisor = tmp_path / "ADVISOR.json"
    fork_memory(main, advisor, memory_scope="advisor", question="Improve?")
    event = {"hook_event_name": "SessionStart", "session_id": "fixture", "event_id": "start"}
    main_hook = research_hooks.handle_event(str(main), event, host="codex", state_dir=tmp_path / "hooks")
    advisor_hook = research_hooks.handle_event(str(advisor), event, host="codex", state_dir=tmp_path / "hooks")
    assert main_hook["ok"] and advisor_hook["ok"]
    assert main_hook["receipt_path"] != advisor_hook["receipt_path"]
    patch_progress(advisor, {"summary": "private-new-hypothesis"})
    changed = research_hooks.handle_event(str(advisor), {**event, "event_id": "after"}, host="codex", state_dir=tmp_path / "hooks")
    unchanged = research_hooks.handle_event(str(main), {**event, "event_id": "after"}, host="codex", state_dir=tmp_path / "hooks")
    assert "private-new-hypothesis" in str(changed["host_output"])
    assert "private-new-hypothesis" not in str(unchanged["host_output"])
    # A constructed event exercises delivery logic; it is not a native compaction experiment.


def test_handoff_discovers_only_its_entry_memory_scope(project, tmp_path):
    from etalon.research.note_handoff import _note_records
    journal, main, _, _ = project
    advisor = tmp_path / "ADVISOR.json"
    fork_memory(main, advisor, memory_scope="advisor", question="Improve?")
    _, shared = _note_records(journal, entry_value(main), 100)
    _, private = _note_records(journal, entry_value(advisor), 100)
    assert len(shared) == 1
    assert len([n for n in private.values() if n.get("memory_scope") == "advisor"]) == 1
    # The advisor explicitly cites its shared starting record. That original
    # travels with it; the main entry never acquires the private draft.
    assert set(shared) < set(private)


def test_layered_catalog_pages_exact_resources_and_rechecks_selected_source(project):
    _, entry, _, source = project
    page = build_resource_catalog(entry=entry, limit=1, max_bytes=4096)
    rows = []
    while True:
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 4096
        rows.extend(page["items"])
        if page["next_cursor"] is None:
            break
        page = build_resource_catalog(entry=entry, limit=1, max_bytes=4096, cursor=page["next_cursor"])
    assert [row["resource_id"] for row in rows[1:]] == ["compute", "lesson", "usage"]
    method = build_resource_catalog(entry=entry, kinds=["method"])["items"][0]
    resolved = resolve_method(method["content_ref"], method["pointer"])
    assert resolved["source"]["verification"] == "verified" and not resolved["execution_performed"]
    assert read_current(entry)["coverage"]["counter_count"] == 1
    source.write_text("print('changed')")
    with pytest.raises(ValueError):
        resolve_method(method["content_ref"], method["pointer"])


def test_resource_discovery_preserves_definition_context(project):
    _, entry, _, _ = project
    definitions = {"property": {"sha256": "a" * 64}}
    patch_progress(entry, {"definitions": definitions})
    page = build_resource_catalog(entry=entry, kinds=["method"])
    assert page["items"][0]["definitions"] == definitions


def test_fresh_process_and_skill_export_discover_usable_forms(project, tmp_path):
    _, entry, _, _ = project
    done = subprocess.run([sys.executable, "-m", "etalon", "research", "resources", "--entry", str(entry), "--kind", "method"],
        capture_output=True, text=True, check=True)
    assert json.loads(done.stdout)["items"][0]["form"] == "executable"
    manifest, contents = skills.snapshot("rsi-skill-creator")
    assert manifest["name"] == "rsi-skill-creator" and "SKILL.md" in contents
    assert "rsi-skill-creator" in skills.library_catalog()["libraries"]["rsi"]
