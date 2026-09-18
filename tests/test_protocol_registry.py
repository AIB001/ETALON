"""Protocol changes are journaled experiments, not an escape from evidence or budgets."""

from __future__ import annotations

import json
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
from etalon.active.protocols import (
    ProtocolRegistry,
    ProtocolUnavailable,
    TrialPolicy,
    protocol_limits,
)
from etalon.active.schema import canonical
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose
from etalon.faults.attribution import Observation


@pytest.fixture(scope="module")
def seed_recipe():
    configuration = compose("registry-seed", [{
        "id": "measure", "title": "One explicit molecular property", "mode": "serial",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0")],
    }])
    return CascadeRecipe.freeze(configuration, Readout("properties", "property/v1", "mw"))


@pytest.fixture(scope="module")
def insertion():
    return {
        "op": "insert_criterion", "tier_id": "measure", "before": "properties",
        "criterion": component("synthesis", "synthesis.rdkit_sa_score@0.1.0"),
        "allowed_failures": ["F_BUILD_INCOMPLETE"],
    }


def setup_registry(tmp_path, seed, edit, *, policy="cost_aware"):
    endpoint = seed.endpoint("original", target="demo", quantity="molecular_weight", units="Da", cost=1)
    store = CampaignStore(tmp_path / "campaign.sqlite")
    store.configure(CampaignSpec(endpoint.id, 60, "declared_CPU_quotes", "test/1",
                                 batch_size=4, policy=policy), [endpoint])
    smiles = ["CCO", "CCN", "c1ccccc1", "CC(=O)O", "CCCO", "CCCC", "CC(C)O", "CNC"]
    store.add_candidates([Candidate(f"m{i}", text, (float(i), float(i % 3)))
                          for i, text in enumerate(smiles)])
    registry = ProtocolRegistry(store)
    registry.bind_seed(endpoint.id, seed, rationale="reviewed CPU-only starting protocol")
    space_id = registry.bind_space(DesignSpace((edit,), max_edits=1), rationale="reviewed one exact insertion")
    return store, registry, space_id


def propose(registry, space_id, edit, **kwargs):
    return registry.propose("original", "variant", space_id=space_id, edits=[edit], cost=2,
                            rationale="test an explicitly enumerated component combination",
                            proposed_by="test-reviewed-proposer", **kwargs)


def trial(registry, space_id, edit, *, limits=None):
    identifier = propose(registry, space_id, edit)
    assert registry.validate(identifier)["ok"]
    registry.start_trial(identifier, limits or TrialPolicy(6, 3), rationale="authorize bounded CPU pilot")
    return identifier


def simulated_attestation(store, endpoint_id, artifact, *, candidate_id="m0", action_id="unit-fixture-action"):
    """Unit fixture only: model a trusted executor, not a security proof or real artifact."""
    endpoint = store.configuration()[1][endpoint_id]
    record = next(record for record in ProtocolRegistry(store).proposals()
                  if record["body"]["endpoint"]["id"] == endpoint_id)
    graph = record["validation"]["graph"]
    return {
        "mode": "live_molcascade", "protocol_id": endpoint.protocol,
        "action_id": action_id, "candidate_id": candidate_id,
        "artifact_id": artifact, "unit_test_simulated_executor": True,
        "evidence_graph": {"graph_hash": graph["graph_hash"], "protocol_id": endpoint.protocol,
                           "readout": {**graph["readout"], "available": True, "artifact_id": artifact}},
    }


