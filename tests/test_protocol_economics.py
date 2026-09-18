"""One-complete-audit improvement is bounded and distinct from posterior-mean KG."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from etalon.active import protocol_score
from etalon.active.knowledge import knowledge_gradient
from etalon.active.protocol_score import (
    AUDIT_RANKER_VERSION,
    _audit_expected_improvement,
    rank_variants,
)


def rank(*, features=None, observed=(), costs=None, opportunity_cost=0.02, **kwargs):
    pool = {"a": [1.0], "b": [0.0], "seen": [1.0]} if features is None else features
    prices = dict.fromkeys(pool, 1.0) if costs is None else costs
    return rank_variants(pool, observed, prices, policy="audit_ei", opportunity_cost=opportunity_cost, **kwargs)


@pytest.mark.parametrize("mean,scale,incumbent", [
    (-0.5, 0.1, 0.0), (0, 0.5, 0), (0.5, 0.5, 0), (1.5, 0.1, 0),
    (-0.5, 2, 0.6), (0, 2, 0.6), (0.5, 0.1, 0.6), (1.5, 2, 0.6),
    (0, 0.5, 1), (1.5, 2, 1),
])
def test_bounded_integral_matches_an_independent_closed_form_at_moderate_scales(mean, scale, incumbent):
    from scipy.special import ndtr

    def positive_part(threshold):
        distance = (mean - threshold) / scale
        return ((mean - threshold) * float(ndtr(distance))
                + scale * math.exp(-distance ** 2 / 2) / math.sqrt(2 * math.pi))

    expected = positive_part(incumbent) - positive_part(1)
    actual = _audit_expected_improvement(mean, scale, incumbent)
    assert actual == pytest.approx(expected, abs=1e-13)
    assert 0 <= actual <= 1 - incumbent


@pytest.mark.parametrize("mean,incumbent,expected", [
    (-2, 0, 0), (0.4, 0, 0.4), (0.4, 0.2, 0.2), (0.4, 0.6, 0), (2, 0.2, 0.8), (2, 1, 0),
])
def test_deterministic_limit_clips_before_measuring_improvement(mean, incumbent, expected):
    assert _audit_expected_improvement(mean, 0, incumbent) == pytest.approx(expected)
    assert _audit_expected_improvement(mean, 1e-300, incumbent) == pytest.approx(expected)


@pytest.mark.parametrize("mean,scale,incumbent", [(0.2, 0.4, 0.1), (-0.3, 1.5, 0.5), (1.1, 0.8, 0.7)])
def test_integral_agrees_with_independent_clipped_gaussian_monte_carlo(mean, scale, incumbent):
    draws = np.random.default_rng(923).normal(mean, scale, size=300000)
    gains = np.maximum(0, np.clip(draws, -1, 1) - incumbent)
    assert _audit_expected_improvement(mean, scale, incumbent) == pytest.approx(gains.mean(), abs=0.002)


def test_numerical_limits_never_subtract_two_enormous_gaussian_hinges():
    assert _audit_expected_improvement(1e300, 1, 0.3) == pytest.approx(0.7)
    assert _audit_expected_improvement(-1e300, 1, 0.3) == 0
    assert _audit_expected_improvement(0, 1e300, 0.3) == pytest.approx(0.35)
    assert _audit_expected_improvement(0.4, 1e-300, 0.1) == pytest.approx(0.3)
    tail = _audit_expected_improvement(-38, 1, 0)
    assert math.isfinite(tail) and 0 < tail < 1e-310


def test_fixed_audited_incumbent_is_not_refitted_to_the_posterior_mean():
    observed = [{"id": "seen", "reward": 0.9, "evidence_hash": "fixed-panel-realized-skill"}]
    result = rank(features={"seen": [0], "a": [0]}, observed=observed)
    assert result["economics"]["incumbent"] == 0.9
    assert result["economics"]["incumbent_ids"] == ["seen"]
    assert result["ranking"][0]["mean"] == 0
    assert result["ranking"][0]["expected_improvement"] == pytest.approx(_audit_expected_improvement(0, 0.5, 0.9))


def test_free_no_edit_option_dominates_negative_audited_scores():
    result = rank(observed=[{"id": "seen", "reward": -0.9, "evidence_hash": "bad-panel"}])
    assert result["economics"]["incumbent"] == 0
    assert result["economics"]["incumbent_ids"] == []
    assert result["economics"]["no_edit_score"] == 0
    assert rank()["economics"]["incumbent"] == 0


def test_incumbent_id_ties_are_explicit_and_deterministic():
    observed = [{"id": "seen", "reward": 0.7, "evidence_hash": "one"},
                {"id": "a", "reward": 0.7, "evidence_hash": "two"}]
    result = rank(observed=observed)
    assert result["economics"]["incumbent_ids"] == ["a", "seen"]
    assert [row["id"] for row in result["ranking"]] == ["b"]


def test_predictive_variance_includes_the_declared_residual_noise():
    result = rank(features={"a": [0]}, noise=0.5)
    row = result["ranking"][0]
    assert row["std"] == 0 and row["predictive_std"] == 0.5
    assert row["expected_improvement"] > 0
    assert row["opportunity_charge"] == 0.02
    assert row["score"] == pytest.approx(row["expected_improvement"] - 0.02)
    other = rank(features={"a": [2]}, noise=0.5)["ranking"][0]
    assert other["predictive_std"] == pytest.approx(math.hypot(other["std"], 0.5))


def test_full_skill_incumbent_has_no_impossible_above_one_gaussian_gain():
    result = rank(observed=[{"id": "seen", "reward": 1, "evidence_hash": "perfect-panel"}])
    assert all(row["expected_improvement"] == 0 and row["score"] < 0 for row in result["ranking"])
    assert result["economics"]["incumbent"] == 1


def test_audited_improvement_is_not_posterior_mean_knowledge_gradient():
    # The unqueried arm is not yet a permitted terminal choice. Even a deterministic
    # result can improve the audited choice by becoming eligible after its complete panel.
    audited_gain = _audit_expected_improvement(0.8, 0, 0.2)
    posterior_mean_kg = knowledge_gradient([0.2, 0.8], [0, 0])
    assert audited_gain == pytest.approx(0.6)
    assert posterior_mean_kg == 0


def test_policy_ranks_net_one_step_value_not_gain_per_cost():
    result = rank(features={"higher_gain": [2], "lower_gain": [0]},
                  costs={"higher_gain": 3, "lower_gain": 1}, opportunity_cost=0.02)
    high, low = result["ranking"]
    assert high["id"] == "higher_gain" and high["score"] > low["score"]
    assert high["expected_improvement"] / high["cost"] < low["expected_improvement"] / low["cost"]


def test_opportunity_charge_is_explicit_changes_the_hash_and_can_make_all_options_negative():
    low = rank(opportunity_cost=0.01)
    high = rank(opportunity_cost=2)
    low_rows, high_rows = ({row["id"]: row for row in report["ranking"]} for report in (low, high))
    assert low["training_hash"] != high["training_hash"]
    for identifier, row in low_rows.items():
        assert row["expected_improvement"] == high_rows[identifier]["expected_improvement"]
        assert row["score"] - high_rows[identifier]["score"] == pytest.approx(1.99 * row["cost"])
    assert all(row["score"] < 0 for row in high["ranking"])
    # Numerical ranking does not itself dispatch or silently drop negative alternatives.
    assert len(high["ranking"]) == 3


def test_cost_unit_change_with_inverse_exchange_rate_preserves_economic_decisions():
    costs = {"a": 1, "b": 2, "seen": 3}
    original = rank(costs=costs, opportunity_cost=0.03)
    converted = rank(costs={key: value * 1000 for key, value in costs.items()}, opportunity_cost=0.00003)
    assert [row["id"] for row in original["ranking"]] == [row["id"] for row in converted["ranking"]]
    assert [row["score"] for row in original["ranking"]] == pytest.approx([row["score"] for row in converted["ranking"]])


def test_beta_and_seed_do_not_change_audit_economic_rankings():
    one = rank(beta=0, seed=0)
    two = rank(beta=1e300, seed=123456)
    assert one["ranking"] == two["ranking"]
    assert one["economics"] == two["economics"]
    assert one["training_hash"] != two["training_hash"]  # Inputs remain auditable.


def test_audit_policy_has_a_separate_version_and_explicit_approximation_scope():
    result = rank()
    assert result["version"] == AUDIT_RANKER_VERSION != "shared-linear-protocol-ranker/1"
    assert result["economics"]["opportunity_cost"] == 0.02
    assert "not a censored likelihood" in result["economics"]["scope"]
    assert "Not cross-arm knowledge gradient" in result["economics"]["scope"]
    json.dumps(result, allow_nan=False)


def test_prediction_contract_version_is_fingerprinted_only_for_the_opt_in_policy(monkeypatch):
    audit = rank()
    legacy = rank_variants({"a": [1]}, [], {"a": 1})
    monkeypatch.setattr(protocol_score, "AUDIT_ECONOMICS_VERSION", "test-only-revised-prediction-contract")
    changed = rank()
    assert changed["training_hash"] != audit["training_hash"]
    assert changed["economics"]["version"] == "test-only-revised-prediction-contract"
    assert changed["ranking"] == audit["ranking"]
    assert rank_variants({"a": [1]}, [], {"a": 1}) == legacy


@pytest.mark.parametrize("value", [None, 0, -1, float("inf"), float("nan"), True, "0.1"])
def test_economic_policy_requires_an_explicit_positive_finite_exchange_rate(value):
    with pytest.raises(ValueError, match="opportunity_cost"):
        rank(opportunity_cost=value)


@pytest.mark.parametrize("policy", ["linear_ucb", "random", "fixed"])
def test_exchange_rate_cannot_silently_change_a_legacy_policy(policy):
    with pytest.raises(ValueError, match="only defined"):
        rank_variants({"a": [1]}, [], {"a": 1}, policy=policy, opportunity_cost=0.1)


@pytest.mark.parametrize("arguments", [(float("inf"), 1, 0), (0, -1, 0), (0, 1, -0.1), (0, 1, 1.1)])
def test_invalid_integral_inputs_are_rejected(arguments):
    with pytest.raises(ValueError):
        _audit_expected_improvement(*arguments)


@pytest.mark.parametrize("policy,expected_ids,expected_scores,expected_hash", [
    ("linear_ucb", ["a", "b"], [0.8472135954999579, 0.42360679774997895],
     "7b2265aed0ee75baf1454d08db60307951a999ce3a974dd68bc8a38d7bb240c6"),
    ("random", ["b", "a"], [0.8771344652781969, 0.3598654867153208],
     "3d45f41e759ba4fa8ac5f0aafbef38b26c50fdbc5133d45cb004f9657fa868ad"),
    ("fixed", ["a", "b"], [0, 0],
     "930ae06693d0e296892668f59b7926b9a9a840708badcd135ce6f01396a32a48"),
])
def test_legacy_outputs_and_fingerprints_match_pre_extension_golden_records(
        policy, expected_ids, expected_scores, expected_hash):
    features = {"a": [1.0], "b": [1.0], "seen": [1.0]}
    observed = [{"id": "seen", "reward": 0.5, "evidence_hash": "evidence-one"}]
    costs = {"a": 1.0, "b": 2.0, "seen": 1.0}
    expected = {
        "ranking": [{"id": identifier, "score": score, "mean": 0.39999999999999997,
                     "std": 0.4472135954999579, "cost": costs[identifier]}
                    for identifier, score in zip(expected_ids, expected_scores, strict=True)],
        "training_hash": expected_hash, "training_size": 1, "policy": policy,
        "version": "shared-linear-protocol-ranker/1",
        "uncertainty": "Posterior latent standard deviation under a shared Gaussian linear working model; "
                       "not calibrated confidence, independent validation, or a generalization guarantee.",
        "feature_scope": "Caller-supplied features; no implicit normalization or intercept.",
    }
    assert rank_variants(features, observed, costs, policy=policy) == expected
    assert rank_variants(features, observed, costs, policy=policy, opportunity_cost=None) == expected
