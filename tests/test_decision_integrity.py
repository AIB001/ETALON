"""Decision-chain regressions: evidence identity, physical units and strict budget boundaries."""

import math
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from etalon.active.decision import choose_decision
from etalon.active.knowledge import knowledge_gradient
from etalon.active.model import MultiEndpointGP
from etalon.active.policy import choose
from etalon.active.recommendation import recommend
from etalon.active.replay import synthetic_manifest
from etalon.active.schema import CampaignSpec, Candidate, Endpoint, Evaluation


def _fixture(*, cost=1.0, budget=20.0, policy="decision_aware", **settings):
    candidates = {str(i): Candidate(str(i), "C", (float(i),)) for i in range(6)}
    endpoints = {"high": Endpoint("high", "t", "reference", "u", "high/1", cost,
                                  requires_handoff=False, max_replicates=2)}
    spec = CampaignSpec("high", budget, "cost", "feature/1", policy=policy, **settings)
    return spec, candidates, endpoints


def _row(identifier="0", value=3.0, *, action_id="a", status="ok", admitted=True):
    return {"action_id": action_id, "admitted": admitted,
            "result": Evaluation(identifier, "high", value, "u", 1.0, status=status).as_dict()}


def test_duplicate_action_identity_is_not_an_independent_replication():
    _, candidates, endpoints = _fixture()
    row = _row()
    with pytest.raises(ValueError, match="unique.*duplicates"):
        MultiEndpointGP(candidates, endpoints, "high", [row, deepcopy(row)])
    model = MultiEndpointGP(candidates, endpoints, "high", [row])
    independent = MultiEndpointGP(candidates, endpoints, "high", [row, _row(action_id="b")])
    assert independent.predict(["0"], "high")[1][0] < model.predict(["0"], "high")[1][0]
    assert independent.training_action_ids == ["a", "b"]


@pytest.mark.parametrize("status", ["failed", "invalid", "blocked"])
def test_non_ok_value_cannot_train_even_when_caller_marks_it_admitted(status):
    _, candidates, endpoints = _fixture()
    with pytest.raises(ValueError, match="status ok"):
        MultiEndpointGP(candidates, endpoints, "high", [_row(status=status)])


@pytest.mark.parametrize("flag", [1, 0, "false", "true", None])
def test_admission_is_a_boolean_contract_not_python_truthiness(flag):
    _, candidates, endpoints = _fixture()
    with pytest.raises(ValueError, match="explicit boolean"):
        MultiEndpointGP(candidates, endpoints, "high", [_row(admitted=flag)])


@pytest.mark.parametrize("uncertainty", [-1, float("nan"), float("inf"), True, "0.1"])
def test_invalid_training_uncertainty_fails_closed(uncertainty):
    _, candidates, endpoints = _fixture()
    row = _row()
    row["result"]["uncertainty"] = uncertainty
    with pytest.raises(ValueError, match="uncertainty"):
        MultiEndpointGP(candidates, endpoints, "high", [row])


def test_unadmitted_numeric_labels_do_not_change_any_model_state():
    _, candidates, endpoints = _fixture()
    first = MultiEndpointGP(candidates, endpoints, "high", [_row()])
    rejected = _row("1", -1e100, action_id="rejected", admitted=False, status="invalid")
    second = MultiEndpointGP(candidates, endpoints, "high", [_row(), rejected])
    rejected["result"]["value"] = float("nan")  # Never inspected as a scientific label.
    third = MultiEndpointGP(candidates, endpoints, "high", [_row(), rejected])
    assert first.fingerprint == second.fingerprint == third.fingerprint
    assert first.snapshot() == second.snapshot() == third.snapshot()
    np.testing.assert_array_equal(first.predict(first.ids, "high"), third.predict(third.ids, "high"))


def test_training_snapshot_does_not_alias_mutable_caller_evidence_or_mappings():
    _, candidates, endpoints = _fixture()
    observation = _row()
    model = MultiEndpointGP(candidates, endpoints, "high", [observation])
    original = deepcopy(model.training)
    snapshot = model.snapshot()
    prediction = model.predict(model.ids, "high")
    observation["result"]["value"] = 1000
    endpoints["high"] = replace(endpoints["high"], noise=999)
    candidates.clear()
    assert model.training == original
    assert model.snapshot() == snapshot
    assert len(model.candidates) == 6
    assert model.endpoints["high"].noise == 0.1
    np.testing.assert_array_equal(model.predict(model.ids, "high"), prediction)


