"""Finite-alternative, one-observation Gaussian knowledge gradient.

This is a known Bayesian experimental-design acquisition, not a new ETALON invention. It values
the expected improvement of the best posterior-mean *decision*, rather than the same molecule's
uncertainty reduction. The integral is exact for fixed Gaussian model parameters and a finite
decision set (up to floating-point error), not a calibration or real-world optimality guarantee.
Neither function refits the model or learns its hyperparameters inside hypothetical outcomes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from etalon.active.model import MultiEndpointGP


def _normal_hinge(distance: float) -> float:
    """E[(Z - distance)+] for Z standard Normal and nonnegative distance.

    Log survival probabilities and expm1 retain small tail differences. Beyond 39 standard
    deviations the result is below double precision's subnormal range and is returned as zero.
    """
    from scipy.special import log_ndtr

    if distance >= 39:
        return 0.0
    log_density = -0.5 * distance * distance - 0.5 * math.log(2 * math.pi)
    if distance == 0:
        return math.exp(log_density)
    log_ratio = math.log(distance) + float(log_ndtr(-distance)) - log_density
    return math.exp(log_density) * max(-math.expm1(min(log_ratio, 0.0)), 0.0)


def _weighted_normal_hinge(distance: float, weight: Any) -> Any:
    """Multiply a Gaussian hinge without losing a representable weighted tail.

    A unit hinge can underflow even when multiplying it by a large, finite physical
    slope restores a representable value. Ordinary inputs retain the historical
    arithmetic. At distance >= 55 even the largest difference between two finite
    float64 slopes cannot restore a representable float64 contribution.
    """
    import numpy as np
    from scipy.special import log_ndtr

    if distance < 37:
        return weight * _normal_hinge(distance)
    if distance >= 55 or weight == 0:
        return 0.0
    log_density = -0.5 * distance * distance - 0.5 * math.log(2 * math.pi)
    log_ratio = math.log(distance) + float(log_ndtr(-distance)) - log_density
    correction = -math.expm1(min(log_ratio, 0.0))
    return np.exp(np.log(np.longdouble(weight)) + log_density + math.log(correction))


def knowledge_gradient(means: Sequence[float], slopes: Sequence[float]) -> float:
    """Return E[max_i(means[i] + slopes[i] Z)] - max_i(means[i]), Z ~ N(0, 1).

    Larger means are better. Identical slopes retain only their largest intercept. Sorting by
    slope and constructing the affine upper envelope costs O(n log n), then integration is
    linear in the number of envelope segments. A common shift of all means OR all slopes has
    no effect. Only nonnegative hinge contributions are summed, avoiding cancellation against
    a possibly large incumbent mean. This is a single-observation, not joint-batch acquisition.
    """
    import numpy as np

    a, b = np.asarray(means, dtype=float), np.asarray(slopes, dtype=float)
    if a.ndim != 1 or b.ndim != 1 or len(a) != len(b) or not len(a):
        raise ValueError("means and slopes must be nonempty one-dimensional arrays of equal length")
    if not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        raise ValueError("knowledge-gradient means and slopes must be finite")
    # Extended intermediates avoid overflow when subtracting finite, opposite-sign inputs.
    # They do not recover precision already absent from the caller's float64 observations.
    lines = sorted(zip(b.astype(np.longdouble), a.astype(np.longdouble), strict=True))
    distinct = []
    for slope, intercept in lines:
        if distinct and slope == distinct[-1][0]:
            distinct[-1] = (slope, intercept)  # Sorted intercepts: the largest parallel line wins.
        else:
            distinct.append((slope, intercept))
    hull: list[tuple[Any, Any]] = []
    starts: list[Any] = []
    for slope, intercept in distinct:
        start = -math.inf
        while hull:
            previous_slope, previous_intercept = hull[-1]
            start = (previous_intercept - intercept) / (slope - previous_slope)
            if start > starts[-1]:
                break
            hull.pop()
            starts.pop()
        hull.append((slope, intercept))
        starts.append(start if len(hull) > 1 else -math.inf)
    # A convex piecewise-affine function is a line plus nonnegative slope jumps times hinges.
    # Subtracting its value at zero removes the intercept; E[Z] removes the common linear part.
    # Both signs of a breakpoint have the same contribution: phi(|t|) - |t| Phi(-|t|).
    gain = np.longdouble(0)
    for index in range(1, len(hull)):
        jump = hull[index][0] - hull[index - 1][0]
        gain += _weighted_normal_hinge(float(abs(starts[index])), jump)
    result = float(gain)
    if not math.isfinite(result):
        raise ValueError("knowledge-gradient value exceeds finite floating-point range")
    return max(result, 0.0)


def query_knowledge_gradient(model: MultiEndpointGP, decision_ids: Sequence[str],
                             query_ids: Sequence[str], endpoint: str, *,
                             chunk_size: int = 128) -> Any:
    """Value each query against every supplied terminal decision; return a float NumPy vector.

    The observation innovation is one standard Normal variate, with slopes given by objective
    cross-covariance divided by the query's predictive observation SD. Future observation noise
    uses the registered endpoint floor; actual heteroscedastic uncertainty is not known yet.
    Costs, validity, attempt eligibility and terminal verification constraints belong to the
    calling policy, not this mathematical kernel.

    Query columns are chunked to bound cross-covariance memory, never silently pruned. Work is
    still quadratic for a full pool queried against itself: callers must impose explicit pool
    limits or declare a reduced decision set. Output is independent of chunk size.
    """
    import numpy as np

    if len(decision_ids) == 0:
        raise ValueError("knowledge gradient needs a nonempty terminal decision set")
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size < 1:
        raise ValueError("chunk_size must be a positive integer")
    objective = model.objective
    sign = 1 if model.endpoints[objective].direction == "maximize" else -1
    noise = model.endpoints[endpoint].noise
    means, _ = model.predict(decision_ids, objective)
    utilities = sign * means
    values = np.empty(len(query_ids), dtype=float)
    for start in range(0, len(query_ids), chunk_size):
        queries = query_ids[start:start + chunk_size]
        _, query_sd = model.predict(queries, endpoint)
        covariance = model.posterior_covariance(decision_ids, objective, queries, endpoint)
        slopes = sign * covariance / np.hypot(query_sd, noise)[None, :]
        for offset in range(len(queries)):
            values[start + offset] = knowledge_gradient(utilities, slopes[:, offset])
    return values
