"""Coherent decision snapshots and refusal of stale execution intent."""

from __future__ import annotations

from dataclasses import replace

import pytest

from etalon.active import (
    ActiveCampaign,
    CampaignSpec,
    CampaignStore,
    Candidate,
    Endpoint,
    Evaluation,
    StateError,
)


def campaign(tmp_path, *, batch_size=1):
    store = CampaignStore(tmp_path / "journal.sqlite")
    endpoint = Endpoint("objective", "target", "score", "u", "test/1", 1, requires_handoff=False)
    store.configure(CampaignSpec(endpoint.id, 20, "quotes", "vector/1", batch_size=batch_size), [endpoint])
    store.add_candidates([Candidate(f"m{i}", "CCO", (float(i),)) for i in range(6)])

    def execute(action, candidate, endpoint, grant):
        return Evaluation(candidate.id, endpoint.id, candidate.features[0], endpoint.units, endpoint.cost)

    return ActiveCampaign(store, execute)


def test_plan_uses_one_snapshot_even_if_a_label_arrives_after_read(tmp_path, monkeypatch):
    run = campaign(tmp_path)
    original = run.store.snapshot
    baseline = original()

    def read_then_update():
        state = original()
        run.store.import_evaluation(Evaluation("m5", "objective", -100, "u", 1), source_id="late")
        return state

    monkeypatch.setattr(run.store, "snapshot", read_then_update)
    model, choices, reason = run.plan()
    assert reason == "ready" and model.snapshot()["training_size"] == 0
    assert all(c.evidence["planning_event_cutoff"] == baseline["event_cutoff"] for c in choices)
    assert run.store.balance()["spent"] == 1


def test_combined_inspection_uses_one_snapshot_and_one_model_fit(tmp_path, monkeypatch):
    run = campaign(tmp_path)
    original = run.store.snapshot
    baseline = original()
    calls = []

    def read_then_update():
        calls.append("snapshot")
        state = original()
        run.store.import_evaluation(Evaluation("m5", "objective", -100, "u", 1), source_id="late-inspection")
        return state

    monkeypatch.setattr(run.store, "snapshot", read_then_update)
    result = run.inspect()
    assert calls == ["snapshot"]
    assert result["event_cutoff"] == baseline["event_cutoff"]
    assert result["model"] == result["recommendation"]["model"]
    assert result["model"]["training_size"] == 0
    assert result["recommendation"]["evidence_backed"] is None
    assert result["recommendation"]["remaining_budget"] == 20
    assert all(choice["evidence"]["planning_event_cutoff"] == result["event_cutoff"]
               for choice in result["choices"])
    assert run.store.balance()["spent"] == 1


def test_empty_combined_inspection_is_still_a_read_only_snapshot(tmp_path):
    store = CampaignStore(tmp_path / "journal.sqlite")
    endpoint = Endpoint("o", "target", "score", "u", "test/1", 1, requires_handoff=False)
    store.configure(CampaignSpec("o", 20, "quotes", "vector/1"), [endpoint])
    before = store.path.read_bytes()
    result = ActiveCampaign(store).inspect()
    assert result["stop_reason"] == "empty_pool"
    assert result["model"] is None and result["choices"] == []
    assert result["recommendation"]["model"] is None
    assert store.path.read_bytes() == before


def test_stale_execution_refuses_before_round_reservation_or_tool(tmp_path, monkeypatch):
    run = campaign(tmp_path)
    original = run.plan

    def stale_plan():
        plan = original()
        run.store.add_candidates([Candidate("arrived", "CCN", (7.0,))])
        return plan

    monkeypatch.setattr(run, "plan", stale_plan)
    run.executor = lambda *_: pytest.fail("stale plan must not call a scientific tool")
    with pytest.raises(StateError, match="stale"):
        run.run_round()
    assert run.store.actions() == run.store.rounds() == []
    assert run.store.balance()["spent"] == run.store.balance()["reserved"] == 0


def test_round_and_choices_record_the_same_snapshot_cutoff(tmp_path):
    run = campaign(tmp_path, batch_size=2)
    cutoff = run.store.snapshot()["event_cutoff"]
    result = run.run_round()
    record = run.store.rounds()[0]
    assert record["planning_event_cutoff"] == cutoff
    assert len(result["actions"]) == 2
    assert all(a["decision"]["planning_event_cutoff"] == cutoff for a in run.store.actions())


@pytest.mark.parametrize("cutoff", [None, True, "1"])
def test_unauditable_choice_cannot_be_dispatched(tmp_path, monkeypatch, cutoff):
    run = campaign(tmp_path)
    model, choices, reason = run.plan()
    choices = [replace(c, evidence={**c.evidence, "planning_event_cutoff": cutoff}) for c in choices]
    monkeypatch.setattr(run, "plan", lambda: (model, choices, reason))
    with pytest.raises(StateError, match="coherent journal snapshot"):
        run.run_round()
    assert not run.store.actions() and not run.store.rounds()


def test_late_label_between_start_and_reserve_closes_round_without_retry(tmp_path, monkeypatch):
    run = campaign(tmp_path)
    selected = run.plan()[1][0].candidate_id
    original = run.store.start_round

    def start_then_import(model, **kwargs):
        identifier = original(model, **kwargs)
        run.store.import_evaluation(Evaluation(selected, "objective", -9, "u", 1), source_id="arrived-after-start")
        return identifier

    monkeypatch.setattr(run.store, "start_round", start_then_import)
    run.executor = lambda *_: pytest.fail("the consumed replicate must not be retried")
    result = run.run_round()
    assert result["stop_reason"] == "state_changed" and result["actions"] == []
    assert "replicate" in result["reservation_error"]
    assert run.store.rounds()[0]["status"] == "completed"
    assert len(run.store.actions()) == 1 and run.store.actions()[0]["round_id"] == 0
    assert run.store.balance()["spent"] == 1 and run.store.balance()["reserved"] == 0


@pytest.mark.parametrize("rounds", [True, False, 0, -1, 1.5])
def test_round_limit_requires_a_real_positive_integer(tmp_path, rounds):
    run = campaign(tmp_path)
    with pytest.raises(ValueError):
        run.run(max_rounds=rounds)
    assert not run.store.actions()
