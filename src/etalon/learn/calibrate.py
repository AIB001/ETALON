"""Score a screen against known answers, with the uncertainty attached.

A feedback loop needs a number that says whether the screen got better. The number is
easy to compute and easy to over-read, and over-reading it is the characteristic failure
of an adaptive campaign: a round reports that enrichment rose from 0.71 to 0.74, the
update is accepted, and nothing has been learned because the difference is smaller than
what the panel can resolve.

With 231 known molecules of which roughly 40 are potent, the standard error of an AUC
near 0.75 is about 0.045 by the Hanley-McNeil formula. A 0.03 observed improvement is
unresolved by this working rule, not proof of no effect. Every metric here carries its
standard error, and :func:`decide` refuses an update it cannot distinguish from noise --
and records the refusal, because a loop that silently declines to learn looks exactly
like one that had nothing to learn.

Two further choices, both of which make the reported numbers smaller and more honest.

**Grouped by scaffold, not split at random.** A congeneric series shares a core, so a
random split puts near-duplicates on both sides and the score measures memorisation of
the core rather than ranking of the decoration. Grouping by Bemis-Murcko scaffold is the
useful leakage control, but it does not necessarily lower every score or identify every
related chemical series.

**The difference's uncertainty is bounded conservatively.** Two AUCs computed on the same
molecules are correlated, and the standard error of their difference is smaller than the
sum of theirs -- usually much smaller, because the two rankings mostly agree. The
correlation is not estimated here, so the bound used is the sum, which is the largest the
difference's error can be. The cost is real and worth naming: genuine small improvements
will be refused. That is the right direction to err when the thing being updated is the
policy applied to every molecule afterwards.

This is a conservative working acceptance heuristic, not a calibrated significance test.
It does not adjust for repeated use of the same selection panel, scaffold dependence or
adaptive proposal selection. A frozen independent evaluation is still needed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from numbers import Integral, Real


def _real(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    try:
        valid = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite real number")


def _classes(positives: int, negatives: int) -> None:
    if any(isinstance(count, bool) or not isinstance(count, Integral) or count < 1
           for count in (positives, negatives)):
        raise ValueError("both classes need positive integer counts")


def _rounded(value: float, name: str) -> float:
    """Validate before converting accepted NumPy scalars to native JSON numbers."""
    _real(value, name)
    return round(float(value), 4)


class Split(StrEnum):
    """How the molecules behind a score were divided, as a closed set rather than a string.

    It was a free-text field defaulting to ``"scaffold"``, and :func:`decide` printed "on a
    scaffold-grouped holdout" whenever it saw that value. The only production caller ranked the
    entire panel with an external function and passed the default, so every accepted update was
    reported as having survived a scaffold-grouped holdout that was never taken. A label nobody can
    mistype is the cheapest way to stop a claim being made by accident.
    """

    #: Whole scaffolds held out of fitting. The strongest of the three, and the only one whose name
    #: a reader can take at face value.
    SCAFFOLD_GROUPED = "scaffold_grouped"
    #: A random holdout. Flatters a congeneric series by putting near-duplicates on both sides.
    RANDOM = "random"
    #: Every molecule scored, nothing held out. Honest for a ranker that was not fitted on this
    #: panel. Repeatedly selecting edited scorers on it can still overfit this panel, even
    #: if no scorer directly trains on its labels.
    WHOLE_PANEL = "whole_panel"


@dataclass(frozen=True, slots=True)
class Score:
    """One ranking quality measurement, with what it can resolve."""

    auc: float
    standard_error: float
    positives: int
    negatives: int
    #: How the molecules were divided. Recorded because the same data gives a higher
    #: number under a random split and a reader must know which they are looking at.
    #:
    #: Defaults to the weakest of the three, on purpose. The previous default was the strongest,
    #: which meant a caller who said nothing was recorded as having done the most careful thing
    #: available.
    split: Split = Split.WHOLE_PANEL

    def __post_init__(self) -> None:
        _real(self.auc, "AUC")
        _real(self.standard_error, "standard error")
        if not 0 <= self.auc <= 1 or self.standard_error < 0:
            raise ValueError("AUC must be in [0, 1] and standard error must be nonnegative")
        _classes(self.positives, self.negatives)
        if not isinstance(self.split, Split):
            raise ValueError("split must be an explicit Split enum, not an unverified string")

    @property
    def resolvable(self) -> float:
        """The smallest difference this panel can distinguish, roughly: two SEs.

        This describes *one* score's own uncertainty and is what a reader should quote when asking
        whether a panel can see an effect at all. It is deliberately not the bound :func:`decide`
        applies to a *difference* between two scores -- see :attr:`Decision.bound`, which adds the
        two standard errors. The two coincide when both scores have the same error, and they are
        answers to different questions; publishing one under the other's name was how this field
        came to be read as the acceptance threshold.
        """

        value = 2.0 * float(self.standard_error)
        _real(value, "score resolution")
        return value

    def as_dict(self) -> dict[str, object]:
        return {
            "auc": _rounded(self.auc, "AUC"),
            "standard_error": _rounded(self.standard_error, "standard error"),
            "positives": int(self.positives),
            "negatives": int(self.negatives),
            "split": self.split.value,
            "smallest_resolvable_difference": round(self.resolvable, 4),
            "what_that_resolves": (
                "the smallest effect this panel can see at all, from one score's own error. The "
                "threshold a proposed change must beat is Decision.uncertainty_bound, which is the "
                "sum of two such errors."
            ),
        }


class Verdict(StrEnum):
    ACCEPTED = "accepted"
    #: The change may be real and this panel cannot tell. Not the same as no change.
    WITHIN_NOISE = "within_noise"
    WORSE = "worse"
    #: Too few knowns for any comparison to mean anything.
    UNDERPOWERED = "underpowered"


@dataclass(frozen=True, slots=True)
class Decision:
    """Whether a proposed update has earned its way in."""

    verdict: Verdict
    before: Score
    after: Score
    delta: float
    bound: float
    note: str

    @property
    def accepted(self) -> bool:
        return self.verdict is Verdict.ACCEPTED

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict.value,
            "accepted": self.accepted,
            "delta_auc": _rounded(self.delta, "AUC difference"),
            "uncertainty_bound": _rounded(self.bound, "uncertainty bound"),
            "before": self.before.as_dict(),
            "after": self.after.as_dict(),
            "note": self.note,
        }


def auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Probability that a positive outranks a negative, ties counting a half.

    The Mann-Whitney form rather than a trapezoid over a sampled curve: it is exact,
    and the tie handling is explicit, which matters because a screen that assigns many
    molecules the same score would otherwise be flattered by the interpolation.

    Higher scores must mean more likely positive. A docking score is the other way
    round, so negate it before calling this.
    """

    if len(scores) != len(labels):
        raise ValueError("scores and labels must be the same length")
    for value in scores:
        _real(value, "ranking score")
    if any(not isinstance(label, Real) or label not in (0, 1) for label in labels):
        raise ValueError("AUC labels must be binary 0/1 values")
    positives = [s for s, y in zip(scores, labels, strict=True) if y]
    negatives = [s for s, y in zip(scores, labels, strict=True) if not y]
    if not positives or not negatives:
        raise ValueError(
            f"an AUC needs both classes; got {len(positives)} positive and "
            f"{len(negatives)} negative"
        )
    wins = 0.0
    for p in positives:
        for n in negatives:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def hanley_mcneil_se(value: float, positives: int, negatives: int) -> float:
    """The standard error of an AUC, by the 1982 exponential approximation.

    Hanley JA, McNeil BJ. The meaning and use of the area under a receiver operating
    characteristic (ROC) curve. Radiology. 1982;143(1):29-36.
    doi:10.1148/radiology.143.1.7063747

    The approximation assumes exponential score distributions, which a docking score is
    not. It is used anyway because it needs only the AUC and the two class sizes, and
    because the alternative in practice is no error bar at all. Where the two differ, a
    bootstrap gives a slightly larger interval on real data, so this errs toward
    confidence -- worth knowing when a decision sits exactly on the boundary.
    """

    _real(value, "AUC")
    if not 0 <= value <= 1:
        raise ValueError("AUC must be in [0, 1]")
    _classes(positives, negatives)
    q1 = value / (2.0 - value)
    q2 = 2.0 * value * value / (1.0 + value)
    variance = (
        value * (1.0 - value)
        + (positives - 1) * (q1 - value * value)
        + (negatives - 1) * (q2 - value * value)
    ) / (positives * negatives)
    return math.sqrt(max(variance, 0.0))


