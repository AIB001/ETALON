"""Measure the council, and refuse it when it is not an instrument.

``economics/allocate.py`` refuses a screening tier whose rank correlation is not distinguishably
better than the tier above it, and ``findings/0009`` is what that refusal looks like when it bites:
docking measured at Spearman 0.108 with an interval including zero, so the planner refuses the tier
and says the funnel has no affordable form. Nobody enjoyed that result. It is the correct one.

A council of language models is a ranking tier. It takes records in, emits verdicts, and those
verdicts decide whether compute is spent and whether a measurement teaches the screen. Every
argument ``economics`` makes about a tier applies to it, and this module is that argument carried
through: **a council that has not been measured is refused, and a council that has been measured
and found indistinguishable from chance is refused with its number.**

Three quantities, because the consensus precondition has two halves and the second has a floor.

**Is each seat individually better than chance?** Measured as Youden's J -- sensitivity plus
specificity minus one -- against a labelled set of adjudications. J is zero for a seat at chance
*and* for a seat that always refuses *and* for a seat that always clears, which is why it is the
statistic here: a council's characteristic failure is not a wrong vote but an unconditional one,
and accuracy would flatter a seat that refuses everything on a set where most records are bad.
This is ADR 0002's failure -- a gate that refuses the whole population -- restated as a seat.

**Are the seats redundant?** Measured as Cohen's kappa between each pair. ``tuning/knob.py`` says
two members correlating above about 0.9 contribute one opinion at two prices, and that line is
where :data:`REDUNDANT_ABOVE` comes from. It is a convention inherited from a sentence about
scoring functions, and it is labelled as one.

**Does the council as a body agree more than chance?** Fleiss' kappa across all seats. Reported
rather than gated on, because a council that disagrees constantly is not broken -- disagreement is
the signal this layer exists to produce -- but a reader needs to know whether it is a panel or a
random number generator.

What none of this establishes: that a qualified council is *right*. It establishes that its
agreement carries information about the labels it was measured against, on the distribution those
labels were drawn from. A council qualified on handoff records has said nothing about trajectories.
:meth:`Reliability.as_dict` carries the label set's description for that reason.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from etalon.council.ballot import Vote

#: Pairwise chance-corrected agreement above which two seats are reported as one opinion at two
#: prices. Inherited verbatim from ``tuning/knob.py``'s consensus precondition, which says "two
#: members correlating above about 0.9 with each other contribute one opinion at two prices".
#:
#: A convention, and twice over: that line is about Spearman correlation between rankings and this
#: is Cohen's kappa between categorical votes, which are different quantities that happen to share
#: a scale. Nothing here has measured where the boundary falls for advisors. It is reported as a
#: warning rather than enforced as a refusal for exactly that reason -- see :func:`rule`.
REDUNDANT_ABOVE = 0.9

#: Below this many labelled adjudications, no verdict on a seat is offered at all. Ten is the same
#: floor ``learn/calibrate.py`` puts on positives before it will call a change anything but
#: UNDERPOWERED, and it is a floor rather than a sufficiency: at ten labels a Wilson interval on a
#: rate spans most of the unit interval, which is the honest reading and usually a refusal.
MINIMUM_LABELS = 10


@dataclass(frozen=True, slots=True)
class Skill:
    """One seat measured against labelled adjudications.

    ``youden_j`` is the headline and ``j_low`` is what decides. The interval is built by adding the
    two Wilson lower bounds and subtracting one, which is **conservative** -- wider than the exact
    interval for J, because it pretends the two error rates attain their worst bounds together.
    Stated because it matters in one direction: it refuses seats that a sharper interval would
    admit, and never the reverse.
    """

    seat: str
    #: Fault present and the seat voted REFUSE.
    true_refusals: int
    #: Fault present and the seat did not vote REFUSE.
    missed: int
    #: Fault absent and the seat voted REFUSE.
    false_refusals: int
    #: Fault absent and the seat did not vote REFUSE.
    true_clears: int
    #: Records where this seat abstained, counted separately. An abstention is not an error and is
    #: not a judgement; a seat that abstains on everything has a perfect error rate and no skill,
    #: which is why the rate is reported beside J rather than folded into it.
    abstentions: int = 0
    #: Abstentions on records where the fault was present, and where it was absent. Split apart
    #: after the first real measurement of a council, which produced a seat with a Youden's J of
    #: exactly 1.000 that had abstained on ten of the twelve records where the fault was there. Its
    #: J was computed on the two it answered and was perfectly true of them; read as a headline it
    #: said a seat had solved a detection problem it had almost entirely declined to attempt. An
    #: overall rate could not show that -- the seat's total abstention rate was 46%, under the
    #: threshold that would have drawn a note -- because the quantity that matters is not how often
    #: a seat declines but *which class it declines on*.
    abstained_when_present: int = 0
    abstained_when_absent: int = 0

    @property
    def labelled(self) -> int:
        return self.true_refusals + self.missed + self.false_refusals + self.true_clears

    @property
    def sensitivity(self) -> float | None:
        present = self.true_refusals + self.missed
        return self.true_refusals / present if present else None

    @property
    def specificity(self) -> float | None:
        absent = self.true_clears + self.false_refusals
        return self.true_clears / absent if absent else None

    @property
    def abstention_rate(self) -> float:
        total = self.labelled + self.abstentions
        return self.abstentions / total if total else 0.0

    def interval(self) -> tuple[float | None, float | None]:
        """Conservative bounds on Youden's J, or ``(None, None)`` when a class is missing."""

        from etalon.generate.audit import wilson

        present = self.true_refusals + self.missed
        absent = self.true_clears + self.false_refusals
        if present < 1 or absent < 1:
            # One-class label sets measure nothing. A seat scored only on records where the fault
            # was present cannot be distinguished from one that refuses unconditionally, which is
            # the exact failure this statistic exists to catch.
            return None, None
        sens_low, sens_high = wilson(self.true_refusals, present)
        spec_low, spec_high = wilson(self.true_clears, absent)
        return sens_low + spec_low - 1.0, sens_high + spec_high - 1.0

    @property
    def youden_j(self) -> float | None:
        if self.sensitivity is None or self.specificity is None:
            return None
        return self.sensitivity + self.specificity - 1.0

    @property
    def better_than_chance(self) -> bool:
        low, _ = self.interval()
        return low is not None and low > 0.0

    def as_dict(self) -> dict[str, Any]:
        low, high = self.interval()
        return {
            "seat": self.seat,
            "labelled": self.labelled,
            "sensitivity": None if self.sensitivity is None else round(self.sensitivity, 4),
            "specificity": None if self.specificity is None else round(self.specificity, 4),
            "youden_j": None if self.youden_j is None else round(self.youden_j, 4),
            "youden_j_conservative_95": [
                None if low is None else round(low, 4),
                None if high is None else round(high, 4),
            ],
            "abstentions": self.abstentions,
            "abstention_rate": round(self.abstention_rate, 4),
            "abstained_when_present": self.abstained_when_present,
            "abstained_when_absent": self.abstained_when_absent,
            "answered_of_present": self.true_refusals + self.missed,
            "answered_of_absent": self.true_clears + self.false_refusals,
            "better_than_chance": self.better_than_chance,
            "counts": {
                "true_refusals": self.true_refusals,
                "missed": self.missed,
                "false_refusals": self.false_refusals,
                "true_clears": self.true_clears,
            },
        }


