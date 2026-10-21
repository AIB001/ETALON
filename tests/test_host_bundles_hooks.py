"""C component fixtures; these do not establish native host or scientific benefit."""
import json
from pathlib import Path

import pytest

from etalon import host, skills
from etalon.runtime import api


def bundle(tmp_path):
    root = tmp_path / "new-method"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: new-method\ndescription: exploratory fixture\n---\nPartial adoption allowed.\n", encoding="utf-8")
    (root / "references").mkdir()
    (root / "references" / "claim.md").write_text("Unknown effect.\n", encoding="utf-8")
    (root / "script.py").write_text("print('fixture')\n", encoding="utf-8")
    return root, {"root": str(root)}


def test_open_bundle_snapshots_survive_reference_changes_and_deletion(tmp_path):
    root, ref = bundle(tmp_path)
    old, _ = skills.snapshot("new-method", bundle_ref=ref)
    saved = skills.persist_snapshot("new-method", tmp_path / "frozen-v1", bundle_ref=ref)
    assert skills.verify_snapshot(saved) == old
    (root / "references" / "claim.md").write_text("New hypothesis.\n", encoding="utf-8")
    changed, _ = skills.snapshot("new-method", bundle_ref=ref)
    assert changed["manifest_sha256"] != old["manifest_sha256"]
    (root / "script.py").unlink()
    deleted, _ = skills.snapshot("new-method", bundle_ref=ref)
    assert deleted["manifest_sha256"] != changed["manifest_sha256"]
    assert skills.verify_snapshot(saved) == old
    with pytest.raises(FileExistsError):
        skills.persist_snapshot("new-method", tmp_path / "frozen-v1", bundle_ref=ref)
    with pytest.raises(ValueError, match="digest"):
        skills.read_member("new-method", bundle_ref={**ref, "manifest_sha256": old["manifest_sha256"]})


def test_snapshot_verification_reads_bytes_and_inventory(tmp_path):
    root, ref = bundle(tmp_path)
    saved = skills.persist_snapshot("new-method", tmp_path / "frozen", bundle_ref=ref)
    snapshot_root = Path(saved["manifest_path"]).parent / "bundle"
    (snapshot_root / "script.py").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        skills.verify_snapshot(saved)


@pytest.mark.parametrize("member", ["../secret", "C:/secret", "a/../secret", "a\\secret", "/secret"])
def test_external_bundle_member_paths_stay_bounded(tmp_path, member):
    _, ref = bundle(tmp_path)
    with pytest.raises(ValueError):
        skills.read_member("new-method", member, bundle_ref=ref)