def score(
    scores: Sequence[float], labels: Sequence[int], *, split: Split = Split.WHOLE_PANEL
) -> Score:
    """An AUC with its standard error and the class sizes that set it.

    Args:
        split: What the caller actually did. Defaults to the weakest claim; a caller that held out
            whole scaffolds has to say so, rather than a caller that did not having to remember to
            deny it.
    """

    value = auc(scores, labels)
    positives = sum(1 for y in labels if y)
    negatives = len(labels) - positives
    return Score(
        auc=value,
        standard_error=hanley_mcneil_se(value, positives, negatives),
        positives=positives,
        negatives=negatives,
        split=split,
    )


def scaffold_groups(smiles: Sequence[str]) -> list[str]:
    """A Bemis-Murcko scaffold per molecule, for grouping a split.

    Acyclic molecules have no Murcko core. Their canonical molecule identity is used as
    a stable fallback, so equivalent SMILES/duplicate molecules stay together and training
    and candidate pools do not accidentally share row-number-based scaffold ids. This
    does NOT group all related acyclic analogues into chemical series.
    """

    from rdkit import Chem, rdBase
    from rdkit.Chem.Scaffolds import MurckoScaffold

    groups: list[str] = []
    for index, text in enumerate(smiles):
        with rdBase.BlockLogs():
            molecule = Chem.MolFromSmiles(text)
        if molecule is None:
            groups.append(f"unparsed:{index}")
            continue
        try:
            core = MurckoScaffold.GetScaffoldForMol(molecule)
            key = Chem.MolToSmiles(core) if core is not None else ""
        except Exception:
            key = ""
        groups.append(key or f"acyclic:{Chem.MolToSmiles(molecule)}")
    return groups


