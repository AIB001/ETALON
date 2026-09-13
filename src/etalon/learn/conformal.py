"""Turn a model's guess about its own uncertainty into an interval with a stated coverage.

A random forest's ensemble spread is a heuristic. It correlates with error, it has no units in
common with error, and it can be confidently small where the model is badly wrong. Conformal
prediction converts such a heuristic into an interval carrying a finite-sample, distribution-free
guarantee: if calibration and test molecules are exchangeable, the true value falls inside the
interval at least ``1 - alpha`` of the time, whatever the underlying model is worth. A weak
heuristic does not break the guarantee; it makes the intervals wider.

That warranty is the right shape for this project, because ETALON already refuses what it
cannot distinguish from noise and an interval is what lets that refusal be made one level down:
two candidates whose intervals overlap are not ranked, they are tied.

Two departures from the textbook recipe, both forced by the size of a real panel.

**Cross-conformal, not split-conformal.** The standard recipe holds out a calibration set. On
231 molecules a held-out third is both a third less training data and a calibration quantile
estimated from seventy points. So every molecule is scored out-of-fold across a grouped
k-fold, and all the residuals are pooled into one quantile. The guarantee becomes approximate
rather than exact -- the folds are not independent of each other -- and that is stated rather
than glossed, because it is the one place the warranty is weaker than the textbook's.

**Grouped folds, and coverage reported twice.** The guarantee assumes exchangeability, and a
congeneric series breaks it: a random fold puts near-duplicates on both sides, so the residuals
look small, the quantile comes out tight, and the intervals are too narrow for any molecule
outside the series. Folds are therefore grouped by scaffold. And because the literature is
explicit that marginal coverage can hold while one series is badly under-covered, this module
measures both and :attr:`Calibration.worst_group_coverage` is part of the record. A campaign
that reads only the marginal figure has been told the average of a thing whose variance is the
point.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from etalon.learn.surrogate import Features, Surrogate

#: Added to the spread before dividing, so a tree-unanimous prediction does not produce an
#: infinite nonconformity score. In pIC50 units: 0.1 is well below this panel's out-of-fold
#: error, so it floors the denominator without flattening the adaptivity it exists to preserve.
SPREAD_FLOOR = 0.1


def _quantile_index(count: int, alpha: float) -> int:
    """The rank the split-conformal theorem asks for: ceil((n+1)(1-alpha)).

    Written out rather than delegated to a percentile function because the off-by-one is the
    whole guarantee. ``numpy.quantile`` with linear interpolation gives a slightly smaller
    threshold and slightly under-covers, which is invisible in any test that only checks the
    coverage is "about right".
    """

    rank = math.ceil((count + 1) * (1.0 - alpha))
    return min(max(rank, 1), count) - 1


@dataclass(frozen=True, slots=True)
class Calibration:
    """A conformal quantile, and the coverage it actually achieved where it was fitted."""

    alpha: float
    #: The nonconformity quantile. An interval is ``mean +/- q * (spread + SPREAD_FLOOR)``.
    q: float
    calibration_count: int
    #: Out-of-fold coverage over every molecule. What the theorem speaks about.
    marginal_coverage: float
    #: The worst coverage of any scaffold group with enough members to measure, and the group.
    #: The number a campaign should read second and worry about first.
    worst_group_coverage: float
    worst_group: str
    groups_measured: int
    #: Median interval half-width, in the target's units. Coverage without this is meaningless:
    #: an interval from minus infinity to plus infinity covers everything.
    median_half_width: float
    #: Identifies this calibration in a ``prediction/v1`` row's ``calibration_id``.
    calibration_id: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "alpha": self.alpha,
            "nominal_coverage": round(1.0 - self.alpha, 4),
            "q": round(self.q, 4),
            "calibration_count": self.calibration_count,
            "marginal_coverage": round(self.marginal_coverage, 4),
            "worst_group_coverage": round(self.worst_group_coverage, 4),
            "worst_group": self.worst_group,
            "groups_measured": self.groups_measured,
            "median_half_width": round(self.median_half_width, 4),
            "calibration_id": self.calibration_id,
            "guarantee": (
                "Finite-sample and distribution-free under exchangeability, and approximate "
                "here rather than exact: the residuals come from grouped cross-validation "
                "folds, which are not independent of one another. The marginal figure is what "
                "the theorem speaks about; the worst-group figure is what a congeneric series "
                "will actually experience."
            ),
        }


def calibrate(
    features: Features,
    target: Sequence[float],
    groups: Sequence[str],
    *,
    alpha: float = 0.1,
    folds: int = 5,
    surrogate: Surrogate | None = None,
    min_group: int = 5,
) -> tuple[Calibration, Any, Any]:
    """Fit the conformal quantile out-of-fold, and measure what it covers.

    Returns the calibration together with the out-of-fold predictions and spreads, because a
    caller that wants to score the model should score the same numbers the calibration was
    derived from rather than refit and get a different answer.

    Args:
        min_group: Groups smaller than this are pooled out of the per-group coverage figure. A
            coverage of 0.0 measured on one molecule is not a finding about that series.
    """

    import numpy as np
    from sklearn.model_selection import GroupKFold

    truth = np.asarray(list(target), dtype=float)
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")
    if len(truth) != len(features) or len(groups) != len(truth):
        raise ValueError("features, target and groups must describe the same molecules")
    distinct = len(set(groups))
    if distinct < folds:
        raise ValueError(
            f"{distinct} scaffold group(s) cannot be split into {folds} grouped folds. Lower "
            "folds, or accept that this panel is one series and says nothing about another."
        )

    template = surrogate or Surrogate()
    predicted = np.zeros_like(truth)
    spread = np.zeros_like(truth)
    for train, test in GroupKFold(n_splits=folds).split(features.matrix, truth, groups=groups):
        fitted = Surrogate(
            trees=template.trees,
            min_samples_leaf=template.min_samples_leaf,
            seed=template.seed,
        ).fit(Features(features.matrix[train], features.names), truth[train])
        mean, sigma = fitted.predict(Features(features.matrix[test], features.names))
        predicted[test], spread[test] = mean, sigma

    scores = np.abs(truth - predicted) / (spread + SPREAD_FLOOR)
    ordered = np.sort(scores)
    q = float(ordered[_quantile_index(len(ordered), alpha)])

    half = q * (spread + SPREAD_FLOOR)
    covered = np.abs(truth - predicted) <= half

    worst, worst_name, measured = 1.0, "(none large enough)", 0
    labels = np.asarray(list(groups), dtype=object)
    for name in set(groups):
        member = labels == name
        if int(member.sum()) < min_group:
            continue
        measured += 1
        rate = float(covered[member].mean())
        if rate < worst:
            worst, worst_name = rate, str(name)

    calibration = Calibration(
        alpha=alpha,
        q=q,
        calibration_count=len(ordered),
        marginal_coverage=float(covered.mean()),
        worst_group_coverage=worst,
        worst_group=worst_name,
        groups_measured=measured,
        median_half_width=float(np.median(half)),
        calibration_id=f"crossconf-a{alpha:g}-k{folds}-n{len(ordered)}",
    )
    return calibration, predicted, spread


@dataclass(frozen=True, slots=True)
class Interval:
    """One molecule's calibrated prediction."""

    parent_id: str
    mean: float
    spread: float
    lower: float
    upper: float

    @property
    def half_width(self) -> float:
        return (self.upper - self.lower) / 2.0

    def overlaps(self, other: Interval) -> bool:
        """Whether these two molecules are distinguishable at the calibrated level.

        The question an acquisition decision actually asks. Two overlapping intervals are a
        tie, and ranking a tie is the same mistake as accepting an AUC improvement smaller
        than its own standard error -- one level down, and with a guarantee attached.
        """

        return self.lower <= other.upper and other.lower <= self.upper


def intervals(
    parent_ids: Sequence[str],
    mean: Any,
    spread: Any,
    calibration: Calibration,
) -> list[Interval]:
    """Apply a calibration to predictions, producing intervals with its stated coverage."""

    if not (len(parent_ids) == len(mean) == len(spread)):
        raise ValueError("parent ids, means and spreads must be the same length")
    half = [calibration.q * (float(s) + SPREAD_FLOOR) for s in spread]
    return [
        Interval(
            parent_id=str(identifier),
            mean=float(m),
            spread=float(s),
            lower=float(m) - h,
            upper=float(m) + h,
        )
        for identifier, m, s, h in zip(parent_ids, mean, spread, half, strict=True)
    ]


__all__ = ["SPREAD_FLOOR", "Calibration", "Interval", "calibrate", "intervals"]
