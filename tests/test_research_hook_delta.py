"""Delivery/recovery contracts; fixtures do not establish native host delivery."""
import copy
import json
from pathlib import Path

import pytest

from etalon import research_hooks as hooks


@pytest.fixture
def context(monkeypatch):
    ref = {"source_id": "fixture", "record_id": "result", "record_digest": "a" * 64}
    value = {"schema": "fixture/1", "context_id": "c1", "checkpoint_ref": ref,
        "checkpoint_progress": {"step_count": 17, "tail_step_refs": [ref]},
        "counterevidence": {"coverage_complete": False, "relationship_count": 2},
        "items": [{"ref": ref, "summary": "bounded evidence " * 180}],
        "reference_index": [ref], "source_watermarks": [{"source_id": "fixture", "status": "complete", "sequence": 1}],
        "continuation": {"authority_state": "paused", "pending": [{"request_id": "running-1"}]},
        "branches": [{"branch_id": "R4b", "state": "paused"}], "execution_permission": "not_granted",
        "automatic_retry": False, "publications": {"incomplete_count": 1, "operations": [{"status": "in_progress"}]}}
    monkeypatch.setattr(hooks, "_build_context", lambda *a, **kw: copy.deepcopy(value))
    return value


def call(tmp_path, event_id, name="PreToolUse", session="A", **kwargs):
    event = {"hook_event_name": name, "session_id": session, "event_id": event_id}
    if name == "PreToolUse":
        event.update(tool_name="mcp__etalon__research_context", tool_use_id=event_id, tool_input={})
    return hooks.handle_event(tmp_path / "entry.json", event, host="codex",
                              state_dir=tmp_path / "receipts", **kwargs)


def payload(result):
    return json.loads(result["host_output"]["hookSpecificOutput"]["additionalContext"].split("\n", 1)[1])


def test_unchanged_is_small_but_retains_controls_and_exact_recovery(tmp_path, context):
    first = call(tmp_path, "start", "SessionStart")
    second = call(tmp_path, "read")
    data = payload(second)
    assert data["delivery"]["mode"] == "unchanged"
    assert len(json.dumps(data)) < len(json.dumps(payload(first)))
    assert data["current_controls"]["continuation"]["pending"][0]["request_id"] == "running-1"
    assert data["current_controls"]["branches"][0]["state"] == "paused"
    assert data["current_controls"]["publications"]["incomplete_count"] == 1
    assert data["current_controls"]["checkpoint_progress"] == context["checkpoint_progress"]
    assert data["current_controls"]["counterevidence"] == context["counterevidence"]
    assert data["delivery"]["recovery"]["arguments"]["entry"] == str(tmp_path / "entry.json")
    assert data["delivery"]["recovery"]["arguments"]["view"] == "summary"
    assert data["delivery"]["base_receipt"] == first["receipt_path"]
    assert data["delivery"]["consumption_verified"] is False


def test_delta_reconstructs_change_and_deleted_keys_refs(tmp_path, context):
    removed_ref = {"source_id": "fixture", "record_id": "obsolete", "record_digest": "b" * 64}
    context["removed_example"] = removed_ref
    first = payload(call(tmp_path, "start", "SessionStart"))["context"]
    context.pop("removed_example")
    context["context_id"] = "c2"
    context["source_watermarks"][0]["sequence"] = 2
    context["continuation"]["pending"] = []
    data = payload(call(tmp_path, "changed"))
    assert data["delivery"]["mode"] == "delta"
    delta = data["context_delta"]
    reconstructed = {**first, **{key: data["current_controls"][key]
        for key in delta["set_from_current_controls"]}, **delta["set"]}
    for key in delta["remove"]:
        reconstructed.pop(key)
    assert reconstructed == context
    assert removed_ref in data["delivery"]["reference_removals"]
    assert data["current_controls"]["continuation"]["pending"] == []


@pytest.mark.parametrize("change", ["session", "policy", "conditions", "version", "clear", "compact"])
def test_scope_and_reset_require_self_contained_baseline(tmp_path, context, monkeypatch, change):
    call(tmp_path, "start", "SessionStart")
    args = {}
    name = "PreToolUse"
    if change == "session":
        args["session"] = "B"
    elif change == "policy":
        args["policy"] = {"purpose": "different"}
    elif change == "conditions":
        args["conditions"] = {"chemical_state": "changed"}
    elif change == "version":
        monkeypatch.setattr(hooks, "VERSION", "fixture-handler/next")
    elif change == "clear":
        name = "SessionStart"
    else:
        ack = call(tmp_path, "compact", "PreCompact")
        assert ack["receipt"]["checkpoint_ack"]["publications"]["incomplete_count"] == 1
    data = payload(call(tmp_path, "next", name, **args))
    assert data["delivery"]["mode"] == "baseline"
    assert data["delivery"]["replaces_previous_context"] is True
    assert data["context"] == context


