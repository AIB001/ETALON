"""Atomic journal snapshots, strict spending boundaries and explicit crash recovery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from etalon.active import (
    BudgetExhausted,
    CampaignSpec,
    CampaignStore,
    Candidate,
    Endpoint,
    Evaluation,
    StateError,
)
from etalon.active.budget import affordable_capacity, fits_budget
from etalon.active.cascade import CascadeRecipe, Readout
from etalon.active.mutations import DesignSpace
from etalon.active.protocols import ProtocolRegistry, ProtocolUnavailable, TrialPolicy
from etalon.active.schema import canonical, digest, finite
from etalon.campaign.design import component, compose
from etalon.faults.attribution import Observation


def journal(tmp_path, *, budget=20.0, cost=2.0):
    store = CampaignStore(tmp_path / "integrity.sqlite")
    endpoint = Endpoint("high", "t", "score", "u", "protocol/v1", cost, requires_handoff=False, max_replicates=3)
    store.configure(CampaignSpec("high", budget, "explicit_units", "test/1", batch_size=1), [endpoint])
    store.add_candidates([Candidate(f"m{i}", "CCO", (float(i),)) for i in range(4)])
    return store


def state(store):
    with store.connection() as db:
        rows = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("metadata", "candidates", "rounds", "actions", "observations", "events")}
    return canonical(rows)


@pytest.mark.parametrize("scale", [1e-12, 1.0, 1e12])
def test_budget_comparison_and_capacity_are_invariant_to_declared_cost_units(scale):
    assert fits_budget((0.1 + 0.2) * scale, 0.3 * scale)
    assert affordable_capacity(0.1 * scale, 0.3 * scale, limit=10) == 3
    assert not fits_budget(0.31 * scale, 0.3 * scale)
    assert not fits_budget(0.1 * scale, 0)
    assert affordable_capacity(0.1 * scale, 0, limit=10) == 0
    assert not fits_budget(0, -1e-20 * scale)


@pytest.mark.parametrize("value", [True, False, "1", None, float("nan"), float("inf"), 10**1000])
def test_non_real_or_unrepresentable_money_is_rejected_with_value_error(value):
    with pytest.raises(ValueError):
        finite(value, "money")
    with pytest.raises(ValueError):
        fits_budget(value, 10)
    with pytest.raises(ValueError):
        fits_budget(1, value)


def test_capacity_is_bounded_even_when_unbounded_division_would_overflow():
    assert affordable_capacity(1e-300, 1e300, limit=7) == 7
    assert affordable_capacity(1e300, 1e300, limit=7) == 1
    assert affordable_capacity(1, -0.01, limit=7) == 0
    with pytest.raises(ValueError):
        affordable_capacity(0, 1, limit=7)
    with pytest.raises(ValueError):
        affordable_capacity(1, 1, limit=True)


@pytest.mark.parametrize("field,value", [
    ("requires_handoff", 0), ("requires_handoff", 1), ("requires_handoff", None),
    ("requires_handoff", "false"), ("max_replicates", True), ("quantity", 7),
    ("id", True), ("target", []), ("units", " "), ("protocol", 42), ("cost", True),
])
def test_endpoint_contract_never_coerces_identity_or_execution_authority(field, value):
    body = {"id": "endpoint", "target": "target", "quantity": "score", "units": "u", "protocol": "v1", "cost": 1}
    body[field] = value
    with pytest.raises(ValueError):
        Endpoint(**body)


@pytest.mark.parametrize("field", ["batch_size", "bootstrap", "seed", "max_observations", "max_candidates",
                                  "confirmation_reserve", "max_kg_candidates", "budget", "explore_fraction",
                                  "calibration_fraction"])
def test_campaign_counts_and_numeric_controls_do_not_accept_booleans(field):
    body = {"objective": "high", "budget": 20, "cost_unit": "u", "representation": "test/1"}
    body[field] = True
    with pytest.raises(ValueError):
        CampaignSpec(**body)


@pytest.mark.parametrize("field,value", [("candidate_id", 1), ("endpoint_id", True), ("units", []),
                                        ("provenance", []), ("checks", [{"fired": True}]), ("value", True)])
def test_malformed_evaluation_is_rejected_before_it_can_leave_a_running_action(field, value):
    body = {"candidate_id": "m0", "endpoint_id": "high", "units": "u", "cost": 1, "value": 2}
    body[field] = value
    with pytest.raises(ValueError):
        Evaluation(**body)


@pytest.mark.parametrize("field,value", [("id", 1), ("smiles", True), ("features", "123"),
                                        ("features", (True,)), ("handoff", [])])
def test_malformed_candidate_identity_features_or_handoff_is_rejected(field, value):
    body = {"id": "m0", "smiles": "CCO", "features": (1.0,)}
    body[field] = value
    with pytest.raises(ValueError):
        Candidate(**body)


def test_trial_failure_threshold_cannot_be_a_boolean_or_nonfinite_number():
    for value in (True, "0.25", float("nan"), float("inf")):
        with pytest.raises(ValueError):
            TrialPolicy(10, 4, max_failure_fraction=value)


def test_valid_list_of_checks_is_frozen_to_the_tuple_used_by_executor_admission():
    checks = [Observation("F_BUILD_INCOMPLETE", True, "documented failure")]
    result = Evaluation("m0", "high", 1, "u", 2, checks=checks)
    assert isinstance(result.checks, tuple) and result.checks == tuple(checks)
    checks.clear()
    assert len(result.checks) == 1 and () + result.checks == result.checks


@pytest.mark.parametrize("cost", [1e-24, 1e-12, 1.0, 1e12])
def test_zero_campaign_budget_never_authorizes_a_positive_charge(tmp_path, cost):
    store = journal(tmp_path, budget=0, cost=cost)
    round_id = store.start_round({})
    before = state(store)
    with pytest.raises(BudgetExhausted):
        store.reserve(round_id, "m0", "high", {})
    assert state(store) == before and store.balance()["reserved"] == 0


def test_starting_a_second_worker_cannot_interrupt_an_existing_empty_round(tmp_path):
    store = journal(tmp_path)
    first = store.start_round({"worker": "still-alive"})
    before = state(store)
    with pytest.raises(StateError, match="unfinished round"):
        CampaignStore(store.path).start_round({"worker": "competing"})
    assert state(store) == before
    assert store.rounds()[0]["status"] == "running"
    assert store.recover_idle_rounds(reason="operator verified the original worker is stopped") == (first,)
    second = store.start_round({"worker": "explicitly-recovered"})
    assert second != first and store.rounds()[0]["status"] == "interrupted"


def test_starting_a_second_worker_between_actions_cannot_steal_the_round(tmp_path):
    store = journal(tmp_path)
    round_id = store.start_round({})
    first = store.reserve(round_id, "m0", "high", {})
    store.resolve(first.id, Evaluation("m0", "high", 1, "u", 2))
    with pytest.raises(StateError, match="unfinished round"):
        CampaignStore(store.path).start_round({})
    second = store.reserve(round_id, "m1", "high", {})
    assert second.round_id == round_id


def test_two_connections_can_commit_only_one_start_from_the_same_snapshot(tmp_path):
    store = journal(tmp_path)
    cutoff = store.snapshot()["event_cutoff"]

    def start(_):
        try:
            return CampaignStore(store.path).start_round({}, expected_event_cutoff=cutoff)
        except StateError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        rounds = list(pool.map(start, range(2)))
    assert sum(value is not None for value in rounds) == 1
    assert len(store.rounds()) == 1 and store.rounds()[0]["status"] == "running"
    assert not any(event["kind"] == "round_interrupted" for event in store.events())


def test_a_late_import_invalidates_the_snapshot_before_round_creation(tmp_path):
    store = journal(tmp_path)
    snapshot = store.snapshot()
    store.import_evaluation(Evaluation("m0", "high", 1, "u", 2), source_id="late-measurement")
    before = state(store)
    with pytest.raises(StateError, match="stale"):
        store.start_round({}, expected_event_cutoff=snapshot["event_cutoff"])
    assert state(store) == before and store.rounds() == []


def test_snapshot_is_single_committed_state_even_when_a_writer_commits_during_read(tmp_path, monkeypatch):
    store = journal(tmp_path)
    with store.connection() as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    original_cutoff = store.events()[-1]["sequence"]
    original = CampaignStore._observations
    injected = False

    def read_then_import(db, *, admitted_only=False):
        nonlocal injected
        rows = original(db, admitted_only=admitted_only)
        if not injected:
            injected = True
            store.import_evaluation(Evaluation("m0", "high", 1, "u", 2), source_id="concurrent-writer")
        return rows

    monkeypatch.setattr(CampaignStore, "_observations", staticmethod(read_then_import))
    snapshot = CampaignStore(store.path, read_only=True).snapshot()
    assert snapshot["event_cutoff"] == original_cutoff
    assert snapshot["actions"] == snapshot["observations"] == []
    assert snapshot["balance"]["spent"] == snapshot["balance"]["reserved"] == 0
    assert snapshot["endpoint_limits"] == snapshot["endpoint_candidates"] == {}
    assert len(store.snapshot()["actions"]) == 1 and store.balance()["spent"] == 2


def test_export_keeps_displayed_state_evidence_and_event_tail_in_one_snapshot(tmp_path, monkeypatch):
    store = journal(tmp_path)
    with store.connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
    cutoff = store.events()[-1]["sequence"]
    original = CampaignStore._actions
    injected = False

    def read_actions_then_import(db):
        nonlocal injected
        actions = original(db)
        if not injected:
            injected = True
            store.import_evaluation(Evaluation("m0", "high", 1, "u", 2), source_id="arrived-during-export")
        return actions

    monkeypatch.setattr(CampaignStore, "_actions", staticmethod(read_actions_then_import))
    exported = CampaignStore(store.path, read_only=True).export()
    assert exported["event_cutoff"] == cutoff == exported["events"][-1]["sequence"]
    assert exported["actions"] == exported["observations"] == []
    assert exported["state"]["observations"] == exported["state"]["admitted"] == 0
    assert exported["state"]["balance"]["spent"] == 0 and exported["state"]["pending"] == []
    assert len(exported["candidates"]) == exported["state"]["candidates"] == 4
    assert not any(event["kind"] == "observation_imported" for event in exported["events"])
    current = store.export()
    assert len(current["actions"]) == len(current["observations"]) == current["state"]["admitted"] == 1
    assert current["state"]["balance"]["spent"] == 2
    assert current["event_cutoff"] > cutoff


def test_read_only_export_is_serializable_and_does_not_mutate_the_journal(tmp_path):
    store = journal(tmp_path)
    store.import_evaluation(Evaluation("m0", "high", 1, "u", 2), source_id="already-acquired")
    before = state(store)
    exported = CampaignStore(store.path, read_only=True).export()
    assert canonical(exported)
    assert exported["state"] == store.status()
    assert exported["actions"] == store.actions() and exported["observations"] == store.observations()
    assert exported["events"] == store.events()
    assert state(store) == before


def test_user_round_metadata_cannot_shadow_authoritative_round_identity_or_status(tmp_path):
    store = journal(tmp_path)
    round_id = store.start_round({"round_id": 987, "status": "completed", "trace": "caller-metadata"})
    assert store.rounds() == [{"round_id": round_id, "status": "running", "trace": "caller-metadata"}]
    assert store.events()[-1]["body"]["round_id"] == round_id
    store.finish_round(round_id, {"round_id": 654, "status": "interrupted", "actions": []})
    assert store.rounds()[0]["round_id"] == round_id and store.rounds()[0]["status"] == "completed"
    assert store.events()[-1]["body"]["round_id"] == round_id
    store.bind_resource("real-name", {"name": "caller-alias", "version": 1})
    assert store.events()[-1]["body"]["name"] == "real-name"


def test_unknown_import_or_reservation_subjects_fail_before_any_journal_write(tmp_path):
    store = journal(tmp_path)
    round_id = store.start_round({})
    before = state(store)
    with pytest.raises(ValueError, match="candidate"):
        store.reserve(round_id, "absent", "high", {})
    with pytest.raises(ValueError, match="candidate"):
        store.import_evaluation(Evaluation("absent", "high", 1, "u", 2), source_id="unknown-subject")
    assert state(store) == before


@pytest.mark.parametrize("field,value", [("round_id", True), ("round_id", "1"), ("candidate_id", True),
                                        ("endpoint_id", 1), ("decision", [])])
def test_sqlite_coercion_cannot_turn_wrongly_typed_action_arguments_into_authority(tmp_path, field, value):
    store = journal(tmp_path)
    round_id = store.start_round({})
    arguments = {"round_id": round_id, "candidate_id": "m0", "endpoint_id": "high", "decision": {}}
    arguments[field] = value
    before = state(store)
    with pytest.raises(ValueError):
        store.reserve(**arguments)
    assert state(store) == before


def test_round_metadata_and_completion_id_are_explicitly_typed(tmp_path):
    store = journal(tmp_path)
    before = state(store)
    with pytest.raises(ValueError):
        store.start_round([])
    assert state(store) == before
    identifier = store.start_round({})
    before = state(store)
    with pytest.raises(ValueError):
        store.finish_round(True, {})
    with pytest.raises(ValueError):
        store.finish_round(identifier, [])
    assert state(store) == before


@pytest.mark.parametrize("runtime_state", ["empty", "completed", "pending", "orphan-action"])
def test_implementation_pin_cannot_retroactively_claim_an_existing_runtime_history(tmp_path, runtime_state):
    store = journal(tmp_path)
    round_id = store.start_round({})
    if runtime_state in {"pending", "orphan-action"}:
        store.reserve(round_id, "m0", "high", {})
    elif runtime_state == "completed":
        store.finish_round(round_id, {"actions": []})
    if runtime_state == "orphan-action":
        # Simulate an old incomplete journal whose action survived without its round row.
        with store.connection(write=True) as db:
            db.execute("DELETE FROM rounds WHERE id=?", (round_id,))
    before = state(store)
    with pytest.raises(StateError, match="retroactively"):
        store.bind_resource("implementation", {"version": "new-code/1"}, before_first_round=True)
    assert state(store) == before


def test_matching_implementation_pin_can_resume_after_execution_without_rewriting_history(tmp_path):
    store = journal(tmp_path)
    store.bind_resource("implementation", {"version": "code/1"}, before_first_round=True)
    round_id = store.start_round({})
    store.finish_round(round_id, {"actions": []})
    before = state(store)
    CampaignStore(store.path).bind_resource("implementation", {"version": "code/1"}, before_first_round=True)
    assert state(store) == before
    with pytest.raises(StateError, match="changed"):
        store.bind_resource("implementation", {"version": "code/2"}, before_first_round=True)
    assert state(store) == before


def test_historical_imports_do_not_falsely_look_like_runtime_rounds_for_first_pin(tmp_path):
    store = journal(tmp_path)
    store.import_evaluation(Evaluation("m0", "high", 1, "u", 2), source_id="historical")
    store.bind_resource("implementation", {"version": "code/1"}, before_first_round=True)
    assert store.events()[-1]["kind"] == "resource_bound"
    assert store.actions()[0]["round_id"] == 0 and store.rounds() == []


def test_unrestricted_legacy_resource_binding_is_unchanged_after_a_round(tmp_path):
    store = journal(tmp_path)
    store.start_round({})
    store.bind_resource("operator-context", {"reference": "explicit-later-annotation"})
    assert store.events()[-1]["kind"] == "resource_bound"


def test_concurrent_first_pin_and_round_start_have_an_auditable_atomic_order(tmp_path):
    store = journal(tmp_path)

    def bind():
        try:
            CampaignStore(store.path).bind_resource("implementation", {"version": "code/1"}, before_first_round=True)
            return True
        except StateError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        binding = pool.submit(bind)
        starting = pool.submit(CampaignStore(store.path).start_round, {})
        bound, round_id = binding.result(), starting.result()
    events = store.events()
    pins = [event for event in events if event["kind"] == "resource_bound"]
    started = next(event for event in events if event["kind"] == "round_started")
    assert started["body"]["round_id"] == round_id
    if bound:
        assert len(pins) == 1 and pins[0]["sequence"] < started["sequence"]
    else:
        assert pins == []


def test_imports_remain_possible_while_a_real_action_is_pending_and_preserve_overrun(tmp_path):
    store = journal(tmp_path, budget=5)
    round_id = store.start_round({})
    action = store.reserve(round_id, "m0", "high", {})
    store.import_evaluation(Evaluation("m1", "high", 1, "u", 7), source_id="external-job-finished")
    assert store.balance() == {"budget": 5, "spent": 7, "reserved": 2, "remaining": -4}
    store.resolve(action.id, Evaluation("m0", "high", None, "u", 2, status="failed"))
    assert store.balance() == {"budget": 5, "spent": 9, "reserved": 0, "remaining": -4}
    with pytest.raises(BudgetExhausted):
        store.reserve(round_id, "m2", "high", {})
    store.finish_round(round_id, {"actions": [action.id]})


def test_concurrent_resolution_records_exactly_one_result_and_charge(tmp_path):
    store = journal(tmp_path)
    round_id = store.start_round({})
    action = store.reserve(round_id, "m0", "high", {})

    def resolve(_):
        try:
            CampaignStore(store.path).resolve(action.id, Evaluation("m0", "high", 1, "u", 2))
            return True
        except StateError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(resolve, range(2))) == 1
    assert len(store.observations()) == 1 and store.balance()["spent"] == 2
    assert sum(event["kind"] == "action_resolved" for event in store.events()) == 1


def test_concurrent_identical_import_is_idempotent_including_its_charge(tmp_path):
    store = journal(tmp_path)

    def import_result(_):
        CampaignStore(store.path).import_evaluation(Evaluation("m0", "high", 1, "u", 2), source_id="same-source")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(import_result, range(2)))
    assert len(store.observations()) == len(store.actions()) == 1
    assert store.balance()["spent"] == 2


def test_overflowing_accounting_fails_closed_without_erasing_finite_actual_charges(tmp_path):
    from etalon.active.protocols import _counts

    store = journal(tmp_path, budget=1e308)
    for identifier in ("m0", "m1"):
        store.import_evaluation(Evaluation(identifier, "high", 1, "u", 1e308), source_id=identifier)
    before = state(store)
    for read in (store.balance, store.snapshot, store.status, store.export):
        with pytest.raises(StateError, match="finite numeric range.*rescale"):
            read()
    with store.connection() as db, pytest.raises(StateError, match="finite numeric range.*rescale"):
        _counts(db, "high")
    assert state(store) == before
    assert [row["result"]["cost"] for row in store.observations()] == [1e308, 1e308]
    assert len(store.events()) == 7  # configure + four molecules + both retained imports
    round_id = store.start_round({"purpose": "must not turn infinite spent into availability"})
    with pytest.raises(StateError, match="finite numeric range"):
        store.reserve(round_id, "m2", "high", {})
    assert len(store.actions()) == 2


@pytest.mark.parametrize("transition", ["round_started", "action_reserved", "action_resolved", "observation_imported"])
def test_failed_event_write_rolls_back_the_entire_state_transition(tmp_path, monkeypatch, transition):
    store = journal(tmp_path)
    if transition != "round_started":
        round_id = store.start_round({})
    if transition == "action_resolved":
        action = store.reserve(round_id, "m0", "high", {})
    before = state(store)

    def broken_event(db, kind, body):
        raise RuntimeError("injected durable event failure")

    monkeypatch.setattr(CampaignStore, "_event", staticmethod(broken_event))
    with pytest.raises(RuntimeError, match="durable event"):
        if transition == "round_started":
            store.start_round({})
        elif transition == "action_reserved":
            store.reserve(round_id, "m0", "high", {})
        elif transition == "action_resolved":
            store.resolve(action.id, Evaluation("m0", "high", 1, "u", 2))
        else:
            store.import_evaluation(Evaluation("m1", "high", 1, "u", 2), source_id="rollback-import")
    assert state(store) == before


@pytest.fixture(scope="module")
def recipe():
    config = compose("integrity", [{"id": "measure", "title": "CPU properties", "criteria": [
        component("properties", "features.rdkit_properties@0.1.0", settings={"batch_size": 128})]}])
    return CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))


def protocol_fixture(tmp_path, recipe):
    store = journal(tmp_path)
    endpoint = replace(store.configuration()[1]["high"], id="seed", protocol=recipe.protocol_id, max_replicates=1)
    store.register_endpoints([endpoint], rationale="explicit source protocol")
    registry = ProtocolRegistry(store)
    registry.bind_seed("seed", recipe, rationale="reviewed source binding")
    edits = [{"op": "set_setting", "criterion_id": "properties", "path": "/batch_size",
              "expected": 128, "value": value} for value in (64, 32)]
    space = registry.bind_space(DesignSpace(tuple(edits), 1), rationale="two independent exact variants")
    return store, registry, space, edits


def test_pending_protocol_proposals_cannot_share_one_endpoint_identity(tmp_path, recipe):
    store, registry, space, edits = protocol_fixture(tmp_path, recipe)
    arguments = {"space_id": space, "edits": [edits[0]], "cost": 2,
                 "rationale": "reviewed first variant", "proposed_by": "reviewer"}
    first = registry.propose("seed", "owned-slot", **arguments)
    before = state(store)
    assert registry.propose("seed", "owned-slot", **arguments) == first
    with pytest.raises(StateError, match="another proposal"):
        registry.propose("seed", "owned-slot", **{**arguments, "edits": [edits[1]]})
    with pytest.raises(StateError, match="another proposal"):
        registry.propose("seed", "owned-slot", **{**arguments, "cost": 3})
    assert state(store) == before


def test_competing_pending_proposals_cannot_race_for_the_same_endpoint_slot(tmp_path, recipe):
    store, registry, space, edits = protocol_fixture(tmp_path, recipe)

    def propose(edit):
        try:
            return ProtocolRegistry(CampaignStore(store.path)).propose(
                "seed", "contended-slot", space_id=space, edits=[edit], cost=2,
                rationale="concurrent reviewed variant", proposed_by="reviewer")
        except StateError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        proposals = list(pool.map(propose, edits))
    assert sum(identifier is not None for identifier in proposals) == 1
    assert len(registry.proposals()) == 1 and store.actions() == []


def test_legacy_colliding_proposal_cannot_claim_another_protocols_actual_trial_evidence(tmp_path, recipe):
    store, registry, space, edits = protocol_fixture(tmp_path, recipe)
    first = registry.propose("seed", "actual-slot", space_id=space, edits=[edits[0]], cost=2,
                             rationale="actual controlled variant", proposed_by="reviewer")
    other = registry.propose("seed", "unused-slot", space_id=space, edits=[edits[1]], cost=2,
                             rationale="never executed second variant", proposed_by="reviewer")
    legacy = registry.get(other)
    legacy["body"]["endpoint"]["id"] = "actual-slot"
    legacy["id"] = "proposal/1:" + digest(legacy["body"])
    # Reproduce an old journal that accepted both pending recipes in the same slot.
    with store.connection(write=True) as db:
        db.execute("INSERT INTO metadata(key,body) VALUES (?,?)",
                   ("protocol:proposal:" + legacy["id"], canonical(legacy)))
    assert registry.validate(first)["ok"]
    registry.start_trial(first, TrialPolicy(2, 2, 1, 0), rationale="activate only the first protocol")
    round_id = store.start_round({})
    action = store.reserve(round_id, "m0", "actual-slot", {})
    store.resolve(action.id, Evaluation("m0", "actual-slot", None, "u", 2, status="failed"))
    store.finish_round(round_id, {"actions": [action.id]})
    assert registry.report(first)["runtime_results"] == 1
    before = state(store)
    with pytest.raises(StateError, match="different registration"):
        registry.report(legacy["id"])
    assert state(store) == before


def test_uncontrolled_registration_cannot_supply_evidence_for_a_pending_protocol_proposal(tmp_path, recipe):
    store, registry, space, edits = protocol_fixture(tmp_path, recipe)
    proposal = registry.propose("seed", "pending-slot", space_id=space, edits=[edits[0]], cost=2,
                                rationale="pending reviewed protocol", proposed_by="reviewer")
    store.register_endpoints([Endpoint(**registry.get(proposal)["body"]["endpoint"])],
                             rationale="explicit legacy registration, not activation of this proposal")
    with pytest.raises(StateError, match="different registration"):
        registry.report(proposal)


@pytest.mark.parametrize("scale", [1e-12, 1.0, 1e12])
def test_exhausted_trial_budget_never_regains_an_absolute_epsilon_allowance(tmp_path, recipe, scale):
    store = journal(tmp_path, budget=10 * scale, cost=scale)
    original = store.configuration()[1]["high"]
    endpoint = replace(original, id="seed", protocol=recipe.protocol_id, max_replicates=1)
    store.register_endpoints([endpoint], rationale="explicit CPU source for a bounded protocol trial")
    registry = ProtocolRegistry(store)
    registry.bind_seed("seed", recipe, rationale="compile-only recipe binding")
    edit = {"op": "set_setting", "criterion_id": "properties", "path": "/batch_size", "expected": 128, "value": 64}
    space = registry.bind_space(DesignSpace((edit,), 1), rationale="one reviewed exact edit")
    proposal = registry.propose("seed", "trial", space_id=space, edits=[edit], cost=scale,
                                 rationale="finite budget test", proposed_by="test-reviewer")
    assert registry.validate(proposal)["ok"]
    registry.start_trial(proposal, TrialPolicy(scale, 3, 1, 0, 1), rationale="authorize one quote, not three")
    round_id = store.start_round({})
    action = store.reserve(round_id, "m0", "trial", {})
    store.resolve(action.id, Evaluation("m0", "trial", None, "u", scale, status="failed"))
    before = state(store)
    with pytest.raises(ProtocolUnavailable):
        store.reserve(round_id, "m1", "trial", {})
    assert state(store) == before
    assert store.snapshot()["endpoint_limits"]["trial"] == 0
