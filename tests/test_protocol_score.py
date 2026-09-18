"""Numerical mechanism tests: no oracle outcomes or campaign GP enter protocol selection."""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from etalon.active.protocol_score import MAX_ARMS, MAX_FEATURES, panel_skill, rank_variants


def panel(*, values=None, objective=None, costs=None, quoted_cost=1, **kwargs):
    return panel_skill(objective if objective is not None else [1, 3, 4, 8, 11],
                       values if values is not None else [1, 3, 4, 8, 11],
                       costs if costs is not None else [1] * 5, quoted_cost, **kwargs)


def test_affine_loo_matches_the_declared_closed_form_and_excludes_the_heldout_row():
    y = np.array([1, 3, 4, 8, 11], dtype=float)
    x = np.array([0, 2, 3, 5, 6], dtype=float)
    result = panel(values=x, objective=y)
    expected = []
    for heldout in range(5):
        training = [j for j in range(5) if j != heldout]
        train_x, train_y = x[training], y[training]
        z = (train_x - train_x.mean()) / train_x.std()
        slope = (z @ (train_y - train_y.mean())) / (z @ z + 0.1 * len(training))
        expected.append(train_y.mean() + slope * (x[heldout] - train_x.mean()) / train_x.std())
        assert result["fold_train_indices"][heldout] == training
        assert result["baseline_predictions"][heldout] == pytest.approx(train_y.mean())
    assert result["predictions"] == pytest.approx(expected)
    baseline_sse = np.sum((y - result["baseline_predictions"]) ** 2)
    expected_skill = 1 - np.sum((y - expected) ** 2) / baseline_sse
    assert result["skill"] == pytest.approx(expected_skill)
    assert result["fold_fit_used"] == [True] * 5
    assert result["utility"] == pytest.approx(expected_skill / 5)


@pytest.mark.parametrize("heldout", range(5))
def test_changing_a_heldout_objective_never_updates_its_own_fit(heldout):
    original = [1, 3, 4, 8, 11]
    changed = list(original)
    changed[heldout] = 987654
    before, after = panel(objective=original), panel(objective=changed)
    assert before["predictions"][heldout] == after["predictions"][heldout]
    assert before["baseline_predictions"][heldout] == after["baseline_predictions"][heldout]
    assert before["fold_train_indices"][heldout] == after["fold_train_indices"][heldout]
    assert heldout not in after["fold_train_indices"][heldout]


@pytest.mark.parametrize("x_scale,y_scale", [(1000, 1), (-2, 1), (1, 0.001), (1, -5), (1e-15, 1)])
def test_skill_is_invariant_to_affine_units_and_direction(x_scale, y_scale):
    x = [1, 3, 4, 8, 11]
    y = [1, 2, 4, 7, 12]
    baseline = panel(values=x, objective=y)
    # A huge offset on 1e-15-unit inputs would itself discard significant float digits.
    transformed = panel(values=[x_scale * (value + 7) for value in x],
                        objective=[y_scale * value + 21 for value in y])
    assert transformed["skill"] == pytest.approx(baseline["skill"], abs=1e-10)
    assert transformed["utility"] == pytest.approx(baseline["utility"], abs=1e-10)
    assert transformed["predictions"] == pytest.approx([y_scale * value + 21 for value in baseline["predictions"]])


def test_missing_values_pay_baseline_loss_plus_an_explicit_penalty_and_are_not_dropped():
    y = np.asarray([1, 3, 4, 8, 11])
    result = panel(values=[1, None, 4, 8, 11], ridge=0)
    baseline_errors = (y - result["baseline_predictions"]) ** 2
    assert result["predictions"][1] is None
    expected_skill = 1 - (baseline_errors[1] + sum(baseline_errors) / 5) / sum(baseline_errors)
    assert result["skill"] == pytest.approx(expected_skill)
    assert result["failure_count"] == 1 and result["admitted_count"] == 4
    assert result["coverage"] == pytest.approx(0.8)
    assert all(1 not in indices for indices in result["fold_train_indices"])
    assert panel(values=[None] * 5)["skill"] == -1
    assert panel(values=[None] * 5)["raw_skill"] == -1


