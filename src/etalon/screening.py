"""Real bulk screening with durable job IDs and a detached local worker.

The LLM submits a reviewed plan once and polls its journal. Disconnecting an MCP
client does not restart a calculation. This is a local POSIX executor, not an HPC
scheduler; interrupted workers require investigation and are never auto-replayed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from etalon.active.cascade import _configuration_input_paths, _file_hash
from etalon.boundary.screen import Screen


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _identity(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _run_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise ValueError("run_id must contain 1–80 letters/digits/underscore/dot/hyphen and start with a letter or digit")
    return value


def _prepare(config: str | Path, library: str | Path | None, workspace: str | Path,
             *, allow_copyleft: bool = False, delivery: dict | None = None) -> tuple[dict[str, Any], Screen, Any]:
    if type(allow_copyleft) is not bool:
        raise ValueError("allow_copyleft must be a boolean")
    config_path = Path(config).expanduser().absolute()
    library_path = Path(library).expanduser().absolute() if library else None
    # Hash before AND after compile so a plan never approves a different config/library.
    paths = {config_path, *([library_path] if library_path and not library_path.is_dir() else [])}
    initial = {str(p): _file_hash(p) for p in paths}
    screen = Screen(workspace, allow_copyleft=allow_copyleft)
    plan = screen.plan(config_path, library_path)
    raw = plan._internal["pipeline"].model_dump(mode="json")
    paths |= _configuration_input_paths({"pipeline": raw})
    inputs = {str(p): _file_hash(p) for p in sorted(paths)}
    if any(inputs[p] != sha for p, sha in initial.items()):
        raise ValueError("screen inputs changed while compiling; make a new plan")
    request = {"config": str(config_path), "library": str(library_path) if library_path else None,
               "workspace": str(screen.workspace), "allow_copyleft": allow_copyleft,
               "revision_id": plan.revision_id, "inputs": inputs,
               "infrastructure": screen.infra.provenance()}
    if delivery is not None:
        from etalon.screening_delivery import delivery_options
        request["delivery"] = delivery_options(delivery, raw)
    report = {"plan_id": _identity(request), "plan": plan.as_dict(), "request": request,
              "cost": "not estimated or reserved; determined by the supplied cascade and library",
              "scope": "bulk screening only; no automatic MD/affinity feedback or active-campaign budget"}
    return report, screen, plan


def plan_screen(config: str | Path, library: str | Path | None, workspace: str | Path,
                *, allow_copyleft: bool = False, delivery: dict | None = None) -> dict[str, Any]:
    """Compile and check dependencies without executing any screening stage."""
    return _prepare(config, library, workspace, allow_copyleft=allow_copyleft, delivery=delivery)[0]


@contextmanager
def _journal(workspace: str | Path, *, create: bool = False):
    root = Path(workspace).expanduser().resolve()
    path = root / "etalon-screen-jobs.sqlite"
    if any(Path(str(path) + suffix).is_symlink() for suffix in ("", "-wal", "-shm", "-journal")):
        raise ValueError("screen journal and its sidecars must not be symlinks")
    if not create and not path.is_file():
        raise FileNotFoundError(f"no screen journal at {path}")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path if create else path.as_uri() + "?mode=rw", uri=not create, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        if create:
            db.execute("""CREATE TABLE IF NOT EXISTS screen_jobs (
                run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, request TEXT NOT NULL,
                state TEXT NOT NULL, pid INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL,
                result TEXT, error TEXT, log TEXT NOT NULL)""")
        yield db
    finally:
        db.close()


def screen_status(workspace: str | Path, run_id: str) -> dict[str, Any]:
    """Read persisted state; queued/running is the last recorded state, not a heartbeat."""
    _run_id(run_id)
    with _journal(workspace) as db:
        row = db.execute("SELECT * FROM screen_jobs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown screen run {run_id}")
    report = dict(row)
    for key in ("request", "result"):
        report[key] = json.loads(report[key]) if report[key] else None
    report["progress"] = Screen.read_progress(workspace, run_id)
    report["cost_status"] = "not_metered"
    report["state_semantics"] = "last journaled state; investigate logs after a host/worker crash; no automatic retry"
    # ``state`` has two values and a screen has three outcomes. A run in which every molecule was
    # gated out is ``succeeded`` here, because it finished and its artifacts are valid; whether it
    # committed a shortlist or found nothing left is ``result["outcome"]``, which is the field to
    # branch on. Saying so here because a reader who branches on ``state`` alone cannot tell a
    # shortlist from an exhausted funnel, and the two call for opposite next actions.
    report["outcome_semantics"] = (
        "state is terminal-or-not; result.outcome is the three-way answer "
        "(committed / exhausted / failed)"
    )
    return report


def submit_screen(config: str | Path, library: str | Path | None, workspace: str | Path, *,
                  run_id: str, expected_plan_id: str, workers: int = 1,
                  devices: tuple[str, ...] = (), allow_copyleft: bool = False, delivery: dict | None = None) -> dict[str, Any]:
    """Submit exactly this plan once. Repeating the same run id returns the same job.

    Changing inputs, plan, devices or worker count under the same ID is refused.
    No API accepts arbitrary commands, Python code, or implicit scientific defaults.
    """
    if os.name != "posix":
        raise ValueError("detached screening currently requires a POSIX host")
    _run_id(run_id)
    if type(workers) is not int or not 1 <= workers <= 256:
        raise ValueError("workers must be an integer between 1 and 256")
    if (isinstance(devices, str) or not isinstance(devices, (list, tuple))
            or any(not isinstance(d, str) or not re.fullmatch(r"cpu|cuda:\d+", d) for d in devices)
            or len(set(devices)) != len(devices)):
        raise ValueError("devices must be distinct cpu/cuda:N lane names")
    prepared, _, _ = _prepare(config, library, workspace, allow_copyleft=allow_copyleft, delivery=delivery)
    if expected_plan_id != prepared["plan_id"]:
        raise ValueError("screen plan changed; inspect a new plan before submitting")
    request = {**prepared["request"], "workers": workers, "devices": list(devices)}
    encoded = json.dumps(request, sort_keys=True, allow_nan=False)
    root = Path(request["workspace"])
    log_path = root / f"etalon-screen-{run_id}.log"
    with _journal(root, create=True) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT request, plan_id FROM screen_jobs WHERE run_id=?", (run_id,)).fetchone()
        if existing is not None:
            if existing["request"] != encoded or existing["plan_id"] != expected_plan_id:
                raise ValueError("run_id already belongs to different inputs or execution settings")
            db.commit()
        else:
            now = _now()
            db.execute("INSERT INTO screen_jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (run_id, expected_plan_id, encoded, "queued", None, now, now, None, None, str(log_path)))
            try:
                # Exclusive log creation prevents clobbering a prior run or following a symlink.
                with log_path.open("x", encoding="utf-8") as log:
                    child_env = dict(os.environ)
                    child_env["PYTHONPATH"] = os.pathsep.join(filter(None, (
                        str(Path(__file__).resolve().parents[1]), child_env.get("PYTHONPATH", ""))))
                    process = subprocess.Popen(
                        [sys.executable, "-m", "etalon.screening", str(root), run_id],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        start_new_session=True, cwd=root, env=child_env,
                    )
                db.execute("UPDATE screen_jobs SET pid=? WHERE run_id=?", (process.pid, run_id))
                # Child blocks on this transaction, and cannot claim an uncommitted job.
                db.commit()
                threading.Thread(target=process.wait, daemon=True).start()
            except Exception as error:
                db.execute("UPDATE screen_jobs SET state='failed', error=?, updated=? WHERE run_id=?",
                           (f"launch failed: {type(error).__name__}: {error}", _now(), run_id))
                db.commit()
                raise
    return screen_status(root, run_id)


def _worker(workspace: str, run_id: str) -> int:
    _run_id(run_id)
    # Only the process whose PID was recorded at submission may claim the job.
    with _journal(workspace) as db:
        db.execute("BEGIN IMMEDIATE")
        claimed = db.execute("UPDATE screen_jobs SET state='running', updated=? "
                             "WHERE run_id=? AND state='queued' AND pid=?", (_now(), run_id, os.getpid()))
        if claimed.rowcount != 1:
            return 1
        db.commit()
    state, result, error = "failed", None, None
    try:
        job = screen_status(workspace, run_id)
        request = job["request"]
        prepared, screen, plan = _prepare(request["config"], request["library"], workspace,
                                           allow_copyleft=request["allow_copyleft"],
                                           **({"delivery": request["delivery"]} if "delivery" in request else {}))
        if prepared["plan_id"] != job["plan_id"]:
            raise ValueError("screen inputs changed between submission and execution")
        run = screen.run(plan, run_id=run_id, workers=request["workers"], devices=request["devices"] or None)
        result = run.as_dict()
        # Preserve partial artifacts, but never declare a partial/failed run successful.
        #
        # Read through ``outcome``, not through ``status`` and ``failed``, because there are three
        # outcomes and this read them as two. MolCascade reports a run in which every molecule was
        # gated out as ``FAILED`` with a failed stage -- correctly, a stage did raise -- and
        # ``ScreenResult.outcome`` exists to classify exactly that case as ``"exhausted"`` rather
        # than ``"failed"``. This function held the ``ScreenResult`` the whole time and never asked
        # it: the condition above made every exhausted batch a failed job.
        #
        # The consequence was not cosmetic. An exhausted batch's docking scores are all committed
        # to the artifact store -- one measured batch held 7,545 of them -- so filing it as failed
        # invites a retry of work that already did what it was asked, and a campaign whose alarm
        # condition is "a batch failed" fires on a measurement. The job row carried ``failed``
        # while its own ``result`` payload carried ``"outcome": "exhausted"``, which is the two
        # halves of this function disagreeing in the same write.
        #
        # ``terminal`` is required as well, and not as decoration: ``outcome`` is derived from the
        # stages, so a run that stopped before any stage failed classifies as ``"committed"`` --
        # the same string a finished screen produces. See ``Sweep.record``, which refuses a
        # non-terminal result for the same reason.
        state = "succeeded" if run.terminal and run.outcome != "failed" else "failed"
        if state == "failed":
            error = (
                f"MolCascade's run record is {run.status}, which is not a terminal state; "
                "the screen did not finish"
                if not run.terminal
                else "MolCascade did not complete successfully; inspect result.stages and the log"
            )
        if any(_file_hash(Path(p)) != sha for p, sha in request["inputs"].items()):
            state, error = "failed", "screen inputs changed during execution; artifacts are not validated results"
        if request.get("delivery") is not None:
            from etalon.screening_delivery import export_screen_delivery
            output = Path(workspace) / "deliveries" / run_id
            try:
                exported = export_screen_delivery(workspace, run_id, output,
                    rmsd_threshold=request["delivery"]["rmsd_threshold"])
                result["delivery"] = {"state": "incomplete" if exported["source_coverage"]["unresolved_origins"] else "complete",
                                      "path": str(output / "delivery.json"),
                                      "reused": exported["reused"]}
            except Exception as delivery_error:
                # A delivery failure is not a failed computation or permission to
                # repeat it. The existing export endpoint can finish materialization.
                result["delivery"] = {"state": "failed", "path": str(output),
                                      "error": f"{type(delivery_error).__name__}: {delivery_error}",
                                      "next": "export committed artifacts; do not resubmit science"}
    except Exception as caught:
        error = f"{type(caught).__name__}: {caught}"
    with _journal(workspace) as db:
        db.execute("UPDATE screen_jobs SET state=?, updated=?, result=?, error=? WHERE run_id=? AND pid=?",
                   (state, _now(), json.dumps(result, allow_nan=False) if result else None,
                    error, run_id, os.getpid()))
        db.commit()
    return 0 if state == "succeeded" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Internal detached screen worker")
    parser.add_argument("workspace")
    parser.add_argument("run_id")
    args = parser.parse_args()
    raise SystemExit(_worker(args.workspace, args.run_id))
