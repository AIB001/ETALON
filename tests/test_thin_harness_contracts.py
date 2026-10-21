"""V independent acceptance: synthetic execution, real SQLite identities and costs.

Only the executor/worker fixture is shared with A; assertions and interventions
are owned by V. These tests do not claim native host or scientific validation.
"""
from copy import deepcopy
from dataclasses import replace

import pytest

from etalon.active.schema import canonical
from etalon.active.store import CampaignStore, StateError
from etalon.research import MethodVersion, ResearchJournal
from etalon.runtime import api
from test_runtime_actions import fixture_action


def dispatch(request, root):
    prepared = api.prepare_action(request)
    return api.submit_action(request, workspace=root / "runtime", plan_id=prepared["plan_id"])


def test_independent_replay_after_two_actions_and_conflicting_payloads(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    first = dispatch(request, tmp_path)
    later = deepcopy(request)
    later["request_id"] = "unplanned-next"
    later["arguments"]["candidate_id"] = "two"
    dispatch(later, tmp_path)
    assert len(calls) == 2
    assert store.balance()["spent"] == 3
    before = canonical(store.export())
    replay = api.submit_action(request, workspace=tmp_path / "runtime", plan_id=first["plan_id"])
    assert replay["job_id"] == first["job_id"]
    for key in ("candidate", "limit"):
        conflicting = deepcopy(request)
        if key == "candidate":
            conflicting["arguments"]["candidate_id"] = "two"
        else:
            conflicting["limits"]["campaign:fixture-cost"] = 3
        with pytest.raises(StateError, match="conflict"):
            api.submit_action(conflicting, workspace=tmp_path / "runtime", plan_id=first["plan_id"])
    assert canonical(store.export()) == before and len(calls) == 2
    result = api.read_action_result(replay)
    assert result["method_use_refs"] == []
    assert result["accounting_refs"][-1]["additive"] is False
    assert all(r["model"] is None for r in store.rounds())


def test_real_method_record_does_not_stale_preparation(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    frozen = api.prepare_action(request)
    journal = ResearchJournal(store)
    method = MethodVersion("unrelated hypothesis", ("domain_skill",),
                           {"manifest_sha256": "4" * 64})
    journal.record(method)
    assert api.prepare_action(request)["plan_id"] == frozen["plan_id"]
    handle = api.submit_action(request, workspace=tmp_path / "runtime", plan_id=frozen["plan_id"])
    assert api.read_action_result(handle)["execution_state"] == "completed"
    assert len(calls) == 1
    assert journal.get(method.id)["verification_basis"] == "host_reported"


@pytest.mark.parametrize("field", ["executor_id", "configuration"])
def test_actual_executor_binding_change_stales_before_reservation(tmp_path, monkeypatch, field):
    store, request, calls, registry = fixture_action(tmp_path, monkeypatch)
    frozen = api.prepare_action(request)
    registry["score"][field] = "changed-v2" if field == "executor_id" else {"receptor": "changed"}
    with pytest.raises(StateError, match="stale"):
        api.submit_action(request, workspace=tmp_path / "runtime", plan_id=frozen["plan_id"])
    assert calls == [] and store.actions() == [] and store.balance()["reserved"] == 0


@pytest.mark.parametrize("outcome,state,admission", [
    ("wrong_units", "completed", "rejected"),
    ("failed", "failed", "rejected"),
    ("pending", "unresolved", None),
])
def test_execution_validation_and_actual_cost_remain_separate(tmp_path, monkeypatch, outcome, state, admission):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch, outcome=outcome)
    handle = dispatch(request, tmp_path)
    result = api.read_action_result(handle)
    assert result["execution_state"] == state
    if outcome == "pending":
        assert result["observations"] == []
        assert store.balance()["reserved"] == 2
        assert result["accounting_refs"][0]["overhead"] is None
        with pytest.raises(StateError, match="reconciliation"):
            api.prepare_action({**request, "request_id": "cannot-bypass-unknown"})
    else:
        assert result["observations"][0]["admission"] == admission
        assert result["observations"][0]["result"]["value"] is None
        assert store.balance()["spent"] == 1.5
        assert result["accounting_refs"][0]["charged"] == 1.5
        assert result["accounting_refs"][-1]["additive"] is False
    before = canonical(store.export())
    api.submit_action(request, workspace=tmp_path / "runtime", plan_id=handle["plan_id"])
    assert canonical(store.export()) == before and len(calls) == 1


def test_two_real_logs_pagination_and_cursor_scope(tmp_path):
    from etalon.active.schema import CampaignSpec, Endpoint
    stores = [CampaignStore(tmp_path / f"log-{i}.sqlite") for i in range(2)]
    logs, cursors, histories = [], [], []
    for store in stores:
        store.configure(CampaignSpec("score", 1, "fixture", "fixture/1"),
                        [Endpoint("score", "fixture", "score", "u", "fixture/1", 1)])
        with store.connection(write=True) as db:
            for index in range(137):
                store._event(db, "fixture", {"index": index})
        log = {"kind": "campaign", "database": str(store.path)}
        events, cursor = [], 0
        while True:
            page = api.read_events(log, after_sequence=cursor, limit=17)
            events.extend(page["events"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        assert [e["body"]["index"] for e in events if e["kind"] == "fixture"] == list(range(137))
        assert len(events) == len({e["sequence"] for e in events})
        assert api.read_events(log, after_sequence=cursor)["next_cursor"] == cursor
        with store.connection(write=True) as db:
            store._event(db, "fixture", {"index": 137})
        appended = api.read_events(log, after_sequence=cursor)
        assert [e["body"]["index"] for e in appended["events"]] == [137]
        logs.append(log)
        cursors.append(cursor)
        histories.append(events)
    assert histories[0][-1]["sequence"] == histories[1][-1]["sequence"]
    assert histories[0][-1]["event_ref"] != histories[1][-1]["event_ref"]
    with pytest.raises(StateError, match="another log"):
        api.read_events(logs[1], after_sequence=cursors[0])


def test_prior_exposure_and_self_declared_independence_rejected(tmp_path):
    # Use B's file/receipt builders only; V owns the negative interventions.
    from test_research_methods import evaluation_fixture, independent_receipt
    store, journal, candidate = evaluation_fixture(tmp_path)
    diagnostic = journal.record(candidate)
    with pytest.raises(StateError, match="actual results"):
        journal.record(replace(candidate, assessment_role="independent_validation"))
    pre_seen = independent_receipt(tmp_path, journal, candidate, pre_seen=True)
    before = canonical(store.export())
    with pytest.raises(StateError, match="seen artifact bytes"):
        journal.record(pre_seen)
    assert canonical(store.export()) == before
    assert journal.get(diagnostic)["assessment_role"] == "diagnostic"
    assert store.balance()["spent"] == 1  # failed fixture execution remains charged
    assert not store.observations()  # method metadata never becomes molecular labels


def test_real_mcp_wrappers_share_action_identity_and_errors(tmp_path, monkeypatch):
    import asyncio
    import json
    from etalon.mcp.server import register_tools
    from test_thin_harness_integration import Collector

    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    collector = Collector()
    register_tools(collector)

    async def run():
        async def call(name, **args):
            return json.loads(await collector.tools[name](**args))
        prepared = await call("etalon_action_prepare", request=request)
        assert prepared["ok"]
        plan_id = prepared["prepared"]["plan_id"]
        first = await call("etalon_action_submit", request=request,
                           workspace=str(tmp_path / "runtime"), plan_id=plan_id)
        assert first["ok"]
        result = await call("etalon_action_result", handle=first["handle"])
        assert result["ok"] and result["result"] == api.read_action_result(first["handle"])
        replay = api.submit_action(request, workspace=tmp_path / "runtime", plan_id=plan_id)
        assert replay["job_id"] == first["handle"]["job_id"]
        changed = deepcopy(request)
        changed["arguments"]["candidate_id"] = "two"
        refused = await call("etalon_action_submit", request=changed,
                             workspace=str(tmp_path / "runtime"), plan_id=plan_id)
        assert not refused["ok"] and refused["error"]["code"] == "StateError"
        assert not refused["error"]["retryable"]
    asyncio.run(run())
    assert len(calls) == 1 and store.balance()["spent"] == 1.5


def test_durable_observer_survives_admission_crash_and_reconciles_same_action(tmp_path, monkeypatch):
    from etalon.runtime import operations, service
    from etalon.runtime.store import RuntimeStore
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    original = CampaignStore.resolve
    def crash(*args, **kwargs):
        raise RuntimeError("V crash after execution, before admission commit")
    monkeypatch.setattr(CampaignStore, "resolve", crash)
    handle = dispatch(request, tmp_path)
    result = api.read_action_result(handle)
    assert result["execution_state"] == "unresolved"
    assert store.balance()["reserved"] == 2
    assert len(calls) == 1
    monkeypatch.setattr(CampaignStore, "resolve", original)
    runtime = RuntimeStore(tmp_path / "runtime")
    context = service.Context(runtime, handle["job_id"], 0, "action")
    recovered = operations.recover("active.evaluate", request["arguments"], context)
    node = runtime.get(handle["job_id"])["nodes"][0]
    assert service._complete(context, node, request["arguments"], recovered, recovered=True)
    runtime.set_state(handle["job_id"], 0, "succeeded")
    after = api.read_action_result(handle)
    assert after["execution_state"] == "completed"
    assert after["observations"][0]["admission"] == "accepted"
    assert store.balance()["spent"] == 1.5 and store.balance()["reserved"] == 0
    assert len(calls) == 1


def test_existing_non_scalar_research_cost_does_not_corrupt_action_receipt(tmp_path, monkeypatch):
    store, request, calls, _ = fixture_action(tmp_path, monkeypatch)
    store.reserve_resource_execution("historical-preparation", "d" * 64, 1,
                                     unit="fixture-cost", provenance={"fixture": True})
    store.settle_resource_execution("historical-preparation", measured_cost=0.5,
                                    receipt={"status": "failed", "score": None})
    assert store.balance()["spent"] == 0.5
    handle = dispatch(request, tmp_path)
    result = api.read_action_result(handle)
    assert result["execution_state"] == "completed"
    assert result["verification"]["state"] == "passed"
    assert result["observations"][0]["admission"] == "accepted"
    assert result["accounting_refs"][0]["charged"] == 1.5
    assert result["accounting_refs"][-1]["additive"] is False
    assert store.balance()["spent"] == 2
    before = canonical(store.export())
    api.submit_action(request, workspace=tmp_path / "runtime", plan_id=handle["plan_id"])
    assert canonical(store.export()) == before and len(calls) == 1
    # A later independent research charge must not be attributed to this action.
    store.reserve_resource_execution("later-research", "e" * 64, 0.5,
                                     unit="fixture-cost", provenance={"fixture": True})
    store.settle_resource_execution("later-research", measured_cost=0.25,
                                    receipt={"status": "completed", "value": None})
    reread = api.read_action_result(handle)
    assert reread["execution_state"] == "completed"
    assert reread["accounting_refs"][0]["charged"] == 1.5
    assert store.balance()["spent"] == 2.25
    store.reserve_resource_execution("unknown-preparation", "f" * 64, 0.5,
                                     unit="fixture-cost", provenance={"fixture": True})
    with pytest.raises(StateError, match="reconciliation"):
        api.prepare_action({**request, "request_id": "unknown-resource-cannot-bypass"})
    assert store.balance()["reserved"] == 0.5 and len(calls) == 1
