"""Read declared completion signals without executing commands or choosing science.

An optional host turn's wait.json describes existing outputs/attempts. These
checks never launch, recover, retry or settle a job. Completion only wakes the
host; scientific meaning remains with the caller.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from .files import EvidenceError

SCHEMA = "etalon-host-wait/1"


def inspect_host_process(record):
    """Prove a recorded local host exited, or leave its outcome unknown.

    A failed identity read alone is not exit evidence (it may mean access was
    denied). This reader never signals a process and never inspects job children.
    A different creation identity proves the old PID occupant has exited.
    """
    from .native import process_identity

    saved = record.get("identity") if isinstance(record, dict) else None
    platform = "windows" if os.name == "nt" else "linux"
    keys = ("pid", "platform", "creation_filetime") if platform == "windows" else (
        "pid", "platform", "boot_id", "start_ticks")
    if (not isinstance(saved, dict) or saved.get("platform") != platform
            or type(saved.get("pid")) is not int or saved["pid"] < 1
            or record.get("pid") != saved["pid"] or any(saved.get(k) is None for k in keys)
            or type(saved.get("creation_filetime" if platform == "windows" else "start_ticks")) is not int
            or (platform == "linux" and (not isinstance(saved.get("boot_id"), str) or not saved["boot_id"]))):
        return {"state": "unknown", "basis": "missing_or_invalid_process_identity"}
    current = process_identity(saved["pid"])
    if current is not None:
        if any(current.get(key) != saved[key] for key in keys):
            return {"state": "exited", "basis": "different_creation_identity", "observed": current}
        if current.get("alive") is False:
            return {"state": "exited", "basis": "observed_terminated_identity", "observed": current}
        if current.get("alive") is True and os.name != "nt":
            return {"state": "running", "basis": "same_live_creation_identity", "observed": current}
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, saved["pid"])  # SYNCHRONIZE only
        if not handle:
            error = ctypes.get_last_error()
            # A nonzero nonexistent PID produces ERROR_INVALID_PARAMETER.
            return {"state": "exited" if error == 87 else "unknown",
                    "basis": "pid_absent" if error == 87 else "process_open_unavailable", "os_error": error}
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result == 258 and current is not None and current.get("alive") is True:
                return {"state": "running", "basis": "same_live_creation_identity", "observed": current}
            # Waiting also distinguishes an exited Windows process whose actual
            # return code happens to equal STILL_ACTIVE (259).
            return {"state": "exited" if result == 0 else "unknown",
                    "basis": "signaled_process_handle" if result == 0 else "process_identity_unavailable"}
        finally:
            kernel.CloseHandle(handle)
    # A missing /proc mount is a failed observation, not an absent host.
    try:
        (Path("/proc") / "self" / "stat").read_text()
    except OSError as exc:
        return {"state": "unknown", "basis": "proc_unavailable", "os_error": exc.errno}
    try:
        (Path("/proc") / str(saved["pid"])).stat()
    except FileNotFoundError:
        return {"state": "exited", "basis": "pid_absent"}
    except OSError as exc:
        return {"state": "unknown", "basis": "process_stat_unavailable", "os_error": exc.errno}
    return {"state": "unknown", "basis": "process_identity_unavailable"}


def validate_wait(body):
    if not isinstance(body, dict) or body.get("schema") != SCHEMA:
        raise EvidenceError("host wait declaration schema required")
    checks = body.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= 16:
        raise EvidenceError("wait requires 1..16 declared read-only checks")
    interval = body.get("poll_seconds", 5)
    if type(interval) not in (int, float) or not 0.05 <= interval <= 3600:
        raise EvidenceError("wait poll_seconds must be 0.05..3600")
    if type(body.get("max_checks", 1200)) is not int or not 1 <= body.get("max_checks", 1200) <= 100000:
        raise EvidenceError("wait max_checks must be 1..100000")
    if body.get("wake_when", "any") not in {"any", "all"}:
        raise EvidenceError("wait wake_when must be any or all")
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") not in {"native_attempt", "file", "json_status"}:
            raise EvidenceError("unsupported read-only wait check")
        if not isinstance(check.get("path"), str) or not Path(check["path"]).is_absolute():
            raise EvidenceError("wait check requires an absolute existing handle/output path")
        if check["kind"] == "native_attempt":
            if any(not isinstance(check.get(k), str) or not check[k] for k in ("request_id", "attempt_id")):
                raise EvidenceError("native wait requires exact request_id and attempt_id")
        if check["kind"] == "json_status":
            if not isinstance(check.get("field"), str) or not check["field"]:
                raise EvidenceError("JSON wait requires an explicit top-level status field")
            states = [check.get(k, []) for k in ("running", "ready", "failed")]
            if any(not isinstance(values, list) or any(not isinstance(v, str) for v in values) for values in states):
                raise EvidenceError("JSON wait requires declared string status values")
            if not states[1] or len(set(sum(states, []))) != len(sum(states, [])):
                raise EvidenceError("JSON wait states must be disjoint with a ready value")
    return body


def _check(check):
    path = Path(check["path"])
    if check["kind"] == "native_attempt":
        from etalon.runtime.native import inspect_attempt
        status = inspect_attempt(path)
        if any(status[k] != check[k] for k in ("request_id", "attempt_id")):
            raise EvidenceError("native wait handle belongs to a different attempt")
        state = status["state"]
        if state in {"finished-unpublished", "published"}:
            finished = status.get("finished", status.get("receipt", {}).get("finished", {}))
            return {"state": "ready" if finished.get("returncode") == 0 and not finished.get("missing_outputs") else "failed",
                    "native_state": state, "returncode": finished.get("returncode"),
                    "request_id": check["request_id"], "attempt_id": check["attempt_id"]}
        return {"state": "running" if state == "running" else "unknown", "native_state": state}
    if not path.exists():
        return {"state": "running" if check.get("missing_is_running", True) else "unknown", "exists": False}
    if not path.is_file():
        raise EvidenceError("wait output is not a regular file")
    if check["kind"] == "file":
        stat = path.stat()
        return {"state": "ready", "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                "verification": "completion_marker_presence_only"}
    if path.stat().st_size > 1024 * 1024:
        raise EvidenceError("JSON wait status exceeds 1 MiB")
    body = json.loads(path.read_text(encoding="utf-8"))
    observed = body.get(check["field"]) if isinstance(body, dict) else None
    for state in ("running", "ready", "failed"):
        if observed in check.get(state, []):
            return {"state": state, "observed_status": observed}
    return {"state": "unknown", "observed_status": str(observed)[:200]}


def inspect_wait(body):
    """Return a bounded observation. A read error is never reported as running."""
    start = time.perf_counter()
    validate_wait(body)
    results = []
    for check in body["checks"]:
        try:
            observed = _check(check)
        except (OSError, ValueError, KeyError, EvidenceError) as exc:
            observed = {"state": "check_error", "error": str(exc)[:500]}
        results.append({"kind": check["kind"], "path": check["path"], **observed})
    states = [item["state"] for item in results]
    state = "running"
    for exceptional in ("check_error", "unknown", "failed"):
        if exceptional in states:
            state = exceptional
            break
    else:
        if (all(s == "ready" for s in states) if body.get("wake_when", "any") == "all" else "ready" in states):
            state = "ready"
    return {"schema": "etalon-host-wait-observation/1", "state": state, "checks": results,
            "read_wall_seconds": time.perf_counter() - start, "scientific_completion_inferred": False}


def _condition(body):
    """Policy changes do not create a second observer of the same condition."""
    validate_wait(body)
    checks = []
    for original in body["checks"]:
        check = dict(original, path=os.path.normcase(os.path.abspath(original["path"])))
        if check["kind"] != "native_attempt":
            check.setdefault("missing_is_running", True)
        if check["kind"] == "json_status":
            for name in ("running", "ready", "failed"):
                check[name] = sorted(check.get(name, []))
        checks.append(check)
    return {"schema": SCHEMA, "wake_when": body.get("wake_when", "any"),
            "checks": sorted(checks, key=lambda item: json.dumps(item, sort_keys=True))}


def register_wait(body, workspace):
    """Share bounded observations of the existing task; no extra worker.

    Prefer a host's existing completion notification when available. Otherwise
    save this handle in the task entry; subscribers read it, not the source job.
    The first registration's policy wins. Re-registration never resets an
    exhausted observation budget or starts/restarts the scientific task.
    """
    import hashlib
    from .files import _read, _save

    root = Path(workspace)
    if not root.is_absolute():
        raise EvidenceError("wait workspace must be absolute")
    condition = _condition(body)
    key = hashlib.sha256(json.dumps(condition, sort_keys=True).encode()).hexdigest()
    folder = root / "waits" / key
    folder.mkdir(parents=True, exist_ok=True)
    handle = {"schema": "etalon-wait-handle/1", "directory": str(folder), "condition_id": key}
    with _wait_lock(folder):
        declaration = folder / "declaration.json"
        if declaration.exists():
            saved = _read(declaration)
            if _condition(saved) != condition:
                raise EvidenceError("saved wait condition changed")
        else:
            # Normalize for deduplication only. Native receipts deliberately
            # preserve the producer's exact path spelling (including Windows case).
            saved = dict(body, wake_when=body.get("wake_when", "any"), poll_seconds=body.get("poll_seconds", 5),
                         max_checks=body.get("max_checks", 1200))
            _save(declaration, saved)
    return {"handle": handle, "policy": {k: saved[k] for k in ("poll_seconds", "max_checks")},
            "observation": read_wait(handle)}


def read_wait(handle):
    """Refresh once when due, shared by all readers. Never dispatch any process."""
    import hashlib
    from .files import _read, _save

    if not isinstance(handle, dict) or handle.get("schema") != "etalon-wait-handle/1":
        raise EvidenceError("saved wait handle required")
    folder = Path(handle["directory"])
    if not folder.is_absolute():
        raise EvidenceError("wait handle directory must be absolute")
    saved = _read(folder / "declaration.json")
    key = hashlib.sha256(json.dumps(_condition(saved), sort_keys=True).encode()).hexdigest()
    if key != handle["condition_id"] or folder.name != key:
        raise EvidenceError("wait handle does not identify its saved condition")
    terminal, snapshot = folder / "result.json", folder / "observation.json"
    with _wait_lock(folder):
        if terminal.exists():
            return dict(_read(terminal), handle=handle, terminal=True)
        previous = _read(snapshot) if snapshot.exists() else {}
        now = time.time()
        if previous and 0 <= now - previous["checked_at_unix"] < saved["poll_seconds"]:
            return dict(previous, handle=handle, terminal=False)
        observed = inspect_wait(saved)
        observed.update(check_count=previous.get("check_count", 0) + 1,
            checked_at_unix=now,
            total_read_seconds=previous.get("total_read_seconds", 0) + observed["read_wall_seconds"])
        # An observation error can be repaired without changing the scientific
        # attempt. It consumes the same finite read budget, not a new task.
        if observed["state"] not in {"ready", "failed"} and observed["check_count"] >= saved["max_checks"]:
            observed.update(last_observed_state=observed["state"], state="wait_check_bound")
        _save(snapshot, observed, replace=True)
        done = observed["state"] in {"ready", "failed", "wait_check_bound"}
        if done:
            _save(terminal, observed)
        return dict(observed, handle=handle, terminal=done)


def _wait_lock(folder):
    """Serialize short observations across clients; not a job polling loop."""
    import errno
    from contextlib import ExitStack
    from .files import _consumer_lock

    locks = ExitStack()
    deadline = time.monotonic() + 5
    while True:
        try:
            locks.enter_context(_consumer_lock(folder / "registration"))
            return locks
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN} or time.monotonic() >= deadline:
                locks.close()
                raise
            time.sleep(.05)