def evaluate(store, candidate_id, endpoint_id, *, executor=None, status="ok", checks=(), provenance=None,
             cost=None, attested=True, evidence_transform=None):
    endpoint = store.configuration()[1][endpoint_id]
    candidate = store.candidates()[candidate_id]
    round_id = store.start_round({"scope": "explicit registry integration test"})
    action = store.reserve(round_id, candidate_id, endpoint_id, {})
    store.start_action(action.id)
    evidence = dict(provenance or {})
    if executor is None and attested and endpoint_id == "variant":
        # Unit-test-only executor stub; the real CPU integration test above uses no fabricated
        # artifacts. These fields intentionally model the trusted executor's attestation.
        artifact = "unit-fixture-artifact-" + action.id
        evidence.update(simulated_attestation(store, endpoint_id, artifact,
                                              candidate_id=candidate_id, action_id=action.id))
    if evidence_transform is not None:
        evidence_transform(evidence)
    result = (executor(action, candidate, endpoint, None) if executor is not None else
              Evaluation(candidate_id, endpoint_id, 40 + candidate.features[0] if status == "ok" else None,
                         endpoint.units, endpoint.cost if cost is None else cost, status=status,
                         checks=checks, provenance=evidence))
    store.resolve(action.id, result)
    store.finish_round(round_id, {"actions": [action.id]})
    return action, result


def test_proposing_and_validating_are_compile_only_and_do_not_register_or_spend(
        tmp_path, seed_recipe, insertion, monkeypatch):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    monkeypatch.setattr(Screen, "run", lambda *_a, **_k: pytest.fail("compile-only operation executed a tool"))
    before = store.balance()
    identifier = propose(registry, space, insertion)
    assert registry.get(identifier)["status"] == "proposed"
    validation = registry.validate(identifier)
    assert validation["ok"] and validation["scope"] == "compile_only"
    assert validation["graph"]["library_is_placeholder"]
    assert store.balance() == before and store.actions() == []
    assert set(store.configuration()[1]) == {"original"}
    assert set(registry.recipes()) == {"original"}
    assert propose(registry, space, insertion) == identifier
    assert len(registry.proposals()) == 1


def test_real_cpu_paired_trial_survives_restart_promotes_and_retires_without_relabeling(
        tmp_path, seed_recipe, insertion):
    from rdkit import Chem
    from rdkit.Chem import Descriptors

    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    executor = CascadeExecutor.from_journal(tmp_path / "calculations", store)
    for i in range(3):
        evaluate(store, f"m{i}", "original", executor=executor)
    historical = store.observations()
    identifier = trial(registry, space, insertion)
    resumed = CampaignStore(store.path)
    registry = ProtocolRegistry(resumed)
    executor = CascadeExecutor.from_journal(tmp_path / "calculations", resumed)
    assert set(executor.recipes) == {"original", "variant"}
    assert executor.recipes["original"] == seed_recipe
    assert executor.recipes["variant"].protocol_id != seed_recipe.protocol_id
    for i in range(3):
        action, result = evaluate(resumed, f"m{i}", "variant", executor=executor)
        expected = Descriptors.MolWt(Chem.MolFromSmiles(resumed.candidates()[f"m{i}"].smiles))
        assert result.value == pytest.approx(expected)
        assert result.provenance["action_id"] == action.id
        assert result.provenance["candidate_id"] == f"m{i}"
        assert result.provenance["evidence_graph"]["readout"]["available"]
    report = registry.report(identifier)
    assert report["rollout_criteria_met"], report
    assert report["admitted_molecules"] == report["paired_molecules"] == 3
    assert report["actions"] == 3 and report["spent"] == 6
    assert "not an independent comparison" in report["scope"]
    assert resumed.observations()[:3] == historical
    assert resumed.configuration()[0].objective == "original"
    assert len(resumed.observations(admitted_only=True)) == 6
    registry.promote(identifier, rationale="predeclared operational rollout criteria met")
    assert registry.get(identifier)["status"] == "promoted"
    registry.retire(identifier, rationale="stop future queries without deleting historical evidence")
    assert registry.get(identifier)["status"] == "retired"
    model, choices, _ = ActiveCampaign(resumed).plan()
    assert model.snapshot()["training_size"] == 6
    assert set(model.endpoints) == {"original", "variant"}
    assert all(choice.endpoint_id != "variant" for choice in choices)
    assert resumed.observations()[:3] == historical
    assert resumed.balance() == {"budget": 60, "spent": 9, "reserved": 0, "remaining": 51}


