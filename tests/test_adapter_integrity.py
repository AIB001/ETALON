"""The live adapter must validate intent before factories and preserve honest costs."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from etalon.active.adapters import StageExecutor, molecular_candidates
from etalon.active.policy import Choice
from etalon.active.runner import ActiveCampaign
from etalon.active.schema import Action, CampaignSpec, Candidate, Endpoint, Evaluation
from etalon.active.store import CampaignStore
from etalon.authority.grant import NotAuthorized, authorize
from etalon.campaign.expensive import PrismStage


@pytest.fixture
def execution(monkeypatch):
    # Exercise real token signatures and row digests without loading scientific tools.
    monkeypatch.setattr("etalon.faults.preflight.check_record", lambda *args, **kwargs: ())
    candidate = Candidate("m1", "CCO", (1.0,),
                          handoff={"parent_id": "m1", "parent_smiles": "CCO"}, cheap_value=0.0)
    endpoint = Endpoint("objective", "target", "score", "u", "fake/1", 3.0)
    action = Action("action-1", 1, candidate.id, endpoint.id, 0, endpoint.cost)
    grant = authorize([candidate.handoff]).grants[candidate.id]
    rows = [SimpleNamespace(parent_id=candidate.id, expensive_value=-2.0, units=endpoint.units,
                            observations=(), provenance={"source": "fake stage"})]
    calls = []

    def stage(handoffs, cheap, grants):
        calls.append(("stage", handoffs, cheap, grants))
        return rows

    def factory(request):
        calls.append(("factory", request))
        return stage

    return SimpleNamespace(candidate=candidate, endpoint=endpoint, action=action, grant=grant,
                           rows=rows, calls=calls, stage=stage, factory=factory,
                           executor=StageExecutor({endpoint.id: factory}))


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "different"), ("endpoint_id", "different"),
    ("round_id", 0), ("round_id", -1), ("round_id", True), ("round_id", 1.0),
    ("replicate", -1), ("replicate", 1), ("replicate", True), ("replicate", 0.0),
    ("id", ""), ("id", " "), ("id", "."), ("id", ".."), ("id", "../outside"),
    ("id", "/outside"), ("id", "nested/action"), ("id", "nested\\action"),
    ("id", "action\0suffix"), ("id", 3),
])
def test_invalid_execution_identity_never_reaches_factory(execution, field, value):
    # Even malformed objects must fail closed at the actual dispatch boundary.
    object.__setattr__(execution.action, field, value)
    with pytest.raises(ValueError):
        execution.executor(execution.action, execution.candidate, execution.endpoint, execution.grant)
    assert execution.calls == []


@pytest.mark.parametrize("missing", ["handoff_endpoint", "grant"])
def test_missing_authorization_contract_never_reaches_factory(execution, missing):
    endpoint = (replace(execution.endpoint, requires_handoff=False)
                if missing == "handoff_endpoint" else execution.endpoint)
    grant = None if missing == "grant" else execution.grant
    with pytest.raises(ValueError, match="authorization"):
        execution.executor(execution.action, execution.candidate, endpoint, grant)
    assert execution.calls == []


@pytest.mark.parametrize("field,value", [("parent_id", "other"), ("parent_smiles", "CCN")])
def test_mutable_handoff_cannot_change_candidate_identity_even_with_new_grant(execution, field, value):
    execution.candidate.handoff[field] = value
    grant = authorize([execution.candidate.handoff]).grants[execution.candidate.handoff["parent_id"]]
    with pytest.raises(ValueError, match="identity and handoff"):
        execution.executor(execution.action, execution.candidate, execution.endpoint, grant)
    assert execution.calls == []


@pytest.mark.parametrize("invalid", ["expired", "tampered", "changed_row", "different_subject"])
def test_authorization_is_verified_before_factory(execution, invalid):
    grant = execution.grant
    if invalid == "expired":
        grant = authorize([execution.candidate.handoff], lifetime_hours=-1).grants[execution.candidate.id]
    elif invalid == "tampered":
        grant = replace(grant, signature="invalid")
    elif invalid == "changed_row":
        execution.candidate.handoff["coordinates"] = "changed after preflight"
    else:
        grant = authorize([{**execution.candidate.handoff, "parent_id": "other"}]).grants["other"]
    with pytest.raises(NotAuthorized):
        execution.executor(execution.action, execution.candidate, execution.endpoint, grant)
    assert execution.calls == []


def test_factory_cannot_invalidate_authorized_handoff_before_dispatch(execution):
    def factory(action):
        execution.calls.append(("factory", action))
        execution.candidate.handoff["coordinates"] = "changed during setup"
        return execution.stage

    executor = StageExecutor({execution.endpoint.id: factory})
    with pytest.raises(NotAuthorized, match="different version"):
        executor(execution.action, execution.candidate, execution.endpoint, execution.grant)
    assert [call[0] for call in execution.calls] == ["factory"]


@pytest.mark.parametrize("replicate", [0, 1])
def test_legacy_prism_cannot_claim_independent_replicates(execution, monkeypatch, replicate):
    endpoint = replace(execution.endpoint, max_replicates=2)
    action = replace(execution.action, replicate=replicate)
    # Construct a real PrismStage instance without creating an environment or workspace.
    stage = object.__new__(PrismStage)
    monkeypatch.setattr(PrismStage, "__call__", lambda *args, **kwargs: pytest.fail("scientific stage ran"))
    executor = StageExecutor({endpoint.id: lambda request: stage})
    with pytest.raises(ValueError, match="independent replicas"):
        executor(action, execution.candidate, endpoint, execution.grant)


def test_action_scoped_factory_can_execute_a_declared_replica(execution):
    endpoint = replace(execution.endpoint, max_replicates=2)
    action = replace(execution.action, replicate=1)
    result = execution.executor(action, execution.candidate, endpoint, execution.grant)
    assert result.status == "ok" and result.value == -2.0
    assert execution.calls[0] == ("factory", action)
    assert execution.calls[1][1:] == ([execution.candidate.handoff], {"m1": 0.0}, {"m1": execution.grant})
    assert result.cost == endpoint.cost
    assert result.provenance["cost_basis"] == "endpoint quote; not measured"


@pytest.mark.parametrize("cost", [True, False, "1.5", None, -1, float("nan"), float("inf")])
def test_measured_cost_must_be_a_finite_nonnegative_real(execution, cost):
    executor = StageExecutor({execution.endpoint.id: execution.factory}, actual_cost=lambda action, rows: cost)
    with pytest.raises(ValueError, match="actual cost"):
        executor(execution.action, execution.candidate, execution.endpoint, execution.grant)
    assert [call[0] for call in execution.calls] == ["factory", "stage"]


@pytest.mark.parametrize("cost", [0, 1.25, 9])
def test_valid_measured_cost_is_preserved_and_identified(execution, cost):
    observed = []

    def actual_cost(action, rows):
        observed.append((action, rows))
        return cost

    executor = StageExecutor({execution.endpoint.id: execution.factory}, actual_cost=actual_cost)
    result = executor(execution.action, execution.candidate, execution.endpoint, execution.grant)
    assert result.cost == cost and result.provenance["cost_basis"] == "measured"
    assert observed == [(execution.action, execution.rows)]


def test_falsey_cost_reader_is_still_an_explicit_measured_cost_reader(execution):
    class CostReader:
        def __bool__(self):
            return False

        def __call__(self, action, rows):
            return 1.25

    executor = StageExecutor({execution.endpoint.id: execution.factory}, actual_cost=CostReader())
    result = executor(execution.action, execution.candidate, execution.endpoint, execution.grant)
    assert result.cost == 1.25 and result.provenance["cost_basis"] == "measured"


@pytest.mark.parametrize("cardinality", [0, 2, "wrong_subject"])
def test_missing_duplicate_or_wrong_labels_remain_invalid_and_costed(execution, cardinality):
    if cardinality == "wrong_subject":
        execution.rows[0].parent_id = "other"
    else:
        execution.rows[:] = execution.rows * cardinality
    result = execution.executor(execution.action, execution.candidate, execution.endpoint, execution.grant)
    assert result.status == "invalid" and result.value is None
    assert result.cost == execution.endpoint.cost and result.candidate_id == execution.candidate.id


@pytest.mark.parametrize("cost", [True, "0.0", None])
def test_bad_cost_reader_is_persisted_as_a_failure_charged_at_reservation(execution, tmp_path, monkeypatch, cost):
    store = CampaignStore(tmp_path / "campaign.sqlite")
    store.configure(CampaignSpec("objective", 30, "quotes", "fake/1", batch_size=1), [execution.endpoint])
    history = Candidate("history", "CCN", (2.0,))
    store.add_candidates([execution.candidate, history])
    # Prior measured costs raise the live reservation above the static endpoint quote.
    store.import_evaluation(Evaluation(history.id, "objective", -1.0, "u", 7.0), source_id="history")
    cutoff = store.snapshot()["event_cutoff"]
    choice = Choice(execution.candidate.id, "objective", 1.0, {"planning_event_cutoff": cutoff})
    model = SimpleNamespace(snapshot=lambda: {}, training_action_ids=[])
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text("fake receptor")
    executor = StageExecutor({"objective": execution.factory}, actual_cost=lambda action, rows: cost)
    run = ActiveCampaign(store, executor, receptor_path=receptor)
    monkeypatch.setattr(run, "plan", lambda: (model, [choice], "ready"))
    monkeypatch.setattr("etalon.active.runner.check_record", lambda *args, **kwargs: ())
    outcome = run.run_round()
    action = next(action for action in store.actions() if action["round_id"])
    result = next(row["result"] for row in store.observations() if row["action_id"] == action["id"])
    assert len(outcome["actions"]) == 1 and action["reserved_cost"] == 7.0
    assert action["status"] == "failed" and result["cost"] == 7.0
    assert result["provenance"]["cost_basis"] == "reservation; actual cost unavailable"
    assert "actual cost" in result["provenance"]["error"]
    assert store.balance()["spent"] == 14.0 and store.balance()["reserved"] == 0.0


def test_molecular_adapter_keeps_full_library_alignment_without_scientific_tools(monkeypatch):
    from etalon.boundary import infra
    from etalon.learn import calibrate, surrogate

    calls = []
    monkeypatch.setattr(infra, "load", lambda name: calls.append(("load", name)))

    def featurize(smiles):
        calls.append(("features", smiles))
        return SimpleNamespace(matrix=[(2.0,), (3.0,)], unparsed=(1,),
                               align=lambda identifiers: [identifiers[0], identifiers[2]])

    def scaffolds(smiles):
        calls.append(("scaffolds", smiles))
        return ["acyclic:CCO", "acyclic:CCN"]

    monkeypatch.setattr(surrogate, "featurize", featurize)
    monkeypatch.setattr(calibrate, "scaffold_groups", scaffolds)
    pool = {"c": "CCN", "bad": "invalid", "a": "CCO"}
    candidates, rejected = molecular_candidates(pool, cheap={"a": 0.0}, source="full library")
    assert rejected == ["bad"] and [candidate.id for candidate in candidates] == ["a", "c"]
    assert [candidate.features for candidate in candidates] == [(2.0,), (3.0,)]
    assert [candidate.scaffold for candidate in candidates] == ["acyclic:CCO", "acyclic:CCN"]
    assert candidates[0].cheap_value == 0.0 and candidates[1].cheap_value is None
    assert all(candidate.source == "full library" for candidate in candidates)
    assert calls == [("load", "molcascade"), ("features", ["CCO", "invalid", "CCN"]),
                     ("scaffolds", ["CCO", "CCN"])]
    reordered, rejected_again = molecular_candidates(dict(reversed(list(pool.items()))),
                                                     cheap={"a": 0.0}, source="full library")
    assert reordered == candidates and rejected_again == rejected
