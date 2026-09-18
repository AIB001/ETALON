"""Legacy JSONL branch replay must restore a real, unambiguous historical state."""

from __future__ import annotations

import multiprocessing
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from etalon.campaign.ledger import Entry, Ledger


def _process_writer_holding_lock(path, ready, release, outcome):
    """Spawn-safe worker: pause inside append's exclusive critical section."""
    ledger = Ledger(path)
    original = ledger._entries_unlocked

    def paused_read():
        ready.set()
        if not release.wait(10):
            raise RuntimeError("test worker was never released")
        return original()

    ledger._entries_unlocked = paused_read
    try:
        ledger.append("round", "same", change={"value": 1}, decision={"accepted": True})
        outcome.put("written")
    except Exception as error:
        outcome.put(f"{type(error).__name__}: {error}")


def append(ledger, name, value):
    ledger.append("round", name, change={"value": value}, decision={"accepted": True})


def test_rewind_can_restore_an_abandoned_branch_exactly(tmp_path):
    ledger = Ledger(tmp_path / "history.jsonl")
    for name, value in (("r1", 1), ("r2", 2), ("r3", 3)):
        append(ledger, name, value)
    ledger.rewind("r1", reason="abandon the first unvalidated branch", by="reviewer")
    append(ledger, "r4", 4)
    assert ledger.replay()["rounds_live"] == ["r1", "r4"]
    ledger.rewind("r3", reason="restore the original validated branch", by="reviewer")
    assert ledger.replay() == {
        "rounds_live": ["r1", "r2", "r3"], "rounds_abandoned": ["r4"],
        "changes_accepted_in": ["r1", "r2", "r3"], "changes_refused_in": [], "parameters": {"value": 3},
    }
    append(ledger, "r5", 5)
    ledger.rewind("r4", reason="review and return to the second branch", by="reviewer")
    assert ledger.replay()["rounds_live"] == ["r1", "r4"]
    assert ledger.replay()["parameters"] == {"value": 4}
    assert len(ledger.rounds()) == 5  # No historical work was erased.


def test_unfinished_measurements_are_not_a_rewind_target(tmp_path):
    ledger = Ledger(tmp_path / "history.jsonl")
    ledger.append("measurements", "partial", measurements=[])
    before = ledger.path.read_bytes()
    with pytest.raises(KeyError):
        ledger.rewind("partial", reason="this round was never actually finished", by="reviewer")
    assert ledger.path.read_bytes() == before


def test_duplicate_completed_round_cannot_ambiguate_future_rewind(tmp_path):
    ledger = Ledger(tmp_path / "history.jsonl")
    append(ledger, "r1", 1)
    before = ledger.path.read_bytes()
    with pytest.raises(ValueError, match="unique"):
        append(ledger, "r1", 2)
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("payload", [{"at": "forged"}, {"value": float("nan")}, {"value": float("inf")}])
def test_invalid_payload_never_appends_a_line(tmp_path, payload):
    ledger = Ledger(tmp_path / "history.jsonl")
    with pytest.raises(ValueError):
        ledger.append("round", "r1", **payload)
    assert not ledger.path.exists()


def test_entry_payload_cannot_override_identity_metadata():
    entry = Entry("round", "r1", "real-time", {"kind": "rewind", "round_id": "r2", "at": "forged"})
    assert entry.as_dict() == {"kind": "round", "round_id": "r1", "at": "real-time"}


@pytest.mark.parametrize("text", ["[]", '{"kind": null, "round_id": "r1"}',
                                 '{"kind": "round", "round_id": "r1", "value": NaN}'])
def test_corrupt_legacy_rows_have_a_line_number_and_are_not_skipped(tmp_path, text):
    ledger = Ledger(tmp_path / "history.jsonl")
    ledger.path.write_text(text + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="history.jsonl:1"):
        ledger.entries()


def test_forged_rewind_target_fails_in_replay_instead_of_noop(tmp_path):
    ledger = Ledger(tmp_path / "history.jsonl")
    append(ledger, "r1", 1)
    ledger.append("rewind", "invalid-event", target="absent")
    with pytest.raises(ValueError, match="preceding completed round"):
        ledger.replay()