def cohen_kappa(left: Sequence[str], right: Sequence[str]) -> tuple[float, float]:
    """Chance-corrected agreement between two raters, and its large-sample standard error.

    The SE is the common approximation ``sqrt(p_o (1 - p_o) / (n (1 - p_e)^2))``. It assumes the
    observed agreement is a binomial proportion and treats the marginals as fixed, neither of which
    is exactly true, and it is known to be optimistic for small samples. Named rather than hidden,
    on the same footing as ``learn/calibrate.py``'s note that the Hanley-McNeil approximation
    assumes exponential score distributions which a docking score is not.

    Returns ``(0.0, 0.0)`` when the two raters agree on everything *and* used one category: kappa
    is undefined there -- ``1 - p_e`` is zero -- and reporting 1.0 for two raters who both said the
    same word every time would be the most misleading number this module could produce.
    """

    if len(left) != len(right):
        raise ValueError(
            f"{len(left)} and {len(right)} votes cannot be paired. A kappa over mismatched "
            "sequences pairs one record's vote with another record's."
        )
    count = len(left)
    if count < 1:
        raise ValueError("kappa needs at least one paired judgement")
    categories = sorted(set(left) | set(right))
    observed = sum(1 for a, b in zip(left, right, strict=True) if a == b) / count
    expected = sum(
        (sum(1 for a in left if a == c) / count) * (sum(1 for b in right if b == c) / count)
        for c in categories
    )
    if math.isclose(expected, 1.0):
        return 0.0, 0.0
    kappa = (observed - expected) / (1.0 - expected)
    variance = observed * (1.0 - observed) / (count * (1.0 - expected) ** 2)
    return kappa, math.sqrt(max(variance, 0.0))


