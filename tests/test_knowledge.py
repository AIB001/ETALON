"""Independent checks of posterior moments and exact one-observation decision value."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import quad

from etalon.active.knowledge import knowledge_gradient, query_knowledge_gradient
from etalon.active.model import MultiEndpointGP
from etalon.active.schema import Candidate, Endpoint, Evaluation


def model_fixture(*, trained=True, scale=1.0, direction="maximize"):
    candidates = {str(i): Candidate(str(i), "C", (float(i), float(i % 2)))
                  for i in range(7)}
    endpoints = {
        "high": Endpoint("high", "target", "affinity", "u", "high/1", 8,
                         noise=0.15, prior_scale=2, requires_handoff=False, direction=direction),
        "low": Endpoint("low", "target", "proxy", "v", "low/1", 1,
                        noise=0.4 * scale, prior_scale=3 * scale, requires_handoff=False),
    }
    observations = [{"admitted": True,
                     "result": Evaluation(str(i), task, (i if task == "high" else -3 * i * scale),
                                          endpoints[task].units, endpoints[task].cost).as_dict()}
                    for i in range(4) for task in endpoints] if trained else []
    return MultiEndpointGP(candidates, endpoints, "high", observations)


@pytest.mark.parametrize("trained", [False, True])
def test_posterior_covariance_transpose_diagonal_and_predict(trained):
    model = model_fixture(trained=trained)
    left, right = ["1", "4", "6"], ["0", "5"]
    covariance = model.posterior_covariance(left, "high", right, "low")
    reverse = model.posterior_covariance(right, "low", left, "high")
    np.testing.assert_allclose(covariance, reverse.T, atol=1e-14)
    for task in model.endpoints:
        square = model.posterior_covariance(left, task, left, task)
        _, sd = model.predict(left, task)
        np.testing.assert_allclose(np.diag(square), sd**2, atol=1e-14)
        assert np.linalg.eigvalsh(square).min() >= -1e-12
    assert model.posterior_covariance([], "high", right, "low").shape == (0, 2)
    assert model.posterior_covariance(left, "high", [], "low").shape == (3, 0)


def test_cross_covariance_has_signed_transfer_and_original_units():
    first, scaled = model_fixture(), model_fixture(scale=1000)
    ids = ["4", "5", "6"]
    cross = first.posterior_covariance(ids, "high", ids, "low")
    assert np.all(np.diag(cross) < 0)
    np.testing.assert_allclose(scaled.posterior_covariance(ids, "high", ids, "low"),
                               cross * 1000, rtol=1e-11, atol=1e-11)
    np.testing.assert_allclose(scaled.posterior_covariance(ids, "low", ids, "low"),
                               first.posterior_covariance(ids, "low", ids, "low") * 1e6,
                               rtol=1e-11, atol=1e-10)


@pytest.mark.parametrize("trained", [True, False])
def test_chunked_predictions_preserve_order_repeats_and_covariance(trained, monkeypatch):
    model = model_fixture(trained=trained)
    ids = ["6", "0", "4", "4", "1", "5", "2"]
    expected = model.predict(ids, "low", batch_size=100)
    reduction = model.objective_reduction(ids, "low", batch_size=100)
    covariance = model.posterior_covariance(ids, "low", ids, "low")
    original = model._query

    def limited(ids, endpoint):
        assert len(ids) <= 2, "prediction must not allocate the entire candidate/training rectangle"
        return original(ids, endpoint)

    monkeypatch.setattr(model, "_query", limited)
    actual = model.predict(ids, "low", batch_size=2)
    np.testing.assert_allclose(actual, expected, atol=1e-12)
    np.testing.assert_allclose(actual[1] ** 2, np.diag(covariance), atol=1e-12)
    np.testing.assert_allclose(model.objective_reduction(ids, "low", batch_size=2), reduction, atol=1e-12)


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_prediction_refuses_invalid_allocation_bounds(batch_size):
    model = model_fixture()
    with pytest.raises(ValueError, match="batch_size"):
        model.predict(["1"], "high", batch_size=batch_size)
    with pytest.raises(ValueError, match="batch_size"):
        model.objective_reduction(["1"], "high", batch_size=batch_size)


def test_equal_mean_two_alternative_analytic_value():
    expected = 5 / math.sqrt(2 * math.pi)
    assert knowledge_gradient([4, 4], [-2, 3]) == pytest.approx(expected, rel=1e-14)
    assert knowledge_gradient([4, 4, -20], [-2, 3, 0]) == pytest.approx(expected, rel=1e-14)


def test_parallel_lines_and_single_alternative_have_zero_value():
    assert knowledge_gradient([-2, 5, 1], [3, 3, 3]) == 0
    assert knowledge_gradient([100], [1e6]) == 0


def test_translation_and_permutation_invariance():
    means = np.asarray([-2.0, 0.5, 1.0, 0.0, -10])
    slopes = np.asarray([3.0, 0.2, -1.0, 0.2, 8])
    gain = knowledge_gradient(means, slopes)
    assert knowledge_gradient(means + 1e9, slopes) == pytest.approx(gain, rel=1e-12)
    assert knowledge_gradient(means, slopes + 1000) == pytest.approx(gain, rel=1e-12)
    assert knowledge_gradient(means[::-1], slopes[::-1]) == pytest.approx(gain, rel=1e-14)
    assert knowledge_gradient(7 * means, 7 * slopes) == pytest.approx(7 * gain, rel=1e-14)


def test_knowledge_gradient_matches_numerical_integral_and_monte_carlo():
    means = np.asarray([0.8, -0.1, 0.4, -1.2, -4])
    slopes = np.asarray([-0.8, 1.0, 0.2, 2.1, -3])
    exact = knowledge_gradient(means, slopes)
    integral, _ = quad(lambda z: (np.max(means + slopes * z) - means.max())
                      * math.exp(-z*z/2) / math.sqrt(2 * math.pi), -12, 12,
                      epsabs=1e-10, points=[-4, -2, -1, 0, 1, 2, 4], limit=200)
    assert exact == pytest.approx(integral, abs=1e-8)
    rng = np.random.default_rng(417)
    z = rng.standard_normal(200_000)
    # Antithetic sampling gives a stable independent, nonnegative MC check.
    samples = (np.max(means[:, None] + slopes[:, None] * z, axis=0)
               + np.max(means[:, None] - slopes[:, None] * z, axis=0)) / 2 - means.max()
    assert abs(exact - samples.mean()) < 6 * samples.std() / math.sqrt(len(z))
    assert exact >= 0


def test_tiny_tail_value_survives_large_incumbent_subtraction():
    from scipy.special import ndtr

    threshold = 12.0
    expected = math.exp(-threshold**2 / 2) / math.sqrt(2 * math.pi) - threshold * ndtr(-threshold)
    value = knowledge_gradient([0, -threshold], [0, 1])
    assert value > 0
    assert value == pytest.approx(expected, rel=1e-10, abs=0)
    assert knowledge_gradient([0, -100], [0, 1]) == 0


@pytest.mark.parametrize("means,slopes", [([], []), ([1], [1, 2]), ([[1]], [[2]]),
                                         ([float("nan")], [1]), ([1], [float("inf")])])
def test_invalid_knowledge_gradient_inputs_refused(means, slopes):
    with pytest.raises(ValueError):
        knowledge_gradient(means, slopes)


def test_query_values_match_manual_conditioning_and_chunking():
    model = model_fixture()
    decisions, queries = ["0", "2", "4", "6"], ["1", "3", "5"]
    means, _ = model.predict(decisions, "high")
    _, sd = model.predict(queries, "low")
    cross = model.posterior_covariance(decisions, "high", queries, "low")
    expected = [knowledge_gradient(means, cross[:, i] / math.hypot(sd[i], model.endpoints["low"].noise))
                for i in range(len(queries))]
    actual = query_knowledge_gradient(model, decisions, queries, "low", chunk_size=1)
    np.testing.assert_allclose(actual, expected, rtol=1e-12)
    np.testing.assert_allclose(query_knowledge_gradient(model, decisions, queries, "low", chunk_size=100),
                               actual, rtol=1e-12)
    assert np.all(actual >= 0)
    assert query_knowledge_gradient(model, decisions, [], "low").shape == (0,)


@pytest.mark.parametrize("scale", [1000, 1e-16])
def test_query_values_are_independent_of_proxy_units(scale):
    first, scaled = model_fixture(), model_fixture(scale=scale)
    np.testing.assert_allclose(first.task_cov, scaled.task_cov, rtol=1e-12)
    np.testing.assert_allclose(query_knowledge_gradient(first, first.ids, first.ids, "low"),
                               query_knowledge_gradient(scaled, scaled.ids, scaled.ids, "low"),
                               rtol=1e-10, atol=1e-12)


def test_independent_endpoint_has_no_objective_knowledge_value():
    model = model_fixture(trained=False)
    np.testing.assert_array_equal(query_knowledge_gradient(model, model.ids, model.ids, "low"), 0)
    assert np.all(query_knowledge_gradient(model, model.ids, model.ids, "high") > 0)


def test_minimize_uses_negated_objective_utilities_and_covariances():
    model = model_fixture(direction="minimize")
    ids = ["1", "3", "5"]
    means, sd = model.predict(ids, "high")
    covariance = model.posterior_covariance(ids, "high", ids, "high")
    expected = [knowledge_gradient(-means, -covariance[:, i] / math.hypot(sd[i], model.endpoints["high"].noise))
                for i in range(len(ids))]
    np.testing.assert_allclose(query_knowledge_gradient(model, ids, ids, "high"), expected)
    # Direction changes acquisition utility, not physical label covariance.
    model.endpoints["high"] = replace(model.endpoints["high"], direction="maximize")
    np.testing.assert_allclose(model.posterior_covariance(ids, "high", ids, "high"), covariance)


@pytest.mark.parametrize("chunk_size", [0, -1, 1.5, True])
def test_invalid_chunk_size_refused(chunk_size):
    model = model_fixture(trained=False)
    with pytest.raises(ValueError, match="chunk_size"):
        query_knowledge_gradient(model, model.ids, model.ids, "high", chunk_size=chunk_size)


def test_empty_decision_set_refused():
    model = model_fixture(trained=False)
    with pytest.raises(ValueError, match="decision set"):
        query_knowledge_gradient(model, [], model.ids, "high")
