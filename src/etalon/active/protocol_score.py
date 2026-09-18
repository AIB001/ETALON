"""Small, inspectable numerical policies for reviewed protocol experiments.

These functions neither execute tools nor select their own evidence. A caller must supply
an immutable audit panel and only completed protocol rewards. Panel selection skill is not
an independent campaign evaluation, a generalization guarantee, or knowledge gradient.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from etalon.active.schema import digest

PANEL_VERSION = "fixed-panel-affine-loo/1"
RANKER_VERSION = "shared-linear-protocol-ranker/1"
AUDIT_RANKER_VERSION = "shared-linear-audit-ei-ranker/1"
AUDIT_ECONOMICS_VERSION = "clipped-panel-audit-ei/1"
MAX_ARMS = 256
MAX_FEATURES = 512


def _number(value: Any, name: str, *, lower: float | None = None, strict: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    try:
        converted = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} is outside the finite numerical range") from error
    if (not math.isfinite(converted)
            or (lower is not None and (converted <= lower if strict else converted < lower))):
        relation = ">" if strict else ">="
        raise ValueError(f"{name} must be finite" + (f" and {relation} {lower}" if lower is not None else ""))
    return converted


def _sequence(value: Any, name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError(f"{name} must be a sequence of numbers")
    try:
        return tuple(value)
    except TypeError as error:
        raise ValueError(f"{name} must be a sequence of numbers") from error


def _finite_output(value: Any, name: str) -> float:
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} exceeds numerical range; rescale the supplied units")
    return converted


def panel_skill(objective: Sequence[float], values: Sequence[float | None], costs: Sequence[float],
                quoted_cost: float, *, ridge: float = 0.1) -> dict[str, Any]:
    """LOO affine transfer skill on a preselected, fixed objective audit panel.

    Each held-out prediction excludes its objective label from all fitting, centering and
    scaling. Unfit folds fall back to the other objective labels' mean. A missing held-out
    value contributes its baseline squared error PLUS one nth of the total baseline SSE;
    dropping failures from either the loss or its denominator is forbidden.

    ``fold_train_indices`` lists available paired training rows, even when too few or
    constant to fit; ``fold_fit_used`` distinguishes those baseline fallbacks. Costs are
    real nonnegative reported totals with a full-panel quote floor, never a zero-cost win.
    This reuses an audit panel for protocol selection; LOO does NOT make it an independent
    downstream campaign test. No campaign GP or labels outside this panel are consulted.
    """
    import numpy as np

    raw_y, raw_x, raw_costs = (_sequence(objective, "objective"), _sequence(values, "values"),
                              _sequence(costs, "costs"))
    size = len(raw_y)
    if size < 4 or len(raw_x) != size or len(raw_costs) != size:
        raise ValueError("the fixed panel needs at least four rows and equal objective/value/cost lengths")
    y = np.asarray([_number(value, "objective") for value in raw_y], dtype=np.longdouble)
    if np.all(y == y[0]):
        raise ValueError("objective panel must be nonconstant")
    x = [None if value is None else _number(value, "value") for value in raw_x]
    expenses = [_number(value, "cost", lower=0) for value in raw_costs]
    quote = _number(quoted_cost, "quoted_cost", lower=0, strict=True)
    penalty = _number(ridge, "ridge", lower=0)
    indices = list(range(size))
    baseline = [np.mean(y[[j for j in indices if j != i]]) for i in indices]
    baseline_errors = [(y[i] - baseline[i]) ** 2 for i in indices]
    baseline_sse = np.sum(baseline_errors, dtype=np.longdouble)
    predictions: list[float | None] = []
    train_indices, fitted, errors = [], [], []
    for heldout in indices:
        training = [j for j in indices if j != heldout and x[j] is not None]
        train_indices.append(training)
        fit_used = False
        prediction = baseline[heldout]
        if x[heldout] is not None and len(training) >= 3:
            train_x = np.asarray([x[j] for j in training], dtype=np.longdouble)
            x_mean = np.mean(train_x)
            x_scale = np.sqrt(np.mean((train_x - x_mean) ** 2))
            # No absolute raw-unit threshold: changing units must not change degeneracy.
            if x_scale > 0 and np.isfinite(x_scale):
                z = (train_x - x_mean) / x_scale
                train_y = y[training]
                y_mean = np.mean(train_y)
                slope = np.sum(z * (train_y - y_mean)) / (np.sum(z ** 2) + penalty * len(training))
                prediction = y_mean + slope * ((np.longdouble(x[heldout]) - x_mean) / x_scale)
                fit_used = True
        fitted.append(fit_used)
        if x[heldout] is None:
            predictions.append(None)
            errors.append(baseline_errors[heldout] + baseline_sse / size)
        else:
            predictions.append(_finite_output(prediction, "prediction"))
            errors.append((y[heldout] - prediction) ** 2)
    sse = np.sum(errors, dtype=np.longdouble)
    raw_skill = _finite_output(1 - sse / baseline_sse, "raw skill")
    skill = min(1.0, max(-1.0, raw_skill))
    actual_cost = _finite_output(np.sum(expenses, dtype=np.longdouble), "actual cost")
    quote_floor = _finite_output(np.longdouble(size) * quote, "full-panel quote")
    effective_cost = max(actual_cost, quote_floor)
    utility = _finite_output(max(0.0, skill) / effective_cost, "utility")
    admitted = sum(value is not None for value in x)
    return {
        "version": PANEL_VERSION, "panel_size": size, "skill": skill, "raw_skill": raw_skill,
        "predictions": predictions, "baseline_predictions": [_finite_output(v, "baseline") for v in baseline],
        "fold_train_indices": train_indices, "fold_fit_used": fitted,
        "admitted_count": admitted, "failure_count": size - admitted, "coverage": admitted / size,
        "actual_cost": actual_cost, "quoted_cost": quote, "effective_cost": effective_cost,
        "utility": utility, "ridge": penalty,
        "failure_penalty": "baseline squared error plus baseline SSE / panel size per missing value",
        "scope": "Fixed audit-panel protocol-selection score; not knowledge gradient, independent campaign "
                 "evaluation, or a generalization guarantee. Repeated selection can overfit this panel.",
    }


def _audit_expected_improvement(mean: float, predictive_std: float, incumbent: float) -> float:
    """E[(clip(N(mean, predictive_std**2), -1, 1) - incumbent)+].

    The incumbent is an already observed audit score, including the free zero-score
    option. Integrating a bounded survival probability avoids subtracting two potentially
    enormous Gaussian hinges. Splits at the mean and eight-sigma shoulders resolve narrow
    transitions. This is numerical quadrature, not a posterior-mean knowledge gradient.
    """
    from scipy.integrate import quad
    from scipy.special import log_ndtr, ndtr

    location = _number(mean, "predictive mean")
    scale = _number(predictive_std, "predictive_std", lower=0)
    best = _number(incumbent, "incumbent", lower=0)
    if best > 1:
        raise ValueError("incumbent must be a bounded audit score in [0, 1]")
    if best == 1:
        return 0.0
    if scale == 0:
        return max(0.0, min(1.0, location) - best)
    points = sorted({point for point in (location, location - 8 * scale, location + 8 * scale)
                     if best < point < 1})

    def survival(threshold: float) -> float:
        z = (location - threshold) / scale
        # scipy.ndtr can underflow before the subnormal limit in the far negative tail.
        return math.exp(float(log_ndtr(z))) if z < -8 else float(ndtr(z))

    integral, _ = quad(survival, best, 1.0, points=points, epsabs=1e-13, epsrel=1e-11, limit=100)
    return min(1.0 - best, max(0.0, _finite_output(integral, "audited expected improvement")))


def rank_variants(features: Mapping[str, Sequence[float]], observed: Sequence[dict[str, Any]],
                  costs: Mapping[str, float], *, policy: str = "linear_ucb", beta: float = 1.0,
                  ridge: float = 1.0, noise: float = 0.5, seed: int = 0,
                  opportunity_cost: float | None = None) -> dict[str, Any]:
    """Rank unobserved protocol arms with a shared Bayesian linear working model.

    Uses the supplied feature map unchanged: no implicit scaling or intercept. Include a
    constant feature explicitly if desired. Prior weights are N(0, I/ridge), observation
    noise is the supplied standard deviation, and returned std is latent uncertainty.
    Rewards must be distinct completed protocol skills in [-1, 1], not hidden oracle labels.
    The Gaussian working model is not constrained to predict within those bounds.

    ``fixed`` orders ids; ``random`` hashes immutable inputs, with no mutable RNG state.
    Neither alternative uses reward predictions for ranking. All policies expose the same
    fitted model for auditability, and exclude every already-observed id.

    Opt-in ``audit_ei`` instead values the next fully audited, clipped realized score above
    the best frozen score (or free zero-score option), minus an explicitly positive exchange
    rate times the complete panel quote. It does not make unaudited arms terminally eligible,
    predict multi-step transfer value, or perform stopping/affordability checks for callers.
    Beta and seed are validated and fingerprinted but do not affect this policy's ranking.
    Its separate version leaves the legacy policies' output and fingerprints unchanged.
    """
    import numpy as np
    from scipy.linalg import cho_solve, solve_triangular

    if not isinstance(policy, str) or policy not in {"linear_ucb", "random", "fixed", "audit_ei"}:
        raise ValueError("policy must be linear_ucb, random, fixed or audit_ei")
    if policy == "audit_ei":
        exchange_rate = _number(opportunity_cost, "opportunity_cost", lower=0, strict=True)
    elif opportunity_cost is not None:
        raise ValueError("opportunity_cost is only defined for the opt-in audit_ei policy")
    else:
        exchange_rate = None
    exploration = _number(beta, "beta", lower=0)
    prior_precision = _number(ridge, "ridge", lower=0, strict=True)
    observation_noise = _number(noise, "noise", lower=0, strict=True)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if not isinstance(features, Mapping) or not 1 <= len(features) <= MAX_ARMS:
        raise ValueError(f"features must declare between 1 and {MAX_ARMS} protocol arms")
    if any(not isinstance(key, str) or not key.strip() for key in features):
        raise ValueError("protocol ids must be nonempty strings")
    ids = sorted(features)
    vectors = {key: [_number(value, "feature") for value in _sequence(features[key], "feature vector")]
               for key in ids}
    width = len(vectors[ids[0]])
    if not 1 <= width <= MAX_FEATURES or any(len(row) != width for row in vectors.values()):
        raise ValueError(f"feature vectors need one common width between 1 and {MAX_FEATURES}")
    if not isinstance(costs, Mapping) or set(costs) != set(ids):
        raise ValueError("cost ids must exactly match the declared protocol arms")
    quotes = {key: _number(costs[key], "cost", lower=0, strict=True) for key in ids}
    rows, seen_ids, seen_evidence = [], set(), set()
    for row in _sequence(observed, "observations"):
        if not isinstance(row, Mapping) or set(row) != {"id", "reward", "evidence_hash"}:
            raise ValueError("each observation must contain exactly id, reward and evidence_hash")
        identifier, evidence = row["id"], row["evidence_hash"]
        if not isinstance(identifier, str) or identifier not in vectors:
            raise ValueError("observation id must identify a declared protocol arm")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("each observation requires a nonempty evidence_hash")
        if identifier in seen_ids or evidence in seen_evidence:
            raise ValueError("duplicate observation id or evidence_hash is not independent feedback")
        reward = _number(row["reward"], "reward")
        if not -1 <= reward <= 1:
            raise ValueError("reward must be a bounded panel skill in [-1, 1]")
        rows.append({"id": identifier, "reward": reward, "evidence_hash": evidence})
        seen_ids.add(identifier)
        seen_evidence.add(evidence)
    rows.sort(key=lambda row: row["id"])
    version = AUDIT_RANKER_VERSION if policy == "audit_ei" else RANKER_VERSION
    identity = {"version": version, "features": vectors, "observed": rows,
                "costs": quotes, "policy": policy, "beta": exploration,
                "ridge": prior_precision, "noise": observation_noise, "seed": seed}
    if policy == "audit_ei":
        identity["opportunity_cost"] = exchange_rate
        identity["economics_version"] = AUDIT_ECONOMICS_VERSION
    fingerprint = digest(identity)
    pool = np.asarray([vectors[key] for key in ids])
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            precision = prior_precision * np.eye(width)
            response = np.zeros(width)
            if rows:
                training = np.asarray([vectors[row["id"]] for row in rows])
                rewards = np.asarray([row["reward"] for row in rows])
                variance = observation_noise ** 2
                precision += (training.T @ training) / variance
                response += (training.T @ rewards) / variance
            chol = np.linalg.cholesky(precision)
            weights = cho_solve((chol, True), response)
            mean = pool @ weights
            projected = solve_triangular(chol, pool.T, lower=True)
            # Squaring can erase a representable SD (e.g. 1e-200 -> 0) or overflow
            # while the norm itself is finite. Preserve the historical arithmetic
            # in its ordinary range, and use a scaled hypot reduction only for
            # extreme columns. Exact zero feature vectors still have zero variance.
            magnitude = np.max(np.abs(projected), axis=0)
            extreme = ((magnitude > 0) & (magnitude < math.sqrt(np.finfo(float).tiny))) | (
                magnitude > math.sqrt(np.finfo(float).max / width))
            ordinary = np.where(extreme[None, :], 0.0, projected)
            std = np.sqrt(np.sum(ordinary ** 2, axis=0))
            if np.any(extreme):
                std[extreme] = np.hypot.reduce(projected[:, extreme], axis=0)
            ucb = np.maximum(0.0, mean + exploration * std) if policy != "audit_ei" else None
    except (FloatingPointError, OverflowError, np.linalg.LinAlgError, ValueError) as error:
        raise ValueError("linear posterior exceeds numerical range; rescale features or hyperparameters") from error
    incumbent = max(0.0, *(row["reward"] for row in rows)) if rows else 0.0
    ranking = []
    for index, identifier in enumerate(ids):
        if identifier in seen_ids:
            continue
        economics = {}
        if policy == "audit_ei":
            predictive_std = _finite_output(math.hypot(float(std[index]), observation_noise), "predictive std")
            improvement = _audit_expected_improvement(float(mean[index]), predictive_std, incumbent)
            charge = _finite_output(exchange_rate * quotes[identifier], "opportunity charge")
            score = _finite_output(improvement - charge, "net audited improvement")
            economics = {"expected_improvement": improvement, "opportunity_charge": charge,
                         "predictive_std": predictive_std}
        elif policy == "fixed":
            score = 0.0
        elif policy == "random":
            hashed = digest({"seed": seed, "training_hash": fingerprint, "id": identifier})
            score = int(hashed[:13], 16) / 16 ** 13
        else:
            score = _finite_output(ucb[index] / quotes[identifier], "acquisition score")
        ranking.append({"id": identifier, "score": score, "mean": _finite_output(mean[index], "mean"),
                        "std": _finite_output(std[index], "std"), "cost": quotes[identifier], **economics})
    ranking.sort(key=lambda row: (-row["score"], row["id"]))
    result = {
        "ranking": ranking, "training_hash": fingerprint, "training_size": len(rows),
        "policy": policy, "version": version,
        "uncertainty": "Posterior latent standard deviation under a shared Gaussian linear working model; "
                       "not calibrated confidence, independent validation, or a generalization guarantee.",
        "feature_scope": "Caller-supplied features; no implicit normalization or intercept.",
    }
    if policy == "audit_ei":
        result["economics"] = {
            "version": AUDIT_ECONOMICS_VERSION, "opportunity_cost": exchange_rate,
            "incumbent": incumbent, "incumbent_ids": [row["id"] for row in rows if row["reward"] == incumbent],
            "no_edit_score": 0.0,
            "prediction": "clip(Normal(mean, latent_std**2 + noise**2), -1, 1)",
            "scope": "One-next-complete-audit expected improvement in frozen realized panel skill minus "
                     "explicit skill-per-campaign-cost-unit opportunity charge. Not cross-arm knowledge "
                     "gradient, independent generalization, or a multi-step optimum. Clipped-predictive "
                     "Gaussian working approximation; the fitted Gaussian likelihood is not a censored "
                     "likelihood at the clipping boundaries. Beta and seed do not affect selection. "
                     "The caller must filter affordable complete panels and stop if the best net value is <= 0.",
        }
    return result