def fleiss_kappa(votes: Sequence[Sequence[str]]) -> float:
    """Chance-corrected agreement across a whole council.

    ``votes`` is one sequence per record, each holding every seat's vote on it. Records where the
    seats are not all present are refused rather than padded: a missing seat padded with any
    category invents agreement or disagreement that nobody expressed.
    """

    if not votes:
        raise ValueError("Fleiss' kappa needs at least one record")
    raters = len(votes[0])
    if raters < 2:
        raise ValueError("Fleiss' kappa needs at least two raters")
    for index, row in enumerate(votes):
        if len(row) != raters:
            raise ValueError(
                f"record {index} carries {len(row)} votes where the first carries {raters}. A "
                "council whose membership changed between records has not been measured as one "
                "council; score the subsets separately."
            )
    categories = sorted({vote for row in votes for vote in row})
    records = len(votes)
    agreement = 0.0
    for row in votes:
        counts = [sum(1 for vote in row if vote == c) for c in categories]
        agreement += (sum(n * n for n in counts) - raters) / (raters * (raters - 1))
    observed = agreement / records
    expected = sum(
        (sum(1 for row in votes for vote in row if vote == c) / (records * raters)) ** 2
        for c in categories
    )
    if math.isclose(expected, 1.0):
        return 0.0
    return (observed - expected) / (1.0 - expected)


def effective_votes(pairwise: Mapping[tuple[str, str], tuple[float, float]], seats: int) -> float:
    """How many independent opinions a council of ``seats`` actually carries.

    Kish's design effect, applied to advisors: ``n_eff = M / (1 + (M - 1) * rho)`` for a mean
    pairwise agreement ``rho``. It answers the question a pair-by-pair kappa table makes a reader
    assemble for themselves -- *four seats, and how many opinions?* -- and it is the statistic the
    redundancy half of the consensus precondition is really about.

    The formal ground is Ueda and Nakano's bias-variance-covariance decomposition of ensemble error
    (ICNN 1996): averaging M estimators scales the variance term by 1/M and the covariance term by
    (1 - 1/M), so as M grows the variance term vanishes and **the covariance term does not**. Past a
    small M, only lowering the correlation between members buys anything; more correlated members
    cost full price and return nothing. Wang and Wang's idealised experiment on consensus scoring
    (J. Chem. Inf. Comput. Sci. 2001) is the same law measured in this field: error cancels at
    roughly the square root of N *because the members' errors were modelled as independent*, and
    where they are not independent the advantage largely disappears. That is the mechanism behind
    the sentence tuning/knob.py quotes.

    Measured on nine LLM judges, one 2026 report puts the effective count at 2.18 of a nominal 9 --
    24% -- with the single best judge matching or beating the full panel. A council that has not
    computed this number does not know whether it is a panel or an echo.

    Two honest limits. ``rho`` here is the mean Cohen's kappa over pairs, which is categorical
    agreement rather than the product-moment correlation Kish's formula assumes; they share a scale
    and are not the same quantity. And a negative mean kappa -- seats systematically disagreeing --
    makes the formula return more effective votes than there are seats, which is not a finding
    about independence. It is clamped at ``seats`` and :func:`rule` says so rather than reporting a
    council of three as carrying four opinions.
    """

    if seats < 1:
        raise ValueError("a council has at least one seat")
    if seats == 1 or not pairwise:
        return float(seats)
    mean_rho = sum(value for value, _ in pairwise.values()) / len(pairwise)
    if mean_rho <= 0.0:
        return float(seats)
    return seats / (1.0 + (seats - 1) * mean_rho)


