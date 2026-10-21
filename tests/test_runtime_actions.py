"""Action semantics with explicit in-process/executor fixtures, no scientific dispatch."""
from contextlib import nullcontext
from copy import deepcopy

import pytest

from etalon.active.runner import ExecutionPending
from etalon.active.schema import CampaignSpec, Candidate, Endpoint, Evaluation, canonical, digest
from etalon.active.store import CampaignStore, StateError
from etalon.runtime import api, executors, operations, service
from etalon.runtime.store import RuntimeStore


def fixture_action(tmp_path, monkeypatch, *, outcome="ok"):
    store = CampaignStore(tmp_path / "campaign.sqlite")
    endpoint = Endpoint("score", "fixture", "score", "fixture-unit", "fixture/1", 2,
                        requires_handoff=False, max_replicates=3)
    store.configure(CampaignSpec("score", 6, "fixture-cost", "fixture/1"), [endpoint])
    store.add_candidates([Candidate("one", "C", (1,)), Candidate("two", "CC", (2,))])
    record = {"schema": "etalon-executor/1", "kind": "test_fixture", "endpoint": endpoint.as_dict(),
              "configuration": {}, "inputs": {}, "executor_id": "fixture-version-1"}
    registry = {"score": record}
    monkeypatch.setattr(executors, "registered_executors", lambda database: deepcopy(registry))
    monkeypatch.setattr(executors, "_verify", lambda record: None)
    calls = []

    def run(action, candidate, endpoint, grant):
        calls.append(action.id)
        if outcome == "pending":
            raise ExecutionPending("fixture response lost; execution state unknown")
        return Evaluation(candidate.id, endpoint.id, 3 if outcome != "failed" else None,
                          "wrong" if outcome == "wrong_units" else endpoint.units, 1.5,
                          status="failed" if outcome == "failed" else "ok",
                          provenance={"cost_basis": "fixture measured", "test_fixture": True})

    monkeypatch.setattr(executors, "build_executor", lambda *a, **kw: (run, None))
    monkeypatch.setattr(service, "_resource_lock", lambda params: nullcontext())

    def launch(runtime, job_id):
        runtime.set_state(job_id, 0, "running")
        try:
            service._control(runtime, job_id, 0, lambda: None)
        except Exception as exc:
            job = runtime.get(job_id)
            state = "reconciliation_required" if any(n["state"] == "reconciliation_required" for n in job["nodes"]) else "blocked"
            runtime.set_state(job_id, 0, state, error=str(exc))

    monkeypatch.setattr(service, "_launch", launch)
    request = {"schema": "etalon-action/1", "request_id": "first", "operation": "active.evaluate",
               "arguments": {"database": str(store.path), "candidate_id": "one", "endpoint_id": "score"},
               "limits": {"campaign:fixture-cost": 2}, "method_use_refs": []}
    return store, request, calls, registry


def submit(request, tmp_path):
    prepared = api.prepare_action(request)
    return api.submit_action(request, workspace=tmp_path / "runtime", plan_id=prepared["plan_id"])