def test_constant_or_insufficient_paired_training_uses_the_baseline():
    constant = panel(values=[7] * 5)
    assert constant["predictions"] == constant["baseline_predictions"]
    assert constant["skill"] == 0 and not any(constant["fold_fit_used"])
    sparse = panel(values=[1, 2, None, None, None])
    assert sparse["predictions"][:2] == sparse["baseline_predictions"][:2]
    assert sparse["fold_fit_used"] == [False] * 5


def test_zero_reported_cost_cannot_create_an_unbounded_success_utility():
    zero = panel(costs=[0] * 5, quoted_cost=2, ridge=0)
    assert zero["actual_cost"] == 0 and zero["effective_cost"] == 10
    assert zero["skill"] == 1 and zero["utility"] == pytest.approx(0.1)
    overrun = panel(costs=[3] * 5, quoted_cost=2, ridge=0)
    assert overrun["effective_cost"] == 15 and overrun["utility"] == pytest.approx(1 / 15)


def test_negative_skill_is_clipped_but_raw_loss_remains_auditable():
    result = panel(values=[1, 2, 3, 4, 1000000], objective=[1, 2, 3, 4, 5])
    assert result["raw_skill"] < -1 and result["skill"] == -1 and result["utility"] == 0
    assert "not knowledge gradient" in result["scope"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("kwargs", [
    {"objective": [1, 2, 3]}, {"objective": [1] * 5}, {"values": [1, 2]},
    {"objective": [1, 2, float("nan"), 4, 5]}, {"values": [1, 2, float("inf"), 4, 5]},
    {"values": [1, 2, "3", 4, 5]}, {"costs": [1, 2, -1, 4, 5]},
    {"quoted_cost": 0}, {"quoted_cost": float("inf")}, {"ridge": -1},
])
def test_invalid_panel_data_is_rejected_explicitly(kwargs):
    with pytest.raises(ValueError):
        panel(**kwargs)


def arm_data():
    return {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [-1.0, 0.0], "observed": [1.0, 0.0]}


def rank(*, observed=(), features=None, costs=None, **kwargs):
    pool = arm_data() if features is None else features
    return rank_variants(pool, observed, dict.fromkeys(pool, 1.0) if costs is None else costs, **kwargs)


def test_cold_start_uses_the_declared_gaussian_prior_and_lexical_ties():
    result = rank()
    assert result["training_size"] == 0
    assert [row["id"] for row in result["ranking"]] == ["a", "b", "c", "observed"]
    assert all(row["mean"] == 0 and row["std"] == 1 and row["score"] == 1 for row in result["ranking"])
    assert "not calibrated confidence" in result["uncertainty"]


def test_feedback_transfers_to_related_arms_and_changes_the_next_ranking():
    def feedback(reward):
        return [{"id": "observed", "reward": reward, "evidence_hash": "completed-panel-evidence"}]

    positive = rank(observed=feedback(1), beta=0)
    negative = rank(observed=feedback(-1), beta=0)
    assert positive["ranking"][0]["id"] == "a"
    assert negative["ranking"][0]["id"] == "c"
    assert all(row["id"] != "observed" for row in positive["ranking"])
    assert positive["training_hash"] != negative["training_hash"]
    by_id = {row["id"]: row for row in positive["ranking"]}
    assert by_id["a"]["mean"] == pytest.approx(0.8)
    assert by_id["a"]["std"] == pytest.approx(np.sqrt(0.2))
    assert by_id["b"]["mean"] == 0 and by_id["b"]["std"] == 1


def test_ucb_uses_cost_and_latent_uncertainty_not_fake_measurement_noise():
    result = rank(features={"expensive": [2], "cheap": [1]}, costs={"expensive": 4, "cheap": 1},
                  beta=2, ridge=4, noise=999)
    assert [row["id"] for row in result["ranking"]] == ["cheap", "expensive"]
    assert result["ranking"][0]["std"] == 0.5 and result["ranking"][0]["score"] == 1


@pytest.mark.parametrize("policy", ["linear_ucb", "fixed", "random"])
def test_rankings_are_reproducible_after_restart_and_mapping_or_observation_reordering(policy):
    features = arm_data()
    observations = [{"id": "observed", "reward": 0.75, "evidence_hash": "panel-one"},
                    {"id": "b", "reward": -0.4, "evidence_hash": "panel-two"}]
    before = copy.deepcopy((features, observations))
    first = rank(features=features, observed=observations, policy=policy, seed=42)
    second = rank(features=dict(reversed(list(features.items()))), observed=list(reversed(observations)),
                  policy=policy, seed=42)
    assert first == second and (features, observations) == before
    assert first["training_size"] == 2
    assert {row["id"] for row in first["ranking"]} == {"a", "c"}
    if policy == "fixed":
        assert [row["id"] for row in first["ranking"]] == ["a", "c"]
    json.dumps(first, allow_nan=False)


def test_random_scores_are_stable_hashes_and_do_not_consume_numpy_rng_state():
    np.random.seed(7)
    state = np.random.get_state()
    before = rank(policy="random", seed=42)
    after = np.random.get_state()
    assert state[0] == after[0] and np.array_equal(state[1], after[1]) and state[2:] == after[2:]
    assert before == rank(policy="random", seed=42)
    assert before["training_hash"] != rank(policy="random", seed=43)["training_hash"]
    assert [row["score"] for row in before["ranking"]] != [row["score"] for row in rank(policy="random", seed=43)["ranking"]]


def test_only_explicitly_observed_rewards_enter_the_model():
    hidden_oracle = {"a": 1, "b": -1, "c": 0.2, "observed": 0.5}
    observed = [{"id": "observed", "reward": hidden_oracle["observed"], "evidence_hash": "one-query"}]
    before = rank(observed=observed)
    hidden_oracle.update({"a": -1, "b": 1, "c": -0.8})
    assert rank(observed=observed) == before
    with pytest.raises(ValueError, match="exactly"):
        rank(observed=[{**observed[0], "unqueried_rewards": hidden_oracle}])


@pytest.mark.parametrize("change", ["evidence", "features", "cost", "ridge", "noise", "beta", "policy"])
def test_training_fingerprint_pins_evidence_features_costs_and_hyperparameters(change):
    features = arm_data()
    observed = [{"id": "observed", "reward": 0.5, "evidence_hash": "one-query"}]
    costs = dict.fromkeys(features, 1)
    original = rank(features=features, observed=observed, costs=costs)
    kwargs = {}
    if change == "evidence":
        observed[0]["evidence_hash"] = "corrected-source-version"
    elif change == "features":
        features["a"][0] = 1.1
    elif change == "cost":
        costs["a"] = 1.1
    else:
        kwargs[change] = "fixed" if change == "policy" else 1.1
    assert original["training_hash"] != rank(features=features, observed=observed, costs=costs, **kwargs)["training_hash"]


def test_all_observed_arms_return_an_empty_ranking_without_losing_history():
    rows = [{"id": key, "reward": 0, "evidence_hash": f"panel:{key}"} for key in arm_data()]
    result = rank(observed=rows)
    assert result["ranking"] == [] and result["training_size"] == len(rows)


@pytest.mark.parametrize("kwargs", [
    {"features": {}}, {"features": {"a": []}}, {"features": {"a": [1], "b": [1, 2]}},
    {"features": {"a": [float("nan")]}}, {"features": {"a": [float("inf")]}},
    {"features": {"a": [True]}}, {"features": {"": [1]}},
    {"features": {str(i): [1] for i in range(MAX_ARMS + 1)}},
    {"features": {"a": [1] * (MAX_FEATURES + 1)}},
    {"costs": {"a": 1}}, {"costs": dict.fromkeys(arm_data(), 0)},
    {"costs": {key: float("inf") for key in arm_data()}},
    {"policy": "unknown"}, {"policy": ["linear_ucb"]},
    {"beta": -1}, {"ridge": 0}, {"noise": 0}, {"seed": True},
    {"observed": [{"id": "not-an-arm", "reward": 0, "evidence_hash": "x"}]},
    {"observed": [{"id": "a", "reward": 1.01, "evidence_hash": "x"}]},
    {"observed": [{"id": "a", "reward": float("nan"), "evidence_hash": "x"}]},
    {"observed": [{"id": "a", "reward": 0, "evidence_hash": ""}]},
    {"observed": [{"id": "a", "reward": 0, "evidence_hash": "x"},
                  {"id": "a", "reward": 1, "evidence_hash": "y"}]},
    {"observed": [{"id": "a", "reward": 0, "evidence_hash": "x"},
                  {"id": "b", "reward": 1, "evidence_hash": "x"}]},
])
def test_invalid_ranking_inputs_and_pseudoreplicated_feedback_are_rejected(kwargs):
    with pytest.raises(ValueError):
        rank(**kwargs)