@dataclass(frozen=True, slots=True)
class Reliability:
    """Whether this council may sit, with the measurement that decided it."""

    qualified: bool
    skills: tuple[Skill, ...]
    #: ``(left, right) -> (kappa, standard_error)``.
    pairwise: Mapping[tuple[str, str], tuple[float, float]]
    council_kappa: float | None
    #: Kish effective sample size over the seats. A council of four carrying 1.8 of these is
    #: paying four times for less than two opinions. No default: a Reliability that did not
    #: compute it would report 0.0, which reads as a measured finding of total redundancy.
    effective_votes: float
    #: What the labels were and where they came from. Free text and mandatory, for the reason
    #: ``economics/measure.py`` makes ``truth`` mandatory: a council measured against one person's
    #: opinion of a handoff row has been measured against one person's opinion.
    labels_described: str
    refusals: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "qualified": self.qualified,
            "labels_described": self.labels_described,
            "seats": [skill.as_dict() for skill in self.skills],
            "pairwise_cohen_kappa": [
                {
                    "seats": list(pair),
                    "kappa": round(value, 4),
                    "standard_error": round(error, 4),
                    "redundant": value > REDUNDANT_ABOVE,
                }
                for pair, (value, error) in sorted(self.pairwise.items())
            ],
            "council_fleiss_kappa": (
                None if self.council_kappa is None else round(self.council_kappa, 4)
            ),
            "seats_seated": len(self.skills),
            "effective_votes": round(self.effective_votes, 3),
            "refusals": list(self.refusals),
            "notes": list(self.notes),
            "what_this_does_not_establish": (
                "That a qualified council is right. That its agreement carries information about "
                "the labels it was measured against, on the distribution those labels came from. "
                "A council qualified on handoff records has established nothing about "
                "trajectories, and one qualified on a congeneric series has established nothing "
                "about a diverse shortlist."
            ),
        }

    def render(self) -> str:
        lines = [
            f"Council: {'QUALIFIED' if self.qualified else 'REFUSED'}"
            f"  ({len(self.skills)} seats, labels: {self.labels_described})",
            "",
            f"  {'seat':<24}{'sens':>7}{'spec':>7}{'J':>8}{'J low':>8}  abst p/a  verdict",
        ]
        for skill in self.skills:
            low, _ = skill.interval()
            fmt = lambda v: "   --  " if v is None else f"{v:>7.3f}"  # noqa: E731
            lines.append(
                f"  {skill.seat:<24}{fmt(skill.sensitivity)}{fmt(skill.specificity)}"
                f"{fmt(skill.youden_j)} {fmt(low)}"
                f"{skill.abstained_when_present:>4}/{skill.abstained_when_absent:<4}"
                f"  {'skilled' if skill.better_than_chance else 'NOT ABOVE CHANCE'}"
            )
        if self.pairwise:
            lines.append("")
            for pair, (value, _) in sorted(self.pairwise.items()):
                mark = "  <- one opinion at two prices" if value > REDUNDANT_ABOVE else ""
                lines.append(f"  {pair[0]} vs {pair[1]}: kappa {value:+.3f}{mark}")
        if self.council_kappa is not None:
            lines.append(f"\n  council Fleiss kappa: {self.council_kappa:+.3f}")
        if self.skills:
            lines.append(
                f"  effective votes:      {self.effective_votes:.2f} of {len(self.skills)} seats"
            )
        if self.refusals:
            lines.append("\n  refused:")
            lines.extend(f"    {reason}" for reason in self.refusals)
        if self.notes:
            lines.append("")
            lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)