def test_source_failure_and_incomplete_coverage_never_become_unchanged(tmp_path, context, monkeypatch):
    call(tmp_path, "start", "SessionStart")
    context["source_watermarks"][0]["status"] = "unavailable"
    one = payload(call(tmp_path, "unavailable-1"))
    two = payload(call(tmp_path, "unavailable-2"))
    assert one["delivery"]["reason"] == two["delivery"]["reason"] == "source_coverage_incomplete"
    context["source_watermarks"][0]["status"] = "complete"
    assert payload(call(tmp_path, "restored"))["delivery"]["mode"] == "baseline"
    def fail(*a, **kw):
        raise OSError("source offline")
    monkeypatch.setattr(hooks, "_build_context", fail)
    failed = call(tmp_path, "failed")
    assert not failed["ok"] and failed["host_output"] == {}
    monkeypatch.setattr(hooks, "_build_context", lambda *a, **kw: context)
    assert payload(call(tmp_path, "after-failure"))["delivery"]["mode"] == "baseline"


def test_strong_replay_does_not_reread_or_rewind_cursor(tmp_path, context, monkeypatch):
    start = call(tmp_path, "start", "SessionStart")
    context["context_id"] = "c2"
    changed = call(tmp_path, "changed")
    builder = hooks._build_context
    monkeypatch.setattr(hooks, "_build_context", lambda *a, **kw: pytest.fail("must replay"))
    assert call(tmp_path, "start", "SessionStart")["host_output"] == start["host_output"]
    with pytest.raises(ValueError, match="conflicts"):
        call(tmp_path, "start", "SessionStart", conditions={"new": "condition"})
    monkeypatch.setattr(hooks, "_build_context", builder)
    current = payload(call(tmp_path, "current"))
    assert current["delivery"]["mode"] == "unchanged"
    assert current["delivery"]["base_receipt"] == changed["receipt_path"]


@pytest.mark.parametrize("interrupted", ["receipt", "cursor"])
def test_interrupted_receipt_cursor_transaction_is_recoverable(tmp_path, context, monkeypatch, interrupted):
    real_save = hooks._save_cursor
    def crash(path, value):
        if (interrupted == "receipt" and path.suffix == ".json" or
                interrupted == "cursor" and path.name == "delivery-cursor"):
            raise OSError("simulated host delivery interruption")
        real_save(path, value)
    monkeypatch.setattr(hooks, "_save_cursor", crash)
    with pytest.raises(OSError, match="interruption"):
        call(tmp_path, "start", "SessionStart")
    monkeypatch.setattr(hooks, "_save_cursor", real_save)
    replay = call(tmp_path, "start", "SessionStart")
    assert replay["duplicate"] == (interrupted == "cursor")
    assert payload(replay)["delivery"]["mode"] == "baseline"
    after = payload(call(tmp_path, "next"))
    assert after["delivery"]["mode"] == "unchanged"
    assert not list((tmp_path / "receipts").rglob("delivery-pending"))


def test_incomplete_saved_baseline_is_not_used(tmp_path, context):
    first = call(tmp_path, "start", "SessionStart")
    cursor = Path(first["receipt_path"]).parent / "delivery-cursor"
    state = json.loads(cursor.read_text())
    state["usable"] = False
    cursor.write_text(json.dumps(state))
    assert payload(call(tmp_path, "next"))["delivery"]["mode"] == "baseline"


def test_missing_publication_manifest_is_not_reported_unchanged(tmp_path, context):
    context["publications"]["operations"][0]["status"] = "unavailable"
    call(tmp_path, "start", "SessionStart")
    assert payload(call(tmp_path, "next"))["delivery"]["reason"] == "source_coverage_incomplete"
    stopped = call(tmp_path, "stop", "Stop")
    assert stopped["receipt"]["checkpoint_ack"]["publication_attention_required"] is True
    assert stopped["receipt"]["scientific_writes"] == 0


