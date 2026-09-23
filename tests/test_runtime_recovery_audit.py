"""Fault injection at scientific dispatch, recovery and cost-settlement boundaries."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from etalon.active.schema import CampaignSpec, Candidate, Endpoint, Evaluation
from etalon.active.store import CampaignStore, StateError
from etalon.boundary.simulate import _run_owned
from etalon.data.artifacts import new_run, seal, write_json
from etalon.runtime import operations, process, service
from etalon.runtime.store import RuntimeStore


def pending_active(tmp_path, count=1):
    campaign = CampaignStore(tmp_path / "campaign.sqlite")
    endpoint = Endpoint("metric", "fixture", "fixture_score", "u", "fixture/1", 1,
                        requires_handoff=False)
    campaign.configure(CampaignSpec(endpoint.id, 10, "credits", "fixture/1"), [endpoint])
    campaign.add_candidates([Candidate(f"m{i}", "CCO", (float(i),)) for i in range(count)])
    start = {"action_ids": [], "balance": campaign.balance(), "admitted": 0}
    arguments = {"database": str(campaign.path), "max_rounds": 1}
    spec = {"schema": "etalon-workflow/1", "objective": "recovery fixture", "limits": {"campaign:credits": 10},
            "nodes": [{"id": "active", "operation": "active.run", "arguments": arguments,
                       "resources": {"campaign:credits": 10}}]}
    prepared = service.plan(spec)
    runtime = RuntimeStore(tmp_path / "runtime", create=True)
    runtime.create("audit", prepared)
    definition = prepared["spec"]["nodes"][0]
    runtime.start_node("audit", 0, definition, arguments, {"reason": "fault injection"})
    ctx = service.Context(runtime, "audit", 0, "active")
    ctx.root.mkdir(parents=True)
    ctx.receipt("active_start", start)
    (ctx.root / "failure-evidence.txt").write_text("Fake executor fixture; documented failure cost for this test.")
    round_id = campaign.start_round({})
    actions = [campaign.reserve(round_id, f"m{i}", endpoint.id, {}) for i in range(count)]
    for action in actions:
        campaign.start_action(action.id)
    return campaign, runtime, ctx, actions


def test_invalid_failure_total_cannot_partially_settle_scientific_journal(tmp_path):
    campaign, runtime, ctx, actions = pending_active(tmp_path)
    before = campaign.export()
    settlement = {"costs": {"campaign:credits": 0}, "action_costs": {actions[0].id: 1},
                  "reason": "Deliberately inconsistent test settlement", "evidence": "failure-evidence.txt"}
    with pytest.raises(ValueError):
        service._settle_failure(ctx, runtime.get("audit")["nodes"][0], settlement)
    assert campaign.export() == before


def test_invalid_later_action_cost_cannot_commit_earlier_action(tmp_path):
    campaign, runtime, ctx, actions = pending_active(tmp_path, count=2)
    before = campaign.export()
    settlement = {"costs": {"campaign:credits": 2},
                  "action_costs": {actions[0].id: 1, actions[1].id: "invalid"},
                  "reason": "The whole settlement must validate before any commit", "evidence": "failure-evidence.txt"}
    with pytest.raises((ValueError, TypeError)):
        service._settle_failure(ctx, runtime.get("audit")["nodes"][0], settlement)
    assert campaign.export() == before


def test_active_receipt_recovery_settles_once_without_reexecution(tmp_path):
    campaign, _, ctx, actions = pending_active(tmp_path, count=2)
    for action in actions:
        value = Evaluation(action.candidate_id, action.endpoint_id, 2, "u", 1,
                           provenance={"endpoint_protocol": "fixture/1", "authorization": None,
                                       "source": "explicit numerical fixture"})
        ctx.receipt("action:" + action.id, {"action": action.as_dict(), "result": value.as_dict()})
    params = {"database": str(campaign.path), "max_rounds": 1}
    recovered = operations.recover("active.run", params, ctx)
    assert recovered["new_admitted"] == 2 and recovered["cost"] == 2
    assert campaign.status()["pending"] == []
    before = campaign.export()
    repeated = operations.recover("active.run", params, ctx)
    assert repeated["cost"] == 2 and campaign.export() == before


def test_missing_active_receipt_retains_unknown_cost_reservation(tmp_path):
    campaign, _, ctx, _ = pending_active(tmp_path)
    before = campaign.export()
    assert operations.recover("active.run", {"database": str(campaign.path), "max_rounds": 1}, ctx) is None
    assert campaign.export() == before and campaign.balance()["reserved"] == 1


def completed_active(tmp_path, count=1):
    campaign, runtime, ctx, actions = pending_active(tmp_path, count=count)
    for action in actions:
        value = Evaluation(action.candidate_id, action.endpoint_id, 2, "u", 1,
                           provenance={"endpoint_protocol": "fixture/1", "authorization": None,
                                       "source": "explicit numerical fixture"})
        ctx.receipt("action:" + action.id, {"action": action.as_dict(), "result": value.as_dict()})
    params = {"database": str(campaign.path), "max_rounds": 1}
    result = operations.recover("active.run", params, ctx)
    return campaign, runtime, ctx, actions, params, result


@pytest.mark.parametrize("claim", [{"cost": 0}, {"cost_unit": "invented"}, {"new_admitted": 99}])
def test_active_verification_rejects_forged_summary_but_accounting_uses_real_charges(tmp_path, claim):
    _, _, ctx, _, params, result = completed_active(tmp_path)
    forged = {**result, **claim}
    ownership = {"active_start": ctx.receipts()["active_start"], "action_receipts": ctx.receipts()}
    with pytest.raises(StateError, match="charges|units|admission"):
        operations.verify("active.run", params, forged, **ownership)
    spent, _ = operations.accounting("active.run", forged, {"campaign:credits": 10},
                                     database=params["database"], **ownership)
    assert spent == {"campaign:credits": 1}


def test_active_result_cannot_substitute_a_different_database(tmp_path):
    _, _, ctx, _, params, result = completed_active(tmp_path)
    wrong = {**params, "database": str(tmp_path / "different.sqlite")}
    with pytest.raises(StateError, match="different campaign database"):
        operations.verify("active.run", wrong, result, action_receipts=ctx.receipts())
    assert not (tmp_path / "different.sqlite").exists()


def test_active_result_cannot_omit_an_owned_action(tmp_path):
    _, _, ctx, _, params, result = completed_active(tmp_path, count=2)
    chosen = result["action_ids"][0]
    forged = {**result, "action_ids": [chosen], "cost": 1, "new_admitted": 1,
              "observations": [o for o in result["observations"] if o["action_id"] == chosen]}
    with pytest.raises(StateError, match="omitted|added"):
        operations.verify("active.run", params, forged, action_receipts=ctx.receipts())


def test_active_result_starting_boundary_is_bound_to_durable_receipt(tmp_path):
    _, _, ctx, _, params, result = completed_active(tmp_path, count=2)
    forged = {**result, "active_start": {**result["active_start"], "action_ids": result["end_action_ids"][:1]}}
    with pytest.raises(StateError, match="starting ownership receipt"):
        operations.verify("active.run", params, forged, active_start=ctx.receipts()["active_start"],
                           action_receipts=ctx.receipts())


def test_active_result_cannot_supply_its_own_missing_starting_receipt(tmp_path):
    _, _, ctx, _, params, result = completed_active(tmp_path)
    incomplete = {key: body for key, body in ctx.receipts().items() if key != "active_start"}
    with pytest.raises(StateError, match="missing.*starting ownership receipt"):
        operations.accounting("active.run", result, {"campaign:credits": 10},
                               database=params["database"], action_receipts=incomplete)


def test_action_charge_corruption_cannot_override_a_durable_observation(tmp_path):
    campaign, _, ctx, actions, params, result = completed_active(tmp_path)
    with campaign.connection(write=True) as db:
        db.execute("UPDATE actions SET cost=0 WHERE id=?", (actions[0].id,))
    with pytest.raises(StateError, match="charges"):
        operations.accounting("active.run", {**result, "cost": 0}, {"campaign:credits": 10},
                               database=params["database"], action_receipts=ctx.receipts())


def test_unowned_direct_runner_result_cannot_complete_a_runtime_task(tmp_path):
    campaign, _, ctx, actions = pending_active(tmp_path)
    value = Evaluation(actions[0].candidate_id, actions[0].endpoint_id, 2, "u", 1,
                       provenance={"endpoint_protocol": "fixture/1", "authorization": None,
                                   "source": "another direct runner"})
    campaign.resolve(actions[0].id, value)
    params = {"database": str(campaign.path), "max_rounds": 1}
    before = campaign.export()
    assert operations.recover("active.run", params, ctx) is None
    assert campaign.export() == before
    unowned = operations.active_result(campaign, ctx.receipts()["active_start"])
    with pytest.raises(StateError, match="durable outcome receipts"):
        operations.verify("active.run", params, unowned, action_receipts=ctx.receipts())


def test_verified_active_interval_stays_valid_after_later_tasks_append_actions(tmp_path):
    campaign, _, ctx, _, params, result = completed_active(tmp_path)
    campaign.add_candidates([Candidate("later", "CCN", (3.0,))])
    round_id = campaign.start_round({})
    later = campaign.reserve(round_id, "later", "metric", {})
    value = Evaluation("later", "metric", 3, "u", 1,
                       provenance={"endpoint_protocol": "fixture/1", "authorization": None})
    campaign.resolve(later.id, value)
    campaign.finish_round(round_id, {})
    verified = operations.verify("active.run", params, result, active_start=ctx.receipts()["active_start"],
                                  action_receipts=ctx.receipts())
    assert verified["admitted"] == 1 and verified["cost"] == 1


def test_individual_failure_settlement_receipt_replays_idempotently(tmp_path):
    campaign, _, ctx, actions = pending_active(tmp_path)
    value = Evaluation(actions[0].candidate_id, actions[0].endpoint_id, None, "u", 0.75, status="failed",
                       provenance={"reason": "Documented interrupted fixture cost", "cost_basis": "caller assertion"})
    ctx.receipt("settlement:" + actions[0].id, {"result": value.as_dict()})
    params = {"database": str(campaign.path), "max_rounds": 1}
    result = operations.recover("active.run", params, ctx)
    assert result["new_admitted"] == 0 and result["cost"] == 0.75 and not result["pending"]
    before = campaign.export()
    assert operations.recover("active.run", params, ctx)["cost"] == 0.75
    assert campaign.export() == before


def test_late_invalid_recovery_receipt_cannot_partially_commit_prior_action(tmp_path):
    campaign, _, ctx, actions = pending_active(tmp_path, count=2)
    for index, action in enumerate(actions):
        value = Evaluation(action.candidate_id, action.endpoint_id, None, "wrong" if index else "u", 1, status="failed")
        ctx.receipt("settlement:" + action.id, {"result": value.as_dict()})
    before = campaign.export()
    with pytest.raises(StateError, match="units"):
        operations.recover("active.run", {"database": str(campaign.path), "max_rounds": 1}, ctx)
    assert campaign.export() == before


def test_partial_manual_failure_batch_recovers_without_reexecution_or_false_success(tmp_path, monkeypatch):
    campaign, runtime, ctx, actions = pending_active(tmp_path, count=3)
    success = Evaluation(actions[0].candidate_id, actions[0].endpoint_id, 2, "u", 1,
                         provenance={"endpoint_protocol": "fixture/1", "authorization": None})
    ctx.receipt("action:" + actions[0].id, {"action": actions[0].as_dict(), "result": success.as_dict()})
    campaign.resolve(actions[0].id, success)
    settlement = {"costs": {"campaign:credits": 3}, "action_costs": {a.id: 1 for a in actions[1:]},
                  "reason": "Documented fixture failure batch", "evidence": "failure-evidence.txt"}
    original_resolve = CampaignStore.resolve

    def die_after_one_commit(store, action_id, value, **kwargs):
        original_resolve(store, action_id, value, **kwargs)
        raise KeyboardInterrupt("Injected process death after first committed failure")

    monkeypatch.setattr(CampaignStore, "resolve", die_after_one_commit)
    with pytest.raises(KeyboardInterrupt):
        service._settle_failure(ctx, runtime.get("audit")["nodes"][0], settlement)
    assert len(campaign.status()["pending"]) == 1
    assert "failure_settlement" in ctx.receipts()
    monkeypatch.setattr(CampaignStore, "resolve", original_resolve)

    def forbidden(*args, **kwargs):
        pytest.fail("recovery must not execute another scientific task")

    monkeypatch.setattr(operations, "execute", forbidden)
    report = service.reconcile(runtime.workspace, "audit", resume=True, reason="Replay the whole recorded failure batch")
    assert report["state"] == "failed" and report["resources"]["campaign:credits"]["spent"] == 3
    assert not campaign.status()["pending"] and len(campaign.observations(admitted_only=True)) == 1
    before = campaign.export()
    service.reconcile(runtime.workspace, "audit", reason="Repeated recovery is idempotent")
    assert campaign.export() == before


@pytest.mark.parametrize("foreign_cost", [0, 2])
def test_frozen_failure_batch_rejects_later_unowned_actions_before_any_replay(tmp_path, monkeypatch, foreign_cost):
    campaign, runtime, ctx, actions = pending_active(tmp_path, count=2)
    settlement = {"costs": {"campaign:credits": 2}, "action_costs": {a.id: 1 for a in actions},
                  "reason": "Fixed ownership and costs before interrupted settlement", "evidence": "failure-evidence.txt"}
    original_resolve = CampaignStore.resolve

    def die_after_one_commit(store, action_id, value, **kwargs):
        original_resolve(store, action_id, value, **kwargs)
        raise KeyboardInterrupt("Injected death while the frozen batch is only partly applied")

    monkeypatch.setattr(CampaignStore, "resolve", die_after_one_commit)
    with pytest.raises(KeyboardInterrupt):
        service._settle_failure(ctx, runtime.get("audit")["nodes"][0], settlement)
    monkeypatch.setattr(CampaignStore, "resolve", original_resolve)
    campaign.add_candidates([Candidate("foreign", "CCN", (3.0,))])
    extra = campaign.reserve(actions[0].round_id, "foreign", "metric", {"source": "another direct caller"})
    value = Evaluation("foreign", "metric", 2, "u", foreign_cost,
                       provenance={"endpoint_protocol": "fixture/1", "authorization": None})
    campaign.resolve(extra.id, value)
    before = campaign.export()
    report = service.reconcile(runtime.workspace, "audit", resume=True,
                               reason="Extra actions must not be silently folded into a frozen settlement")
    assert report["state"] == "reconciliation_required"
    assert report["resources"]["campaign:credits"]["spent"] == 0
    assert report["resources"]["campaign:credits"]["reserved"] == 10
    assert campaign.export() == before


def test_failure_settlement_detects_foreign_action_inserted_between_its_commits(tmp_path, monkeypatch):
    campaign, runtime, _, actions = pending_active(tmp_path, count=2)
    campaign.add_candidates([Candidate("foreign", "CCN", (3.0,))])
    settlement = {"costs": {"campaign:credits": 2}, "action_costs": {a.id: 1 for a in actions},
                  "reason": "Concurrent writer must be detected at final reconciliation", "evidence": "failure-evidence.txt"}
    original_resolve = CampaignStore.resolve
    inserted = False

    def insert_between_commits(store, action_id, value, **kwargs):
        nonlocal inserted
        original_resolve(store, action_id, value, **kwargs)
        if not inserted:
            inserted = True
            extra = campaign.reserve(actions[0].round_id, "foreign", "metric", {"source": "concurrent direct caller"})
            result = Evaluation("foreign", "metric", 2, "u", 2,
                                provenance={"endpoint_protocol": "fixture/1", "authorization": None})
            original_resolve(campaign, extra.id, result)

    monkeypatch.setattr(CampaignStore, "resolve", insert_between_commits)
    report = service.reconcile(runtime.workspace, "audit", resume=True, settlements={"active": settlement},
                               reason="A concurrent action must prevent publication of stale settlement costs")
    assert inserted and report["state"] == "reconciliation_required"
    assert report["resources"]["campaign:credits"]["spent"] == 0
    assert report["resources"]["campaign:credits"]["reserved"] == 10
    assert campaign.balance()["spent"] == 4


def test_final_failure_publication_excludes_a_real_concurrent_sqlite_writer(tmp_path, monkeypatch):
    campaign, runtime, ctx, actions = pending_active(tmp_path)
    settlement = {"costs": {"campaign:credits": 1}, "action_costs": {actions[0].id: 1},
                  "reason": "Publish charges under the authoritative journal lock", "evidence": "failure-evidence.txt"}
    original_finish = RuntimeStore.finish_node
    observed = []

    def finish_while_contested(store, *args, **kwargs):
        program = (
            "import sqlite3,sys\n"
            "db=sqlite3.connect(sys.argv[1],timeout=0.05,isolation_level=None)\n"
            "try:\n"
            "    db.execute('BEGIN IMMEDIATE')\n"
            "except sqlite3.OperationalError as error:\n"
            "    print(str(error))\n"
            "else:\n"
            "    db.rollback()\n"
            "    print('unexpected concurrent writer')\n"
            "finally:\n"
            "    db.close()\n"
        )
        contender = subprocess.run([sys.executable, "-c", program, str(campaign.path)],
                                   capture_output=True, text=True, timeout=5)
        assert contender.returncode == 0 and contender.stdout.strip() == "database is locked"
        observed.append(True)
        return original_finish(store, *args, **kwargs)

    monkeypatch.setattr(RuntimeStore, "finish_node", finish_while_contested)
    service._settle_failure(ctx, runtime.get("audit")["nodes"][0], settlement)
    assert observed == [True] and runtime.get("audit")["nodes"][0]["state"] == "failed"
    with sqlite3.connect(campaign.path, timeout=0.05, isolation_level=None) as released:
        released.execute("BEGIN IMMEDIATE")
        released.rollback()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="process identities require Linux /proc")
def test_live_recorded_scientific_child_blocks_reconciliation(tmp_path):
    campaign, runtime, ctx, _ = pending_active(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], start_new_session=True)
    try:
        ctx.process_observer(child.pid)
        before = campaign.export()
        with pytest.raises(StateError, match="scientific child"):
            service.reconcile(runtime.workspace, "audit", resume=True, reason="A living child must block recovery")
        assert campaign.export() == before and runtime.get("audit")["epoch"] == 0
    finally:
        child.kill()
        child.wait(timeout=3)


def test_target_resolution_failure_is_not_verified_as_complete_acquisition(tmp_path):
    root = new_run(tmp_path, "unresolved", {})
    result = {"ok": True, "status": "needs_target_resolution", "counts": {"resolved_targets": 0}}
    write_json(root / "result.json", result)
    snapshot = seal(root, kind="collect", result=result, infrastructure={"fixture": True},
                    usage={"requests": 1, "bytes": 10})
    with pytest.raises(StateError):
        operations.verify("data.acquire", {"request": {"kind": "collect"}, "allow_partial": False}, snapshot)


def test_data_accounting_uses_sealed_usage_despite_forged_result_usage(tmp_path):
    root = new_run(tmp_path, "metered", {})
    result = {"status": "complete", "returned": 1}
    write_json(root / "result.json", result)
    snapshot = seal(root, kind="query", result=result, infrastructure={"fixture": True},
                    usage={"requests": 3, "response_bytes": 123})
    forged = {**snapshot, "usage": {"requests": 0, "response_bytes": 0}}
    spent, basis = operations.accounting("data.acquire", forged, {"http_requests": 10, "http_bytes": 1000})
    assert spent == {"http_requests": 3, "http_bytes": 123}
    assert all("measured" in value for value in basis.values())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="process ownership assertions require Linux")
def test_parent_killed_before_process_receipt_never_releases_scientific_command(tmp_path):
    marker, pid_path = tmp_path / "science-ran.txt", tmp_path / "child.json"
    program = (
        "import json,os,signal,sys\n"
        "from pathlib import Path\n"
        "from etalon.boundary.simulate import _run_owned\n"
        "from etalon.runtime.process import identity\n"
        "def interrupted_observer(pid):\n"
        "    Path(sys.argv[2]).write_text(json.dumps(identity(pid)))\n"
        "    os.kill(os.getpid(),signal.SIGKILL)\n"
        "command=[sys.executable,'-c','import sys,time;from pathlib import Path;time.sleep(0.1);Path(sys.argv[1]).write_text(\"executed\")',sys.argv[1]]\n"
        "_run_owned(command,timeout=5,env=os.environ,process_observer=interrupted_observer)\n"
    )
    result = subprocess.run([sys.executable, "-c", program, str(marker), str(pid_path)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == -signal.SIGKILL, result.stderr
    owner = json.loads(pid_path.read_text())
    deadline = time.monotonic() + 3
    while process.group_alive(owner) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not process.group_alive(owner), "blocked launcher must exit when the killed parent closes the pipe"
    assert not marker.exists(), "the scientific command ran before its process receipt was durable"


@pytest.mark.skipif(os.name != "posix", reason="owned process groups require POSIX")
def test_durable_process_receipt_releases_gate_and_preserves_pid(tmp_path):
    marker = tmp_path / "science-ran.json"
    identities = []

    def observed(pid):
        assert not marker.exists()
        identities.append(process.identity(pid))

    command = [sys.executable, "-c", "import json,os,sys;from pathlib import Path;Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'group':os.getpgrp()}))", str(marker)]
    captured = _run_owned(command, timeout=5, env=os.environ, process_observer=observed)
    actual = json.loads(marker.read_text())
    assert captured.status == "completed"
    assert actual == {"pid": identities[0]["pid"], "group": identities[0]["group"]}