def decide(before: Score, after: Score, *, minimum_positives: int = 10) -> Decision:
    """Rule on whether the change between two scores is a change.

    The bound is ``before.standard_error + after.standard_error``: the largest the
    difference's own error can be, since the two scores are computed on the same
    molecules and their correlation is not estimated here. Conservative on purpose, and
    the cost is that small real improvements are refused.
    """

    if type(minimum_positives) is not int or minimum_positives < 1:
        raise ValueError("minimum_positives must be a positive integer")
    if not isinstance(before, Score) or not isinstance(after, Score):
        raise ValueError("comparison requires two validated Score objects")
    if (before.positives, before.negatives, before.split) != (after.positives, after.negatives, after.split):
        raise ValueError("before and after must describe the same class counts and evaluation split")
    # Matching counts is necessary, not proof of matching identities or independence.
    # The caller still has to evaluate both scorers on the same declared panel.
    delta = after.auc - before.auc
    bound = before.standard_error + after.standard_error
    _real(bound, "combined standard error")

    if min(before.positives, after.positives) < minimum_positives:
        return Decision(
            verdict=Verdict.UNDERPOWERED,
            before=before,
            after=after,
            delta=delta,
            bound=bound,
            note=(
                f"Only {min(before.positives, after.positives)} positives in the evaluated panel, "
                f"below the {minimum_positives} this comparison needs. The difference of "
                f"{delta:+.3f} is not evidence either way. Enlarge the known set or "
                "accept that this round cannot be judged."
            ),
        )
    if delta < 0 and delta <= -bound:
        return Decision(
            verdict=Verdict.WORSE,
            before=before,
            after=after,
            delta=delta,
            bound=bound,
            note=(
                f"AUC fell by {abs(delta):.3f}, more than the {bound:.3f} this panel "
                "cannot resolve. The update made the screen worse on the declared evaluation "
                "panel; refuse it and keep the previous configuration."
            ),
        )
    if delta <= 0 or delta < bound:
        return Decision(
            verdict=Verdict.WITHIN_NOISE,
            before=before,
            after=after,
            delta=delta,
            bound=bound,
            note=(
                f"AUC moved by {delta:+.3f} against an uncertainty bound of {bound:.3f}, "
                f"set by {before.positives} positives and {before.negatives} negatives. "
                "The change may be real and this panel cannot tell, which is not the "
                "same as no change. Refused, because accepting an update on an "
                "unresolvable difference is how a campaign drifts while reporting "
                "progress every round."
            ),
        )
    qualification = {
        Split.SCAFFOLD_GROUPED: (
            "The improvement is larger than the measurement's own error, on a scaffold-grouped "
            "holdout."
        ),
        Split.RANDOM: (
            "But on a random split, which flatters a congeneric series by putting near-duplicates "
            "on both sides. Re-score it grouped by scaffold before acting on it."
        ),
        Split.WHOLE_PANEL: (
            "But on the whole panel with nothing held out. Repeated selection of edited scorers "
            "can overfit this panel even without direct model fitting. This is selection evidence, "
            "not independent generalization or a multiple-testing-corrected significance claim."
        ),
    }[after.split]
    return Decision(
        verdict=Verdict.ACCEPTED,
        before=before,
        after=after,
        delta=delta,
        bound=bound,
        note=(
            f"AUC rose by {delta:+.3f}, beyond the {bound:.3f} bound on this panel's "
            f"resolution. {qualification}"
        ),
    )


__all__ = [
    "Decision",
    "Score",
    "Split",
    "Verdict",
    "auc",
    "decide",
    "hanley_mcneil_se",
    "scaffold_groups",
    "score",
]
