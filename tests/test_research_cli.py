"""The installed front door uses existing note semantics without dumping history."""
import json
import os
import subprocess
import sys
from pathlib import Path

import etalon
import pytest

from etalon.__main__ import main
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research.journal import ResearchJournal
from etalon.research.notes import entry_value, original, read_entry, save_progress
from etalon.runtime.files import file_identity


@pytest.fixture
def project(tmp_path):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"),
                    [Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0,
                              queryable=False)])
    journal = ResearchJournal(store)
    support = tmp_path / "support.json"
    support.write_text('{"measured": 1}', encoding="utf-8")
    counter = tmp_path / "counter.json"
    counter.write_text('{"measured": -1}', encoding="utf-8")
    note = {"task_id": "fixture", "branch_id": "main", "question": "Which rule generalizes?",
            "summary": "The second case contradicts the first.", "conditions": {"fixture": True},
            "knowledge": {"result": "unresolved", "large": "large result " * 5000},
            "other_pauses": {"R4b": "user-paused"}, "next_action": "Choose a useful contrast",
            "evidence": [original(support)], "counterevidence": [original(counter)]}
    entry = tmp_path / "entry.json"
    rid = save_progress(journal, entry, note)
    return journal, entry, note, rid, counter


def call(capsys, *args):
    status = main(["research", *map(str, args)])
    return status, json.loads(capsys.readouterr().out)


def test_current_is_bounded_read_only_and_leaves_counter_originals_unread(project, capsys, monkeypatch):
    from etalon.research import notes
    journal, entry, _, rid, _ = project
    before = {p: file_identity(p) for p in entry.parent.rglob("*") if p.is_file()}
    monkeypatch.setattr(notes, "_verify", lambda *a, **k: pytest.fail("default read opened originals"))
    code, result = call(capsys, "current", "--entry", entry, "--max-bytes", 4096)
    assert code == 0 and result["record_id"] == rid
    assert result["view"] == "brief" and "note" not in result
    assert result["coverage"]["counter_count"] == 1
    assert result["coverage"]["originals"] == "not_read" and result["originals_verified"] is None
    assert result["controls"]["other_pauses"] == {"R4b": "user-paused"}
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 4096
    assert before == {p: file_identity(p) for p in entry.parent.rglob("*") if p.is_file()}


def test_current_honors_recipe_and_detail_pins_old_record_and_counter(project, capsys):
    journal, entry, note, rid, counter = project
    metadata = entry_value(entry)
    metadata["read_recipe"] = {"view": "selected", "selectors": ["/knowledge/result", "/counterevidence"]}
    entry.write_text(json.dumps(metadata), encoding="utf-8")
    code, result = call(capsys, "current", "--entry", entry)
    assert code == 0 and result["view"] == "selected"
    assert {row["pointer"] for row in result["selected"]} == {"/knowledge/result", "/counterevidence"}
    save_progress(journal, entry, {**note, "summary": "New interpretation"})
    code, pinned = call(capsys, "detail", "--entry", entry, "--record-id", rid,
                        "--select", "/summary", "--select", "/counterevidence")
    assert code == 0 and pinned["record_id"] == rid
    assert pinned["selected"][0]["value"] == note["summary"]
    assert pinned["coverage"]["counter_count"] == 1
    assert pinned["selected"][1]["items"][0]["value"]["path"] == str(counter)
    counter.write_text('{"measured": 999}', encoding="utf-8")
    assert call(capsys, "current", "--entry", entry)[0] == 0
    code, failed = call(capsys, "current", "--entry", entry, "--verify-originals")
    assert code == 2 and "original changed" in failed["error"]["message"]
    assert call(capsys, "detail", "--entry", entry, "--view", "full")[0] == 2


