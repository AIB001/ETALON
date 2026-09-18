"""Finite learned protocol search stays inside explicit authority and acquired evidence."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from etalon.active import (
    ActiveCampaign,
    CampaignSpec,
    CampaignStore,
    Candidate,
    Evaluation,
    StateError,
)
from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout
from etalon.active.mutations import DesignSpace
from etalon.active.proposer import ProtocolSearch
from etalon.active.protocols import ProtocolRegistry, TrialPolicy
from etalon.active.schema import Endpoint, canonical, digest
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose

PANEL = ("m0", "m1", "m2", "m3")


def _measure(store, endpoint_id, candidate_id, executor):
    endpoint, candidate = store.configuration()[1][endpoint_id], store.candidates()[candidate_id]
    round_id = store.start_round({"scope": "explicit test panel acquisition"})
    action = store.reserve(round_id, candidate_id, endpoint_id, {})
    store.start_action(action.id)
    result = executor(action, candidate, endpoint, None)
    store.resolve(action.id, result)
    store.finish_round(round_id, {"actions": [action.id]})
    return result


@pytest.fixture(scope="module")
def acquired_panel(tmp_path_factory):
    """Acquire four real labels once; each test receives an independent SQLite copy."""
    workspace = tmp_path_factory.mktemp("protocol-search-acquired-panel")
    config = compose("search-base", [{
        "id": "measure", "title": "CPU molecular properties", "mode": "serial",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0",
                               settings={"batch_size": 128, "include_sa_score": False})],
    }])
    recipe = CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))
    endpoint = recipe.endpoint("original", target="demo", quantity="molecular_weight", units="Da", cost=1)
    store = CampaignStore(workspace / "campaign.sqlite")
    store.configure(CampaignSpec("original", 80, "CPU_quotes", "test/1", batch_size=1), [endpoint])
    smiles = ["CCO", "CCN", "c1ccccc1", "CC(=O)O", "CCCO", "CCCC", "CC(C)O", "CNC"]
    store.add_candidates([Candidate(f"m{i}", text, (float(i), float(i % 3)))
                          for i, text in enumerate(smiles)])
    registry = ProtocolRegistry(store)
    registry.bind_seed("original", recipe, rationale="reviewed local CPU measurement")
    edits = (
        {"op": "insert_criterion", "tier_id": "measure", "before": "properties",
         "criterion": component("sa", "synthesis.rdkit_sa_score@0.1.0")},
        {"op": "set_setting", "criterion_id": "properties", "path": "/batch_size",
         "expected": 128, "value": 64},
    )
    space = registry.bind_space(DesignSpace(edits, max_edits=2), rationale="two exact reviewed operators")
    executor = CascadeExecutor.from_journal(workspace / "calculations", store)
    for identifier in PANEL:
        result = _measure(store, "original", identifier, executor)
        assert result.status == "ok", result.as_dict()
    assert len(store.observations(admitted_only=True)) == 4
    return store.path, space


@pytest.fixture
def search_case(tmp_path, acquired_panel):
    source_path, space = acquired_panel
    path = tmp_path / "campaign.sqlite"
    with sqlite3.connect(source_path) as source, sqlite3.connect(path) as destination:
        source.backup(destination)
    store = CampaignStore(path)
    return {"store": store, "registry": ProtocolRegistry(store), "search": ProtocolSearch(store),
            "space": space, "workspace": tmp_path / "calculations"}


def _catalogue(case, **kwargs):
    return case["search"].catalogue("original", case["space"], **kwargs)


def _authorize(case, **overrides):
    options = {"panel_ids": PANEL, "quotes": {v["id"]: 2.0 for v in _catalogue(case)["variants"]},
               "budget": 16.0, "max_trials": 2, "rationale": "reviewed finite CPU search"}
    options.update(overrides)
    return case["search"].authorize("original", case["space"], **options)


def _propose(case, search_id):
    plan = case["search"].plan(search_id)
    assert plan["selected"] is not None, plan
    return case["search"].propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"],
                                       rationale="select one already authorized variant")


def _start(case, search_id):
    proposal_id = _propose(case, search_id)
    assert case["registry"].validate(proposal_id)["ok"]
    case["registry"].start_trial(proposal_id, TrialPolicy(8, 4, 3, 3, 0.25),
                                 rationale="activate exactly the authorized panel trial")
    return proposal_id


def _state(store):
    with store.connection() as db:
        metadata = [tuple(row) for row in db.execute("SELECT key,body FROM metadata ORDER BY key")]
    return canonical({"metadata": metadata, "events": store.events(), "actions": store.actions(),
                      "observations": store.observations(), "rounds": store.rounds(), "balance": store.balance()})


def test_catalogue_and_plan_are_read_only_and_do_not_execute(search_case, monkeypatch):
    case = search_case
    monkeypatch.setattr(Screen, "run", lambda *_a, **_k: pytest.fail("read-only planning executed science"))
    before = _state(case["store"])
    catalog = _catalogue(case)
    assert _state(case["store"]) == before
    assert len(catalog["variants"]) == 3 and not catalog["rejected"]
    assert sorted(len(v["edits"]) for v in catalog["variants"]) == [1, 1, 2]
    assert len({v["protocol_id"] for v in catalog["variants"]}) == 3
    assert all({"id", "edits", "recipe", "graph", "protocol_id"} <= set(v) for v in catalog["variants"])
    repeated = _catalogue(case)
    assert repeated["variants"] == catalog["variants"] and repeated["rejected"] == catalog["rejected"]
    search_id = _authorize(case)
    before = _state(case["store"])
    plan = case["search"].plan(search_id)
    assert plan["training_size"] == 0
    assert plan["selected"] in {v["id"] for v in catalog["variants"]}
    assert case["search"].plan(search_id) == plan
    assert _state(case["store"]) == before


def test_catalogue_refuses_combinatorial_limit_before_attempting_mutations(search_case, monkeypatch):
    import etalon.active.proposer as proposer

    case = search_case
    if hasattr(proposer, "mutate_recipe"):
        monkeypatch.setattr(proposer, "mutate_recipe", lambda *_a, **_k: pytest.fail("oversized catalogue mutated recipes"))
    before = _state(case["store"])
    with pytest.raises(ValueError):
        _catalogue(case, max_variants=2)
    assert _state(case["store"]) == before


def test_catalogue_reports_invalid_variants_without_registering_them(search_case):
    case = search_case
    edit = {"op": "set_setting", "criterion_id": "properties", "path": "/batch_size",
            "expected": 999, "value": 64}
    bad_space = case["registry"].bind_space(DesignSpace((edit,), 1), rationale="inspect an inapplicable reviewed edit")
    before = _state(case["store"])
    catalog = case["search"].catalogue("original", bad_space)
    assert catalog["variants"] == [] and len(catalog["rejected"]) == 1
    assert {"id", "edits", "error"} <= set(catalog["rejected"][0])
    assert _state(case["store"]) == before


@pytest.mark.parametrize("quotes", [{}, {"unreviewed-variant": 2.0}])
def test_authorization_requires_a_nonempty_explicit_catalogue_subset(search_case, quotes):
    before = _state(search_case["store"])
    with pytest.raises(ValueError):
        _authorize(search_case, quotes=quotes)
    assert _state(search_case["store"]) == before


@pytest.mark.parametrize("quote", [0, -1, float("nan"), float("inf")])
def test_authorization_rejects_invalid_quotes(search_case, quote):
    variant = _catalogue(search_case)["variants"][0]["id"]
    with pytest.raises(ValueError):
        _authorize(search_case, quotes={variant: quote})


@pytest.mark.parametrize("panel", [("m0", "m1", "m2"), ("m0", "m0", "m1", "m2"),
                                   ("m0", "m1", "m2", "m4"), ("m0", "m1", "m2", "absent")])
def test_authorization_requires_unique_complete_acquired_panel(search_case, panel):
    with pytest.raises((ValueError, StateError)):
        _authorize(search_case, panel_ids=panel)


def test_imported_objective_labels_cannot_create_an_audit_panel(search_case):
    case = search_case
    panel = ("m4", "m5", "m6", "m7")
    for i, identifier in enumerate(panel):
        case["store"].import_evaluation(Evaluation(identifier, "original", 30.0 + i, "Da", 0),
                                        source_id=f"historical-{identifier}")
    with pytest.raises((ValueError, StateError)):
        _authorize(case, panel_ids=panel)


def test_constant_objective_panel_is_not_a_predictive_utility_experiment(search_case):
    case = search_case
    panel = tuple(f"constant-{i}" for i in range(4))
    case["store"].add_candidates([Candidate(identifier, "CCO", (float(i), 0.0))
                                  for i, identifier in enumerate(panel)])
    executor = CascadeExecutor.from_journal(case["workspace"], case["store"])
    for identifier in panel:
        assert _measure(case["store"], "original", identifier, executor).status == "ok"
    with pytest.raises(ValueError):
        _authorize(case, panel_ids=panel)


@pytest.mark.parametrize("override", [{"budget": 7}, {"budget": 100}, {"max_trials": 0}])
def test_search_authorization_obeys_budget_and_trial_count_caps(search_case, override):
    with pytest.raises((ValueError, StateError)):
        _authorize(search_case, **override)


def test_authorized_subset_does_not_silently_expand_to_all_edits(search_case):
    case = search_case
    variant = _catalogue(case)["variants"][0]["id"]
    search_id = _authorize(case, quotes={variant: 2.0}, budget=8, max_trials=1)
    plan = case["search"].plan(search_id)
    assert {v["id"] for v in plan["ranking"]} == {variant}
    assert plan["selected"] == variant


def test_real_panel_trial_restarts_scores_once_and_preserves_original_labels(search_case):
    case = search_case
    original = case["store"].observations()
    search_id = _authorize(case)
    proposal_id = _start(case, search_id)
    assert case["search"].plan(search_id)["selected"] is None
    endpoint_id = case["registry"].get(proposal_id)["body"]["endpoint"]["id"]
    reopened = CampaignStore(case["store"].path)
    search = ProtocolSearch(reopened)
    audit = search.audit_plan(search_id)
    assert audit["endpoint_id"] == endpoint_id and audit["proposal_id"] == proposal_id
    assert set(audit["candidate_ids"]) == set(PANEL)
    executor = CascadeExecutor.from_journal(case["workspace"], reopened)
    search.run_audit(search_id, executor, max_actions=4)
    rows = [row for row in reopened.observations() if row["result"]["endpoint_id"] == endpoint_id]
    assert len(rows) == 4 and all(row["admitted"] for row in rows), rows
    assert reopened.observations()[:4] == original
    registry = ProtocolRegistry(reopened)
    with pytest.raises(StateError):
        registry.promote(proposal_id, rationale="complete panel is not yet a frozen reward")
    reward = search.score(search_id)
    before = _state(reopened)
    assert ProtocolSearch(CampaignStore(reopened.path)).score(search_id) == reward
    assert _state(reopened) == before
    record = search.get(search_id)
    assert len(record["experiments"]) == 1 and record["experiments"][0]["reward"] == reward
    assert search.plan(search_id)["training_size"] == 1
    assert reopened.balance()["spent"] == 12
    audit_budget = search.plan(search_id)["budget"]
    registry.promote(proposal_id, rationale="operational criteria and frozen audit reward reviewed")
    promoted = CascadeExecutor.from_journal(case["workspace"], reopened)
    _measure(reopened, endpoint_id, "m4", promoted)
    assert search.score(search_id) == reward  # Later promoted observations never rewrite the trial.
    assert search.plan(search_id)["budget"] == audit_budget
    assert reopened.balance()["spent"] == 14


def test_partial_panel_cannot_score_or_promote_and_is_resumable(search_case):
    case = search_case
    search_id = _authorize(case)
    proposal_id = _start(case, search_id)
    executor = CascadeExecutor.from_journal(case["workspace"], case["store"])
    case["search"].run_audit(search_id, executor, max_actions=1)
    assert len(case["search"].audit_plan(search_id)["candidate_ids"]) == 3
    with pytest.raises(StateError):
        case["search"].score(search_id)
    with pytest.raises(StateError):
        case["registry"].promote(proposal_id, rationale="too soon")
    before = len(case["store"].actions())
    case["search"].run_audit(search_id, executor, max_actions=3)
    assert len(case["store"].actions()) == before + 3
    case["search"].score(search_id)


def test_all_failed_attempts_are_complete_evidence_not_missing_panel_members(search_case):
    case = search_case
    search_id = _authorize(case)
    proposal_id = _start(case, search_id)

    def failed(action, candidate, endpoint, grant):
        return Evaluation(candidate.id, endpoint.id, None, endpoint.units, endpoint.cost, status="failed",
                          provenance={"scope": "synthetic executor-failure unit fixture"})

    case["search"].run_audit(search_id, failed, max_actions=4)
    reward = case["search"].score(search_id)
    assert reward["skill"] == -1.0
    assert reward["failure_count"] == reward["panel_size"] == 4
    assert case["search"].get(search_id)["experiments"][0]["reward"] == reward
    assert case["search"].plan(search_id)["training_size"] == 1
    report = case["registry"].report(proposal_id)
    assert report["runtime_results"] == report["failed_results"] == 4
    assert not report["rollout_criteria_met"]
    assert case["store"].balance()["spent"] == 12


def test_direct_reserve_cannot_bypass_audit_candidate_panel(search_case):
    case = search_case
    search_id = _authorize(case)
    proposal_id = _start(case, search_id)
    endpoint_id = case["registry"].get(proposal_id)["body"]["endpoint"]["id"]
    round_id = case["store"].start_round({})
    with pytest.raises(StateError):
        case["store"].reserve(round_id, "m4", endpoint_id, {})
    assert case["store"].balance()["reserved"] == 0
    case["store"].finish_round(round_id, {"actions": []})


def test_inner_campaign_planner_preserves_the_outer_trial_panel(search_case):
    case = search_case
    search_id = _authorize(case)
    proposal_id = _start(case, search_id)
    endpoint_id = case["registry"].get(proposal_id)["body"]["endpoint"]["id"]
    before = _state(case["store"])
    _, choices, _ = ActiveCampaign(case["store"]).plan()
    audit_choices = [choice for choice in choices if choice.endpoint_id == endpoint_id]
    assert audit_choices
    assert all(choice.candidate_id in PANEL for choice in audit_choices)
    assert _state(case["store"]) == before


def test_preauthorized_trial_policy_cannot_be_replaced(search_case):
    case = search_case
    search_id = _authorize(case)
    proposal_id = _propose(case, search_id)
    case["registry"].validate(proposal_id)
    for changed in (TrialPolicy(10, 5), TrialPolicy(8, 4, 1, 0), TrialPolicy(8, 4, 3, 3, 1)):
        with pytest.raises((StateError, ValueError)):
            case["registry"].start_trial(proposal_id, changed, rationale="post-hoc relaxation")
    assert case["registry"].get(proposal_id)["status"] == "validated"


def test_stale_snapshot_and_running_round_cannot_start_another_proposal(search_case):
    case = search_case
    search_id = _authorize(case)
    plan = case["search"].plan(search_id)
    round_id = case["store"].start_round({})
    with pytest.raises(StateError):
        case["search"].propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"], rationale="stale")
    case["store"].finish_round(round_id, {"actions": []})
    executor = CascadeExecutor.from_journal(case["workspace"], case["store"])
    _measure(case["store"], "original", "m4", executor)
    with pytest.raises(StateError):
        case["search"].propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"], rationale="stale evidence")
    assert case["search"].get(search_id)["experiments"] == []


def test_concurrent_proposals_cannot_allocate_two_variants(search_case):
    case = search_case
    search_id = _authorize(case)
    plan = case["search"].plan(search_id)

    def propose_once(_):
        search = ProtocolSearch(CampaignStore(case["store"].path))
        try:
            return search.propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"], rationale="concurrency fixture")
        except StateError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(propose_once, range(2)))
    assert len({result for result in results if result is not None}) == 1
    assert len(case["search"].get(search_id)["experiments"]) == 1
    assert len(case["registry"].proposals()) == 1


def test_search_trial_budget_and_count_remain_exhausted_after_restart(search_case):
    case = search_case
    search_id = _authorize(case, budget=8, max_trials=1)
    _start(case, search_id)
    executor = CascadeExecutor.from_journal(case["workspace"], case["store"])
    case["search"].run_audit(search_id, executor, max_actions=4)
    case["search"].score(search_id)
    resumed = ProtocolSearch(CampaignStore(case["store"].path))
    assert resumed.plan(search_id)["selected"] is None
    assert len(resumed.get(search_id)["experiments"]) == 1


def test_free_failed_queries_do_not_expand_total_quoted_trial_authority(search_case):
    case = search_case
    search_id = _authorize(case, budget=8, max_trials=2)
    _start(case, search_id)

    def blocked(action, candidate, endpoint, grant):
        return Evaluation(candidate.id, endpoint.id, None, endpoint.units, 0, status="blocked")

    case["search"].run_audit(search_id, blocked, max_actions=4)
    case["search"].score(search_id)
    assert case["store"].balance()["spent"] == 4
    assert case["search"].plan(search_id)["selected"] is None


def test_closing_search_prevents_new_proposals_and_unfinished_trial_dispatch(search_case):
    case = search_case
    search_id = _authorize(case)
    proposal_id = _start(case, search_id)
    endpoint_id = case["registry"].get(proposal_id)["body"]["endpoint"]["id"]
    case["search"].close(search_id, rationale="stop further experimental protocol spending")
    assert case["search"].plan(search_id)["selected"] is None
    assert case["search"].get(search_id)["status"] == "closed"
    round_id = case["store"].start_round({})
    with pytest.raises(StateError):
        case["store"].reserve(round_id, "m0", endpoint_id, {})
    case["store"].finish_round(round_id, {"actions": []})
    assert case["store"].balance()["spent"] == 4


def test_read_only_journal_supports_ranking_but_no_search_mutation(search_case):
    case = search_case
    search_id = _authorize(case)
    search = ProtocolSearch(CampaignStore(case["store"].path, read_only=True))
    before = _state(case["store"])
    assert search.get(search_id) == case["search"].get(search_id)
    plan = search.plan(search_id)
    assert plan == case["search"].plan(search_id)
    with pytest.raises(StateError):
        search.propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"], rationale="read-only mutation")
    with pytest.raises(StateError):
        search.close(search_id, rationale="read-only mutation")
    assert _state(case["store"]) == before


def test_event_failure_rolls_back_search_authorization(search_case, monkeypatch):
    case = search_case
    before = _state(case["store"])

    def fail_event(db, kind, body):
        raise RuntimeError("injected audit event failure")

    monkeypatch.setattr(CampaignStore, "_event", staticmethod(fail_event))
    with pytest.raises(RuntimeError, match="injected audit event failure"):
        _authorize(case)
    assert _state(case["store"]) == before


def test_event_failure_rolls_back_proposal_intent(search_case, monkeypatch):
    case = search_case
    search_id = _authorize(case)
    plan = case["search"].plan(search_id)
    before = _state(case["store"])

    def fail_event(db, kind, body):
        raise RuntimeError("injected intent event failure")

    monkeypatch.setattr(CampaignStore, "_event", staticmethod(fail_event))
    with pytest.raises(RuntimeError, match="injected intent event failure"):
        case["search"].propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"], rationale="rollback fixture")
    assert _state(case["store"]) == before


def test_durable_proposal_intent_resumes_without_alias_or_second_allocation(search_case, monkeypatch):
    case = search_case
    search_id = _authorize(case)
    plan = case["search"].plan(search_id)
    before_actions = case["store"].actions()

    def interrupt_materialization(*args, **kwargs):
        raise RuntimeError("simulated interruption after durable intent")

    with monkeypatch.context() as context:
        context.setattr(ProtocolRegistry, "propose", interrupt_materialization)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            case["search"].propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"], rationale="crash fixture")
    assert len(case["search"].get(search_id)["experiments"]) == 1
    assert case["registry"].proposals() == []
    assert case["store"].actions() == before_actions
    restarted = ProtocolSearch(CampaignStore(case["store"].path))
    proposal_id = restarted.resume_proposal(search_id)
    assert proposal_id == restarted.resume_proposal(search_id)
    assert len(restarted.get(search_id)["experiments"]) == 1
    assert restarted.get(search_id)["experiments"][0]["proposal_id"] == proposal_id
    assert len(case["registry"].proposals()) == 1
    assert case["registry"].get(proposal_id)["status"] == "proposed"


def test_registry_api_cannot_change_a_durable_search_intents_quote(search_case, monkeypatch):
    import etalon.active.proposer as proposer

    case = search_case
    search_id = _authorize(case)
    plan = case["search"].plan(search_id)

    def interrupt_materialization(*args, **kwargs):
        raise RuntimeError("interrupted materialization")

    with monkeypatch.context() as context:
        context.setattr(ProtocolRegistry, "propose", interrupt_materialization)
        with pytest.raises(RuntimeError):
            case["search"].propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"], rationale="fixed intent reason")
    record = case["search"].get(search_id)
    intent = record["experiments"][0]
    variant = next(row for row in record["body"]["variants"] if row["id"] == intent["variant_id"])
    before = _state(case["store"])
    with pytest.raises(StateError):
        case["registry"].propose("original", intent["endpoint_id"], space_id=case["space"],
                                 edits=variant["edits"], cost=variant["quote"] + 1,
                                 rationale=intent["rationale"], proposed_by=proposer.VERSION)
    assert _state(case["store"]) == before
    assert case["search"].resume_proposal(search_id)


def test_event_failure_rolls_back_frozen_reward_without_reexecuting_trial(search_case, monkeypatch):
    case = search_case
    search_id = _authorize(case)
    _start(case, search_id)

    def failed(action, candidate, endpoint, grant):
        return Evaluation(candidate.id, endpoint.id, None, endpoint.units, endpoint.cost, status="failed")

    case["search"].run_audit(search_id, failed, max_actions=4)
    before = _state(case["store"])

    def fail_event(db, kind, body):
        raise RuntimeError("injected reward event failure")

    with monkeypatch.context() as context:
        context.setattr(CampaignStore, "_event", staticmethod(fail_event))
        with pytest.raises(RuntimeError, match="injected reward event failure"):
            case["search"].score(search_id)
    assert _state(case["store"]) == before
    case["search"].score(search_id)
    assert len(case["store"].actions()) == 8


def test_imported_trial_readings_cannot_replace_actual_panel_attempts(search_case):
    case = search_case
    search_id = _authorize(case)
    proposal_id = _start(case, search_id)
    endpoint_id = case["registry"].get(proposal_id)["body"]["endpoint"]["id"]
    for i, identifier in enumerate(PANEL):
        case["store"].import_evaluation(Evaluation(identifier, endpoint_id, 40.0 + i, "Da", 0),
                                        source_id=f"not-an-audit-run-{identifier}")
    with pytest.raises(StateError):
        case["search"].score(search_id)
    assert case["search"].get(search_id)["experiments"][0]["reward"] is None


def test_existing_registered_variant_cannot_be_reauthorized_under_new_search(search_case):
    case = search_case
    catalog = _catalogue(case)
    selected = catalog["variants"][0]
    search_id = _authorize(case, quotes={selected["id"]: 2.0}, budget=8, max_trials=1)
    _start(case, search_id)
    with pytest.raises((ValueError, StateError)):
        _authorize(case, quotes={selected["id"]: 3.0}, budget=12, max_trials=1)


def test_protocol_alias_cannot_escape_search_panel_or_budget(search_case):
    case = search_case
    search_id = _authorize(case)
    proposal_id = _start(case, search_id)
    endpoint_id = case["registry"].get(proposal_id)["body"]["endpoint"]["id"]
    endpoint = case["store"].configuration()[1][endpoint_id]
    with pytest.raises(StateError):
        case["store"].register_endpoints([replace(endpoint, id="uncontrolled-alias")], rationale="must reject alias")


@pytest.mark.parametrize("alias", [False, True], ids=["selected-id", "protocol-alias"])
@pytest.mark.parametrize("materialized", [False, True], ids=["durable-intent", "pending-proposal"])
def test_selected_search_protocol_cannot_register_before_trial(search_case, monkeypatch, alias, materialized):
    case = search_case
    search_id = _authorize(case)
    if materialized:
        proposal_id = _propose(case, search_id)
        endpoint = Endpoint(**case["registry"].get(proposal_id)["body"]["endpoint"])
    else:
        plan = case["search"].plan(search_id)

        def interrupt_materialization(*args, **kwargs):
            raise RuntimeError("interrupted before selected protocol materialization")

        with monkeypatch.context() as context:
            context.setattr(ProtocolRegistry, "propose", interrupt_materialization)
            with pytest.raises(RuntimeError, match="interrupted before"):
                case["search"].propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"],
                                             rationale="preserve controlled durable intent")
        record = case["search"].get(search_id)
        intent = record["experiments"][0]
        variant = next(v for v in record["body"]["variants"] if v["id"] == intent["variant_id"])
        endpoint = replace(case["store"].configuration()[1]["original"], id=intent["endpoint_id"],
                           protocol=variant["protocol_id"], cost=variant["quote"])
        assert intent["proposal_id"] is None and case["registry"].proposals() == []
    if alias:
        endpoint = replace(endpoint, id="pending-protocol-legacy-alias")
    before = _state(case["store"])
    with pytest.raises(StateError):
        case["store"].register_endpoints([endpoint], rationale="cannot bypass selected search authority")
    assert _state(case["store"]) == before
    assert endpoint.id not in case["store"].configuration()[1]


def test_unselected_catalogue_protocol_remains_available_for_explicit_legacy_registration(search_case):
    case = search_case
    search_id = _authorize(case)
    _propose(case, search_id)
    record = case["search"].get(search_id)
    selected = record["experiments"][0]["variant_id"]
    variant = next(v for v in record["body"]["variants"] if v["id"] != selected)
    endpoint = replace(case["store"].configuration()[1]["original"], id="unselected-explicit-legacy",
                       protocol=variant["protocol_id"], cost=variant["quote"])
    actions = case["store"].actions()
    case["store"].register_endpoints([endpoint], rationale="explicitly authorize a protocol not selected by search")
    assert case["store"].configuration()[1][endpoint.id] == endpoint
    assert case["store"].actions() == actions
    assert case["search"].get(search_id) == record


@pytest.mark.parametrize("preexisting", [False, True], ids=["new-alias-proposal", "previously-validated-alias"])
def test_selected_search_protocol_cannot_escape_through_an_independent_registry_trial(search_case, preexisting):
    case = search_case
    variant = _catalogue(case)["variants"][0]
    arguments = {"space_id": case["space"], "edits": variant["edits"], "cost": 2.0,
                 "rationale": "independent proposal for the same protocol", "proposed_by": "explicit-reviewer"}
    if preexisting:
        alias = case["registry"].propose("original", "independent-registry-alias", **arguments)
        assert case["registry"].validate(alias)["ok"]
    search_id = _authorize(case, quotes={variant["id"]: 2.0}, budget=8, max_trials=1)
    _propose(case, search_id)
    before = _state(case["store"])
    with pytest.raises(StateError):
        if preexisting:
            case["registry"].start_trial(alias, TrialPolicy(8, 4, 3, 3, 0.25),
                                         rationale="cannot activate an alias after search owns this protocol")
        else:
            case["registry"].propose("original", "independent-registry-alias", **arguments)
    assert _state(case["store"]) == before
    assert "independent-registry-alias" not in case["store"].configuration()[1]


@pytest.mark.parametrize("corruption", ["missing-control", "wrong-proposal", "wrong-search", "missing-proposal-id"])
def test_complete_audit_cannot_freeze_reward_without_matching_search_control(search_case, corruption):
    case = search_case
    search_id = _authorize(case)
    proposal_id = _start(case, search_id)

    def failed(action, candidate, endpoint, grant):
        return Evaluation(candidate.id, endpoint.id, None, endpoint.units, endpoint.cost, status="failed")

    case["search"].run_audit(search_id, failed, max_actions=4)
    assert case["search"].audit_plan(search_id)["candidate_ids"] == []
    record = case["search"].get(search_id)
    intent = record["experiments"][0]
    key = ("protocol:search:" + search_id if corruption == "missing-proposal-id"
           else "protocol:endpoint:" + intent["endpoint_id"])
    # Fault injection simulates a damaged/legacy journal, never a supported mutation API.
    with case["store"].connection(write=True) as db:
        original_body = db.execute("SELECT body FROM metadata WHERE key=?", (key,)).fetchone()[0]
        if corruption == "missing-control":
            db.execute("DELETE FROM metadata WHERE key=?", (key,))
        elif corruption == "missing-proposal-id":
            intent["proposal_id"] = None
            db.execute("UPDATE metadata SET body=? WHERE key=?", (canonical(record), key))
        else:
            control = {"proposal_id": proposal_id, "search_id": search_id, "variant_id": intent["variant_id"]}
            control["proposal_id" if corruption == "wrong-proposal" else "search_id"] = "unrelated-identity"
            db.execute("UPDATE metadata SET body=? WHERE key=?", (canonical(control), key))
    before = _state(case["store"])
    with pytest.raises(StateError):
        case["search"].score(search_id)
    assert _state(case["store"]) == before
    assert case["search"].get(search_id)["experiments"][0]["reward"] is None
    with case["store"].connection(write=True) as db:
        db.execute("INSERT OR REPLACE INTO metadata(key,body) VALUES (?,?)", (key, original_body))
    assert case["search"].score(search_id)["skill"] == -1


def test_audit_panel_size_limit_rejects_before_catalogue_or_execution(search_case, monkeypatch):
    case = search_case
    quotes = {v["id"]: 2.0 for v in _catalogue(case)["variants"]}
    before = _state(case["store"])
    monkeypatch.setattr(ProtocolSearch, "catalogue", lambda *_a, **_k: pytest.fail("oversized panel was enumerated"))
    with pytest.raises(ValueError):
        case["search"].authorize("original", case["space"], panel_ids=[f"m{i}" for i in range(129)],
                                 quotes=quotes, budget=16, max_trials=2, rationale="too large")
    assert _state(case["store"]) == before


@pytest.mark.parametrize("constant", ["PANEL_VERSION", "RANKER_VERSION"])
def test_search_plan_refuses_a_changed_reward_or_ranker_definition(search_case, monkeypatch, constant):
    import etalon.active.protocol_score as scoring

    case = search_case
    search_id = _authorize(case)
    body = case["search"].get(search_id)["body"]
    assert body["reward_contract"]["version"] == scoring.PANEL_VERSION
    assert body["ranker_version"] == scoring.RANKER_VERSION
    before = _state(case["store"])
    monkeypatch.setattr(scoring, constant, "unreviewed-version/999")
    with pytest.raises(StateError):
        case["search"].plan(search_id)
    assert _state(case["store"]) == before


def test_unfrozen_reward_cannot_silently_use_new_scoring_code(search_case, monkeypatch):
    import etalon.active.protocol_score as scoring

    case = search_case
    search_id = _authorize(case)
    _start(case, search_id)

    def failed(action, candidate, endpoint, grant):
        return Evaluation(candidate.id, endpoint.id, None, endpoint.units, endpoint.cost, status="failed")

    case["search"].run_audit(search_id, failed, max_actions=4)
    before = _state(case["store"])
    with monkeypatch.context() as context:
        context.setattr(scoring, "PANEL_VERSION", "changed-score/999")
        with pytest.raises(StateError):
            case["search"].score(search_id)
    assert _state(case["store"]) == before
    frozen = case["search"].score(search_id)
    monkeypatch.setattr(scoring, "PANEL_VERSION", "changed-score/999")
    assert case["search"].score(search_id) == frozen


def test_audit_ei_economic_stop_is_read_only_and_does_not_close_or_propose(search_case, monkeypatch):
    case = search_case
    monkeypatch.setattr(Screen, "run", lambda *_a, **_k: pytest.fail("economic planning executed science"))
    search_id = _authorize(case, policy="audit_ei", opportunity_cost=1.0)
    before = _state(case["store"])
    plan = case["search"].plan(search_id)
    assert plan["stop_reason"] == "economic_stop" and plan["selected"] is None
    assert plan["ranking"] and max(row["score"] for row in plan["ranking"]) <= 0
    assert plan["budget"]["remaining"] == 16 and plan["budget"]["allocated"] == 0
    assert plan["economics"]["cost_unit"] == "CPU_quotes"
    reopened = ProtocolSearch(CampaignStore(case["store"].path, read_only=True))
    assert reopened.plan(search_id) == plan
    with pytest.raises(StateError, match="economic_stop"):
        case["search"].propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"],
                                     rationale="negative net utility cannot create a new intent")
    assert _state(case["store"]) == before
    assert case["search"].get(search_id)["status"] == "open"
    assert case["search"].get(search_id)["experiments"] == []


@pytest.mark.parametrize("policy", ["linear_ucb", "fixed", "random"])
def test_legacy_search_body_and_plan_remain_unchanged_without_economics(search_case, monkeypatch, policy):
    import etalon.active.protocol_score as scoring

    case = search_case
    search_id = _authorize(case, policy=policy)
    body = case["search"].get(search_id)["body"]
    assert "economic_contract" not in body
    assert "opportunity_cost" not in body["parameters"]
    assert body["ranker_version"] == scoring.RANKER_VERSION
    assert search_id == "protocol-search/1:" + digest(body)
    before = _state(case["store"])
    assert _authorize(case, policy=policy, opportunity_cost=None) == search_id
    assert _state(case["store"]) == before
    plan = case["search"].plan(search_id)
    assert "economics" not in plan and plan["selected"] is not None
    monkeypatch.setattr(scoring, "AUDIT_RANKER_VERSION", "new-audit-definition/999")
    reopened = ProtocolSearch(CampaignStore(case["store"].path, read_only=True))
    assert reopened.plan(search_id) == plan
    assert _state(case["store"]) == before


@pytest.mark.parametrize("constant", ["AUDIT_RANKER_VERSION", "AUDIT_ECONOMICS_VERSION"])
def test_audit_ei_freezes_the_exact_economic_contract_and_version(search_case, monkeypatch, constant):
    import etalon.active.protocol_score as scoring

    case = search_case
    search_id = _authorize(case, policy="audit_ei", opportunity_cost=0.04)
    body = case["search"].get(search_id)["body"]
    assert body["parameters"]["opportunity_cost"] == 0.04
    assert body["ranker_version"] == scoring.AUDIT_RANKER_VERSION
    assert body["economic_contract"] == {
        "version": scoring.AUDIT_RANKER_VERSION, "opportunity_cost": 0.04, "cost_unit": "CPU_quotes",
        "prediction_version": scoring.AUDIT_ECONOMICS_VERSION,
        "utility": "best_completed_panel_skill_with_zero_outside_option",
        "predictive_distribution": "clipped_gaussian_latent_plus_noise",
        "stopping_rule": "max_affordable_expected_improvement_minus_opportunity_charge_le_zero",
    }
    before = _state(case["store"])
    monkeypatch.setattr(scoring, constant, "changed-audit-definition/999")
    with pytest.raises(StateError):
        case["search"].plan(search_id)
    assert _state(case["store"]) == before


@pytest.mark.parametrize("corruption", ["campaign-cost-unit", "contract-rate", "contract-utility", "missing-contract"])
def test_audit_ei_rejects_cost_unit_or_frozen_economic_contract_drift(search_case, corruption):
    case = search_case
    search_id = _authorize(case, policy="audit_ei", opportunity_cost=0.04)
    key = "configuration" if corruption == "campaign-cost-unit" else "protocol:search:" + search_id
    # A supported API cannot rewrite these fields; fault injection audits fail-closed replay.
    with case["store"].connection(write=True) as db:
        record = json.loads(db.execute("SELECT body FROM metadata WHERE key=?", (key,)).fetchone()[0])
        if corruption == "campaign-cost-unit":
            record["spec"]["cost_unit"] = "different-unit"
        elif corruption == "contract-rate":
            record["body"]["economic_contract"]["opportunity_cost"] = 0.01
        elif corruption == "contract-utility":
            record["body"]["economic_contract"]["utility"] = "unreviewed-downstream-benefit"
        else:
            del record["body"]["economic_contract"]
        db.execute("UPDATE metadata SET body=? WHERE key=?", (canonical(record), key))
    before = _state(case["store"])
    with pytest.raises(StateError):
        case["search"].plan(search_id)
    assert _state(case["store"]) == before


@pytest.mark.parametrize("rate", [None, 0, -0.1, float("nan"), float("inf"), True, "0.04"])
def test_audit_ei_requires_an_explicit_finite_positive_opportunity_cost(search_case, rate):
    before = _state(search_case["store"])
    with pytest.raises(ValueError):
        _authorize(search_case, policy="audit_ei", opportunity_cost=rate)
    assert _state(search_case["store"]) == before


@pytest.mark.parametrize("policy", ["linear_ucb", "fixed", "random"])
def test_legacy_policies_cannot_silently_accept_an_unused_opportunity_cost(search_case, policy):
    before = _state(search_case["store"])
    with pytest.raises(ValueError):
        _authorize(search_case, policy=policy, opportunity_cost=0.04)
    assert _state(search_case["store"]) == before


def test_audit_ei_does_not_relax_positive_search_budget_validation(search_case):
    before = _state(search_case["store"])
    with pytest.raises(ValueError):
        _authorize(search_case, policy="audit_ei", opportunity_cost=0.04, budget=-1)
    assert _state(search_case["store"]) == before


def test_audit_ei_completes_the_committed_panel_then_stops_without_double_charging_sunk_cost(search_case):
    case = search_case
    search_id = _authorize(case, policy="audit_ei", opportunity_cost=0.04)
    _start(case, search_id)

    def failed(action, candidate, endpoint, grant):
        return Evaluation(candidate.id, endpoint.id, None, endpoint.units, endpoint.cost, status="failed")

    case["search"].run_audit(search_id, failed, max_actions=1)
    partial = case["search"].plan(search_id)
    assert partial["selected"] is None and partial["stop_reason"] == "audit_incomplete"
    assert partial["training_size"] == 0
    assert len(case["search"].audit_plan(search_id)["candidate_ids"]) == 3
    case["search"].run_audit(search_id, failed, max_actions=3)
    reward = case["search"].score(search_id)
    assert reward["skill"] == -1 and reward["actual_cost"] == 8
    stopped = case["search"].plan(search_id)
    assert stopped["stop_reason"] == "economic_stop" and stopped["selected"] is None
    assert stopped["training_size"] == 1 and stopped["ranking"]
    assert stopped["budget"]["spent"] == 8 and stopped["budget"]["remaining"] == 8
    assert stopped["budget"]["allocated"] == 8 and stopped["budget"]["allocation_remaining"] == 8
    assert case["store"].balance()["spent"] == 12
    for row in stopped["ranking"]:
        assert row["cost"] == 8 and row["opportunity_charge"] == pytest.approx(0.04 * 8)
        assert row["score"] == pytest.approx(row["expected_improvement"] - row["opportunity_charge"])
    # Independent historical expense changes campaign cash, not the frozen audit response
    # or the future trial's opportunity charge. It never supplies a successful audit label.
    case["store"].import_evaluation(Evaluation("m4", "original", None, "Da", 5, status="failed"),
                                    source_id="independent-sunk-cost")
    later = ProtocolSearch(CampaignStore(case["store"].path)).plan(search_id)
    assert later["ranking"] == stopped["ranking"] and later["economics"] == stopped["economics"]
    assert later["budget"] == stopped["budget"]
    assert later["campaign_balance"]["spent"] == 17
    assert case["search"].score(search_id) == reward


def test_audit_ei_filters_unaffordable_ranker_rows_before_economic_stop(search_case, monkeypatch):
    import etalon.active.protocol_score as scoring

    case = search_case
    variants = _catalogue(case)["variants"]
    cheap, expensive = variants[0]["id"], variants[1]["id"]
    search_id = _authorize(case, policy="audit_ei", opportunity_cost=1.0,
                           quotes={cheap: 2.0, expensive: 4.0}, budget=8, max_trials=2)
    original = scoring.rank_variants

    def fixture_ranking(*args, **kwargs):
        # Isolate planner order-of-operations, not numerical EI correctness: only the
        # unaffordable arm reports positive gain in this controlled ranker fixture.
        model = original(*args, **kwargs)
        for row in model["ranking"]:
            row["score"] = 1.0 if row["id"] == expensive else -1.0
        model["ranking"].sort(key=lambda row: -row["score"])
        return model

    monkeypatch.setattr(scoring, "rank_variants", fixture_ranking)
    before = _state(case["store"])
    plan = case["search"].plan(search_id)
    assert [row["id"] for row in plan["ranking"]] == [cheap]
    assert plan["selected"] is None and plan["stop_reason"] == "economic_stop"
    assert _state(case["store"]) == before


def test_audit_ei_durable_intent_recovery_does_not_repeat_economic_selection(search_case, monkeypatch):
    import etalon.active.protocol_score as scoring

    case = search_case
    search_id = _authorize(case, policy="audit_ei", opportunity_cost=0.000001)
    plan = case["search"].plan(search_id)
    assert plan["selected"] is not None

    def interrupt_materialization(*args, **kwargs):
        raise RuntimeError("interrupted after economic decision was committed")

    with monkeypatch.context() as context:
        context.setattr(ProtocolRegistry, "propose", interrupt_materialization)
        with pytest.raises(RuntimeError, match="economic decision"):
            case["search"].propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"],
                                         rationale="commit one bounded audit decision")
    record = case["search"].get(search_id)
    assert len(record["experiments"]) == 1 and record["experiments"][0]["decision"] == plan
    monkeypatch.setattr(scoring, "rank_variants", lambda *_a, **_k: pytest.fail("resume repeated economic selection"))
    restarted = ProtocolSearch(CampaignStore(case["store"].path))
    proposal_id = restarted.resume_proposal(search_id)
    assert restarted.resume_proposal(search_id) == proposal_id
    assert len(restarted.get(search_id)["experiments"]) == 1
    assert restarted.get(search_id)["experiments"][0]["allocated_budget"] == 8
    assert case["registry"].get(proposal_id)["status"] == "proposed"


@pytest.mark.parametrize("guard", ["search_closed", "campaign_not_idle", "no_affordable_untried_variants"])
def test_audit_ei_economic_stop_never_masks_structural_search_guards(search_case, guard):
    case = search_case
    search_id = _authorize(case, policy="audit_ei", opportunity_cost=1.0)
    assert case["search"].plan(search_id)["stop_reason"] == "economic_stop"
    if guard == "search_closed":
        case["search"].close(search_id, rationale="explicitly close the still-open search")
    elif guard == "campaign_not_idle":
        round_id = case["store"].start_round({"mode": "unresolved-structural-guard"})
    else:
        case["store"].import_evaluation(Evaluation("m4", "original", None, "Da", 73, status="failed"),
                                        source_id="independent-expense-leaves-no-affordable-panel")
    before = _state(case["store"])
    plan = case["search"].plan(search_id)
    assert plan["selected"] is None and plan["stop_reason"] == guard
    assert _state(case["store"]) == before
    if guard == "campaign_not_idle":
        case["store"].finish_round(round_id, {"actions": []})


def test_audit_ei_economic_stop_does_not_revoke_independent_explicit_registration(search_case):
    case = search_case
    search_id = _authorize(case, policy="audit_ei", opportunity_cost=1.0)
    assert case["search"].plan(search_id)["stop_reason"] == "economic_stop"
    variant = case["search"].get(search_id)["body"]["variants"][0]
    endpoint = replace(case["store"].configuration()[1]["original"], id="explicit-after-economic-stop",
                       protocol=variant["protocol_id"], cost=variant["quote"])
    actions, balance = case["store"].actions(), case["store"].balance()
    case["store"].register_endpoints([endpoint], rationale="independent explicit registration, not a search decision")
    assert case["store"].configuration()[1][endpoint.id] == endpoint
    assert case["store"].actions() == actions and case["store"].balance() == balance
    assert case["search"].get(search_id)["experiments"] == []
    assert case["search"].get(search_id)["status"] == "open"
    assert case["registry"].proposals() == []
