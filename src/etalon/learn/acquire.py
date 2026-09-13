"""Choose which molecules to spend the next batch of compute on.

This is the one decision in the campaign where a model may act unilaterally, because being
wrong costs a week of GPU time and nothing it records becomes untrue (ADR 0003). It is also the
decision where the obvious implementation is measurably wrong, and the measurement is in
``findings/0003``.

Taking the top k by predicted affinity would have deprioritised the best compound in the panel.
Held out by scaffold, the surrogate predicted 6.14 to 6.47 pIC50 for all eight members of the
series containing a 0.26 nM inhibitor whose true pIC50 is 9.59 -- it regressed an unseen
chemotype to the panel mean. Worse for an acquisition function: that molecule's conformal
interval was the *narrowest* of the eight. The model was confident, and wrong by 3.1 pIC50,
which is 4.4 kcal/mol. Anything reading the mean would have ranked it mid-pack; anything reading
the mean and the uncertainty together would have been reassured.

So three things are true of the selection here, and each of them costs predicted potency.

**A tie is not ranked.** Two molecules whose calibrated intervals overlap are not
distinguishable at the level the calibration guarantees, so ordering them is the same mistake as
accepting an AUC gain smaller than its own standard error -- one level down, with a warranty
attached. Overlapping candidates are resolved by diversity instead, and the batch records how
many of its picks were ties.

**Scaffold novelty is an explicit term, not an emergent one.** The natural argument is that an
uncertainty-aware score already prefers the unfamiliar. The measurement says otherwise: the
spread was small where the model was most wrong, because a forest that has never seen a
chemotype is unanimously mistaken rather than divided. So a scaffold with no training members
earns its place by being unseen, on its own evidence.

**The batch is built one pick at a time, against what is already in it.** Samples in a batch are
not independent -- they share chemistry that moves the model the same way -- so the value of a
set is not the sum of its members' marginal values. Each pick is therefore penalised by its
similarity to the picks already made, which is the cheap form of the batch-aware methods the
active-learning literature prefers over naive top-k.

What the explore fraction actually buys, measured on the real panel over five seeds -- 60
molecules seen, 20 chosen from the remaining 171:

=========================  ====================  ===================
strategy                   best hit, median nM   sub-100 nM, mean
=========================  ====================  ===================
greedy UCB, explore 0      8.00                  8.8
default, explore 0.25      8.00                  7.4
all exploration, 1.0       5.00                  3.6
random                     11.00                 5.4
=========================  ====================  ===================

Read honestly, that does not say the default is best. Every informed strategy beats random on
both counts, and then the two columns disagree: exploitation finds more sub-100 nM compounds,
and exploration finds the stronger single compound -- which is what ``findings/0003`` predicts,
since the best molecules sat in a chemotype the model had never seen and was confident about.

So the fraction is a real knob with a monotone effect and no optimum this panel can establish;
the active-learning literature reports none that transfers between targets either. A campaign
hunting one lead should turn it up, a campaign developing a series should turn it down, and the
default sits between them because a campaign that has not said which is usually doing both.
Diversity is not what it buys: the sibling penalty already produced twenty distinct scaffolds in
every arm. It buys *unfamiliar* scaffolds specifically.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from etalon.learn.conformal import Interval

#: How much an interval's half-width counts toward a candidate's score. 1.0 is an upper
#: confidence bound: a molecule predicted a little weaker but much less certain outranks a
#: confident middling one. A convention, and the one knob here that is purely a preference --
#: 0 is pure exploitation and 2 is close to pure exploration.
DEFAULT_KAPPA = 1.0

#: The share of the budget reserved for scaffolds the surrogate has never seen.
#:
#: An explicit fraction rather than a score bonus, and the first implementation here was the
#: bonus. It was set to 1.0 pIC50 on the reasoning that an unseen chemotype had cost 3.13 pIC50
#: of error in findings/0003, so a third of that was conservative. Measured on the real panel --
#: 60 molecules seen, 20 chosen from 171 -- every one of the twenty picks came back an unseen
#: scaffold and the hit rate on sub-100 nM compounds fell from 13 to 4. A bonus comparable to
#: the spread of the scores it is added to does not tilt a ranking, it replaces it.
#:
#: A fraction cannot do that. It also says something a campaign can read: five of twenty went on
#: chemotypes the model knows nothing about. One quarter is a convention; the literature reports
#: no optimum that transfers across targets.
DEFAULT_EXPLORE_FRACTION = 0.25

#: Retained for a caller that really wants a score bonus, and not used by default. See
#: DEFAULT_EXPLORE_FRACTION for why.
DEFAULT_NOVELTY_BONUS = 0.0

#: A flat deduction for taking a second molecule from a scaffold the batch already holds, on top
#: of discounting that candidate's whole confidence term. Small: it breaks ties toward diversity
#: without refusing a series that genuinely holds the best compounds.
SIBLING_PENALTY = 0.25


@dataclass(frozen=True, slots=True)
class Pick:
    """One selected molecule, and why it was selected."""

    parent_id: str
    rank: int
    score: float
    predicted: float
    half_width: float
    #: ``potency``, ``uncertainty``, ``unseen_scaffold`` or ``diversity``. The reason the
    #: campaign can read back when the batch turns out to have been a poor use of a week.
    reason: str
    scaffold: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "parent_id": self.parent_id,
            "rank": self.rank,
            "score": round(self.score, 4),
            "predicted": round(self.predicted, 4),
            "half_width": round(self.half_width, 4),
            "reason": self.reason,
            "scaffold": self.scaffold,
        }


@dataclass(frozen=True, slots=True)
class Batch:
    """What to measure next, and what the selection could not decide."""

    picks: tuple[Pick, ...]
    #: Candidates whose intervals overlap the top pick's. Not an error: it is the count a
    #: campaign needs in order to know whether its ranking meant anything.
    tied_with_best: int = 0
    scaffolds: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def parent_ids(self) -> tuple[str, ...]:
        return tuple(pick.parent_id for pick in self.picks)

    def as_dict(self) -> dict[str, object]:
        return {
            "picks": [pick.as_dict() for pick in self.picks],
            "parent_ids": list(self.parent_ids),
            "distinct_scaffolds": self.scaffolds,
            "tied_with_best": self.tied_with_best,
            "by_reason": {
                reason: sum(1 for pick in self.picks if pick.reason == reason)
                for reason in sorted({pick.reason for pick in self.picks})
            },
            "notes": list(self.notes),
        }


def acquire(
    candidates: Sequence[Interval],
    *,
    budget: int,
    scaffolds: Mapping[str, str] | None = None,
    seen_scaffolds: frozenset[str] | None = None,
    kappa: float = DEFAULT_KAPPA,
    explore_fraction: float = DEFAULT_EXPLORE_FRACTION,
    novelty_bonus: float = DEFAULT_NOVELTY_BONUS,
) -> Batch:
    """Select up to ``budget`` molecules, spending some of the budget on not knowing.

    Args:
        candidates: Calibrated predictions, higher meaning better. pIC50 qualifies; a docking
            score does not until it is negated.
        scaffolds: ``parent_id`` to scaffold key. Without it the novelty term and the diversity
            penalty both fall away, and the selection degrades to an upper confidence bound --
            which is the behaviour ``findings/0003`` says is not good enough, so the absence is
            reported in the notes rather than passed over.
        seen_scaffolds: Scaffolds the surrogate was trained on. A candidate outside this set is
            one the model has no evidence about.
        explore_fraction: The share of the budget reserved for unseen scaffolds, filled before
            anything else. 0 spends the whole budget on what the model believes, which
            findings/0003 shows can miss the best compound in the library while reporting no
            doubt; 1 measures only the unfamiliar and learns nothing about the series in hand.
        novelty_bonus: A score bonus for an unseen scaffold, on top of the reserved share.
            Defaults to zero because a bonus large enough to change a ranking turned out to be
            large enough to replace it.
    """

    if budget < 1:
        raise ValueError(f"a budget of {budget} selects nothing")
    if not candidates:
        return Batch(picks=(), notes=("No candidates, so there is nothing to spend on.",))

    scaffold_of = dict(scaffolds or {})
    seen = seen_scaffolds or frozenset()
    notes: list[str] = []
    if not scaffold_of:
        notes.append(
            "No scaffolds supplied, so this selection is an upper confidence bound with no "
            "novelty term and no diversity penalty. findings/0003 measured that the surrogate "
            "is most confident where it is most wrong -- on an unseen chemotype -- so a batch "
            "chosen this way can miss the best compound in the library and report no doubt."
        )

    best = max(candidates, key=lambda item: item.mean)
    tied = sum(1 for item in candidates if item is not best and item.overlaps(best))
    if tied:
        notes.append(
            f"{tied} candidate(s) have intervals overlapping the top prediction's, so they are "
            "not distinguishable from it at this calibration's coverage. They are ordered by "
            "diversity rather than by predicted potency, because ranking a tie asserts a "
            "difference the interval says is not there."
        )

    if not 0.0 <= explore_fraction <= 1.0:
        raise ValueError(f"explore_fraction must be in [0, 1]; got {explore_fraction}")
    # Floored rather than rounded. round() is banker's, so round(0.5) is 0 and round(1.5) is 2 --
    # a budget of 2 at a quarter reserved nothing while a budget of 6 reserved two, and neither
    # said so. Flooring is the honest reading of "a quarter of two places", and the case where it
    # comes out zero is now reported rather than silently applied.
    reserved = min(budget, int(budget * explore_fraction))
    if explore_fraction > 0.0 and reserved == 0:
        notes.append(
            f"An explore fraction of {explore_fraction:.0%} over a budget of {budget} reserves "
            "no places, so this batch is pure exploitation. findings/0003 is why that is worth "
            "saying out loud: the surrogate was most confident on the chemotype it was most "
            "wrong about. Raise the budget or the fraction to measure anything unfamiliar."
        )
    unseen_available = [
        item
        for item in candidates
        if scaffold_of.get(item.parent_id, "") and scaffold_of[item.parent_id] not in seen
    ]
    if reserved and not unseen_available:
        notes.append(
            f"{reserved} place(s) were reserved for unseen scaffolds and the pool holds none, "
            "so the whole budget goes on chemotypes the model has already been trained on."
        )
        reserved = 0
    if reserved:
        notes.append(
            f"{reserved} of {budget} place(s) reserved for scaffolds the surrogate has never "
            "seen, filled before anything the model believes. findings/0003 is the reason: on "
            "an unseen chemotype the forest was unanimously wrong by 3.13 pIC50 and its "
            "interval was the narrowest in the series, so uncertainty alone does not find these."
        )

    remaining = list(candidates)
    chosen: list[Pick] = []
    taken_scaffolds: set[str] = set()

    # The reserved places first, by diversity among the unfamiliar: one per scaffold, widest
    # interval first within a scaffold, because if the model must guess it should guess where it
    # admits to guessing.
    if reserved:
        unseen_by_scaffold: dict[str, Interval] = {}
        for item in sorted(unseen_available, key=lambda x: (-x.half_width, x.parent_id)):
            unseen_by_scaffold.setdefault(scaffold_of[item.parent_id], item)
        for item in list(unseen_by_scaffold.values())[:reserved]:
            chosen.append(
                Pick(
                    parent_id=item.parent_id,
                    rank=len(chosen) + 1,
                    score=item.mean + kappa * item.half_width,
                    predicted=item.mean,
                    half_width=item.half_width,
                    reason="unseen_scaffold",
                    scaffold=scaffold_of.get(item.parent_id, ""),
                )
            )
            taken_scaffolds.add(scaffold_of[item.parent_id])
        picked = {pick.parent_id for pick in chosen}
        remaining = [item for item in remaining if item.parent_id not in picked]

    while remaining and len(chosen) < budget:
        scored: list[tuple[float, str, Interval]] = []
        for item in remaining:
            scaffold = scaffold_of.get(item.parent_id, "")
            novel = bool(scaffold) and scaffold not in seen
            # Upper confidence bound, plus what an unseen chemotype is worth, minus a penalty
            # for a scaffold this batch already holds. The penalty is the batch-aware part:
            # two members of one series move the model the same way, so the second is worth
            # less than its own marginal score suggests.
            score = item.mean + kappa * item.half_width
            reason = "potency"
            if kappa * item.half_width > abs(item.mean - best.mean) and item is not best:
                reason = "uncertainty"
            if novel and novelty_bonus:
                score += novelty_bonus
                reason = "unseen_scaffold"
            if scaffold and scaffold in taken_scaffolds:
                # The batch-aware term: two members of one series move the model the same way,
                # so the second is worth less than its marginal score says. Scaled to the
                # candidate's own interval rather than to an absolute, so the penalty cannot
                # dominate a well-determined ranking the way the old fixed bonus did.
                score -= kappa * item.half_width + SIBLING_PENALTY
                if reason == "potency":
                    reason = "diversity"
            scored.append((score, reason, item))

        scored.sort(key=lambda triple: (-triple[0], triple[2].parent_id))
        score, reason, item = scored[0]
        chosen.append(
            Pick(
                parent_id=item.parent_id,
                rank=len(chosen) + 1,
                score=score,
                predicted=item.mean,
                half_width=item.half_width,
                reason=reason,
                scaffold=scaffold_of.get(item.parent_id, ""),
            )
        )
        taken_scaffolds.add(scaffold_of.get(item.parent_id, ""))
        remaining = [other for other in remaining if other.parent_id != item.parent_id]

    if len(chosen) < budget:
        notes.append(
            f"Only {len(chosen)} candidate(s) available for a budget of {budget}."
        )
    unseen_taken = sum(1 for pick in chosen if pick.reason == "unseen_scaffold")
    if seen and not unseen_taken:
        notes.append(
            "Every molecule in this batch comes from a scaffold the surrogate has already been "
            "trained on, so the batch will sharpen what the model knows and test none of it. "
            "That is a legitimate choice late in a campaign and a poor one early."
        )
    return Batch(
        picks=tuple(chosen),
        tied_with_best=tied,
        scaffolds=len({pick.scaffold for pick in chosen if pick.scaffold}),
        notes=tuple(notes),
    )


__all__ = [
    "DEFAULT_EXPLORE_FRACTION",
    "DEFAULT_KAPPA",
    "DEFAULT_NOVELTY_BONUS",
    "SIBLING_PENALTY",
    "Batch",
    "Pick",
    "acquire",
]
