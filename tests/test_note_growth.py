"""Real byte reads and bounded continuation of long-lived host-authored notes."""
import json
import os
from pathlib import Path
import sys

import pytest

from etalon.active.schema import canonical
from etalon.research import notes
from etalon.research.notes import find_notes, read_current, read_entry, save_progress
from etalon.research.resource_catalog import build_resource_catalog
from test_research_notes import case


def _count_original_reads(monkeypatch, original):
    ordinary = Path.open
    measured = {"opens": 0, "bytes": 0}

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, count=-1):
            raw = self.stream.read(count)
            measured["bytes"] += len(raw)
            return raw

    def opened(path, mode="r", *args, **kwargs):
        stream = ordinary(path, mode, *args, **kwargs)
        if Path(path).resolve() == original.resolve() and mode == "rb":
            measured["opens"] += 1
            return Reader(stream)
        return stream

    monkeypatch.setattr(Path, "open", opened)
    return measured


def test_actual_large_original_reads_are_local_and_replay_does_not_publish(case, tmp_path, monkeypatch):
    journal, note, original = case
    payload = b"0123456789abcdef" * (512 * 1024)
    original.write_bytes(payload)
    ref = notes.original(original)
    note.update(evidence=[ref], counterevidence=[{**ref, "context": "ordinary control failed"}])
    measured = _count_original_reads(monkeypatch, original)
    entry = tmp_path / "entry.json"
    first = save_progress(journal, entry, note)
    assert measured == {"opens": 2, "bytes": 2 * len(payload)}
    baseline_entry = tmp_path / "unshared-entry.json"
    notes.publish_note(baseline_entry, journal, first)
    measured.update(opens=0, bytes=0)
    revised = {**note, "summary": "Host interpretation after the negative control"}
    rid = save_progress(journal, entry, revised)
    assert measured == {"opens": 2, "bytes": 2 * len(payload)}
    # Execute the former serial public call sequence against the same bytes.
    # This comparison already benefits from annotation deduplication, so it
    # isolates save_progress reuse rather than claiming both gains twice.
    measured.update(opens=0, bytes=0)
    old = notes.read_note(journal, first)
    baseline_id = notes.record_note(journal, {**revised, "supersedes": [old["record_id"]]}, tmp_path / "memory")
    notes.publish_note(baseline_entry, journal, baseline_id)
    assert baseline_id == rid
    assert measured == {"opens": 3, "bytes": 3 * len(payload)}
    measured.update(opens=0, bytes=0)
    notes.read_note(journal, baseline_id)
    notes.publish_note(baseline_entry, journal, baseline_id)
    assert measured == {"opens": 2, "bytes": 2 * len(payload)}
    measured.update(opens=0, bytes=0)
    before = entry.read_bytes()
    with journal.store.connection() as db:
        count = db.execute("SELECT count(*) FROM metadata WHERE key LIKE 'research:%'").fetchone()[0]
    assert save_progress(journal, entry, revised) == rid
    assert measured == {"opens": 1, "bytes": len(payload)}
    assert entry.read_bytes() == before
    with journal.store.connection() as db:
        assert db.execute("SELECT count(*) FROM metadata WHERE key LIKE 'research:%'").fetchone()[0] == count
    measured.update(opens=0, bytes=0)
    notes.publish_note(entry, journal, rid)
    assert measured == {"opens": 1, "bytes": len(payload)}
    measured.update(opens=0, bytes=0)
    read_entry(entry)
    assert measured == {"opens": 1, "bytes": len(payload)}
    # Equal size and restored mtime are not evidence of unchanged contents.
    stat = original.stat()
    original.write_bytes(b"x" + payload[1:])
    os.utime(original, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="original changed"):
        save_progress(journal, entry, revised)