def test_patch_keeps_omitted_fields_and_replay_preserves_later_interpretation(project, capsys):
    journal, entry, note, rid, _ = project
    patch = entry.parent / "patch.json"
    patch.write_text(json.dumps({"summary": "New result", "next_action": "Compare another case"}), encoding="utf-8")
    args = ("progress", "--entry", entry, "--patch", patch, "--expected-record", rid,
            "--publication-id", "fixture:result")
    code, saved = call(capsys, *args)
    assert code == 0 and len(json.dumps(saved)) < 1024 and "note" not in saved
    written = read_entry(entry)["note"]
    for key in ("knowledge", "conditions", "evidence", "counterevidence", "other_pauses"):
        assert written[key] == note[key]
    assert written["summary"] == "New result" and written["supersedes"] == [rid]
    assert call(capsys, *args)[1]["record_id"] == saved["record_id"]
    later = save_progress(journal, entry, {**note, "summary": "Later host interpretation", "outcome": "continue"})
    code, replayed = call(capsys, *args)
    assert code == 0 and replayed["record_id"] == saved["record_id"]
    assert replayed["current_record_id"] == later and entry_value(entry)["record_id"] == later
    assert read_entry(entry)["note"]["outcome"] == "continue"
    patch.write_text(json.dumps({"summary": "Different result"}), encoding="utf-8")
    code, failed = call(capsys, *args)
    assert code == 2 and "publication identity reused" in failed["error"]["message"]
    assert entry_value(entry)["record_id"] == later


def test_old_pin_keeps_original_definition_scope_when_current_definitions_change(project, capsys):
    journal, entry, note, _, _ = project
    first_definitions = {"score": "a" * 64}
    old = save_progress(journal, entry, {**note, "definitions": first_definitions})
    metadata = entry_value(entry)
    metadata["definitions"] = first_definitions
    entry.write_text(json.dumps(metadata), encoding="utf-8")
    code, first = call(capsys, "current", "--entry", entry)
    assert code == 0
    pin = entry.parent / "details.json"
    pin.write_text(json.dumps(first["details"]), encoding="utf-8")
    second_definitions = {"score": "b" * 64}
    save_progress(journal, entry, {**note, "definitions": second_definitions})
    metadata = entry_value(entry)
    metadata["definitions"] = second_definitions
    entry.write_text(json.dumps(metadata), encoding="utf-8")
    code, failed = call(capsys, "detail", "--entry", entry, "--record-id", old, "--select", "/summary")
    assert code == 2 and "definitions" in failed["error"]["message"]
    code, pinned = call(capsys, "detail", "--entry", pin, "--select", "/definitions")
    assert code == 0 and pinned["record_id"] == old and pinned["definitions"] == first_definitions


def test_append_originals_replays_without_duplicates_and_rejects_changed_bytes(project, capsys):
    _, entry, note, rid, _ = project
    patch = entry.parent / "patch.json"
    patch.write_text('{"summary":"New result with contrary evidence"}', encoding="utf-8")
    result = entry.parent / "new-result.json"
    result.write_text('{"measured":2}', encoding="utf-8")
    args = ("progress", "--entry", entry, "--patch", patch, "--expected-record", rid,
            "--publication-id", "new-result", "--evidence", result, "--counterevidence", result)
    code, saved = call(capsys, *args)
    assert code == 0
    for role in ("evidence", "counterevidence"):
        assert read_entry(entry)["note"][role] == note[role] + [original(result)]
    assert call(capsys, *args)[1]["record_id"] == saved["record_id"]
    result.write_text('{"measured":999}', encoding="utf-8")
    code, failure = call(capsys, *args)
    assert code == 2 and "original changed" in failure["error"]["message"]
    assert entry_value(entry)["record_id"] == saved["record_id"]


