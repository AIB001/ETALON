"""Propose what to measure next, and what to change, without being allowed to decide either.

Two proposers, matching the two acts an advisor may put forward that the harness can act on
without a person (ADR 0003).

:class:`Acquisition` chooses which molecules the next batch of compute goes to. Applied
directly, because being wrong costs a week and nothing recorded becomes untrue.

:class:`ParameterChange` proposes an edit to the screen and scores it. Applied only if
:func:`etalon.learn.calibrate.decide` admits it, which on this panel means an AUC gain larger
than about 0.10 -- so most proposals are refused, and the refusal is the point rather than a
disappointment.

Both are ``ALGORITHM`` advisors rather than language models: their output is reproducible from
their inputs, which changes what a reader can check about them. A language model can stand
behind either by supplying the candidate edit, and then the record carries both identities --
the model that proposed the change and the procedure that scored it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from etalon.judgment.proposal import (
    Act,
    Advisor,
    AdvisorKind,
    Proposal,
    refuse_if_not_an_advisors_decision,
)
from etalon.learn.acquire import DEFAULT_EXPLORE_FRACTION, Batch, acquire
from etalon.learn.admissible import Measurement
from etalon.learn.calibrate import Decision, Score, Split, decide, scaffold_groups, score
from etalon.learn.conformal import Calibration, calibrate, intervals
from etalon.learn.surrogate import Features, Surrogate, featurize, pic50


@dataclass
class Panel:
    """Known molecules with measured affinities: what a proposal is judged against.

    Held separately from the campaign's own measurements on purpose. A change fitted on the
    molecules the campaign just simulated and scored on the same molecules will always look like
    an improvement; the panel is the held-out answer key, and its size is what sets the noise
    floor every proposal has to clear.
    """

    smiles: tuple[str, ...]
    affinity_nM: tuple[float, ...]
    #: Below this, a molecule counts as a hit for the ranking score. 100 nM is conventional and
    #: stated rather than assumed, because moving it moves every AUC this panel reports.
    potent_below_nM: float = 100.0
    _features: Features | None = field(default=None, repr=False)
    _groups: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if len(self.smiles) != len(self.affinity_nM):
            raise ValueError("the panel's smiles and affinities must be the same length")
        if len(self.smiles) < 30:
            raise ValueError(
                f"a panel of {len(self.smiles)} molecules cannot resolve any change worth "
                "making. The Hanley-McNeil standard error at these class sizes exceeds any "
                "plausible improvement, so every proposal would come back underpowered -- "
                "which is true, and not worth the compute to discover one proposal at a time."
            )
        self._warn_if_sorted()

    def _warn_if_sorted(self) -> None:
        """Refuse a panel whose affinities follow its own row order.

        This guard exists because the real panel here is stored sorted by affinity, and the
        first test of the acquisition layer sliced it sequentially: the first 120 rows became
        the training panel and held every compound under 670 nM, while the candidate pool
        started at 690 nM. The acquisition then picked the strongest molecules available and
        looked like it had failed, because everything it could choose from was weak.

        Nothing about that is visible from either half. A sorted file plus a sequential split
        produces a training set and a pool that are different populations, and every number
        measured across them is a number about the slicing. It is cheap to detect and it
        silently invalidates whatever is measured next, so it is an error rather than a note.
        """

        if len(self.affinity_nM) < 10:
            return
        values = list(self.affinity_nM)
        ascending = sum(
            1 for a, b in zip(values, values[1:], strict=False) if b >= a
        ) / (len(values) - 1)
        if ascending > 0.97 or ascending < 0.03:
            raise ValueError(
                f"{ascending:.0%} of this panel's consecutive affinities are monotone, so it "
                "was almost certainly taken as a contiguous slice of a file sorted by "
                "affinity. A sequential split of such a file gives a training set and a "
                "candidate pool that are different populations -- the real panel here holds "
                "everything under 670 nM in its first 120 rows -- and every number measured "
                "across them describes the slicing rather than the chemistry. Shuffle before "
                "splitting, or split by scaffold."
            )

    @property
    def features(self) -> Features:
        if self._features is None:
            self._features = featurize(self.smiles)
        return self._features

    @property
    def dropped(self) -> tuple[int, ...]:
        """Input indices RDKit could not featurise. Visible, because they change every count."""

        return self.features.unparsed

    @property
    def groups(self) -> tuple[str, ...]:
        if not self._groups:
            self._groups = tuple(self.features.align(scaffold_groups(self.smiles)))
        return self._groups

    @property
    def target(self) -> list[float]:
        return self.features.align([pic50(value) for value in self.affinity_nM])

    @property
    def labels(self) -> list[int]:
        return self.features.align(
            [1 if value < self.potent_below_nM else 0 for value in self.affinity_nM]
        )

    @property
    def usable_smiles(self) -> list[str]:
        """The molecules that actually featurised, in matrix order."""

        return self.features.align(list(self.smiles))

    # Every one of the four above is aligned to the feature matrix rather than to the input,
    # and that is a correction rather than a nicety. MolCascade's featuriser drops a molecule it
    # cannot featurise instead of imputing a zero row -- its reasoning, and better than the
    # zero-filling this module first did, since a zero vector is a plausible input a model will
    # happily score. Once rows are dropped, a full-length target list paired against a short
    # matrix trains every molecule after the first failure against another molecule's answer.
    # Measured: a panel of 120 with two unparseable entries featurised 118 rows, and the
    # surrogate refused the mismatch rather than fitting it -- loudly, which is why this was
    # found, and wrongly, because the panel should have been usable.


@dataclass
class Acquisition:
    """Fit a surrogate, calibrate it, and say which molecules to spend on next.

    The campaign applies this without asking anybody, so everything that could make it quietly
    bad is reported rather than absorbed: how much of the batch went on chemotypes the model has
    never seen, how many candidates were tied with the best, and the coverage the calibration
    actually achieved on its worst series.
    """

    panel: Panel
    budget: int = 20
    alpha: float = 0.1
    explore_fraction: float = DEFAULT_EXPLORE_FRACTION
    seed: int = 0
    identifier: str = "conformal-acquisition@0.1.0"
    #: Set by :meth:`propose`, so a caller can put it in the record.
    last_calibration: Calibration | None = field(default=None, repr=False)

    def propose(self, candidates: Mapping[str, str]) -> Proposal:
        """Choose from ``{parent_id: smiles}``, using the panel as training data.

        Raises:
            ValueError: If the panel's scaffolds cannot be split into folds. A campaign whose
                answer key is one chemical series cannot calibrate an interval that means
                anything about another, and proceeding would produce a confident batch on no
                evidence.
        """

        identifiers = list(candidates)
        if not identifiers:
            raise ValueError("no candidates to choose from")

        surrogate = Surrogate(seed=self.seed).fit(self.panel.features, self.panel.target)
        calibration, _, _ = calibrate(
            self.panel.features,
            self.panel.target,
            list(self.panel.groups),
            alpha=self.alpha,
            surrogate=Surrogate(seed=self.seed),
        )
        self.last_calibration = calibration

        pool = featurize([candidates[identifier] for identifier in identifiers])
        mean, spread = surrogate.predict(pool)
        predicted = intervals(identifiers, mean, spread, calibration)

        scaffolds = dict(
            zip(identifiers, scaffold_groups([candidates[i] for i in identifiers]), strict=True)
        )
        batch: Batch = acquire(
            predicted,
            budget=self.budget,
            scaffolds=scaffolds,
            seen_scaffolds=frozenset(self.panel.groups),
            explore_fraction=self.explore_fraction,
        )
        return Proposal(
            act=Act.SPEND,
            advisor=Advisor(
                kind=AdvisorKind.ALGORITHM,
                identifier=self.identifier,
                transport="in-process",
            ),
            payload={
                **batch.as_dict(),
                "calibration": calibration.as_dict(),
                "unparsed_candidates": list(pool.unparsed),
            },
            rationale=(
                f"{len(batch.picks)} of {len(identifiers)} candidates, "
                f"{batch.scaffolds} distinct scaffolds, calibrated at "
                f"{1 - self.alpha:.0%} nominal coverage whose worst series achieved "
                f"{calibration.worst_group_coverage:.0%}."
            ),
        )


@dataclass
class ParameterChange:
    """Score a proposed edit to the screen against the panel, and usually refuse it.

    The edit itself comes from somewhere else -- a language model, an operator, a sweep. This
    class does not invent one, because inventing and judging the same thing is the arrangement
    every part of ETALON exists to avoid.

    What it does is apply the edit to a scoring function over the panel, compute the ranking
    quality before and after over every molecule in it, and hand both to
    :func:`~etalon.learn.calibrate.decide`, which refuses anything the panel cannot resolve. Nothing
    is held out, because nothing here was fitted; see :meth:`score_of` for why that is the right
    comparison and why it used to be described as a stronger one.
    """

    panel: Panel
    identifier: str = "panel-scored-change@0.1.0"

    def score_of(self, ranker: Any) -> Score:
        """Rank the panel with a callable and score it, higher meaning more likely potent.

        Recorded as :attr:`~etalon.learn.calibrate.Split.WHOLE_PANEL`, which is what it is. Every
        molecule is scored and nothing is held out; the grouping this class computes is used for the
        conformal folds and for counting unfamiliar scaffolds, and never entered this AUC.

        That is a defensible comparison here and the label used to overstate it. The ranker is a
        scoring function with an edited parameter, fitted nowhere near this panel, so there is no
        holdout to take and no leakage to prevent -- but the field said ``"scaffold"`` and
        :func:`~etalon.learn.calibrate.decide` printed "on a scaffold-grouped holdout" on the
        strength of it. Anything that *is* fitted on this panel must be scored out of fold before
        being passed here, and must say so.
        """

        # usable_smiles, not smiles: the labels are aligned to the feature matrix, so ranking
        # the full input list would score one sequence against another's answers.
        values = [float(ranker(text)) for text in self.panel.usable_smiles]
        return score(values, self.panel.labels, split=Split.WHOLE_PANEL)

    def judge(
        self,
        change: Mapping[str, Any],
        before: Any,
        after: Any,
        *,
        proposed_by: Advisor | None = None,
    ) -> tuple[Proposal, Decision]:
        """Score two rankers and return the proposal beside the verdict on it.

        Both are returned because the ledger needs both: a record holding only accepted changes
        cannot be read to find out what the campaign tried, and a loop that silently declines to
        learn is indistinguishable from one with nothing to learn.
        """

        baseline = self.score_of(before)
        candidate = self.score_of(after)
        verdict = decide(baseline, candidate)
        advisor = proposed_by or Advisor(
            kind=AdvisorKind.ALGORITHM, identifier=self.identifier, transport="in-process"
        )
        return (
            Proposal(
                act=Act.PARAMETER_CHANGE,
                advisor=advisor,
                payload={
                    "change": dict(change),
                    "before": baseline.as_dict(),
                    "after": candidate.as_dict(),
                    "verdict": verdict.as_dict(),
                },
                rationale=verdict.note,
            ),
            verdict,
        )


def as_loop_proposer(
    judge: ParameterChange,
    change: Mapping[str, Any],
    before: Any,
    after: Any,
) -> Any:
    """Adapt a scored edit to the shape :meth:`etalon.campaign.Campaign.round` expects.

    The loop's ``propose`` hook receives the admitted measurements and returns a change with a
    decision, or ``None``. The measurements are deliberately unused here: a change is judged
    against the panel, which is the held-out answer key, and judging it against the molecules
    the campaign just measured is how a round congratulates itself.
    """

    def proposer(_: Sequence[Measurement]) -> tuple[dict[str, Any], Decision]:
        proposal, verdict = judge.judge(change, before, after)
        # The autonomy gradation, enforced at the point of application rather than declared in a
        # table. This guard existed and had no production caller at all: every act's autonomy was
        # stated once in judgment.proposal.AUTONOMY and checked nowhere, so a COMPARATOR or WAIVER
        # proposal reaching this adapter would have been applied like any other. Being here rather
        # than inside ParameterChange is deliberate -- proposing one of those is legitimate, and it
        # is handing one to the loop that is not.
        refuse_if_not_an_advisors_decision(proposal)
        return dict(proposal.payload["change"]), verdict

    return proposer


def as_loop_acquirer(acquisition: Acquisition) -> Any:
    """Adapt an acquisition to :meth:`etalon.campaign.Campaign.round`'s ``acquire`` hook.

    ``Act.SPEND`` is the one act the autonomy table lets an advisor settle on its own -- being
    wrong costs compute and the next round shows it -- and until this existed it had no way into
    the loop at all. :meth:`Acquisition.propose` takes ``{parent_id: smiles}`` and returns a
    :class:`~etalon.judgment.proposal.Proposal`, while the loop's other hook takes measurements and
    returns a change with a decision; the two signatures cannot be connected, which is how the
    mismatch survived -- nothing ever tried.

    Returns ``None`` when there is nothing to choose from, which is a round with no next batch
    rather than an error.
    """

    def acquirer(candidates: Mapping[str, str]) -> Proposal | None:
        if not candidates:
            return None
        proposal = acquisition.propose(candidates)
        refuse_if_not_an_advisors_decision(proposal)
        return proposal

    return acquirer


__all__ = [
    "Acquisition",
    "Panel",
    "ParameterChange",
    "as_loop_acquirer",
    "as_loop_proposer",
]