def test_change_during_record_is_detected_before_head_advance(case, tmp_path, monkeypatch):
    journal, note, original = case
    entry = tmp_path / "entry.json"
    first = save_progress(journal, entry, note)
    ordinary = notes._save
    stat = original.stat()

    def change_during_record(path, *args, **kwargs):
        result = ordinary(path, *args, **kwargs)
        if Path(path) != entry:
            original.write_bytes(b"x" * stat.st_size)
            os.utime(original, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return result

    monkeypatch.setattr(notes, "_save", change_during_record)
    with pytest.raises(ValueError, match="original changed"):
        save_progress(journal, entry, {**note, "summary": "Must not become the head"})
    assert json.loads(entry.read_text())["record_id"] == first
    # Scope cleanup also applies on failure; the next independent call rechecks.
    with pytest.raises(ValueError, match="original changed"):
        read_entry(entry)


def _collection(details, pointer, *, max_bytes=4096):
    values = {}
    request = dict(view="selected", selectors=[pointer], limit=7, max_bytes=max_bytes)
    while request:
        page = read_entry(details, **request)
        assert len(canonical(page).encode()) <= max_bytes
        row = page["selected"][0]
        for item in row["items"]:
            assert "value" in item
            values[item["pointer"]] = item["value"]
        request = row.get("next_read")
    return values


def test_long_controls_definitions_and_branches_keep_exact_navigation(case, tmp_path):
    journal, note, _ = case
    note.update(definitions={f"metric:{i:03}": f"{i:064x}" for i in range(60)},
        state="paused", constraints=[f"Retain restriction {i:03}" for i in range(100)],
        authorization={f"scope_{i}": "read only" for i in range(80)},
        other_pauses={f"other_{i}": "user-paused" for i in range(80)},
        knowledge={"current_understanding": "Host-authored stable topic"},
        limits=["No independent transfer or scientific benefit established"])
    entry = tmp_path / "entry.json"
    for i in range(18):
        save_progress(journal, entry, {**note, "branch_id": f"topic-{i:03}"})
    body = json.loads(entry.read_text())
    body["definitions"] = note["definitions"]
    entry.write_text(json.dumps(body), encoding="utf-8")
    page = read_current(entry, max_bytes=4096)
    assert len(canonical(page).encode()) <= 4096
    assert page["controls"]["state"] == "paused"
    assert page["coverage"]["controls_complete"] is False
    assert "/constraints" in page["coverage"]["omitted_controls"]
    assert page["execution_permission"] == "not_granted"
    assert page["branch_navigation"]["total_items"] == 18
    assert page["branch_navigation"]["entry"] == str(entry)
    assert len(page["branches"]) == 1
    assert page["definition_binding"]["body_omitted"]
    assert "definitions_digest" in page["details"]
    definitions = _collection(page["details"], "/definitions")
    assert len(definitions) == 60
    constraints = _collection(page["details"], "/constraints")
    assert list(constraints.values()) == note["constraints"]
    counters = _collection(page["details"], "/counterevidence")
    assert list(counters.values()) == note["counterevidence"]
    with pytest.raises(ValueError, match="override"):
        read_current(page["details"], definitions={})
    # The existing catalog provides bounded, change-sensitive head pagination.
    nav = page["branch_navigation"]
    cursor, branches = None, set()
    while True:
        catalog = build_resource_catalog(entry=nav["entry"], cursor=cursor, limit=3, max_bytes=4096)
        branches.update(row["branch_id"] for row in catalog["items"])
        cursor = catalog["next_cursor"]
        if cursor is None:
            break
    assert branches == {f"topic-{i:03}" for i in range(18)}
    # Wrapper callers retain the real project entry for exact branch navigation.
    from etalon.research.research_context import build_research_context
    for view in ("summary", "card", "knowledge"):
        wrapped = build_research_context(entry, view=view, max_bytes=4096)
        assert len(canonical(wrapped).encode()) <= 4096
        assert wrapped["branch_navigation"]["entry"] == str(entry)
        assert len(wrapped["branches"]) == 1
    from etalon.research_hooks import _build_context
    hooked = _build_context(entry, policy=None, max_bytes=4096)
    assert len(canonical(hooked).encode()) <= 4096
    assert hooked["branch_navigation"]["entry"] == str(entry)
    assert len(hooked["branches"]) == 1
    assert hooked["controls"]["state"] == "paused"


def test_host_revises_stable_topic_without_history_or_definition_leak(case, tmp_path):
    journal, note, _ = case
    entry_a, entry_b = tmp_path / "a.json", tmp_path / "b.json"
    a = {**note, "definitions": {"metric:contact": "a" * 64}}
    b = {**note, "task_id": "different-task", "definitions": {"metric:contact": "b" * 64}}
    first = save_progress(journal, entry_a, a)
    save_progress(journal, entry_b, b)
    latest = save_progress(journal, entry_a, {**a, "summary": "Host revised this topic", "lesson": "Current scoped rule"})
    assert read_current(entry_a)["record_id"] == latest != first
    assert read_current(entry_a)["knowledge"]["summary"] == "Host revised this topic"
    older = notes.read_note(journal, first)
    assert older["note"]["summary"] == note["summary"]
    with pytest.raises(ValueError, match="definitions"):
        read_current(entry_a, definitions=b["definitions"])
    found = find_notes(journal, task_id=b["task_id"], conditions=b["conditions"], definitions=b["definitions"])
    assert len(found["matches"]) == 1 and not found["related"]
    assert found["matches"][0]["note"]["task_id"] == b["task_id"]


def test_optional_completion_and_wait_fields_are_bounded_current_facts(case, tmp_path):
    journal, note, _ = case
    note.update(completed_refs=["artifact-result"], completed=["measurement"],
        wait_for={"kind": "external_input", "handle": "review-1"})
    entry = tmp_path / "entry.json"
    save_progress(journal, entry, note)
    for view in ("brief", "index"):
        page = read_entry(entry, view=view, max_bytes=4096)
        for key in ("completed_refs", "completed", "wait_for"):
            assert page["controls"][key] == note[key]
        assert page["execution_permission"] == "not_granted"


def test_lifecycle_capsule_preserves_bounded_branch_navigation(case, tmp_path):
    from examples.research_host_lifecycle import capsule_from_goal, prompt_for
    journal, note, _ = case
    entry = tmp_path / "entry.json"
    for i in range(32):
        save_progress(journal, entry, {**note, "branch_id": f"topic-{i:03}"})
    profile = dict(scope="Local filesystem fixture only", authority="Read saved fixture notes",
        budget={"host_turns": 1}, bounds={"max_host_turns": 1, "max_wall_seconds": 60},
        native={"codex_executable": sys.executable, "cwd": str(tmp_path),
                "model": "fixture-no-launch", "reasoning_effort": "low"})
    capsule = capsule_from_goal("Inspect the current topic", entry, profile)
    initial = capsule["initial_context"]
    assert len(initial["branches"]) == 1
    assert initial["branch_navigation"]["total_items"] == 32
    assert initial["branch_navigation"]["entry"] == str(entry)
    assert len(canonical(initial).encode()) <= 16384
    assert len(json.dumps(capsule).encode()) <= 65536
    prompt = prompt_for(capsule, tmp_path / "run" / "turn-001", None)
    delivered = json.loads(prompt.split("Task capsule:\n", 1)[1])["current_context"]
    assert len(delivered["branches"]) == 1
    assert delivered["branch_navigation"] == initial["branch_navigation"]
    assert len(canonical(delivered).encode()) <= 16384
    assert len(prompt.encode()) <= 65536
    print(json.dumps({"fixture": "32-branch-capsule-no-native-launch",
        "context_bytes": len(canonical(delivered).encode()),
        "capsule_bytes": len(json.dumps(capsule).encode()), "prompt_bytes": len(prompt.encode())}))
