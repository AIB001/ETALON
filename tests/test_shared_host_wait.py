"""Shared observations survive clients; cache reuse never runs science."""
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from etalon.runtime.api import read_action_result
from etalon.runtime.files import EvidenceError, _save
from etalon.runtime.host_wait import register_wait, read_wait, _condition


def specification(path, **updates):
    return dict(schema="etalon-host-wait/1", poll_seconds=0.1, max_checks=100,
                checks=[dict(kind="json_status", path=str(path), field="state",
                             running=["running"], ready=["done"], failed=["failed"])], **updates)


def terminal(handle):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = read_action_result(handle)
        if result["terminal"]:
            return result
        time.sleep(0.05)
    pytest.fail("observer did not finish")


def test_host_exit_reuse_policy_and_terminal_new_process(tmp_path):
    status = tmp_path / "status.json"
    _save(status, {"state": "running"})
    spec = specification(status)
    # No observing process needs to survive the registration client.
    script = ("import json; from etalon.runtime.host_wait import register_wait; "
              f"print(json.dumps(register_wait({spec!r}, {str(tmp_path)!r})))")
    process = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    registered = json.loads(process.stdout)
    handle = registered["handle"]
    folder = Path(handle["directory"])
    declaration = (folder / "declaration.json").read_bytes()
    again = register_wait(dict(spec, poll_seconds=2, max_checks=1), str(tmp_path))
    assert again["handle"] == handle
    assert again["policy"] == {"poll_seconds": .1, "max_checks": 100}
    assert (folder / "declaration.json").read_bytes() == declaration
    _save(status, {"state": "done"}, replace=True)
    result = terminal(handle)
    assert result["state"] == "ready" and not result["scientific_completion_inferred"]
    # Terminal snapshot remains stable even if the producer path changes later.
    _save(status, {"state": "running"}, replace=True)
    for _ in range(20):
        assert read_wait(handle) == result
    fresh = subprocess.run([sys.executable, "-c",
        "import json; from etalon.runtime.api import read_action_result; "
        f"print(json.dumps(read_action_result({handle!r})))"], capture_output=True, text=True, check=True)
    assert json.loads(fresh.stdout) == result
    assert register_wait(spec, str(tmp_path))["observation"] == result
    assert (folder / "declaration.json").read_bytes() == declaration
    assert not (folder / "observer").exists()


def test_cached_reads_do_not_poll_source(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    _save(status, {"state": "running"})
    spec = dict(specification(status), max_checks=1)
    handle = register_wait(spec, str(tmp_path))["handle"]
    result = terminal(handle)
    assert result["state"] == "wait_check_bound" and result["check_count"] == 1
    import etalon.runtime.host_wait as module
    monkeypatch.setattr(module, "_check", lambda _: pytest.fail("consumer polled source"))
    assert all(read_action_result(handle) == result for _ in range(100))
    import etalon.runtime.native as native
    monkeypatch.setattr(native, "launch_detached_attempt", lambda *a, **k: pytest.fail("relaunched observer"))
    assert register_wait(spec, str(tmp_path))["observation"] == result


def test_native_task_handle_is_consumed_without_reexecution(tmp_path):
    from etalon.runtime.native import launch_detached_attempt, inspect_attempt
    job = tmp_path / "science"
    output = tmp_path / "answer.txt"
    request = {"request_id": "fixture-once", "attempt_id": "first"}
    command = [sys.executable, "-c", "import pathlib,time; time.sleep(.3); "
               f"pathlib.Path({str(output)!r}).write_text('one execution')"]
    launch_detached_attempt(job, request, command, outputs={"answer": str(output)})
    original = (job / "intent.json").read_bytes()
    spec = dict(schema="etalon-host-wait/1", poll_seconds=.1, max_checks=100,
                checks=[dict(kind="native_attempt", path=str(job), **request)])
    handle = register_wait(spec, str(tmp_path))["handle"]
    assert terminal(handle)["state"] == "ready"
    finished = (job / "finished.json").read_bytes()
    assert register_wait(spec, str(tmp_path))["handle"] == handle
    assert inspect_attempt(job)["state"] == "finished-unpublished"
    assert (job / "intent.json").read_bytes() == original
    assert (job / "finished.json").read_bytes() == finished


def test_conditions_not_policy_define_identity(tmp_path):
    first = specification(tmp_path / "one.json")
    second = specification(tmp_path / "two.json")
    a = dict(first, checks=first["checks"] + second["checks"])
    b = dict(a, checks=list(reversed(a["checks"])), poll_seconds=2)
    assert _condition(a) == _condition(b)
    assert _condition(dict(a, wake_when="all")) != _condition(a)
    assert _condition(dict(first, checks=[dict(first["checks"][0], ready=["stage_done"])])) != _condition(first)
    n = dict(schema="etalon-host-wait/1", checks=[dict(kind="native_attempt",
        path=str(tmp_path), request_id="a", attempt_id="1")])
    assert _condition(dict(n, checks=[dict(n["checks"][0], attempt_id="2")])) != _condition(n)


def test_concurrent_subscribers_share_one_observation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    status = tmp_path / "status.json"
    _save(status, {"state": "done"})
    spec = specification(status)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: register_wait(spec, str(tmp_path)), range(4)))
    assert all(r["handle"] == results[0]["handle"] for r in results)
    assert terminal(results[0]["handle"])["check_count"] == 1
    assert len(list((tmp_path / "waits").glob("*/result.json"))) == 1


def test_running_reads_coalesce_until_next_interval(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    _save(status, {"state": "running"})
    handle = register_wait(dict(specification(status), poll_seconds=60), str(tmp_path))["handle"]
    import etalon.runtime.host_wait as module
    monkeypatch.setattr(module, "_check", lambda _: pytest.fail("polled within saved interval"))
    for _ in range(50):
        result = read_action_result(handle)
        assert result["check_count"] == 1 and not result["terminal"]


def test_failure_not_success(tmp_path):
    status = tmp_path / "status.json"
    _save(status, {"state": "failed"})
    handle = register_wait(specification(status), str(tmp_path))["handle"]
    assert terminal(handle)["state"] == "failed"
    with pytest.raises(EvidenceError, match="identify"):
        read_wait(dict(handle, condition_id="another-attempt"))


def test_repaired_unknown_uses_same_handle_and_check_budget(tmp_path):
    status = tmp_path / "status.json"
    _save(status, {"state": "unrecognized"})
    handle = register_wait(specification(status), str(tmp_path))["handle"]
    first = read_wait(handle)
    assert first["state"] == "unknown" and not first["terminal"]
    _save(status, {"state": "done"}, replace=True)
    result = terminal(handle)
    assert result["state"] == "ready" and result["check_count"] > first["check_count"]