def score_seat(
    seat: str, votes: Sequence[Vote], truth: Sequence[bool]
) -> Skill:
    """Count one seat's votes against the labels.

    ``truth[i]`` is whether the fault really was present. An abstention is counted in
    ``abstentions`` and in neither error column, because a seat that declines to decide has made no
    error and demonstrated no skill, and folding the two together would let a seat buy a clean
    record by never answering.
    """

    if len(votes) != len(truth):
        raise ValueError(
            f"{len(votes)} votes against {len(truth)} labels cannot be paired, and pairing them "
            "anyway scores one record's vote against another record's answer."
        )
    counts = {"tr": 0, "miss": 0, "fr": 0, "tc": 0, "abst": 0, "abst_p": 0, "abst_a": 0}
    for vote, present in zip(votes, truth, strict=True):
        if vote is Vote.ABSTAIN:
            counts["abst"] += 1
            counts["abst_p" if present else "abst_a"] += 1
        elif present:
            counts["tr" if vote is Vote.REFUSE else "miss"] += 1
        else:
            counts["fr" if vote is Vote.REFUSE else "tc"] += 1
    return Skill(
        seat=seat,
        true_refusals=counts["tr"],
        missed=counts["miss"],
        false_refusals=counts["fr"],
        true_clears=counts["tc"],
        abstentions=counts["abst"],
        abstained_when_present=counts["abst_p"],
        abstained_when_absent=counts["abst_a"],
    )