def test_no_skill_dynamic_second_action_and_replay_after_balance_changes(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    first = submit(request, tmp_path)
    result = api.read_action_result(first)
    assert result["execution_state"] == "completed"
    assert result["verification"]["state"] == "passed"
    assert result["observations"][0]["admission"] == "accepted"
    assert result["method_use_refs"] == []
    assert store.rounds()[0]["model"] is None
    second_request = deepcopy(request)
    second_request["request_id"] = "dynamic-second"
    second_request["arguments"]["candidate_id"] = "two"
    second = submit(second_request, tmp_path)
    assert api.read_action_result(second)["execution_state"] == "completed"
    before = store.export()
    replay = api.submit_action(request, workspace=tmp_path / "runtime", plan_id=first["plan_id"])
    assert replay["job_id"] == first["job_id"] and store.export() == before and len(calls) == 2
    changed = deepcopy(request)
    changed["limits"]["campaign:fixture-cost"] = 3
    with pytest.raises(StateError, match="conflict"):
        api.submit_action(changed, workspace=tmp_path / "runtime", plan_id=first["plan_id"])
    assert len(calls) == 2
    assert result["accounting_refs"][-1]["additive"] is False


def test_unrelated_notes_not_stale_but_executor_changed_is_stale(tmp_path, monkeypatch):
    store, request, calls, registry = fixture_action(tmp_path, monkeypatch)
    prepared = api.prepare_action(request)
    with store.connection(write=True) as db:
        store._event(db, "research_recorded", {"note": "unrelated diagnostic note"})
    assert api.prepare_action(request)["plan_id"] == prepared["plan_id"]
    registry["score"]["executor_id"] = "fixture-version-2"
    with pytest.raises(StateError, match="stale"):
        api.submit_action(request, workspace=tmp_path / "runtime", plan_id=prepared["plan_id"])
    assert calls == [] and store.balance()["reserved"] == 0


def test_wrong_units_retains_completed_execution_rejected_admission_and_charge(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch, outcome="wrong_units")
    result = api.read_action_result(submit(request, tmp_path))
    assert result["execution_state"] == "completed"
    assert result["verification"]["state"] == "failed"
    assert result["observations"][0]["admission"] == "rejected"
    assert result["observations"][0]["result"]["value"] is None
    assert result["accounting_refs"][0]["charged"] == 1.5
    assert store.balance()["spent"] == 1.5


def test_unknown_keeps_reservation_and_new_request_cannot_bypass(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch, outcome="pending")
    handle = submit(request, tmp_path)
    result = api.read_action_result(handle)
    assert result["execution_state"] == "unresolved" and result["observations"] == []
    assert store.balance()["reserved"] == 2 and result["accounting_refs"][0]["reserved"] == 2
    new_request = {**request, "request_id": "new-request"}
    with pytest.raises(StateError, match="reconciliation"):
        api.prepare_action(new_request)
    api.submit_action(request, workspace=tmp_path / "runtime", plan_id=handle["plan_id"])
    assert len(calls) == 1 and store.balance()["reserved"] == 2


def test_failed_measurement_is_not_zero_label(tmp_path, monkeypatch):
    store, request, _, _ = fixture_action(tmp_path, monkeypatch, outcome="failed")
    result = api.read_action_result(submit(request, tmp_path))
    assert result["execution_state"] == "failed"
    assert result["observations"][0]["result"]["value"] is None
    assert store.balance()["spent"] == 1.5


def test_request_validation_method_refs_and_insufficient_budget(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        api.prepare_action({**request, "arguments": {**request["arguments"], "replicate": 0}})
    with pytest.raises((StateError, ValueError)):
        api.prepare_action({**request, "method_use_refs": ["does-not-exist"]})
    with pytest.raises(ValueError, match="insufficient"):
        api.prepare_action({**request, "limits": {}})
    assert calls == []


@pytest.mark.parametrize("kind", ["runtime", "campaign"])
def test_full_event_pagination_empty_page_append_and_cross_log_cursor(tmp_path, monkeypatch, kind):
    store, request, _, _ = fixture_action(tmp_path, monkeypatch)
    if kind == "runtime":
        runtime = RuntimeStore(tmp_path / "runtime", create=True)
        plan = service.plan({"schema": "etalon-workflow/1", "objective": "fixture", "limits": {},
                             "nodes": [{"id": "one", "operation": "screen.export",
                                        "arguments": {"workspace": str(tmp_path), "run_id": "fixture"}}]})
        runtime.create("test-job", plan)
        log_ref = {"kind": kind, "workspace": str(runtime.workspace), "job_id": "test-job"}
        def append(n):
            with runtime.connection(write=True) as db:
                for i in range(n): runtime.event(db, "test-job", "fixture", {"i": i})
    else:
        log_ref = {"kind": kind, "database": str(store.path)}
        def append(n):
            with store.connection(write=True) as db:
                for i in range(n): store._event(db, "fixture", {"i": i})
    append(137)
    all_events, cursor = [], 0
    while True:
        page = api.read_events(log_ref, after_sequence=cursor, limit=17)
        all_events.extend(page["events"])
        cursor = page["next_cursor"]
        if not page["has_more"]: break
    assert sum(e["kind"] == "fixture" for e in all_events) == 137
    assert len({e["sequence"] for e in all_events}) == len(all_events)
    empty = api.read_events(log_ref, after_sequence=cursor, limit=17)
    assert empty["events"] == [] and empty["next_cursor"] == cursor
    append(1)
    assert len(api.read_events(log_ref, after_sequence=cursor)["events"]) == 1
    with pytest.raises(StateError, match="another log"):
        api.read_events(log_ref, after_sequence={**cursor, "log_id": "wrong"})


def test_empty_runtime_log_identity_does_not_change_on_first_append(tmp_path):
    runtime = RuntimeStore(tmp_path, create=True)
    log_ref = {"kind": "runtime", "workspace": str(tmp_path)}
    empty = api.read_events(log_ref)
    spec = {"schema": "etalon-workflow/1", "objective": "fixture", "limits": {}, "nodes": [
        {"id": "one", "operation": "screen.export", "arguments": {"workspace": str(tmp_path), "run_id": "none"}}]}
    runtime.create("first", service.plan(spec))
    assert len(api.read_events(log_ref, after_sequence=empty["next_cursor"])["events"]) == 1


def test_attribution_and_request_name_do_not_change_plan_and_replay_keeps_original(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    plan = api.prepare_action(request)
    annotated = {**request, "request_id": "different-name", "evidence_refs": ["host-note"]}
    assert api.prepare_action(annotated)["plan_id"] == plan["plan_id"]
    handle = api.submit_action(request, workspace=tmp_path / "runtime", plan_id=plan["plan_id"])
    replay = api.submit_action({**request, "evidence_refs": ["late-note"]},
                              workspace=tmp_path / "runtime", plan_id=plan["plan_id"])
    assert replay["job_id"] == handle["job_id"] and len(calls) == 1
    assert RuntimeStore(tmp_path / "runtime").get("first")["plan"]["spec"]["action_request"]["evidence_refs"] == []


def test_recovery_uses_pre_admission_receipt_without_executing_again(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    original = store.resolve
    original_class = CampaignStore.resolve
    def fail_resolve(self, *args, **kwargs):
        raise RuntimeError("fixture crash after durable observer before admission")
    monkeypatch.setattr(CampaignStore, "resolve", fail_resolve)
    handle = submit(request, tmp_path)
    assert api.read_action_result(handle)["execution_state"] == "unresolved"
    assert store.balance()["reserved"] == 2
    monkeypatch.setattr(CampaignStore, "resolve", original_class)
    runtime = RuntimeStore(tmp_path / "runtime")
    ctx = service.Context(runtime, "first", 0, "action")
    recovered = operations.recover("active.evaluate", request["arguments"], ctx)
    assert recovered["new_admitted"] == 1
    node = runtime.get("first")["nodes"][0]
    assert service._complete(ctx, node, request["arguments"], recovered, recovered=True)
    runtime.set_state("first", 0, "succeeded")
    assert len(calls) == 1 and store.balance()["reserved"] == 0 and store.balance()["spent"] == 1.5
    assert api.read_action_result(handle)["observations"][0]["admission"] == "accepted"


def test_host_selection_retains_confirmation_budget_commitment(tmp_path):
    from etalon.active.runner import ActiveCampaign
    from etalon.active.store import BudgetExhausted
    store = CampaignStore(tmp_path / "budget.sqlite")
    endpoints = [Endpoint("objective", "fixture", "score", "u", "fixture/1", 4, requires_handoff=False),
                 Endpoint("proxy", "fixture", "score", "u", "fixture/1", 2, requires_handoff=False)]
    store.configure(CampaignSpec("objective", 5, "quote", "fixture/1", policy="decision_aware",
                                 confirmation_reserve=1), endpoints)
    store.add_candidates([Candidate("one", "C", (1,))])
    with pytest.raises(BudgetExhausted, match="confirmation"):
        ActiveCampaign(store, lambda *a: pytest.fail("must not dispatch")).evaluate("one", "proxy")
    assert store.actions() == [] and store.balance()["reserved"] == 0


def test_zero_hit_screen_is_a_valid_native_outcome_but_unrelated_failure_is_not(tmp_path, monkeypatch):
    from etalon.boundary.screen import Screen, ScreenResult, StageOutcome
    exhausted = StageOutcome("gate", "fixture@1", "FAILED", 1, None,
                             {"code": "EVIDENCE_GATE_EMPTY_PARENT_INPUT"})
    native = ScreenResult("zero", "fixture-revision", "FAILED", (exhausted,))
    monkeypatch.setattr(Screen, "state", lambda self, run_id: native)
    result = {"workspace": str(tmp_path), "run_id": "zero", "run": native.as_dict()}
    verified = operations.verify("screen.run", {}, result)
    assert verified["outcome"] == "exhausted" and verified["artifact_ids"] == []
    defect = StageOutcome("other", "fixture@1", "FAILED", 1, None, {"code": "CUDA_OOM"})
    native = ScreenResult("zero", "fixture-revision", "FAILED", (exhausted, defect))
    result["run"] = native.as_dict()
    with pytest.raises(StateError, match="unrelated"):
        operations.verify("screen.run", {}, result)


def test_existing_non_scalar_research_charge_shares_budget_without_false_receipt_failure(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    store.reserve_resource_execution("prior-research", "1" * 64, 1, unit="fixture-cost", provenance={"fixture": True})
    store.settle_resource_execution("prior-research", measured_cost=0.75, receipt={"status": "failed", "fixture": True})
    handle = submit(request, tmp_path)
    result = api.read_action_result(handle)
    assert result["execution_state"] == "completed" and result["verification"]["state"] == "passed"
    assert store.balance()["spent"] == 2.25 and store.balance()["reserved"] == 0
    assert result["accounting_refs"][0]["charged"] == 1.5
    api.submit_action(request, workspace=tmp_path / "runtime", plan_id=handle["plan_id"])
    assert len(calls) == 1 and store.balance()["spent"] == 2.25
    store.reserve_resource_execution("unknown-research", "2" * 64, 1, unit="fixture-cost", provenance={"fixture": True})
    with pytest.raises(StateError, match="reconciliation"):
        api.prepare_action({**request, "request_id": "blocked-by-unknown"})