@pytest.mark.parametrize("transition", ["propose", "validate", "start_trial", "retire"])
@pytest.mark.parametrize("with_pending", [False, True], ids=["running-empty-round", "pending-action"])
def test_protocol_transitions_require_a_completed_round(
        tmp_path, seed_recipe, insertion, transition, with_pending):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = propose(registry, space, insertion)
    if transition == "start_trial":
        registry.validate(identifier)
    round_id = store.start_round({})
    if with_pending:
        store.reserve(round_id, "m0", "original", {})
    operations = {
        "propose": lambda: propose(registry, space, insertion),
        "validate": lambda: registry.validate(identifier),
        "start_trial": lambda: registry.start_trial(identifier, TrialPolicy(6, 3), rationale="reviewed"),
        "retire": lambda: registry.retire(identifier, rationale="reviewed"),
    }
    before = registry.get(identifier)
    with pytest.raises(StateError, match="completed rounds|pending"):
        operations[transition]()
    assert registry.get(identifier) == before


def test_trial_requires_successful_validation_and_immutable_limits(tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = propose(registry, space, insertion)
    with pytest.raises(StateError, match="validated"):
        registry.start_trial(identifier, TrialPolicy(6, 3), rationale="too early")
    registry.validate(identifier)
    registry.start_trial(identifier, TrialPolicy(6, 3), rationale="reviewed")
    evaluate(store, "m0", "variant")
    with pytest.raises(StateError, match="cannot change"):
        registry.start_trial(identifier, TrialPolicy(20, 10), rationale="post-hoc larger allowance")
    assert registry.get(identifier)["trial"]["limits"] == TrialPolicy(6, 3).as_dict()


@pytest.mark.parametrize("limits", [TrialPolicy(2, 5, 1, 0), TrialPolicy(20, 1, 1, 0)],
                         ids=["budget-cap", "action-cap"])
def test_direct_reservation_cannot_bypass_trial_limits(tmp_path, seed_recipe, insertion, limits):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    trial(registry, space, insertion, limits=limits)
    round_id = store.start_round({})
    first = store.reserve(round_id, "m0", "variant", {})
    with pytest.raises(ProtocolUnavailable):
        store.reserve(round_id, "m1", "variant", {})
    assert len(store.actions()) == 1 and store.balance()["reserved"] == first.reserved_cost
    assert protocol_limits(store, store.configuration()[1], store.observations())["variant"] == 0


def test_two_connections_cannot_race_past_the_trial_allowance(tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    trial(registry, space, insertion, limits=TrialPolicy(2, 1, 1, 0))
    round_id = store.start_round({})

    def reserve(identifier):
        try:
            return CampaignStore(store.path).reserve(round_id, identifier, "variant", {})
        except ProtocolUnavailable:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        actions = list(pool.map(reserve, ["m0", "m1"]))
    assert sum(action is not None for action in actions) == 1
    assert len(store.actions()) == 1 and store.balance()["reserved"] == 2


def test_actual_cost_overrun_exhausts_trial_without_hiding_the_real_cost(tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = trial(registry, space, insertion, limits=TrialPolicy(4, 4, 1, 0))
    evaluate(store, "m0", "variant", cost=5)
    assert registry.report(identifier)["spent"] == 5
    round_id = store.start_round({})
    with pytest.raises(ProtocolUnavailable):
        store.reserve(round_id, "m1", "variant", {})
    assert store.balance()["spent"] == 5


def test_a_failed_attempt_counts_against_trial_even_when_charged_zero(tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = trial(registry, space, insertion, limits=TrialPolicy(20, 1, 1, 0))
    evaluate(store, "m0", "variant", status="blocked", cost=0)
    report = registry.report(identifier)
    assert report["actions"] == report["failed_results"] == 1 and report["spent"] == 0
    assert not report["rollout_criteria_met"]
    round_id = store.start_round({})
    with pytest.raises(ProtocolUnavailable):
        store.reserve(round_id, "m1", "variant", {})


def test_promotion_needs_predeclared_pairs_and_failure_threshold(tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = trial(registry, space, insertion)
    for i in range(3):
        evaluate(store, f"m{i}", "variant")
    assert registry.report(identifier)["unmet_criteria"] == ["insufficient_objective_pairs"]
    with pytest.raises(StateError, match="insufficient_objective_pairs"):
        registry.promote(identifier, rationale="unpaired proxies are not objective calibration")
    for i in range(3):
        evaluate(store, f"m{i}", "original")
    registry.promote(identifier, rationale="now three admitted objective pairs exist")
    assert "variant" not in protocol_limits(store, store.configuration()[1], store.observations())
    evaluate(store, "m3", "variant")
    assert registry.report(identifier)["actions"] == 4


def test_quality_rejected_values_cannot_satisfy_rollout_evidence(tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = trial(registry, space, insertion, limits=TrialPolicy(6, 3, 1, 0, 0.25))
    evaluate(store, "m0", "variant")
    evaluate(store, "m1", "variant")
    evaluate(store, "m2", "variant", checks=(Observation("F_BUILD_INCOMPLETE", True, "missing build"),))
    report = registry.report(identifier)
    assert report["admitted_molecules"] == 2 and report["failure_fraction"] == pytest.approx(1 / 3)
    with pytest.raises(StateError, match="failure_fraction"):
        registry.promote(identifier, rationale="a returned scalar alone does not establish validity")


def test_retired_protocol_cannot_be_reserved_or_rebound_as_an_unrestricted_seed(
        tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = trial(registry, space, insertion)
    registry.retire(identifier, rationale="stop pilot")
    with pytest.raises(StateError, match="unrestricted seed"):
        registry.bind_seed("variant", registry.recipes()["variant"], rationale="attempt to erase trial control")
    with pytest.raises(StateError, match="validated"):
        registry.start_trial(identifier, TrialPolicy(6, 3), rationale="attempt to revive retired pilot")
    round_id = store.start_round({})
    with pytest.raises(ProtocolUnavailable):
        store.reserve(round_id, "m0", "variant", {})


def test_matching_trial_start_is_idempotent_across_connections(tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = propose(registry, space, insertion)
    registry.validate(identifier)

    def start(_):
        ProtocolRegistry(CampaignStore(store.path)).start_trial(identifier, TrialPolicy(6, 3), rationale="reviewed")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(start, range(2)))
    assert registry.get(identifier)["status"] == "trial"
    assert sum(event["kind"] == "protocol_trial_started" for event in store.events()) == 1
    assert sum(event["kind"] == "endpoint_registered" for event in store.events()) == 1


def test_failed_event_write_rolls_back_trial_and_endpoint_registration(
        tmp_path, seed_recipe, insertion, monkeypatch):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = propose(registry, space, insertion)
    registry.validate(identifier)
    before = store.events()
    original = store._event

    def broken_event(db, kind, body):
        if kind == "protocol_trial_started":
            raise RuntimeError("simulated audit-journal failure")
        original(db, kind, body)

    monkeypatch.setattr(store, "_event", broken_event)
    with pytest.raises(RuntimeError, match="audit-journal"):
        registry.start_trial(identifier, TrialPolicy(6, 3), rationale="reviewed")
    assert registry.get(identifier)["status"] == "validated"
    assert set(store.configuration()[1]) == set(registry.recipes()) == {"original"}
    assert store.events() == before


def test_changed_pinned_resource_invalidates_trial_certificate_before_registration(
        tmp_path, seed_recipe, insertion):
    resource = tmp_path / "declared-reference.txt"
    resource.write_text("version-one", encoding="utf-8")
    seed = CascadeRecipe.freeze(json.loads(seed_recipe.configuration),
                                Readout("properties", "property/v1", "mw"), files=(resource,))
    store, registry, space = setup_registry(tmp_path, seed, insertion)
    identifier = propose(registry, space, insertion)
    registry.validate(identifier)
    resource.write_text("version-two", encoding="utf-8")
    with pytest.raises((ValueError, StateError), match="changed"):
        registry.start_trial(identifier, TrialPolicy(6, 3), rationale="stale resource certificate")
    assert registry.get(identifier)["status"] == "validated"
    assert set(store.configuration()[1]) == {"original"} and store.balance()["spent"] == 0


def test_repair_cites_exact_rejected_action_and_matching_structured_code(tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    action, result = evaluate(store, "m0", "original", status="failed",
                              provenance={"failure_code": "F_BUILD_INCOMPLETE", "error": "diagnostic details"})
    identifier = propose(registry, space, insertion, source_action_id=action.id)
    source = registry.get(identifier)["body"]["source_failure"]
    assert source["action_id"] == action.id and source["result_hash"]
    assert source["failure_codes"] == ["F_BUILD_INCOMPLETE"]
    assert canonical(store.observations()[0]["result"]) == canonical(result.as_dict())
    assert len(store.actions()) == 1 and store.balance()["spent"] == 1


@pytest.mark.parametrize("source_kind", ["admitted", "unstructured", "wrong_code", "wrong_endpoint"])
def test_repair_never_guesses_failure_from_prose_or_an_unrelated_result(
        tmp_path, seed_recipe, insertion, source_kind):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    endpoint = "original"
    if source_kind == "wrong_endpoint":
        original = store.configuration()[1][endpoint]
        store.register_endpoints([replace(original, id="other", protocol="other-protocol")], rationale="distinct source")
        endpoint = "other"
    status = "ok" if source_kind == "admitted" else "failed"
    provenance = ({"error": "F_BUILD_INCOMPLETE appears only in untrusted error prose"}
                  if source_kind == "unstructured" else
                  {"failure_code": "UNRELATED_FAILURE" if source_kind == "wrong_code" else "F_BUILD_INCOMPLETE"})
    action, _ = evaluate(store, "m0", endpoint, status=status, provenance=provenance)
    with pytest.raises((ValueError, StateError), match="structured|observed failure|base endpoint"):
        propose(registry, space, insertion, source_action_id=action.id)
    assert registry.proposals() == []


def test_read_only_registry_can_inspect_but_never_change_protocol_state(tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = propose(registry, space, insertion)
    observer = ProtocolRegistry(CampaignStore(store.path, read_only=True))
    assert observer.get(identifier) == registry.get(identifier)
    assert observer.recipes() == {"original": seed_recipe}
    before = store.events()
    operations = [lambda: observer.bind_seed("original", seed_recipe, rationale="read-only"),
                  lambda: observer.bind_space(DesignSpace((insertion,)), rationale="read-only"),
                  lambda: propose(observer, space, insertion),
                  lambda: observer.validate(identifier),
                  lambda: observer.retire(identifier, rationale="read-only")]
    for operation in operations:
        with pytest.raises(StateError, match="read-only"):
            operation()
    assert store.events() == before


def test_zero_cost_historical_imports_cannot_manufacture_successful_trial_evidence(
        tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = trial(registry, space, insertion)
    for i in range(3):
        store.import_evaluation(Evaluation(f"m{i}", "original", 40 + i, "Da", 0), source_id=f"objective-{i}")
        store.import_evaluation(Evaluation(f"m{i}", "variant", 40 + i, "Da", 0), source_id=f"historical-{i}")
    report = registry.report(identifier)
    assert not report["rollout_criteria_met"]
    with pytest.raises(StateError, match="insufficient"):
        registry.promote(identifier, rationale="historical imports are not executions of the authorized trial")
    assert report["actions"] == 3 and report["spent"] == 0
    # Raw imports are retained, but controlled-protocol imports cannot train a surrogate.
    assert len(store.observations()) == 6 and len(store.observations(admitted_only=True)) == 3
    assert all(row["result"]["endpoint_id"] == "original" for row in store.observations(admitted_only=True))
    assert ActiveCampaign(store).plan()[0].snapshot()["training_size"] == 3


def test_unattested_result_cannot_certify_that_the_reviewed_recipe_was_executed(
        tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = trial(registry, space, insertion, limits=TrialPolicy(2, 1, 1, 0))
    evaluate(store, "m0", "variant", attested=False)
    assert not store.observations()[0]["admitted"]
    assert ActiveCampaign(store).plan()[0].snapshot()["training_size"] == 0
    assert not registry.report(identifier)["rollout_criteria_met"]
    with pytest.raises(StateError, match="insufficient"):
        registry.promote(identifier, rationale="a supplied scalar is not evidence of recipe execution")


@pytest.mark.parametrize("retired", [False, True], ids=["trial", "retired"])
def test_legacy_registration_cannot_alias_a_controlled_protocol_to_bypass_its_limits(
        tmp_path, seed_recipe, insertion, retired):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = trial(registry, space, insertion)
    if retired:
        registry.retire(identifier, rationale="stop pilot")
    variant = store.configuration()[1]["variant"]
    with pytest.raises(StateError, match="controlled|alias|protocol"):
        store.register_endpoints([replace(variant, id="uncontrolled-alias")], rationale="attempted quota bypass")
    assert set(store.configuration()[1]) == {"original", "variant"}


@pytest.mark.parametrize("path,value", [
    (("mode",), None),
    (("mode",), "replay"),
    (("protocol_id",), "unrelated-protocol"),
    (("action_id",), "unrelated-action"),
    (("candidate_id",), "unrelated-molecule"),
    (("evidence_graph", "protocol_id"), "unrelated-protocol"),
    (("evidence_graph", "graph_hash"), "unrelated-certificate"),
    (("evidence_graph", "readout", "available"), False),
    (("artifact_id",), ""),
    (("evidence_graph", "readout", "artifact_id"), "unrelated-artifact"),
    (("evidence_graph", "readout"), {}),
])
def test_controlled_protocol_withholds_mismatched_execution_evidence_but_keeps_raw_result_and_cost(
        tmp_path, seed_recipe, insertion, path, value):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = trial(registry, space, insertion, limits=TrialPolicy(2, 1, 1, 0))

    def corrupt_one_field(provenance):
        target = provenance
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value

    action, result = evaluate(store, "m0", "variant", evidence_transform=corrupt_one_field)
    observed = store.observations()[0]
    assert observed["action_id"] == action.id and not observed["admitted"]
    assert canonical(observed["result"]) == canonical(result.as_dict())
    assert store.balance()["spent"] == 2 and store.balance()["reserved"] == 0
    assert ActiveCampaign(store).plan()[0].snapshot()["training_size"] == 0
    assert not registry.report(identifier)["rollout_criteria_met"]


def test_controlled_historical_import_cannot_use_simulated_attestation_to_claim_trial_execution(
        tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = trial(registry, space, insertion, limits=TrialPolicy(2, 1, 1, 0))
    result = Evaluation("m0", "variant", 40, "Da", 2,
                        provenance=simulated_attestation(store, "variant", "unit-fixture-import-artifact"))
    store.import_evaluation(result, source_id="explicitly-historical-not-a-runtime-action")
    observation = store.observations()[0]
    assert not observation["admitted"]
    assert canonical(observation["result"]) == canonical(result.as_dict())
    assert store.balance()["spent"] == 2
    assert ActiveCampaign(store).plan()[0].snapshot()["training_size"] == 0
    assert registry.report(identifier)["actions"] == 1
    assert not registry.report(identifier)["rollout_criteria_met"]


def test_legacy_endpoint_still_accepts_admissible_runtime_and_historical_labels(
        tmp_path, seed_recipe, insertion):
    store, _, _ = setup_registry(tmp_path, seed_recipe, insertion)
    evaluate(store, "m0", "original", attested=False)
    store.import_evaluation(Evaluation("m1", "original", 41, "Da", 1), source_id="legacy-assay")
    assert len(store.observations(admitted_only=True)) == 2
    assert ActiveCampaign(store).plan()[0].snapshot()["training_size"] == 2
    assert store.balance()["spent"] == 2


def test_explicit_idle_recovery_interrupts_every_orphan_round_and_unblocks_protocol_transition(
        tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    identifier = propose(registry, space, insertion)
    first = store.start_round({"test_fixture": "crashed before dispatch"})
    # Simulate a recovered older journal containing two orphan records, without dispatching.
    with store.connection(write=True) as db:
        second = db.execute("INSERT INTO rounds(body,status) VALUES (?,'running')",
                            (canonical({"test_fixture": "second orphan"}),)).lastrowid
    with pytest.raises(StateError, match="completed rounds"):
        registry.validate(identifier)
    before = store.events()
    reason = "operator checked that neither orphan had an external action"
    assert store.recover_idle_rounds(reason=reason) == (first, second)
    assert all(row["status"] == "interrupted" for row in store.rounds())
    recovery_events = store.events()[len(before):]
    assert {event["body"]["round_id"] for event in recovery_events} == {first, second}
    assert all(event["body"]["reason"] == reason for event in recovery_events)
    assert store.recover_idle_rounds(reason="idempotent second inspection") == ()
    assert store.events()[len(before):] == recovery_events
    assert registry.validate(identifier)["ok"]
    assert store.balance()["spent"] == store.balance()["reserved"] == 0


@pytest.mark.parametrize("running", [False, True], ids=["reserved-action", "running-action"])
def test_idle_recovery_never_releases_or_replays_a_pending_action(
        tmp_path, seed_recipe, insertion, running):
    store, _, _ = setup_registry(tmp_path, seed_recipe, insertion)
    round_id = store.start_round({})
    action = store.reserve(round_id, "m0", "original", {})
    if running:
        store.start_action(action.id)
    before_rounds, before_actions, before_events, before_balance = (
        store.rounds(), store.actions(), store.events(), store.balance())
    with pytest.raises(StateError, match="pending|unresolved"):
        store.recover_idle_rounds(reason="cannot assume the external action has stopped")
    assert store.rounds() == before_rounds and store.actions() == before_actions
    assert store.events() == before_events and store.balance() == before_balance
    store.resolve(action.id, Evaluation("m0", "original", None, "Da", 1, status="failed",
                                       provenance={"operator": "external action now confirmed stopped"}))
    assert store.recover_idle_rounds(reason="all external work accounted for") == (round_id,)
    assert len(store.actions()) == len(store.observations()) == 1
    assert store.balance()["spent"] == 1 and store.balance()["reserved"] == 0


def test_idle_recovery_requires_reason_and_a_writable_journal(tmp_path, seed_recipe, insertion):
    store, _, _ = setup_registry(tmp_path, seed_recipe, insertion)
    store.start_round({})
    before = store.rounds(), store.events()
    with pytest.raises(ValueError, match="reason|rationale"):
        store.recover_idle_rounds(reason="  ")
    with pytest.raises(StateError, match="read-only"):
        CampaignStore(store.path, read_only=True).recover_idle_rounds(reason="observer has no write capability")
    assert (store.rounds(), store.events()) == before


def test_idle_recovery_rolls_back_round_status_when_audit_event_fails(
        tmp_path, seed_recipe, insertion, monkeypatch):
    store, _, _ = setup_registry(tmp_path, seed_recipe, insertion)
    store.start_round({})
    before = store.rounds(), store.events()

    def fail_event(_db, _kind, _body):
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(store, "_event", fail_event)
    with pytest.raises(RuntimeError, match="audit write"):
        store.recover_idle_rounds(reason="explicit operator recovery")
    assert (store.rounds(), store.events()) == before


def test_historical_failure_cannot_be_claimed_as_a_runtime_repair_source(tmp_path, seed_recipe, insertion):
    store, registry, space = setup_registry(tmp_path, seed_recipe, insertion)
    result = Evaluation("m0", "original", None, "Da", 1, status="failed",
                        provenance={"failure_code": "F_BUILD_INCOMPLETE", "source": "imported legacy failure"})
    store.import_evaluation(result, source_id="historical-failure-record")
    imported_action = store.actions()[0]
    assert imported_action["round_id"] == 0 and not store.observations()[0]["admitted"]
    with pytest.raises(StateError, match="runtime|historical|resolved|base endpoint"):
        propose(registry, space, insertion, source_action_id=imported_action["id"])
    assert registry.proposals() == [] and store.balance()["spent"] == 1
    assert canonical(store.observations()[0]["result"]) == canonical(result.as_dict())
