"""Filesystem/SQLite integration only; no scientific job is executed."""
import json
import sqlite3
from pathlib import Path

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research import ResearchJournal
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.handoff import (export_research_handoff, plan_research_handoff,
    relocate_research_handoff, verify_research_handoff)
from etalon.research.notes import (entry_journal, entry_value, find_notes, original,
    read_entry, read_note, record_note, save_progress)
from test_research_graph_memory import graph_case
from test_research_handoff import pinned_workset


@pytest.fixture
def notes_case(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    store = CampaignStore(root / "journal.sqlite")
    store.configure(CampaignSpec("x", 0, "wall_second", "fixture"),
        [Endpoint("x", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    result = root / "result.json"
    result.write_text('{"outcome":"negative","unknown":null}', encoding="utf-8")
    note = dict(task_id="fixture", branch_id="main", question="What survives?",
        summary="The original negative result", conditions={"scope": "fixture"},
        outcome="retain", unknown=None, evidence=[original(result)], counterevidence=[original(result)])
    entry = root / "entry.json"
    old = save_progress(journal, entry, note)
    other = save_progress(journal, entry, {**note, "branch_id": "other", "summary": "Another open question",
        "pending_handles": [{"host": "unavailable.example", "job_id": "external-17", "status": "unknown"}]})
    current = save_progress(journal, entry, {**note, "summary": "Scoped explanation; old negative retained"}, activate=False)
    # An interrupted publication is research history too, even without a head.
    unpublished = record_note(journal, {**note, "branch_id": "unpublished", "summary": "Unpublished observation"}, root / "memory")
    return dict(root=root, journal=journal, entry=entry, note=note, result=result,
                old=old, other=other, current=current, unpublished=unpublished)


def test_note_handoff_keeps_branches_history_and_deduplicates_originals(notes_case, tmp_path):
    c = notes_case
    before = {p: file_identity(p) for p in c["root"].rglob("*") if p.is_file()}
    plan = plan_research_handoff(c["entry"])
    assert plan["entry_kind"] == "note" and plan["checkpoint_ref"] is None
    assert plan["counts"]["notes"] == 4
    assert sum(f["source"] == str(c["result"]) for f in plan["files"]) == 1
    assert any(f.get("materialization") == "sqlite_backup" for f in plan["files"])
    target = tmp_path / "copy"
    result = export_research_handoff(plan, target)
    assert result["status"] == "verified" and result["execution_permission"] == "not_granted"
    assert result["pending"][0]["execution_migration"] == "not_performed"
    assert read_entry(target / "entry.json")["record_id"] == c["other"]
    assert read_entry(target / "entry.json", branch_id="main")["record_id"] == c["current"]
    journal = entry_journal(entry_value(target / "entry.json"))
    assert read_note(journal, c["old"])["note"]["unknown"] is None
    assert read_note(journal, c["unpublished"])["note"]["summary"] == "Unpublished observation"
    assert len(find_notes(journal, task_id="fixture", conditions=c["note"]["conditions"])["matches"]) == 4
    assert before == {p: file_identity(p) for p in before}
    assert not c["journal"].store.actions()


def test_note_handoff_survives_originals_and_first_bundle_disappearing(notes_case, tmp_path):
    c = notes_case
    first, moved = tmp_path / "first", tmp_path / "moved"
    export_research_handoff(plan_research_handoff(c["entry"]), first)
    relocate_research_handoff(first, moved)
    # Renaming actual directories makes all historical absolute paths unavailable.
    c["root"].rename(tmp_path / "unavailable-origin")
    first.rename(tmp_path / "unavailable-first")
    verification = verify_research_handoff(moved, verify_originals=False)
    assert verification["status"] == "bundle_verified" and verification["originals"] == "not_checked"
    result = read_entry(moved / "entry.json", branch_id="main", view="knowledge")
    assert read_entry(result["details"])["record_id"] == c["current"]
    journal = entry_journal(entry_value(moved / "entry.json"))
    assert read_note(journal, c["old"])["originals_verified"] == 1
    with pytest.raises((OSError, ValueError)):
        verify_research_handoff(moved)


def test_note_handoff_one_hop_and_no_graph_requirement(notes_case, tmp_path, monkeypatch):
    c = notes_case
    import etalon.research.handoff as handoff
    monkeypatch.setattr(handoff, "resume_research", lambda *a, **kw: pytest.fail("notes require no checkpoint"))
    locator = c["root"] / "CURRENT.json"
    locator.write_text(json.dumps({"entry": "entry.json"}))
    result = export_research_handoff(plan_research_handoff(locator), tmp_path / "bundle")
    assert result["status"] == "verified"


def test_existing_mcp_handoff_and_context_read_ordinary_notes(notes_case, tmp_path):
    from etalon.mcp.evidence import register
    class Collector:
        def __init__(self):
            self.tools = {}
        def tool(self):
            def add(fn):
                self.tools[fn.__name__] = fn
                return fn
            return add
    collector = Collector()
    register(collector)
    target = tmp_path / "mcp-copy"
    result = json.loads(collector.tools["etalon_research_handoff"](str(notes_case["entry"]), str(target)))
    assert result["ok"] and result["status"] == "verified"
    notes_case["root"].rename(tmp_path / "mcp-origin-unavailable")
    verified = json.loads(collector.tools["etalon_research_handoff_verify"](str(target), verify_originals=False))
    read = json.loads(collector.tools["etalon_research_context"](str(target / "entry.json")))
    assert verified["ok"] and read["ok"] and read["record_id"] == notes_case["other"]


def test_note_handoff_active_wal_is_backed_up_without_stopping_writer(notes_case, tmp_path):
    c = notes_case
    connection = sqlite3.connect(c["journal"].store.path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("INSERT INTO metadata VALUES ('fixture:wal', '{}')")
        connection.commit()
        assert Path(str(c["journal"].store.path) + "-wal").stat().st_size
        result = export_research_handoff(plan_research_handoff(c["entry"]), tmp_path / "wal-copy")
        assert result["status"] == "verified"
        connection.execute("INSERT INTO metadata VALUES ('fixture:continues', '{}')")
        connection.commit()
        assert read_entry(tmp_path / "wal-copy/entry.json")["record_id"] == c["other"]
    finally:
        connection.close()


@pytest.mark.parametrize("change", ["original", "journal", "entry"])
def test_note_handoff_detects_changes_after_plan(notes_case, tmp_path, change):
    c = notes_case
    plan = plan_research_handoff(c["entry"])
    if change == "original":
        c["result"].write_text("changed")
    elif change == "entry":
        c["entry"].write_text("{}")
    else:
        with c["journal"].store.connection(write=True) as db:
            db.execute("INSERT INTO metadata VALUES ('changed', '{}')")
    with pytest.raises(EvidenceError, match="changed"):
        export_research_handoff(plan, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_note_handoff_missing_original_conflict_and_bundle_rewrite(notes_case, tmp_path):
    c = notes_case
    plan = plan_research_handoff(c["entry"])
    target = tmp_path / "bundle"
    export_research_handoff(plan, target)
    with pytest.raises(EvidenceError, match="already exists"):
        export_research_handoff(plan, target)
    with pytest.raises(EvidenceError, match="already exists"):
        relocate_research_handoff(target, target)
    member = next(f for f in plan["files"] if f["source"] == str(c["result"]))
    (target / member["target"]).write_text("counterevidence was altered")
    with pytest.raises(EvidenceError, match="identity mismatch"):
        verify_research_handoff(target, verify_originals=False)
    c["result"].unlink()
    with pytest.raises(FileNotFoundError):
        plan_research_handoff(c["entry"])


def test_note_optional_published_workset_keeps_exact_old_skill(pinned_workset, notes_case, graph_case, tmp_path, monkeypatch):
    from etalon import skills
    from etalon.research.entry import resolve_entry
    c = notes_case
    work = pinned_workset
    note = {**c["note"], "task_id": "snd1-fixture", "conditions": {"target": "fixture"},
            "basis_refs": [graph_case["evidence"]]}
    entry = c["root"] / "workset-note.json"
    save_progress(c["journal"], entry, note)
    workspace, checkpoint, _, _ = resolve_entry(work["entry"])
    value = json.loads(entry.read_text())
    value["task_workset"] = {"workspace": str(workspace), "checkpoint_ref": checkpoint}
    entry.write_text(json.dumps(value))
    target = tmp_path / "note-workset"
    plan = plan_research_handoff(entry, skill_bundles=[work["override"]])
    export_research_handoff(plan, target)
    moved = tmp_path / "moved-workset"
    relocate_research_handoff(target, moved)
    c["root"].rename(tmp_path / "lost-notes")
    workspace.rename(workspace.with_name("unavailable-workspace.json"))
    work["old"].rename(tmp_path / "unavailable-old-skill")
    monkeypatch.setattr(skills, "bundle_root", lambda *args: pytest.fail("must use exact archived skill"))
    result = read_entry(moved / "entry.json", view="knowledge")
    selected = result["task_workset"]["selected_skill_sources"][0]
    assert skills.read_member(selected["slug"], bundle_ref=selected["bundle_ref"])["content"].endswith("ORIGINAL")
    assert verify_research_handoff(moved, verify_originals=False)["status"] == "bundle_verified"
    live_entry = moved / "entry.json"
    journal = entry_journal(entry_value(live_entry), read_only=False)
    updated = {**read_entry(live_entry)["note"], "summary": "Continued with the same exact optional workset"}
    updated.pop("supersedes", None)
    save_progress(journal, live_entry, updated)
    next_copy = tmp_path / "continued-workset"
    export_research_handoff(plan_research_handoff(live_entry), next_copy)
    moved.rename(tmp_path / "previous-workset-unavailable")
    latest = read_entry(next_copy / "entry.json", view="knowledge")
    chosen = latest["task_workset"]["selected_skill_sources"][0]
    assert skills.read_member(chosen["slug"], bundle_ref=chosen["bundle_ref"])["content"].endswith("ORIGINAL")


def test_note_saved_skill_reference_moves_without_workset(notes_case, tmp_path, monkeypatch):
    from etalon import skills
    from etalon.research.evidence_refs import EvidenceResolver
    c = notes_case
    content_ref = skills.persist_snapshot("research-context", c["root"] / "saved-skill")
    note = {**c["note"], "branch_id": "method", "method_choices": [{"content_ref": content_ref, "adoption": "unknown"}]}
    rid = save_progress(c["journal"], c["entry"], note)
    target = tmp_path / "skill-copy"
    plan = plan_research_handoff(c["entry"])
    export_research_handoff(plan, target)
    c["root"].rename(tmp_path / "missing-skill-origin")
    monkeypatch.setattr(skills, "bundle_root", lambda *args: pytest.fail("must retain saved version"))
    result = read_entry(target / "entry.json")
    assert result["record_id"] == rid and result["note"]["method_choices"][0]["adoption"] == "unknown"
    resolver = EvidenceResolver(target / "workspace.json", plan["note_source_id"])
    assert skills.verify_snapshot(content_ref, resolver=resolver)["manifest_sha256"] == content_ref["manifest_sha256"]
    assert verify_research_handoff(target, verify_originals=False)["status"] == "bundle_verified"


def test_note_missing_explicit_reference_is_reported(notes_case, tmp_path):
    from etalon.research.evidence_refs import make_ref
    c = notes_case
    ref = make_ref("missing-source", c["old"], c["journal"].get(c["old"]))
    save_progress(c["journal"], c["entry"], {**c["note"], "basis_refs": [ref]})
    with pytest.raises(EvidenceError, match="no explicit source workspace"):
        plan_research_handoff(c["entry"])


def test_relocated_note_can_continue_without_changing_sealed_archive(notes_case, tmp_path):
    import subprocess
    import sys
    c = notes_case
    sealed, active = tmp_path / "sealed", tmp_path / "active"
    export_research_handoff(plan_research_handoff(c["entry"]), sealed)
    relocate_research_handoff(sealed, active)
    before = {p: file_identity(p) for p in sealed.rglob("*") if p.is_file()}
    archived = {p: file_identity(p) for p in active.rglob("*") if p.is_file() and p.name != "entry.json"}
    c["root"].rename(tmp_path / "original-now-unavailable")
    entry = active / "entry.json"
    journal = entry_journal(entry_value(entry), read_only=False)
    result = active / "new-result.json"
    result.write_text('{"new_observation":"also_negative"}', encoding="utf-8")
    rid = save_progress(journal, entry, {**c["note"], "summary": "Additional negative progress after relocation",
        "evidence": [original(result)], "counterevidence": c["note"]["counterevidence"]})
    code = "from etalon.research.notes import *; import sys; e=sys.argv[1]; r=read_current(e); assert r['record_id']==sys.argv[2]; j=entry_journal(entry_value(e),read_only=False); assert read_note(j,sys.argv[3])['note']['summary']=='The original negative result'; n=read_note(j,sys.argv[2])['note']; n.pop('supersedes',None); n['summary']='Second process continued'; new=save_progress(j,e,n); assert read_current(e)['record_id']==new"
    subprocess.run([sys.executable, "-c", code, str(entry), rid, c["old"]], check=True, capture_output=True, text=True)
    assert before == {p: file_identity(p) for p in before}
    assert archived == {p: file_identity(p) for p in archived}
    assert verify_research_handoff(sealed, verify_originals=False)["status"] == "bundle_verified"
    # The active copy can itself be handed off again; old and new references
    # must both resolve after all intermediate active locations disappear.
    next_bundle = tmp_path / "next-generation"
    export_research_handoff(plan_research_handoff(entry), next_bundle)
    active.rename(tmp_path / "prior-active-unavailable")
    newest = read_entry(next_bundle / "entry.json")
    assert newest["note"]["summary"] == "Second process continued"
    assert read_note(entry_journal(entry_value(next_bundle / "entry.json")), c["old"])["originals_verified"] == 1


def test_write_open_reuses_interrupted_note_copy(notes_case, tmp_path):
    c = notes_case
    bundle = tmp_path / "working-copy"
    export_research_handoff(plan_research_handoff(c["entry"]), bundle)
    entry = bundle / "entry.json"
    first = entry_journal(entry_value(entry), read_only=False)
    body = {**c["note"], "summary": "Recovered ordinary progress", "supersedes": [c["current"]]}
    pending = record_note(first, body, bundle / "memory")
    reopened = entry_journal(entry_value(entry), read_only=False)
    assert reopened.store.path == first.store.path
    assert save_progress(reopened, entry, body) == pending
    assert read_entry(entry)["record_id"] == pending