def test_rewind_requires_an_attributed_request(tmp_path):
    ledger = Ledger(tmp_path / "history.jsonl")
    append(ledger, "r1", 1)
    with pytest.raises(ValueError, match="name who"):
        ledger.rewind("r1", reason="the caller must be attributable here", by="   ")


@pytest.mark.parametrize("accepted", ["false", "true", 0, 1, None, [], {}])
def test_round_acceptance_flag_is_never_interpreted_by_truthiness(tmp_path, accepted):
    ledger = Ledger(tmp_path / "history.jsonl")
    with pytest.raises(ValueError, match="explicit boolean"):
        ledger.append("round", "r1", change={"value": 9}, decision={"accepted": accepted})
    assert not ledger.path.exists()


@pytest.mark.parametrize("decision", [False, [], "accepted"])
def test_round_decision_must_be_a_structured_object(tmp_path, decision):
    ledger = Ledger(tmp_path / "history.jsonl")
    with pytest.raises(ValueError, match="object or null"):
        ledger.append("round", "r1", change={"value": 9}, decision=decision)
    assert not ledger.path.exists()


def test_bad_historical_acceptance_flag_fails_with_line_number(tmp_path):
    ledger = Ledger(tmp_path / "history.jsonl")
    append(ledger, "r1", 1)
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"round","round_id":"r2","decision":{"accepted":"false"},'
                     '"change":{"value":9}}\n')
    with pytest.raises(ValueError, match="history.jsonl:2.*explicit boolean"):
        ledger.replay()


def test_unknown_event_kinds_and_missing_decisions_remain_valid(tmp_path):
    ledger = Ledger(tmp_path / "history.jsonl")
    ledger.append("future-event", "event1", decision={"accepted": "external-domain-status"})
    ledger.append("round", "r1", decision=None)
    ledger.append("round", "r2")
    ledger.append("round", "r3", change={"value": 9}, decision={"accepted": False})
    assert len(ledger.entries()) == 4
    assert ledger.replay()["parameters"] == {}


