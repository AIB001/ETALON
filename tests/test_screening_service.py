"""Real detached CPU screening and idempotent submissions, not an oracle replay."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose
from etalon.screening import _worker, plan_screen, screen_status, submit_screen


@pytest.fixture
def prepared(tmp_path):
    config = tmp_path / "cascade.json"
    config.write_text(json.dumps(compose("cpu-smoke", [{"id": "properties", "title": "Measured properties",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}])), encoding="utf-8")
    library = tmp_path / "library.csv"
    library.write_text("id,smiles\nethanol,CCO\nbenzene,c1ccccc1\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    plan = plan_screen(config, library, workspace)
    return config, library, workspace, plan


def wait_for(workspace, run_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = screen_status(workspace, run_id)
        if job["state"] in {"failed", "succeeded"}:
            return job
        time.sleep(0.1)
    pytest.fail(f"worker did not finish: {job}")


def test_detached_screen_survives_submitting_client_exit_and_returns_real_artifacts(prepared):
    config, library, workspace, plan = prepared
    result = subprocess.run([sys.executable, "-m", "etalon", "screen", "submit", "--config", str(config),
        "--library", str(library), "--workspace", str(workspace), "--run-id", "live",
        "--plan-id", plan["plan_id"]], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    initial = json.loads(result.stdout)
    job = wait_for(workspace, "live")
    assert job["state"] == "succeeded", job
    assert job["pid"] == initial["pid"]
    properties = next(s for s in job["result"]["stages"] if s["stage_id"] == "properties")
    rows = Screen(workspace).read(properties["artifact_id"], contract_id="property/v1")
    assert len(rows) == 2
    assert sorted(r["mw"] for r in rows) == pytest.approx([46.069, 78.114], abs=0.02)
    repeat = submit_screen(config, library, workspace, run_id="live", expected_plan_id=plan["plan_id"])
    assert repeat["pid"] == initial["pid"] and repeat["state"] == "succeeded"


def test_concurrent_duplicate_submissions_share_one_worker(prepared):
    config, library, workspace, plan = prepared
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit_screen, config, library, workspace, run_id="one",
                               expected_plan_id=plan["plan_id"]) for _ in range(2)]
        jobs = [future.result() for future in futures]
    assert jobs[0]["pid"] == jobs[1]["pid"]
    assert wait_for(workspace, "one")["state"] == "succeeded"
    with pytest.raises(ValueError, match="different inputs or execution"):
        submit_screen(config, library, workspace, run_id="one", expected_plan_id=plan["plan_id"], workers=2)


def test_changed_library_invalidates_plan_before_a_worker_exists(prepared):
    config, library, workspace, plan = prepared
    library.write_text("id,smiles\nx,CCN\n", encoding="utf-8")
    with pytest.raises(ValueError, match="plan changed"):
        submit_screen(config, library, workspace, run_id="stale", expected_plan_id=plan["plan_id"])
    assert not (workspace / "etalon-screen-jobs.sqlite").exists()


def test_status_does_not_create_unknown_workspace(tmp_path):
    with pytest.raises(FileNotFoundError):
        screen_status(tmp_path / "missing", "missing")
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("run_id", ["../escape", ".", "", "a/b", "a\\b", "a" * 81])
def test_invalid_run_id_cannot_touch_a_workspace(tmp_path, run_id):
    with pytest.raises(ValueError, match="run_id"):
        submit_screen("missing", None, tmp_path / "not-created", run_id=run_id, expected_plan_id="none")
    assert not (tmp_path / "not-created").exists()


def test_worker_cannot_reclaim_a_completed_job(prepared):
    config, library, workspace, plan = prepared
    submit_screen(config, library, workspace, run_id="complete", expected_plan_id=plan["plan_id"])
    initial = wait_for(workspace, "complete")
    assert _worker(str(workspace), "complete") == 1
    assert screen_status(workspace, "complete") == initial


def test_launch_failure_is_durable_and_not_implicitly_retried(prepared, monkeypatch):
    config, library, workspace, plan = prepared

    def fail(*args, **kwargs):
        raise OSError("worker unavailable")

    monkeypatch.setattr("etalon.screening.subprocess.Popen", fail)
    with pytest.raises(OSError, match="unavailable"):
        submit_screen(config, library, workspace, run_id="failed", expected_plan_id=plan["plan_id"])
    assert screen_status(workspace, "failed")["state"] == "failed"
    repeat = submit_screen(config, library, workspace, run_id="failed", expected_plan_id=plan["plan_id"])
    assert "launch failed" in repeat["error"]
    with sqlite3.connect(workspace / "etalon-screen-jobs.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM screen_jobs").fetchone()[0] == 1


def test_actual_mcp_stdio_session_can_plan_submit_poll_and_read_workflow(prepared):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    config, library, workspace, _ = prepared

    async def roundtrip():
        server = StdioServerParameters(command=sys.executable, args=["-m", "etalon.mcp"],
                                        env={**os.environ, "PYTHONUNBUFFERED": "1"})
        async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            catalogue = await session.list_tools()
            assert "etalon_screen_submit" in {t.name for t in catalogue.tools}
            resource = await session.read_resource("etalon://skills/campaign")
            assert resource.contents
            args = {"config_path": str(config), "library_path": str(library), "workspace": str(workspace)}
            planned = await session.call_tool("etalon_screen_plan", args)
            plan = json.loads(planned.content[0].text)
            assert plan["ok"], plan
            submitted = await session.call_tool("etalon_screen_submit", {
                **args, "run_id": "mcp", "plan_id": plan["plan_id"]})
            assert json.loads(submitted.content[0].text)["ok"]
            for _ in range(120):
                status = await session.call_tool("etalon_screen_status", {"workspace": str(workspace), "run_id": "mcp"})
                job = json.loads(status.content[0].text)["job"]
                if job["state"] in {"failed", "succeeded"}:
                    assert job["state"] == "succeeded", job
                    return
                await asyncio.sleep(0.1)
            pytest.fail("MCP screen did not finish within the test limit")

    asyncio.run(asyncio.wait_for(roundtrip(), timeout=60))