def test_published_model_snapshot_cannot_change_pairing_policy_or_training_counts():
    _, candidates, endpoints = _fixture()
    endpoints["low"] = replace(endpoints["high"], id="low", protocol="low/1")
    model = MultiEndpointGP(candidates, endpoints, "high", [_row()])
    before = deepcopy(model.snapshot())
    published = model.snapshot()
    published["counts"]["high"] = 900
    published["paired_molecules"]["low"] = 900
    assert model.snapshot() == before
    assert model.counts["high"] == 1
    assert model.pair_counts["low"] == 0


def test_recommendation_respects_endpoint_capacity_and_fixed_panel_scope():
    spec, candidates, endpoints = _fixture()
    model = MultiEndpointGP(candidates, endpoints, "high", [])
    blocked = recommend(spec, candidates, endpoints, model, [], [], remaining=20,
                        endpoint_limits={"high": 0})
    assert blocked["provisional"] is not None
    assert blocked["provisional"]["confirmation_eligible"] is False
    assert blocked["attainable"] is None
    panel = recommend(spec, candidates, endpoints, model, [], [], remaining=20,
                      endpoint_limits={"high": 1}, endpoint_candidates={"high": {"2"}})
    assert panel["attainable"]["candidate_id"] == "2"
    assert panel["provisional"]["candidate_id"] != "2"
    assert panel["provisional"]["confirmation_affordable"] is False


def test_restricted_confirmation_does_not_erase_already_acquired_objective_evidence():
    spec, candidates, endpoints = _fixture()
    observations = [_row()]
    model = MultiEndpointGP(candidates, endpoints, "high", observations)
    result = recommend(spec, candidates, endpoints, model, [], observations, remaining=20,
                       endpoint_limits={"high": 0}, endpoint_candidates={"high": set()})
    assert result["attainable"]["candidate_id"] == "0"
    assert result["evidence_backed"]["candidate_id"] == "0"
    assert result["attainable"]["confirmation_eligible"] is False


def test_large_finite_features_do_not_collapse_kernel_geometry():
    _, _, endpoints = _fixture()
    ordinary = {str(i): Candidate(str(i), "C", (value,)) for i, value in enumerate((-1.0, 0.0, 1.0))}
    large = {key: replace(row, features=(row.features[0] * 1e308,)) for key, row in ordinary.items()}
    rows = [_row("0", -1, action_id="a"), _row("2", 1, action_id="b")]
    a = MultiEndpointGP(ordinary, endpoints, "high", rows)
    b = MultiEndpointGP(large, endpoints, "high", rows)
    assert b.x[0, 0] < 0 < b.x[2, 0]
    np.testing.assert_allclose(a.x, b.x, rtol=1e-14)
    np.testing.assert_allclose(a.predict(a.ids, "high"), b.predict(b.ids, "high"), rtol=1e-13)


def test_large_equal_finite_labels_have_finite_mean_and_predictions():
    _, candidates, endpoints = _fixture()
    rows = [_row("0", 1e308, action_id="a"), _row("1", 1e308, action_id="b")]
    model = MultiEndpointGP(candidates, endpoints, "high", rows)
    mean, sd = model.predict(model.ids, "high")
    assert np.all(np.isfinite(mean)) and np.all(np.isfinite(sd))
    np.testing.assert_array_equal(mean, np.full(6, 1e308))


@pytest.mark.parametrize("scale", [1e200, 1e-200])
def test_unrepresentable_physical_variances_fail_explicitly_not_nan_or_false_zero(scale):
    _, candidates, endpoints = _fixture()
    endpoints["high"] = replace(endpoints["high"], prior_scale=scale, noise=0.1 * scale)
    model = MultiEndpointGP(candidates, endpoints, "high", [])
    assert np.isfinite(model.predict(["0"], "high")[1][0])
    with pytest.raises(ValueError, match="variance reduction.*rescale endpoint units"):
        model.objective_reduction(["0"], "high")
    with pytest.raises(ValueError, match="covariance.*rescale endpoint units"):
        model.posterior_covariance(["0"], "high", ["0"], "high")


