"""V cross-module acceptance; no mock event reader or mock request deduplication."""
from pathlib import Path

import pytest

from etalon import host, skills
from etalon.active.schema import canonical
from etalon.research import MethodVersion, ResearchJournal
from etalon.runtime import api
from test_runtime_actions import fixture_action


def test_hook_submission_survives_real_checkpoint_loss_without_second_execution(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    log = {"kind": "campaign", "database": str(store.path)}
    cursor_path = tmp_path / "consumer.json"
    frozen = api.prepare_action(request)
    seen = []

    def handler(event, stable_request_id):
        action = {**request, "request_id": stable_request_id}
        seen.append(api.submit_action(action, workspace=tmp_path / "runtime",
                                      plan_id=frozen["plan_id"]))

    params = dict(log_ref=log, state_path=cursor_path, handler_method_version="V-fixture-G1",
                  handler=handler, event_types=["independent_trigger"])
    # Save a real starting cursor, then append a committed trigger.
    host.consume_events(**params)
    start_bytes = cursor_path.read_bytes()
    with store.connection(write=True) as db:
        store._event(db, "independent_trigger", {"scope": "synthetic engineering"})
    save = host._save_cursor

    def fail_checkpoint(*args):
        raise RuntimeError("V injected checkpoint loss after accepted submission")

    monkeypatch.setattr(host, "_save_cursor", fail_checkpoint)
    with pytest.raises(RuntimeError, match="checkpoint loss"):
        host.consume_events(**params)
    assert cursor_path.read_bytes() == start_bytes
    assert len(calls) == 1 and store.balance()["spent"] == 1.5
    before = canonical(store.export())
    monkeypatch.setattr(host, "_save_cursor", save)
    replay = host.consume_events(**params)
    assert len(replay["handled"]) == 1
    assert len(seen) == 2 and seen[0]["job_id"] == seen[1]["job_id"]
    assert len(calls) == 1 and canonical(store.export()) == before
    assert api.read_action_result(seen[1])["execution_state"] == "completed"
    assert host.consume_events(**params)["handled"] == []
    event_ref = replay["handled"][0]["event_ref"]
    assert host.hook_request_id(event_ref, "V-fixture-G2") != seen[0]["request_id"]


def test_handler_failure_preserves_committed_facts_and_position(tmp_path, monkeypatch):
    store, _, _, _ = fixture_action(tmp_path, monkeypatch)
    log = {"kind": "campaign", "database": str(store.path)}
    cursor = tmp_path / "cursor.json"
    def crash(*args):
        raise RuntimeError("V hook failure")
    params = dict(log_ref=log, state_path=cursor, handler_method_version="V1",
                  handler=crash, event_types=["independent_trigger"])
    host.consume_events(**params)
    saved = cursor.read_bytes()
    with store.connection(write=True) as db:
        store._event(db, "independent_trigger", {"committed": True})
    before = canonical(store.export())
    with pytest.raises(RuntimeError, match="V hook failure"):
        host.consume_events(**params)
    assert canonical(store.export()) == before and cursor.read_bytes() == saved


def test_real_research_filter_does_not_recurse_on_method_version(tmp_path, monkeypatch):
    from test_research_methods import evaluation_fixture
    store, journal, evaluation = evaluation_fixture(tmp_path)
    log = {"kind": "campaign", "database": str(store.path)}
    seen = []
    def handler(event, request_id):
        seen.append(request_id)
        journal.record(MethodVersion("new exploratory candidate", ("domain_skill",),
                                     {"manifest_sha256": "f" * 64}))
    params = dict(log_ref=log, state_path=tmp_path / "cursor.json",
                  handler_method_version="V-improvement-v1", handler=handler,
                  event_types=["research_recorded"], record_types=["etalon-method-evaluation/1"])
    host.consume_events(**params)
    journal.record(evaluation)
    assert len(host.consume_events(**params)["handled"]) == 1
    assert host.consume_events(**params)["handled"] == []
    assert len(seen) == 1
    assert store.observations() == []


def test_custom_bundle_changes_bind_bytes_but_do_not_certify_actual_use(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    journal = ResearchJournal(store)
    source = tmp_path / "independent-custom"
    (source / "references").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / "SKILL.md").write_text("---\nname: independent-custom\ndescription: Fixture only\n---\nTry a partial method.\n")
    reference = source / "references" / "evidence.md"
    reference.write_text("Unknown scientific benefit.")
    script = source / "scripts" / "check.py"
    script.write_text("print('fixture')\n")
    ref = {"root": str(source)}
    saved = skills.persist_snapshot("independent-custom", tmp_path / "frozen-v1", bundle_ref=ref)
    old = skills.verify_snapshot(saved)
    version = MethodVersion("unpromoted", ("domain_skill",), saved,
                            verification_basis="content_verified")
    version_id = journal.record(version)
    before = canonical(store.export())
    assert journal.record(version) == version_id and canonical(store.export()) == before
    reference.write_text("Changed hypothesis only.")
    changed, _ = skills.snapshot("independent-custom", bundle_ref=ref)
    script.unlink()
    deleted, _ = skills.snapshot("independent-custom", bundle_ref=ref)
    assert len({old["manifest_sha256"], changed["manifest_sha256"], deleted["manifest_sha256"]}) == 3
    assert skills.verify_snapshot(saved) == old
    assert store.observations() == []
    use = host.record_method_use(journal,
        subject_refs=({"kind": "request", "id": request["request_id"], "scope": str(store.path)},),
        selected_methods=({"method_version_ref": version_id, "scope": "reference only"},),
        reason="try an unpromoted version", host_conditions={"model": None})
    assert journal.get(use)["basis"] == "host_reported"
    request["method_use_refs"] = [use]
    prepared = api.prepare_action(request)
    result = api.read_action_result(api.submit_action(request, workspace=tmp_path / "runtime",
                                                      plan_id=prepared["plan_id"]))
    assert result["execution_state"] == "completed" and result["method_use_refs"] == [use]
    assert len(calls) == 1
    # Tamper persisted bytes: claiming the same verified record must be rechecked.
    (Path(saved["manifest_path"]).parent / "bundle" / "references" / "evidence.md").write_text("tampered")
    with pytest.raises(ValueError):
        journal.record(version)
