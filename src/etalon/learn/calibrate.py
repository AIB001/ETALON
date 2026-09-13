"""Score a screen against known answers, with the uncertainty attached.

A feedback loop needs a number that says whether the screen got better. The number is
easy to compute and easy to over-read, and over-reading it is the characteristic failure
of an adaptive campaign: a round reports that enrichment rose from 0.71 to 0.74, the
update is accepted, and nothing has been learned because the difference is smaller than
what the panel can resolve.

With 231 known molecules of which roughly 40 are potent, the standard error of an AUC
near 0.75 is about 0.045 by the Hanley-McNeil formula. A 0.03 improvement is therefore
not an improvement; it is the same measurement twice. So every metric here carries its
standard error, and :func:`decide` refuses an update it cannot distinguish from noise --
and records the refusal, because a loop that silently declines to learn looks exactly
like one that had nothing to learn.

Two further choices, both of which make the reported numbers smaller and more honest.

**Grouped by scaffold, not split at random.** A congeneric series shares a core, so a
random split puts near-duplicates on both sides and the score measures memorisation of
the core rather than ranking of the decoration. Grouping by Bemis-Murcko scaffold is the
standard correction and it always lowers the number.

**The difference's uncertainty is bounded conservatively.** Two AUCs computed on the same
molecules are correlated, and the standard error of their difference is smaller than the
sum of theirs -- usually much smaller, because the two rankings mostly agree. The
correlation is not estimated here, so the bound used is the sum, which is the largest the
difference's error can be. The cost is real and worth naming: genuine small improvements
will be refused. That is the right direction to err when the thing being updated is the
policy applied to every molecule afterwards.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


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
    #: panel -- an edited docking function has nothing to leak -- and close to meaningless for one
    #: that was.
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

        return 2.0 * self.standard_error

    def as_dict(self) -> dict[str, object]:
        return {
            "auc": round(self.auc, 4),
            "standard_error": round(self.standard_error, 4),
            "positives": self.positives,
            "negatives": self.negatives,
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
            "delta_auc": round(self.delta, 4),
            "uncertainty_bound": round(self.bound, 4),
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

    if positives < 1 or negatives < 1:
        raise ValueError("both classes must be non-empty")
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

    A molecule whose scaffold cannot be derived is given a group of its own rather than
    being pooled with every other failure: pooling them would put unrelated molecules in
    one fold and quietly re-introduce the leakage grouping exists to prevent.
    """

    from rdkit import Chem, RDLogger
    from rdkit.Chem.Scaffolds import MurckoScaffold

    RDLogger.DisableLog("rdApp.*")
    groups: list[str] = []
    for index, text in enumerate(smiles):
        molecule = Chem.MolFromSmiles(text)
        if molecule is None:
            groups.append(f"unparsed:{index}")
            continue
        try:
            core = MurckoScaffold.GetScaffoldForMol(molecule)
            key = Chem.MolToSmiles(core) if core is not None else ""
        except Exception:
            key = ""
        groups.append(key or f"acyclic:{index}")
    return groups


def decide(before: Score, after: Score, *, minimum_positives: int = 10) -> Decision:
    """Rule on whether the change between two scores is a change.

    The bound is ``before.standard_error + after.standard_error``: the largest the
    difference's own error can be, since the two scores are computed on the same
    molecules and their correlation is not estimated here. Conservative on purpose, and
    the cost is that small real improvements are refused.
    """

    delta = after.auc - before.auc
    bound = before.standard_error + after.standard_error

    if min(before.positives, after.positives) < minimum_positives:
        return Decision(
            verdict=Verdict.UNDERPOWERED,
            before=before,
            after=after,
            delta=delta,
            bound=bound,
            note=(
                f"Only {min(before.positives, after.positives)} positives in the holdout, "
                f"below the {minimum_positives} this comparison needs. The difference of "
                f"{delta:+.3f} is not evidence either way. Enlarge the known set or "
                "accept that this round cannot be judged."
            ),
        )
    if delta <= -bound:
        return Decision(
            verdict=Verdict.WORSE,
            before=before,
            after=after,
            delta=delta,
            bound=bound,
            note=(
                f"AUC fell by {abs(delta):.3f}, more than the {bound:.3f} this panel "
                "cannot resolve. The update made the screen worse on molecules it had "
                "not been fitted to; refuse it and keep the previous configuration."
            ),
        )
    if delta < bound:
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
            "But on the whole panel with nothing held out. That is the right comparison for a "
            "ranker this campaign did not fit -- an edited scoring function has no panel labels to "
            "leak -- and it establishes nothing about a model that was trained here. Say which "
            "this was in the record."
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