@pytest.mark.parametrize("distance,scale", [(38.0, 1e100), (39.0, 1e100), (40.0, 1e100),
                                           (45.0, 1e154), (53.0, 1e306)])
def test_gaussian_knowledge_value_preserves_representable_weighted_extreme_tails(distance, scale):
    from scipy.integrate import quad

    # Independent integration after z = distance + u/distance. This pulls the
    # tiny density and large physical scale into one representable log factor.
    factor = math.exp(math.log(scale) - distance**2 / 2 - math.log(2 * math.pi) / 2
                      - 2 * math.log(distance))
    integral, _ = quad(lambda u: u * math.exp(-u - u*u / (2 * distance**2)), 0, 60)
    expected = factor * integral
    means, slopes = [0.0, -distance * scale], [0.0, scale]
    assert expected > 0
    assert knowledge_gradient(means, slopes) == pytest.approx(expected, rel=2e-8, abs=5e-324)


@pytest.mark.parametrize("value", [True, "3", float("nan"), float("inf")])
def test_invalid_numeric_training_values_are_uniformly_refused(value):
    _, candidates, endpoints = _fixture()
    row = _row()
    row["result"]["value"] = value
    with pytest.raises(ValueError, match="training value"):
        MultiEndpointGP(candidates, endpoints, "high", [row])


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_model_observation_limit_is_explicit_integer(limit):
    _, candidates, endpoints = _fixture()
    with pytest.raises(ValueError, match="positive integer"):
        MultiEndpointGP(candidates, endpoints, "high", [], limit=limit)


def test_model_identity_and_target_mismatches_refused():
    _, candidates, endpoints = _fixture()
    with pytest.raises(ValueError, match="objective"):
        MultiEndpointGP(candidates, endpoints, "absent", [])
    with pytest.raises(ValueError, match="candidate mapping"):
        MultiEndpointGP({"wrong": candidates["0"]}, endpoints, "high", [])
    with pytest.raises(ValueError, match="endpoint mapping"):
        MultiEndpointGP(candidates, {"wrong": endpoints["high"]}, "wrong", [])
    with pytest.raises(ValueError, match="one campaign target"):
        MultiEndpointGP(candidates, {**endpoints, "other": replace(endpoints["high"], id="other", target="other")}, "high", [])


@pytest.mark.parametrize("policy", ["random", "greedy", "ucb", "cost_aware", "cost_only", "mf_kg", "decision_aware"])
@pytest.mark.parametrize("remaining", [0.0, -1e-15, 8e-13])
def test_positive_tiny_quotes_never_fit_zero_negative_or_insufficient_budget(policy, remaining):
    spec, candidates, endpoints = _fixture(cost=1e-12, budget=1e-10, policy=policy)
    model = MultiEndpointGP(candidates, endpoints, "high", [])
    assert choose(spec, candidates, endpoints, model, [], [], remaining=remaining, slots=2) == []
    report = recommend(spec, candidates, endpoints, model, [], [], remaining=remaining)
    assert report["attainable"] is None
    assert report["provisional"]["confirmation_affordable"] is False


@pytest.mark.parametrize("policy", ["random", "greedy", "ucb", "cost_aware", "cost_only", "mf_kg", "decision_aware"])
@pytest.mark.parametrize("unit_scale", [1.0, 1e-12, 1e12])
def test_exact_one_quote_budget_remains_affordable_in_all_cost_units(policy, unit_scale):
    spec, candidates, endpoints = _fixture(cost=unit_scale, budget=20 * unit_scale, policy=policy)
    model = MultiEndpointGP(candidates, endpoints, "high", [])
    choices = choose(spec, candidates, endpoints, model, [], [], remaining=unit_scale, slots=3)
    assert len(choices) == 1
    assert choices[0].evidence["quoted_cost"] == unit_scale


