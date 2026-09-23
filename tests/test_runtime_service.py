"""Actual detached workflows, bounded control and failure-preserving recovery."""

from __future__ import annotations

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from etalon.active.store import StateError
from etalon.runtime import operations
from etalon.runtime.schema import workflow
from etalon.runtime.service import (
    advance,
    cancel,
    observe,
    plan,
    read_artifact,
    reconcile,
    status,
    submit,
)
from etalon.runtime.store import RuntimeStore


def setup_spec(mode="ordered"):
    return {"schema": "etalon-workflow/1", "objective": "Initialize an explicit historical campaign",
        "limits": {}, "controller": {"mode": mode}, "max_seconds": 30,
        "nodes": [{"id": "campaign", "operation": "campaign.create", "arguments": {
            "spec": {"objective": "kd", "budget": 0, "cost_unit": "fixture", "representation": "fixture"},
            "endpoints": [{"id": "kd", "target": "fixture", "quantity": "Kd", "units": "nM", "protocol": "historical",
                "cost": 0, "queryable": False, "requires_handoff": False}], "executors": []}}]}


def wait(workspace, job_id="job", *, terminal=None):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        report = status(workspace, job_id)
        if not report["worker_alive"] and report["state"] not in {"queued", "starting", "running"}:
            if terminal:
                assert report["state"] == terminal, report
            return report
        time.sleep(0.05)
    pytest.fail(f"workflow did not stop: {report}")


def test_real_detached_database_screen_active_sourcing_chain(tmp_path):
    from examples.durable_campaign import specification

    spec = specification(tmp_path / "inputs")
    prepared = plan(spec)
    first = submit(spec, tmp_path / "runtime", job_id="job", expected_plan_id=prepared["plan_id"])
    report = wait(tmp_path / "runtime", terminal="succeeded")
    assert all(n["state"] == "verified" for n in report["nodes"])
    assert report["resources"]["http_requests"]["spent"] == 0
    assert report["resources"]["http_bytes"]["spent"] == 0
    assert report["resources"]["campaign:fixture_quotes"]["spent"] == 3
    learning = read_artifact(tmp_path / "runtime", "job", "learn", kind="result", member="/observations")
    assert learning["total"] == 3
    assert all(row["admitted"] for row in learning["data"])
    repeated = submit(spec, tmp_path / "runtime", job_id="job", expected_plan_id=prepared["plan_id"])
    assert repeated["pid"] == first["pid"] and repeated["epoch"] == first["epoch"]
    journal = RuntimeStore(tmp_path / "runtime").get("job")
    assert len([e for e in journal["events"] if e["kind"] == "dispatched"]) == 8


def test_concurrent_submissions_share_the_same_worker(tmp_path):
    spec = setup_spec()
    planned = plan(spec)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(submit, spec, tmp_path, job_id="job", expected_plan_id=planned["plan_id"]) for _ in range(2)]
        for result in results:
            result.result()
    report = wait(tmp_path, terminal="succeeded")
    assert report["epoch"] == 1
    assert len([e for e in report["events"] if e["kind"] == "worker_started"]) == 1


def test_external_llm_proposal_uses_same_worker_and_cannot_finish_early(tmp_path):
    spec = setup_spec("external")
    submit(spec, tmp_path, job_id="job", expected_plan_id=plan(spec)["plan_id"])
    observed = observe(tmp_path, "job")
    with pytest.raises(ValueError, match="finish|verified|complete"):
        advance(tmp_path, "job", {"observation_id": observed["observation_id"], "node_id": "finish", "reason": "Claiming success"})
    assert status(tmp_path, "job")["pid"] is None
    advance(tmp_path, "job", {"observation_id": observed["observation_id"], "node_id": "campaign", "reason": "Initialize declared inputs"})
    assert wait(tmp_path, terminal="succeeded")["llm_calls"] == 0


def test_cancel_before_any_execution_preserves_unstarted_nodes(tmp_path):
    spec = setup_spec("external")
    submit(spec, tmp_path, job_id="job", expected_plan_id=plan(spec)["plan_id"])
    result = cancel(tmp_path, "job", reason="The operator cancelled the unstarted task")
    assert result["state"] == "cancelled"
    assert result["nodes"][0]["state"] == "waiting"
    assert not (tmp_path / "jobs/job/steps/campaign/campaign.sqlite").exists()