def test_patch_rejects_stale_parent_and_supports_background_complete_notes(project, capsys):
    _, entry, note, rid, _ = project
    payload = entry.parent / "note.json"
    payload.write_text(json.dumps({**note, "branch_id": "alternate"}), encoding="utf-8")
    code, saved = call(capsys, "progress", "--entry", entry, "--note", payload, "--background")
    assert code == 0 and saved["current_record_id"] == rid
    assert entry_value(entry)["branches"]["alternate"] == saved["record_id"]
    payload.write_text(json.dumps({"summary": "A new main interpretation"}), encoding="utf-8")
    before = file_identity(entry)
    code, failed = call(capsys, "progress", "--entry", entry, "--patch", payload,
                        "--expected-record", saved["record_id"])
    assert code == 2 and "expected record" in failed["error"]["message"]
    assert file_identity(entry) == before


def test_relocated_locator_reads_and_writes_live_copy_without_changing_snapshot(project, tmp_path, capsys):
    from etalon.research.handoff import export_research_handoff, plan_research_handoff
    _, entry, _, _, _ = project
    bundle = tmp_path / "portable"
    export_research_handoff(plan_research_handoff(entry), bundle)
    portable = bundle / "entry.json"
    sealed = {p: file_identity(p) for p in bundle.rglob("*") if p.is_file() and p != portable}
    locator = tmp_path / "locator.json"
    locator.write_text('{"entry":"portable/entry.json"}', encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(etalon.__file__).parent.parent),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-B", "-m", "etalon", "research", "current",
                             "--entry", str(locator)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout)["task_id"] == "fixture"
    patch = tmp_path / "patch.json"
    patch.write_text('{"summary":"Continue the portable research"}', encoding="utf-8")
    code, saved = call(capsys, "progress", "--entry", locator, "--patch", patch)
    assert code == 0 and saved["record_id"] == read_entry(portable)["record_id"]
    assert json.loads(locator.read_text(encoding="utf-8")) == {"entry": "portable/entry.json"}
    assert all(file_identity(path) == identity for path, identity in sealed.items())


def test_runtime_names_actual_loaded_interpreter_and_reader(capsys):
    from etalon.research import notes
    code, report = call(capsys, "runtime")
    assert code == 0 and report["python"] == sys.executable
    reader = report["modules"]["etalon.research.notes"]
    assert Path(reader["path"]) == Path(notes.__file__)
    assert reader["sha256"] == file_identity(notes.__file__)["sha256"]


def test_producer_patch_and_cli_share_replay_and_bounded_feedback(project, capsys):
    from etalon.research.notes import patch_progress
    journal, entry, note, rid, _ = project
    feedback = {"contrast": "fixture cheaper predicate agrees within its scope",
                "unresolved": "independent conditions remain untested"}
    patch = {"summary": "Fixture method selected", "feedback": feedback}
    saved = patch_progress(entry, patch, expected_record=rid, publication_id="producer:method")
    _, current = call(capsys, "current", "--entry", entry)
    assert current["knowledge"]["feedback"] == feedback
    assert current["coverage"]["counter_count"] == 1
    later = save_progress(journal, entry, {**note, "summary": "A later interpretation"})
    payload = entry.parent / "producer-patch.json"
    payload.write_text(json.dumps(patch), encoding="utf-8")
    code, replayed = call(capsys, "progress", "--entry", entry, "--patch", payload,
                          "--expected-record", rid, "--publication-id", "producer:method")
    assert code == 0 and replayed["record_id"] == saved["record_id"]
    assert replayed["current_record_id"] == later
    patch_progress(entry, {"feedback": {"contrasts": ["fixture" * 4000]}})
    code, brief = call(capsys, "current", "--entry", entry, "--max-bytes", 4096)
    assert code == 0 and "/feedback" in brief["coverage"]["omitted_fields"]
    assert brief["coverage"]["counter_count"] == 1


def test_work_reads_note_as_executable_context_without_running(project, capsys):
    _, entry, _, rid, _ = project
    code, result = call(capsys, "work", "--entry", entry)
    assert code == 0 and result["record_id"] == rid
    assert result["work"]["question"] == "Which rule generalizes?"
    assert result["work"]["next_segment"] is None
    assert result["work"]["other_pauses"] == {"R4b": "user-paused"}
    assert result["execution_permission"] == "not_granted"