@pytest.mark.parametrize("status", ["unknown", "not_inspected"])
def test_unknown_publication_index_requires_attention_and_never_unchanged(tmp_path, context, status):
    context["publications"] = {"status": status, "incomplete_count": None, "operations": []}
    call(tmp_path, "start", "SessionStart")
    for event_id in ("next", "again"):
        assert payload(call(tmp_path, event_id))["delivery"]["reason"] == "source_coverage_incomplete"
    stopped = call(tmp_path, "stop", "Stop")
    assert stopped["receipt"]["checkpoint_ack"]["publication_attention_required"] is True


def test_weak_tool_identity_after_compaction_does_not_reuse_old_delta(tmp_path, context):
    call(tmp_path, "start", "SessionStart")
    event = {"hook_event_name": "PreToolUse", "session_id": "A",
             "tool_name": "mcp__etalon__research_context", "tool_input": {}}
    def weak():
        return hooks.handle_event(tmp_path / "entry.json", event, host="codex",
                                  state_dir=tmp_path / "receipts")
    assert payload(weak())["delivery"]["mode"] == "unchanged"
    call(tmp_path, "compact", "PreCompact")
    after = weak()
    assert payload(after)["delivery"]["mode"] == "baseline"
    assert not after["duplicate"]


def test_repeated_identical_weak_compaction_invalidates_each_recovered_baseline(tmp_path, context):
    call(tmp_path, "start", "SessionStart")
    compact = {"hook_event_name": "PreCompact", "session_id": "A", "trigger": "auto"}
    def reset():
        return hooks.handle_event(tmp_path / "entry.json", compact, host="codex",
                                  state_dir=tmp_path / "receipts")
    first = reset()
    saved_bytes = Path(first["receipt_path"]).read_bytes()
    assert payload(call(tmp_path, "after-first"))["delivery"]["mode"] == "baseline"
    second = reset()
    assert second["duplicate"] and second["receipt_path"] == first["receipt_path"]
    assert Path(first["receipt_path"]).read_bytes() == saved_bytes
    assert payload(call(tmp_path, "after-second"))["delivery"]["mode"] == "baseline"


def test_replayed_strong_compaction_does_not_invalidate_later_baseline(tmp_path, context):
    call(tmp_path, "start", "SessionStart")
    call(tmp_path, "compact", "PreCompact")
    fresh = call(tmp_path, "fresh")
    assert payload(fresh)["delivery"]["mode"] == "baseline"
    assert call(tmp_path, "compact", "PreCompact")["duplicate"]
    after = payload(call(tmp_path, "next"))
    assert after["delivery"]["mode"] == "unchanged"
    assert after["delivery"]["base_receipt"] == fresh["receipt_path"]


def test_changed_source_watermark_produces_delta_without_semantic_result_change(tmp_path, context):
    call(tmp_path, "start", "SessionStart")
    context["source_watermarks"][0]["sequence"] += 1
    delta = payload(call(tmp_path, "changed"))
    assert delta["delivery"]["mode"] == "delta"
    assert "source_watermarks" in delta["context_delta"]["set_from_current_controls"]
    assert "source_watermarks" not in delta["context_delta"]["set"]
    assert delta["current_controls"]["source_watermarks"][0]["sequence"] == 2


def test_large_changed_controls_are_delivered_once_and_reconstruct_exactly(tmp_path, context):
    context["task_workset"] = {"digest": "same-scope", "detail": "conditions " * 180}
    context["source_watermarks"][0]["diagnostic"] = "source identity " * 180
    before = payload(call(tmp_path, "start", "SessionStart"))["context"]
    context["source_watermarks"][0]["sequence"] = 2
    context["checkpoint_ref"] = {**context["checkpoint_ref"], "record_id": "new-result"}
    context["context_id"] = "c2"
    data = payload(call(tmp_path, "changed"))
    assert data["delivery"]["mode"] == "delta"
    patch = data["context_delta"]
    assert not set(patch["set"]) & set(data["current_controls"])
    assert set(patch["set_from_current_controls"]) == {"checkpoint_ref", "source_watermarks"}
    restored = {**before, **{key: data["current_controls"][key]
        for key in patch["set_from_current_controls"]}, **patch["set"]}
    for key in patch["remove"]:
        restored.pop(key)
    assert restored == context
    assert hooks._digest(restored) == data["delivery"]["context_sha256"]
    assert data["current_controls"]["task_workset"] == context["task_workset"]
