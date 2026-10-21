"""Function migration checks: real cheap CPU screens and explicit persisted-state fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose
from etalon.campaign.pipeline import Pipeline
from etalon.campaign.sweep import read_sweep_status
from etalon.generate.productivity import Chunk, Productivity, allocate
from etalon.screening import _journal, _worker, plan_screen, screen_status


def digest_files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def cpu_config(path, minimum):
    path.write_text(json.dumps(compose("existing-batch-only", [
        {"id": "window", "title": "Explicit fixture MW window",
         "criteria": [component("window", "chemistry.rdkit_property_range_gate@0.1.0",
                               settings={"mw_min": minimum})]},
        {"id": "measure", "title": "CPU properties",
         "criteria": [component("properties", "features.rdkit_properties@0.1.0")]},
    ])), encoding="utf-8")
    return path


def test_existing_batch_can_stop_at_zero_hits_then_change_only_future_method(tmp_path):
    library = tmp_path / "existing.csv"
    library.write_text("id,smiles\nethanol,CCO\nbenzene,c1ccccc1\n", encoding="utf-8")
    screen = Screen(tmp_path / "workspace")
    rejected = screen.plan(cpu_config(tmp_path / "strict.json", 100), library)
    first = screen.run(rejected, run_id="strict-batch", workers=1, devices=("cpu",))
    assert first.terminal and first.outcome == "exhausted"
    assert first.status == "FAILED"  # native result retained, not silently rewritten
    assert any(s.artifact_id for s in first.stages)
    before = digest_files(screen.workspace)
    assert Screen.read_progress(screen.workspace, "strict-batch")["outcome"] == "exhausted"
    assert digest_files(screen.workspace) == before  # monitor/stop does not retry

    # The host may later choose a different method for a new batch; old receipts stay bound.
    relaxed = screen.plan(cpu_config(tmp_path / "relaxed.json", 0), library)
    assert relaxed.revision_id != rejected.revision_id
    second = screen.run(relaxed, run_id="relaxed-batch", workers=1, devices=("cpu",))
    assert second.terminal and second.outcome == "committed"
    assert Screen.read_state(screen.workspace, "strict-batch") == first
    for name, digest in before.items():
        if name.startswith(("runs/", "events/", "artifacts/", "revisions/")):
            assert hashlib.sha256((screen.workspace / name).read_bytes()).hexdigest() == digest
    stage = next(s for s in second.stages if s.stage_id == "properties")
    rows = screen.read(stage.artifact_id, contract_id="property/v1")
    assert sorted(r["mw"] for r in rows) == pytest.approx([46.069, 78.114], abs=.02)
    before_read = digest_files(screen.workspace)
    readonly = Screen.open_existing(screen.workspace)
    assert readonly.recall("relaxed-batch", panel_size=2)["registered"] == 2
    assert len(readonly.parent_names(second)) == 2
    assert readonly.artifacts_carrying(second, "property/v1")
    assert readonly.read(stage.artifact_id, contract_id="property/v1") == rows
    assert digest_files(screen.workspace) == before_read
    with pytest.raises(ValueError, match="read-only"):
        readonly.run(relaxed, run_id="forbidden")
    with pytest.raises(ValueError, match="read-only"):
        readonly.export_shortlist("relaxed-batch", tmp_path / "forbidden.sdf")
    assert not list(tmp_path.rglob("*generation*"))
    (tmp_path / "results.json").write_text(json.dumps({
        "strict": first.as_dict(), "relaxed": second.as_dict(),
        "cost_status": "not_metered", "scientific_scalar_for_zero_hits": None,
    }, indent=2), encoding="utf-8")


def test_real_zero_hit_worker_keeps_result_and_does_not_retry(tmp_path):
    """Real CPU work, with this process as the journaled worker; no POSIX launch claim."""
    library = tmp_path / "existing.csv"
    library.write_text("id,smiles\nethanol,CCO\n", encoding="utf-8")
    config = cpu_config(tmp_path / "strict.json", 100)
    workspace = tmp_path / "workspace"
    plan = plan_screen(config, library, workspace)
    request = {**plan["request"], "workers": 1, "devices": ["cpu"]}
    now = datetime.now(UTC).isoformat()
    with _journal(workspace, create=True) as db:
        db.execute("INSERT INTO screen_jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ("one", plan["plan_id"], json.dumps(request), "queued", os.getpid(),
                    now, now, None, None, str(workspace / "one.log")))
        db.commit()
    assert _worker(str(workspace), "one") == 0
    report = screen_status(workspace, "one")
    assert report["state"] == "succeeded" and report["result"]["outcome"] == "exhausted"
    assert report["result"]["status"] == "FAILED" and report["error"] is None
    assert report["cost_status"] == "not_metered"
    before = digest_files(workspace)
    assert _worker(str(workspace), "one") == 1
    assert digest_files(workspace) == before


def test_real_nonempty_input_failure_and_existing_run_refusal_are_not_zero_hits(tmp_path):
    library = tmp_path / "invalid.csv"
    library.write_text("wrong,columns\none,two\n", encoding="utf-8")
    config = cpu_config(tmp_path / "config.json", 0)
    screen = Screen(tmp_path / "workspace")
    plan = screen.plan(config, library)
    result = screen.run(plan, run_id="bad-input", workers=1, devices=("cpu",))
    assert result.terminal and result.outcome == "failed"
    assert any(stage.error for stage in result.stages)
    before = digest_files(screen.workspace)
    with pytest.raises(Exception, match="already exists"):
        screen.run(plan, run_id="bad-input", workers=1, devices=("cpu",))
    assert digest_files(screen.workspace) == before
    with pytest.raises(Exception, match="invalid run ID"):
        screen.run(plan, run_id="", workers=1, devices=("cpu",))


@pytest.mark.parametrize("code", ["RUN_ALREADY_EXISTS", "RUN_LOCKED", "RUN_LOCK_FAILED"])
def test_concurrent_refusal_cannot_adopt_another_writers_failed_result(tmp_path, monkeypatch, code):
    """An injected interleaving counterexample, not a claim to exercise OS locking."""
    from etalon.boundary.screen import ScreenResult, StageOutcome
    from molcascade.errors import ExecutionError
    from molcascade.runtime import LocalRunner

    library = tmp_path / "existing.csv"
    library.write_text("id,smiles\nethanol,CCO\n", encoding="utf-8")
    screen = Screen(tmp_path / "workspace")
    plan = screen.plan(cpu_config(tmp_path / "config.json", 0), library)
    foreign = ScreenResult("race", plan.revision_id, "FAILED", (
        StageOutcome("properties", "features.rdkit_properties@0.1.0", "FAILED", 1, None,
                     error={"code": "FEATURE_EMPTY_INPUT"}),))
    states = iter([None, foreign])
    monkeypatch.setattr(screen, "state", lambda run_id: next(states))

    def refused(*args, **kwargs):
        raise ExecutionError("this caller did not acquire execution ownership", code=code)

    monkeypatch.setattr(LocalRunner, "run", refused)
    with pytest.raises(ExecutionError) as caught:
        screen.run(plan, run_id="race")
    assert caught.value.code == code


def test_productivity_is_independent_advice_and_can_select_only_one_loop(tmp_path, monkeypatch):
    from etalon.campaign.supervisor import Supervisor

    def forbidden(*args, **kwargs):
        pytest.fail("a measurement/planning query dispatched a campaign")

    monkeypatch.setattr(Supervisor, "tick", forbidden)
    monkeypatch.setattr(Screen, "run", forbidden)
    productive = Productivity("chosen", "fixture", "box-a", (Chunk(1000, 900, 800, 60),) * 3)
    exhausted = Productivity("finished", "fixture", "box-b", (Chunk(1000, 900, 0, 60),) * 3)
    result = allocate([productive, exhausted])
    assert set(result["retire"]) == {"finished"}
    assert result["loops"][1]["uniqueness"]["cost_multiplier"] is None
    assert allocate([productive])["retire"] == {}
    assert len(allocate([productive])["loops"]) == 1
    # No generation measurements are needed to plan use of an existing library.
    plan = Pipeline(pool=1000, budget_gpu_hours=1, final_count=10,
                    stages=("lilly_demerits",)).dry_run()
    assert plan.generators is None and plan.as_dict()["generators"] is None
    assert not list(tmp_path.iterdir())


def old_pool(path, *, wal=False):
    db = sqlite3.connect(path)
    if wal:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
    db.executescript("""
        CREATE TABLE molecule(key TEXT PRIMARY KEY,smiles TEXT,source TEXT,seen_at REAL,batch TEXT);
        CREATE TABLE batch(batch_id TEXT PRIMARY KEY,size INTEGER,emitted_at TEXT,claimed_by TEXT,
                           claimed_at TEXT,run_id TEXT,revision_id TEXT,outcome TEXT,recorded_at TEXT);
        INSERT INTO molecule VALUES ('a','CCO','existing',1,'batch-a');
        INSERT INTO batch VALUES ('batch-a',1,'then','worker','then','run-a','rev-a',NULL,NULL);
    """)
    db.commit()
    return db


def logical_snapshot(db):
    return {
        "schema": db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall(),
        "molecules": db.execute("SELECT * FROM molecule ORDER BY key").fetchall(),
        "batches": db.execute("SELECT * FROM batch ORDER BY batch_id").fetchall(),
    }


@pytest.mark.parametrize("wal", [False, True])
def test_readonly_sweep_sees_pending_and_latest_wal_without_migration(tmp_path, wal):
    pool = tmp_path / "pool.sqlite"
    db = old_pool(pool, wal=wal)
    before = logical_snapshot(db)
    files_before = digest_files(tmp_path)
    report = read_sweep_status(pool)
    assert report["batches"] == {"emitted": 1, "pending": 0, "outstanding": 1,
                                  "recorded": 0, "by_outcome": {}}
    assert report["etalon_revisions"] == [] and report["gate"] is None
    assert logical_snapshot(db) == before
    # Actual SQLite read-lock bookkeeping may touch SHM, but no other evidence may change.
    after = digest_files(tmp_path)
    assert {k: v for k, v in after.items() if not k.endswith("-shm")} == {
        k: v for k, v in files_before.items() if not k.endswith("-shm")}
    if wal:
        db.execute("INSERT INTO molecule VALUES ('b','CC','existing',2,NULL)")
        db.commit()
        assert read_sweep_status(pool)["pool"]["unique"] == 2  # immutable=1 would miss this WAL
    db.close()


def test_missing_status_paths_never_create_workspaces_or_databases(tmp_path):
    for call in (
        lambda: read_sweep_status(tmp_path / "absent" / "pool.sqlite"),
        lambda: Screen.read_progress(tmp_path / "absent-screen", "run-a"),
        lambda: Screen.open_existing(tmp_path / "absent-calibration"),
        lambda: screen_status(tmp_path / "absent-journal", "run-a"),
    ):
        with pytest.raises(FileNotFoundError):
            call()
    assert not list(tmp_path.iterdir())


def test_existing_readonly_adapter_refuses_incomplete_artifact_layout_without_repair(tmp_path):
    readonly = Screen.open_existing(tmp_path)
    with pytest.raises(ValueError, match="not a real directory"):
        readonly.read("artifact:sha256:" + "a" * 64)
    assert not list(tmp_path.iterdir())


def test_readonly_status_rejects_unsupported_schema_without_repair(tmp_path):
    pool = tmp_path / "unknown.sqlite"
    with sqlite3.connect(pool) as db:
        db.execute("CREATE TABLE batch(batch_id TEXT)")
    before = digest_files(tmp_path)
    with pytest.raises(Exception, match="unsupported batch schema"):
        read_sweep_status(pool)
    assert digest_files(tmp_path) == before


def test_unknown_action_monitor_keeps_reservation_partial_cost_and_receipts(tmp_path):
    from etalon.active.schema import CampaignSpec, Candidate, Endpoint
    from etalon.active.store import CampaignStore

    store = CampaignStore(tmp_path / "campaign.sqlite")
    store.configure(CampaignSpec("score", 6, "fixture-cost", "fixture/1"), [
        Endpoint("score", "fixture", "score", "fixture-unit", "fixture/1", 2,
                 requires_handoff=False)])
    store.add_candidates([Candidate("one", "C", (1,))])
    round_id = store.start_round({"fixture": "persisted unknown state; no process executed"})
    action = store.reserve(round_id, "one", "score", {"fixture": True})
    store.start_action(action.id)
    store.record_execution_costs(action.id, [{"execution_id": "partial-fixture", "wall_seconds": .25}])
    before = store.export()
    files_before = digest_files(tmp_path)
    readonly = CampaignStore(store.path, read_only=True)
    readonly.status()
    assert readonly.balance()["reserved"] == 2
    assert readonly.balance()["spent"] == .25
    assert readonly.observations() == []
    assert readonly.export() == before and digest_files(tmp_path) == files_before


def test_progress_reads_running_fixture_without_dispatch_or_creating_control_paths(tmp_path):
    from molcascade.artifacts import canonical_json_bytes
    from molcascade.runtime.models import RunState, RunStatus

    now = datetime.now(UTC)
    state = RunState(run_id="pending", revision_id="a" * 64, status=RunStatus.RUNNING,
                     started_at=now, updated_at=now, stages=())
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "pending.json").write_bytes(canonical_json_bytes(state))
    before = digest_files(tmp_path)
    result = Screen.read_progress(tmp_path, "pending")
    assert result["status"] == "RUNNING" and not result["terminal"]
    assert digest_files(tmp_path) == before
    assert {p.name for p in tmp_path.iterdir()} == {"runs"}
    assert Screen.read_progress(tmp_path, "unknown") == {"run_id": "unknown", "exists": False}
    (runs / "pending.json").write_bytes(b"{broken")
    with pytest.raises(Exception, match="state is invalid"):
        Screen.read_progress(tmp_path, "pending")  # corruption is not absence or permission to retry
