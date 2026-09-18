"""Numerical audit: representable uncertainty must not vanish in an intermediate square."""

import math

import numpy as np
import pytest
from scipy.linalg import cho_solve, solve_triangular

from etalon.active.protocol_score import rank_variants


@pytest.mark.parametrize("scale", [1e-200, 1e-160, 1e160, 1e200])
@pytest.mark.parametrize("policy", ["linear_ucb", "audit_ei"])
def test_prior_uncertainty_survives_intermediate_square_range(scale, policy):
    kwargs = {"opportunity_cost": 0.1} if policy == "audit_ei" else {}
    result = rank_variants({"a": [scale, scale]}, [], {"a": 1.0}, policy=policy, **kwargs)
    row = result["ranking"][0]
    assert row["std"] == pytest.approx(math.hypot(scale, scale), rel=1e-14, abs=0)
    assert row["std"] > 0
    if policy == "linear_ucb":
        assert row["score"] == row["std"]
    else:
        assert row["predictive_std"] == math.hypot(row["std"], 0.5)


def test_tiny_nonzero_features_are_distinct_from_genuinely_zero_prior_variance():
    result = rank_variants({"tiny": [1e-200], "zero": [0.0]}, [], {"tiny": 1.0, "zero": 1.0})
    tiny, zero = result["ranking"]
    assert tiny["id"] == "tiny" and tiny["std"] == 1e-200
    assert zero["id"] == "zero" and zero["std"] == 0.0


def test_extreme_feature_norm_with_one_acquired_label_still_has_finite_uncertainty():
    observed = [{"id": "a", "reward": 0.5, "evidence_hash": "a-result"}]
    result = rank_variants({"a": [1e-200], "b": [2e-200]}, observed, {"a": 1.0, "b": 1.0})
    assert result["ranking"][0]["std"] == 2e-200


def test_ordinary_posterior_arithmetic_and_scores_are_bitwise_preserved():
    features = {"a": [0.5, 0.7], "b": [-0.8, 0.1], "c": [1.0, 1.0]}
    observed = [{"id": "a", "reward": 0.6, "evidence_hash": "a-result"}]
    result = rank_variants(features, observed, dict.fromkeys(features, 1.0))
    training = np.asarray([features["a"]])
    precision = np.eye(2) + training.T @ training / 0.25
    response = training.T @ np.asarray([0.6]) / 0.25
    chol = np.linalg.cholesky(precision)
    pool = np.asarray([features[key] for key in sorted(features)])
    means = pool @ cho_solve((chol, True), response)
    historical_sd = np.sqrt(np.sum(solve_triangular(chol, pool.T, lower=True) ** 2, axis=0))
    for row in result["ranking"]:
        index = sorted(features).index(row["id"])
        assert row["mean"] == float(means[index])
        assert row["std"] == float(historical_sd[index])
        assert row["score"] == float(max(0.0, means[index] + historical_sd[index]))


def test_truly_unrepresentable_prior_standard_deviation_is_explicitly_rejected():
    with pytest.raises(ValueError, match="linear posterior exceeds numerical range"):
        rank_variants({"a": [1.7e308, 1.7e308]}, [], {"a": 1.0})