def test_thread_writer_holds_one_lock_across_check_and_append(tmp_path, monkeypatch):
    pytest.importorskip("fcntl")
    ledger = Ledger(tmp_path / "history.jsonl")
    contender = Ledger(ledger.path)
    ready, release = Event(), Event()
    original = ledger._entries_unlocked

    def paused_read():
        ready.set()
        if not release.wait(10):
            raise RuntimeError("test worker was never released")
        return original()

    monkeypatch.setattr(ledger, "_entries_unlocked", paused_read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(append, ledger, "same", 1)
        try:
            assert ready.wait(5)
            with pytest.raises(BlockingIOError, match="ledger is busy"):
                append(contender, "same", 2)
            with pytest.raises(BlockingIOError, match="ledger is busy"):
                contender.entries()
        finally:
            release.set()
        pending.result(timeout=5)
    assert contender.replay()["parameters"] == {"value": 1}
    with pytest.raises(ValueError, match="unique"):
        append(contender, "same", 2)
    assert len(contender.rounds()) == 1


def test_cross_process_writer_cannot_append_a_competing_duplicate(tmp_path):
    pytest.importorskip("fcntl")
    context = multiprocessing.get_context("spawn")
    ready, release, outcome = context.Event(), context.Event(), context.Queue()
    ledger = Ledger(tmp_path / "history.jsonl")
    process = context.Process(target=_process_writer_holding_lock,
                              args=(str(ledger.path), ready, release, outcome))
    process.start()
    try:
        assert ready.wait(8)
        with pytest.raises(BlockingIOError, match="ledger is busy"):
            append(ledger, "same", 2)
        with pytest.raises(BlockingIOError, match="ledger is busy"):
            ledger.entries()
    finally:
        release.set()
        process.join(timeout=8)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0
    assert outcome.get(timeout=2) == "written"
    outcome.close()
    assert ledger.replay()["parameters"] == {"value": 1}
    with pytest.raises(ValueError, match="unique"):
        append(ledger, "same", 2)


def test_shared_readers_coexist_but_exclude_a_writer(tmp_path):
    pytest.importorskip("fcntl")
    ledger = Ledger(tmp_path / "history.jsonl")
    other = Ledger(ledger.path)
    append(ledger, "r1", 1)
    with ledger._locked(exclusive=False):
        assert len(other.entries()) == 1
        with pytest.raises(BlockingIOError, match="ledger is busy"):
            append(other, "r2", 2)
    append(other, "r2", 2)
    assert ledger.replay()["parameters"] == {"value": 2}


def test_unsupported_file_locking_fails_before_a_ledger_line_is_written(tmp_path, monkeypatch):
    import builtins

    ledger = Ledger(tmp_path / "history.jsonl")
    original = builtins.__import__

    def no_fcntl(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("unsupported platform")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_fcntl)
    with pytest.raises(OSError, match="requires POSIX flock"):
        append(ledger, "r1", 1)
    with pytest.raises(OSError, match="requires POSIX flock"):
        ledger.entries()
    assert not ledger.path.exists()


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("operation", ["read", "append"])
def test_sidecar_symlink_is_refused_without_creating_or_changing_its_target(tmp_path, exists, operation):
    pytest.importorskip("fcntl")
    ledger = Ledger(tmp_path / "history.jsonl")
    target = tmp_path / "unrelated-target"
    if exists:
        target.write_bytes(b"preserve unrelated bytes")
    lock_path = ledger.path.with_name(f".{ledger.path.name}.lock")
    lock_path.symlink_to(target)
    with pytest.raises(OSError):
        if operation == "read":
            ledger.entries()
        else:
            append(ledger, "r1", 1)
    assert lock_path.is_symlink()
    assert target.read_bytes() == b"preserve unrelated bytes" if exists else not target.exists()
    assert not ledger.path.exists()


@pytest.mark.parametrize("kind", ["directory", "fifo"])
@pytest.mark.parametrize("operation", ["read", "append"])
def test_nonregular_sidecar_is_refused_before_ledger_access(tmp_path, monkeypatch, kind, operation):
    pytest.importorskip("fcntl")
    ledger = Ledger(tmp_path / "history.jsonl")
    lock_path = ledger.path.with_name(f".{ledger.path.name}.lock")
    if kind == "directory":
        lock_path.mkdir()
    else:
        os.mkfifo(lock_path)
    original_mode = lock_path.stat().st_mode
    monkeypatch.setattr(ledger, "_entries_unlocked", lambda: pytest.fail("accessed ledger behind invalid lock"))
    with pytest.raises(OSError):
        if operation == "read":
            ledger.entries()
        else:
            append(ledger, "r1", 1)
    assert stat.S_IFMT(lock_path.stat().st_mode) == stat.S_IFMT(original_mode)
    assert not ledger.path.exists()


@pytest.mark.parametrize("operation", ["read", "append"])
def test_missing_nofollow_support_fails_closed_before_opening_lock(tmp_path, monkeypatch, operation):
    pytest.importorskip("fcntl")
    ledger = Ledger(tmp_path / "history.jsonl")
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(OSError, match="requires O_NOFOLLOW"):
        if operation == "read":
            ledger.entries()
        else:
            append(ledger, "r1", 1)
    assert list(tmp_path.iterdir()) == []


def test_ledger_symlink_alias_still_uses_the_same_resolved_sidecar(tmp_path):
    pytest.importorskip("fcntl")
    original = Ledger(tmp_path / "history.jsonl")
    append(original, "r1", 1)
    alias_path = tmp_path / "alias.jsonl"
    alias_path.symlink_to(original.path)
    alias = Ledger(alias_path)
    assert alias.path == original.path
    with original._locked(exclusive=True):
        with pytest.raises(BlockingIOError, match="ledger is busy"):
            alias.entries()
        with pytest.raises(BlockingIOError, match="ledger is busy"):
            append(alias, "r2", 2)
    append(alias, "r2", 2)
    assert original.replay()["rounds_live"] == ["r1", "r2"]
    assert not (tmp_path / ".alias.jsonl.lock").exists()