def rule(
    votes_by_seat: Mapping[str, Sequence[Vote]],
    truth: Sequence[bool],
    *,
    labels_described: str,
) -> Reliability:
    """Decide whether this council is a measuring instrument.

    Two refusals disqualify the council outright, and both are the consensus precondition's first
    half rather than an invention here:

    Too few labels. Below :data:`MINIMUM_LABELS` nothing is offered, because an interval on ten
    binary outcomes spans most of the unit interval and a verdict drawn from it would be a
    coin-flip wearing a statistic's clothes.

    A seat not above chance. If any seat's conservative lower bound on Youden's J is at or below
    zero, the council is refused and the seat is named. Not the seat alone: a council carries its
    members' votes into a quorum, so a seat at chance is noise admitted to a vote, and the
    published condition is that *each* member performs well individually.

    Redundancy is **reported and not refused**. Two seats above :data:`REDUNDANT_ABOVE` are one
    opinion at two prices, which wastes calls and inflates a quorum -- both real, neither a reason
    to treat the council's output as uninformative. The threshold is a convention borrowed across a
    change of statistic, and refusing on a borrowed threshold would be the thing this repository
    refuses everywhere else.
    """

    if not votes_by_seat:
        raise ValueError("no seats to score")
    labels = len(truth)
    skills = tuple(
        score_seat(name, votes_by_seat[name], truth) for name in sorted(votes_by_seat)
    )
    pairwise: dict[tuple[str, str], tuple[float, float]] = {}
    names = sorted(votes_by_seat)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            pairwise[(left, right)] = cohen_kappa(
                [vote.value for vote in votes_by_seat[left]],
                [vote.value for vote in votes_by_seat[right]],
            )

    council_kappa: float | None = None
    if len(names) >= 2:
        council_kappa = fleiss_kappa(
            [[votes_by_seat[name][i].value for name in names] for i in range(labels)]
        )

    independent = effective_votes(pairwise, len(names))
    refusals: list[str] = []
    notes: list[str] = []

    if labels < MINIMUM_LABELS:
        refusals.append(
            f"{labels} labelled adjudication(s) is below the floor of {MINIMUM_LABELS}. No verdict "
            "on any seat is offered: a Wilson interval on this many binary outcomes spans most of "
            "the unit interval, so 'qualified' and 'refused' would both be readings of noise. "
            "Label more records, and label some where the fault was absent -- a set drawn only "
            "from bad records cannot distinguish a skilled seat from one that refuses everything."
        )

    for skill in skills:
        low, _ = skill.interval()
        if low is None:
            refusals.append(
                f"seat {skill.seat!r} was scored on a one-class label set "
                f"({skill.true_refusals + skill.missed} present, "
                f"{skill.true_clears + skill.false_refusals} absent), so its sensitivity and "
                "specificity cannot both be estimated and Youden's J is undefined. This is the "
                "case where a seat that refuses unconditionally looks perfect."
            )
        elif low <= 0.0:
            refusals.append(
                f"seat {skill.seat!r} is not distinguishable from chance: Youden's J is "
                f"{skill.youden_j:+.3f} with a conservative lower bound of {low:+.3f}. A member "
                "near chance contributes noise -- that is tuning/knob.py's sentence about "
                "consensus scoring and it is the published condition, not a caution added here. "
                "Give the seat different evidence, a sharper brief, or take it off the council."
            )
        present_total = skill.true_refusals + skill.missed + skill.abstained_when_present
        absent_total = skill.true_clears + skill.false_refusals + skill.abstained_when_absent
        for label, declined, total in (
            ("present", skill.abstained_when_present, present_total),
            ("absent", skill.abstained_when_absent, absent_total),
        ):
            if total and declined / total > 0.5:
                notes.append(
                    f"seat {skill.seat!r} abstained on {declined} of {total} records where the "
                    f"fault was {label}. Its sensitivity and specificity are computed on the ones "
                    "it answered and are true of those; as a summary of the seat they overstate "
                    "it, because most of that class was declined rather than judged. This is "
                    "reported per class rather than overall on purpose -- the first council "
                    "measured here abstained on 46% of records, under any overall threshold worth "
                    "setting, while declining ten of the twelve records that carried the fault."
                )

    for (left, right), (value, error) in sorted(pairwise.items()):
        if value > REDUNDANT_ABOVE:
            notes.append(
                f"seats {left!r} and {right!r} agree at kappa {value:+.3f} (SE {error:.3f}), above "
                f"the {REDUNDANT_ABOVE} this project inherited from tuning/knob.py. They are one "
                "opinion at two prices: the calls cost twice and a quorum counts them twice. "
                "Reported rather than refused -- that threshold is about rank correlation between "
                "scoring functions and this is categorical agreement between advisors, and "
                "nothing here has measured where the line belongs for the second."
            )

    if council_kappa is not None and council_kappa <= 0.0:
        notes.append(
            f"the council's Fleiss kappa is {council_kappa:+.3f}. Its seats agree no more than "
            "chance would produce, so a unanimous verdict from it carries about as much weight as "
            "one seat's. That is not on its own a reason to stop -- disagreement is what this "
            "layer is for -- but a split from this council is not evidence of a hard case."
        )

    if len(names) >= 2 and independent < 2.0:
        notes.append(
            f"{len(names)} seats carry {independent:.2f} effective votes. Ueda and Nakano's "
            "decomposition says why that matters: averaging scales the variance term by 1/M and "
            "the covariance term by (1 - 1/M), so correlated members cost full price and return "
            "nothing. Below two, a unanimous verdict from this council is one opinion reported "
            "several times -- give the seats genuinely different evidence, or seat fewer and spend "
            "the calls elsewhere."
        )
    if len(names) >= 2 and sum(v for v, _ in pairwise.values()) <= 0:
        notes.append(
            "The mean pairwise kappa is at or below zero, so the effective-vote count is clamped "
            "to the number of seats rather than computed. Seats disagreeing more than chance is "
            "not evidence of independence; on this label set it is closer to evidence that at "
            "least one of them is reading noise."
        )

    return Reliability(
        qualified=not refusals,
        skills=skills,
        pairwise=pairwise,
        council_kappa=council_kappa,
        effective_votes=independent,
        labels_described=labels_described,
        refusals=tuple(refusals),
        notes=tuple(notes),
    )


__all__ = [
    "MINIMUM_LABELS",
    "REDUNDANT_ABOVE",
    "Reliability",
    "Skill",
    "cohen_kappa",
    "effective_votes",
    "fleiss_kappa",
    "rule",
    "score_seat",
]
