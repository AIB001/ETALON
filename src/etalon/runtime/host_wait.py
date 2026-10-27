"""Read declared completion signals without executing commands or choosing science.

An optional host turn's wait.json describes existing outputs/attempts. These
checks never launch, recover, retry or settle a job. Completion only wakes the
host; scientific meaning remains with the caller.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from .files import EvidenceError

SCHEMA = "etalon-host-wait/1"


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