def test_external_bundle_requires_metadata_and_bounded_content(tmp_path):
    root, ref = bundle(tmp_path)
    (root / "huge").write_bytes(b"x" * (skills.MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="256 KiB"):
        skills.snapshot("new-method", bundle_ref=ref)
    with pytest.raises(ValueError, match="absolute"):
        skills.snapshot("new-method", bundle_ref={"root": "relative"})


def fixture_events(monkeypatch, events):
    def read(log_ref, *, after_sequence, limit):
        log_id = "fixture:" + log_ref["database"]
        cursor = after_sequence
        if isinstance(cursor, dict):
            if cursor["log_id"] != log_id:
                raise ValueError("wrong log")
            cursor = cursor["after_sequence"]
        selected = [e for e in events if e["sequence"] > cursor][:limit]
        return {"log_id": log_id, "log_ref": log_ref,
                "events": [{**e, "event_ref": {"log_id": log_id, "sequence": e["sequence"], "event_type": e["kind"]}} for e in selected],
                "next_cursor": {"log_id": log_id, "after_sequence": selected[-1]["sequence"] if selected else cursor},
                "has_more": len([e for e in events if e["sequence"] > cursor]) > len(selected)}
    monkeypatch.setattr(api, "read_events", read, raising=False)


def test_hook_submit_then_cursor_failure_replays_stable_request(tmp_path, monkeypatch):
    facts = [{"sequence": 1, "kind": "action_resolved", "body": {"admitted": False}}]
    fixture_events(monkeypatch, facts)
    ids, dispatched = [], set()
    def handler(event, request_id):
        ids.append(request_id)
        dispatched.add(request_id)  # Fixture models downstream request-id deduplication.
    params = dict(log_ref={"kind": "campaign", "database": "fixture-A"},
                  state_path=tmp_path / "cursor.json", handler_method_version="method-v1",
                  handler=handler, event_types=["action_resolved"])
    save = host._save_cursor
    def crash(*args):
        raise RuntimeError("crash after action submission")
    monkeypatch.setattr(host, "_save_cursor", crash)
    with pytest.raises(RuntimeError):
        host.consume_events(**params)
    assert len(facts) == 1  # ordinary hook failure does not rewrite committed facts
    monkeypatch.setattr(host, "_save_cursor", save)
    assert len(host.consume_events(**params)["handled"]) == 1
    assert len(ids) == 2 and ids[0] == ids[1] and len(dispatched) == 1
    assert host.consume_events(**params)["handled"] == []
    with pytest.raises(ValueError, match="different log"):
        host.consume_events(**{**params, "log_ref": {"kind": "campaign", "database": "fixture-B"}})
    with pytest.raises(ValueError, match="handler version"):
        host.consume_events(**{**params, "handler_method_version": "method-v2"})


def test_research_hook_requires_type_filter_and_does_not_trigger_on_own_version(tmp_path, monkeypatch):
    facts = [{"sequence": 1, "kind": "research_recorded", "body": {"record": {"schema": "etalon-method-evaluation/1"}}},
             {"sequence": 2, "kind": "research_recorded", "body": {"record": {"schema": "etalon-method-version/1"}}}]
    fixture_events(monkeypatch, facts)
    seen = []
    params = dict(log_ref={"kind": "campaign", "database": "fixture-A"}, state_path=tmp_path / "cursor.json",
                  handler_method_version="G-v1", handler=lambda event, request_id: seen.append(event["sequence"]),
                  event_types=["research_recorded"])
    with pytest.raises(ValueError, match="schema filters"):
        host.consume_events(**params)
    result = host.consume_events(**params, record_types=["etalon-method-evaluation/1"])
    assert seen == [1] and len(result["skipped"]) == 1
    assert result["next_cursor"]["after_sequence"] == 2
    assert host.consume_events(**params, record_types=["etalon-method-evaluation/1"])["handled"] == []


def test_hook_empty_page_does_not_skip_future_and_can_skip_all(tmp_path, monkeypatch):
    facts = []
    fixture_events(monkeypatch, facts)
    params = dict(log_ref={"kind": "campaign", "database": "fixture-A"}, state_path=tmp_path / "cursor.json",
                  handler_method_version="none", handler=lambda *args: pytest.fail("explicit skip"), event_types=[])
    assert host.consume_events(**params)["next_cursor"]["after_sequence"] == 0
    facts.append({"sequence": 1, "kind": "action_resolved", "body": {}})
    assert len(host.consume_events(**params)["skipped"]) == 1


def test_content_snapshot_and_partial_use_record_in_existing_journal(tmp_path):
    from etalon.research import MethodVersion
    from test_research_journal import setup
    store, journal, _ = setup(tmp_path)
    _, ref = bundle(tmp_path)
    saved = skills.persist_snapshot("new-method", tmp_path / "frozen", bundle_ref=ref)
    identifier = journal.record(MethodVersion("exploratory", ("domain_skill",), saved,
                                            verification_basis="content_verified"))
    use_id = host.record_method_use(journal,
        subject_refs=({"kind": "request", "id": "next-step", "scope": str(store.path)},),
        selected_methods=({"method_version_ref": identifier, "scope": "reference only"},),
        host_conditions={"model": None}, reason="exploration without promotion")
    record = journal.get(use_id)
    assert record["basis"] == "host_reported"
    assert record["selected_methods"][0]["method_version_ref"] == identifier
    assert not store.observations()