@pytest.mark.parametrize("change", [
    lambda s: s.update(nodes=[]),
    lambda s: s["nodes"][0].update(id="finish"),
    lambda s: s["nodes"][0].update(id="../escape"),
    lambda s: s["nodes"][0].update(operation="shell.run"),
    lambda s: s["nodes"][0].update(depends_on=["unknown"]),
    lambda s: s["nodes"][0].update(depends_on=["campaign"]),
    lambda s: s["nodes"][0]["arguments"].update(command="echo hidden"),
    lambda s: s.update(limits={"GPU": -1}),
    lambda s: s.update(limits={"GPU": float("nan")}),
    lambda s: s.update(max_seconds=True),
    lambda s: s.update(controller={"mode": "shell"}),
])
def test_invalid_workflows_are_rejected_before_workspace_creation(tmp_path, change):
    spec = setup_spec()
    change(spec)
    with pytest.raises((ValueError, TypeError, KeyError)):
        submit(spec, tmp_path / "never", job_id="job", expected_plan_id="not-a-plan")
    assert not (tmp_path / "never").exists()


def test_plan_pins_nested_receptor_and_catalog_bytes(tmp_path):
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text("first receptor")
    config = tmp_path / "screen.json"
    config.write_text(json.dumps({"target": {"receptor_path": str(receptor)}}))
    library = tmp_path / "library.csv"
    library.write_text("id,smiles\nx,CCO\n")
    spec = {"schema": "etalon-workflow/1", "objective": "Bind explicit screen inputs", "limits": {"quote": 1},
            "nodes": [{"id": "screen", "operation": "screen.run", "resources": {"quote": 1},
                       "arguments": {"config_path": str(config), "library_path": str(library)}}]}
    first = plan(spec)
    receptor.write_text("different receptor")
    assert plan(spec)["plan_id"] != first["plan_id"]
    with pytest.raises(StateError, match="changed"):
        submit(spec, tmp_path / "never", job_id="job", expected_plan_id=first["plan_id"])


def test_receipt_write_failure_leaves_experimental_reservation_pending(tmp_path):
    from etalon.active import ActiveCampaign, CampaignSpec
    from etalon.active.schema import Candidate, Endpoint, Evaluation
    from etalon.active.store import CampaignStore

    store = CampaignStore(tmp_path / "active.sqlite")
    endpoint = Endpoint("score", "fixture", "score", "u", "fixture", 1, requires_handoff=False)
    store.configure(CampaignSpec("score", 1, "quote", "fixture", batch_size=1), [endpoint])
    store.add_candidates([Candidate("c", "CCO", (1.0, 2.0))])

    def receipt(action, result):
        assert result.provenance["endpoint_protocol"] == endpoint.protocol
        raise OSError("durable receipt unavailable")

    with pytest.raises(OSError, match="receipt"):
        ActiveCampaign(store, lambda action, candidate, ep, grant: Evaluation(candidate.id, ep.id, 2.0, ep.units, 1),
                       result_observer=receipt).run(max_rounds=1)
    assert store.status()["pending"]
    assert store.balance()["reserved"] == 1 and store.balance()["spent"] == 0


def test_stale_worker_cannot_publish_receipts_or_outcomes(tmp_path):
    store = RuntimeStore(tmp_path, create=True)
    store.create("job", plan(setup_spec()))
    with store.connection(write=True) as db:
        db.execute("UPDATE jobs SET epoch=2 WHERE id='job'")
    with pytest.raises(StateError, match="ownership"):
        store.receipt("job", 1, "campaign", "completed", {"result": {"success": True}})
    assert store.receipts("job", "campaign") == {}