def _manifest_choice(seed, scale, *, policy="cost_aware", negate=False):
    manifest = synthetic_manifest(seed=seed, size=16, policy=policy, batch_size=1)
    manifest["spec"].update(bootstrap=2, explore_fraction=0, calibration_fraction=0)
    warm = {row["result"]["candidate_id"] for row in manifest["initial"]}
    acquired = deepcopy([row for row in manifest["oracle"] if row["candidate_id"] in warm and not row["checks"]])
    definitions = deepcopy(manifest["endpoints"])
    for endpoint in definitions:
        if endpoint["id"] == "reference":
            for field in ("prior_mean", "prior_scale", "noise"):
                endpoint[field] *= scale
            if negate:
                endpoint["prior_mean"] *= -1
                endpoint["direction"] = "maximize"
    for row in acquired:
        if row["endpoint_id"] == "reference":
            row["value"] *= -scale if negate else scale
    candidates = {row["id"]: Candidate.from_dict(row) for row in manifest["candidates"]}
    endpoints = {row["id"]: Endpoint(**row) for row in definitions}
    observations = [{"action_id": str(index), "admitted": True, "result": row}
                    for index, row in enumerate(acquired)]
    actions = [{"id": str(index), "candidate_id": row["candidate_id"], "endpoint_id": row["endpoint_id"],
                "status": "completed", "cost": row["cost"], "decision": {}}
               for index, row in enumerate(acquired)]
    model = MultiEndpointGP(candidates, endpoints, "reference", observations)
    return choose(CampaignSpec(**manifest["spec"]), candidates, endpoints, model, actions, observations,
                  remaining=80, slots=1)[0]


@pytest.mark.parametrize("seed", [5, 17])
@pytest.mark.parametrize("scale", [1e-16, 1e16])
def test_cost_aware_expected_improvement_and_information_fraction_are_unit_invariant(seed, scale):
    first, changed = _manifest_choice(seed, 1), _manifest_choice(seed, scale)
    assert (first.candidate_id, first.endpoint_id) == (changed.candidate_id, changed.endpoint_id)
    assert changed.score == pytest.approx(first.score * scale, rel=1e-12, abs=0)


@pytest.mark.parametrize("policy", ["cost_aware", "mf_kg", "decision_aware"])
def test_negating_objective_and_direction_preserves_decision(policy):
    first = _manifest_choice(17, 1, policy=policy)
    changed = _manifest_choice(17, 1, policy=policy, negate=True)
    assert (first.candidate_id, first.endpoint_id) == (changed.candidate_id, changed.endpoint_id)
    assert changed.score == pytest.approx(first.score, rel=1e-12)


def test_calibration_fraction_cannot_gain_absolute_free_allowance_at_tiny_cost_units():
    spec, candidates, endpoints = _fixture(cost=5e-12, budget=20e-12, bootstrap=2, calibration_fraction=0.025)
    endpoints["low"] = Endpoint("low", "t", "proxy", "v", "low/1", 1e-12, requires_handoff=False)
    rows = [_row(str(index), float(index), action_id=str(index)) for index in range(2)]
    for row in rows:
        row["result"]["cost"] = 0.0
    actions = [{"id": str(index), "candidate_id": str(index), "endpoint_id": "high", "cost": 0.0,
                "status": "completed", "decision": {}} for index in range(2)]
    model = MultiEndpointGP(candidates, endpoints, "high", rows)
    result = choose(spec, candidates, endpoints, model, actions, rows, remaining=20e-12, slots=1)
    assert result
    assert result[0].evidence["reason"] != "protocol-calibration"


@pytest.mark.parametrize("remaining", [float("nan"), float("inf"), True, "1"])
def test_nonfinite_or_nonnumeric_remaining_fails_closed(remaining):
    spec, candidates, endpoints = _fixture()
    model = MultiEndpointGP(candidates, endpoints, "high", [])
    with pytest.raises(ValueError, match="remaining budget"):
        choose(spec, candidates, endpoints, model, [], [], remaining=remaining, slots=1)
    with pytest.raises(ValueError, match="remaining budget"):
        choose_decision(spec, candidates, endpoints, model, [], [], remaining=remaining, slots=1)
    with pytest.raises(ValueError, match="remaining budget"):
        recommend(spec, candidates, endpoints, model, [], [], remaining=remaining)


@pytest.mark.parametrize("slots", [True, -1, 0.5, "1"])
def test_invalid_slot_count_refused(slots):
    spec, candidates, endpoints = _fixture()
    model = MultiEndpointGP(candidates, endpoints, "high", [])
    for policy in (choose, choose_decision):
        with pytest.raises(ValueError, match="slots"):
            policy(spec, candidates, endpoints, model, [], [], remaining=10, slots=slots)