def test_completed_receipt_recovers_without_executing_again(tmp_path, monkeypatch):
    from etalon.runtime.service import Context

    store = RuntimeStore(tmp_path, create=True)
    spec = setup_spec()
    store.create("job", plan(spec))
    node = workflow(spec)["nodes"][0]
    store.start_node("job", 0, node, node["arguments"], {"node_id": "campaign"})
    ctx = Context(store, "job", 0, "campaign")
    ctx.root.mkdir(parents=True)
    result = operations.execute("campaign.create", node["arguments"], ctx)
    ctx.receipt("completed", {"result": result})

    def forbidden(*args, **kwargs):
        pytest.fail("reconciliation reran an operation")

    monkeypatch.setattr(operations, "execute", forbidden)
    restored = reconcile(tmp_path, "job", reason="Owner exited after publishing a complete result receipt")
    assert restored["state"] == "succeeded"
    assert restored["nodes"][0]["evidence"]["recovered"] is True


def test_unresolved_costs_are_never_automatically_zeroed(tmp_path):
    store = RuntimeStore(tmp_path, create=True)
    spec = setup_spec()
    spec["limits"] = {"quote": 2}
    spec["nodes"][0]["resources"] = {"quote": 2}
    store.create("job", plan(spec))
    node = workflow(spec)["nodes"][0]
    store.start_node("job", 0, node, node["arguments"], {"node_id": "campaign"})
    restored = reconcile(tmp_path, "job", reason="Owner exited before an outcome was persisted", resume=True)
    assert restored["state"] == "reconciliation_required"
    assert restored["resources"]["quote"]["reserved"] == 2
    assert restored["resources"]["quote"]["spent"] == 0


def test_status_is_read_only_for_unknown_workspace(tmp_path):
    with pytest.raises(FileNotFoundError):
        status(tmp_path / "missing", "job")
    assert not (tmp_path / "missing").exists()


def test_resource_reservations_cannot_overdraw_shared_workflow_budget(tmp_path):
    store = RuntimeStore(tmp_path, create=True)
    spec = setup_spec()
    spec["limits"] = {"quote": 1}
    spec["nodes"][0]["resources"] = {"quote": 1}
    second = copy.deepcopy(spec["nodes"][0])
    second["id"] = "second"
    spec["nodes"].append(second)
    store.create("job", plan(spec))
    first, second = workflow(spec)["nodes"]
    store.start_node("job", 0, first, first["arguments"], {"node_id": "campaign"})
    with pytest.raises(StateError, match="resource"):
        store.start_node("job", 0, second, second["arguments"], {"node_id": "second"})
    assert store.get("job")["nodes"][1]["state"] == "waiting"


def test_failed_job_commit_leaves_no_unrecoverable_directory(tmp_path, monkeypatch):
    store = RuntimeStore(tmp_path, create=True)
    spec = setup_spec()
    original = RuntimeStore.event

    def unavailable(db, job, kind, body):
        if kind == "created":
            raise OSError("simulated journal failure before commit")
        original(db, job, kind, body)

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeStore, "event", staticmethod(unavailable))
        with pytest.raises(OSError, match="before commit"):
            store.create("job", plan(spec))
    assert not store.job_root("job").exists()
    with pytest.raises(KeyError):
        store.get("job")
    submit(spec, tmp_path, job_id="job", expected_plan_id=plan(spec)["plan_id"])
    assert wait(tmp_path, terminal="succeeded")["epoch"] == 1


def test_submit_resumes_creation_interrupted_after_commit(tmp_path, monkeypatch):
    from pathlib import Path

    store = RuntimeStore(tmp_path, create=True)
    spec = setup_spec()
    original = Path.mkdir

    def unavailable(path, *args, **kwargs):
        if path == store.job_root("job"):
            raise OSError("simulated interruption after journal commit")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", unavailable)
        with pytest.raises(OSError, match="after journal commit"):
            store.create("job", plan(spec))
    assert store.get("job")["state"] == "queued"
    submit(spec, tmp_path, job_id="job", expected_plan_id=plan(spec)["plan_id"])
    assert wait(tmp_path, terminal="succeeded")["epoch"] == 1


def test_unowned_scientific_directory_is_not_adopted_as_new_job(tmp_path):
    store = RuntimeStore(tmp_path, create=True)
    existing = store.job_root("job")
    existing.mkdir(parents=True)
    (existing / "old-science.txt").write_text("Original, independently executed work")
    with pytest.raises(FileExistsError, match="without an owning"):
        store.create("job", plan(setup_spec()))
    with pytest.raises(KeyError):
        store.get("job")
    assert (existing / "old-science.txt").read_text() == "Original, independently executed work"
