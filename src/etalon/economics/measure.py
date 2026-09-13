"""Measure a stage's rank correlation on your own panel, with the uncertainty on the number itself.

Every correlation in :mod:`etalon.economics.stage` is a placeholder, and ``findings/0004`` says the
docking tier's is the input a whole funnel plan turns on: 0.15 of rank correlation there outweighs ten
times the compute budget. So the single most valuable thing a campaign can do with this package is
replace that placeholder, and this module is the two columns and a Spearman it keeps being described
as.

One thing the description leaves out, and it changes how the result should be used. **A measured
correlation has its own uncertainty**, and on a panel of a couple of hundred molecules that uncertainty
is the same size as the differences the planner is asked to act on. The Fisher-transform standard error
of a Spearman is roughly ``1/sqrt(n - 3)``: about 0.066 at 231 molecules, so a measured 0.45 is 0.32 to
0.57 at 95%. Two stages 0.09 apart are not distinguishable on such a panel however carefully each was
measured, which is exactly the refusal the planner already makes -- and this is where the number it
refuses with should come from.

That is worth stating because the planner's ``resolvable_spearman`` default of 0.10 was derived from an
AUC's Hanley-McNeil standard error, which is a different statistic on the same panel. Both land near
0.10 at these sizes, and the correlation's own interval is the more direct answer:
:attr:`Measured.resolvable` computes it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from etalon.economics.stage import Answers, Evidence, Stage


@dataclass(frozen=True, slots=True)
class Measured:
    """A stage's rank correlation, measured, with what the panel can resolve around it."""

    spearman: float
    molecules: int
    #: Fisher-transform 95% interval on the correlation.
    low: float
    high: float
    #: What the panel was ranked against, recorded because "affinity" covers several things and a
    #: correlation against a predicted affinity is a correlation between two models.
    truth: str
    #: How many molecules were dropped for a missing score or a missing answer.
    dropped: int = 0

    @property
    def standard_error(self) -> float:
        """Approximately ``1/sqrt(n - 3)``, on the Fisher-transformed scale."""

        return 1.0 / math.sqrt(max(self.molecules - 3, 1))

    @property
    def resolvable(self) -> float:
        """The smallest correlation difference this panel can distinguish.

        Twice the standard error, matching the convention the rest of the package uses for a
        resolution: below it, two stages measured on this panel are one stage at two prices.
        """

        return 2.0 * self.standard_error

    def as_dict(self) -> dict[str, object]:
        return {
            "spearman": round(self.spearman, 4),
            "confidence_95": [round(self.low, 4), round(self.high, 4)],
            "molecules": self.molecules,
            "dropped": self.dropped,
            "standard_error": round(self.standard_error, 4),
            "smallest_resolvable_difference": round(self.resolvable, 4),
            "ranked_against": self.truth,
        }


def measure(
    scores: Sequence[float | None],
    answers: Sequence[float | None],
    *,
    truth: str,
    lower_is_stronger: bool = True,
    answer_is_concentration: bool = True,
) -> Measured:
    """Rank-correlate a stage's scores against known answers.

    Args:
        scores: The stage's output, one per molecule. ``None`` for a molecule it could not score.
        answers: The known affinities. ``None`` where unknown.
        truth: What the answers are. Write "measured IC50, nM" or "Boltz-2 predicted log(IC50)" --
            the distinction matters, because a correlation against a predicted affinity is a
            correlation between two models and says nothing about either being right.
        lower_is_stronger: True for a docking score in kcal/mol, where -11 beats -8.
        answer_is_concentration: True when a smaller answer means a stronger binder, as for IC50 in
            nanomolar. The two flags are separate because a stage and a panel can disagree about
            direction, and getting that wrong flips the sign of the whole measurement -- which looks
            like a terrible stage rather than like a mistake.

    Raises:
        ValueError: If fewer than 10 molecules have both a score and an answer. A correlation on
            fewer carries an interval wide enough to contain almost any value, and reporting one
            invites it to be used.
    """

    from scipy import stats

    if len(scores) != len(answers):
        raise ValueError(f"{len(scores)} scores and {len(answers)} answers describe different sets")

    pairs = [
        (float(score), float(answer))
        for score, answer in zip(scores, answers, strict=True)
        if score is not None and answer is not None
    ]
    dropped = len(scores) - len(pairs)
    if len(pairs) < 10:
        raise ValueError(
            f"only {len(pairs)} molecule(s) have both a score and a known answer. A rank correlation "
            "on that many carries an interval wide enough to contain almost any value, and publishing "
            "one invites it to be acted on."
        )

    x = [-score if lower_is_stronger else score for score, _ in pairs]
    y = [-answer if answer_is_concentration else answer for _, answer in pairs]
    rho = float(stats.spearmanr(x, y).statistic)

    count = len(pairs)
    error = 1.0 / math.sqrt(max(count - 3, 1))
    # Fisher transform, so the interval does not run outside [-1, 1] near the ends.
    clipped = max(min(rho, 0.999999), -0.999999)
    centre = math.atanh(clipped)
    low, high = math.tanh(centre - 1.96 * error), math.tanh(centre + 1.96 * error)
    return Measured(
        spearman=rho, molecules=count, low=low, high=high, truth=truth, dropped=dropped
    )


@dataclass(frozen=True, slots=True)
class EnrichmentPoint:
    """Enrichment at one keep fraction, with the interval the counts allow."""

    keep_fraction: float
    molecules: int
    hits: int
    actives: int
    base_rate: float
    low: float
    high: float

    @property
    def retained(self) -> float:
        """Share of the actives that survive this cut. What the funnel model consumes."""

        return self.hits / self.actives if self.actives else 0.0

    @property
    def enrichment(self) -> float:
        return (self.hits / self.molecules) / self.base_rate if self.molecules and self.base_rate else 0.0

    @property
    def better_than_random(self) -> bool:
        """Whether the interval excludes an enrichment of one. The only claim worth making."""

        return self.low > self.base_rate

    def as_dict(self) -> dict[str, object]:
        return {
            "keep_fraction": self.keep_fraction,
            "molecules": self.molecules,
            "hits": self.hits,
            "hit_rate": round(self.hits / self.molecules, 4) if self.molecules else 0.0,
            "hit_rate_95": [round(self.low, 4), round(self.high, 4)],
            "enrichment": round(self.enrichment, 3),
            "enrichment_95": [
                round(self.low / self.base_rate, 3) if self.base_rate else 0.0,
                round(self.high / self.base_rate, 3) if self.base_rate else 0.0,
            ],
            "retained_active_fraction": round(self.retained, 4),
            "better_than_random": self.better_than_random,
        }


def measure_enrichment(
    scores: Sequence[float | None],
    answers: Sequence[float | None],
    *,
    active_below: float,
    keep_fractions: Sequence[float] = (0.01, 0.05, 0.10, 0.25, 0.50),
    lower_is_stronger: bool = True,
) -> tuple[EnrichmentPoint, ...]:
    """Measure the enrichment curve, which is what a funnel actually uses.

    A rank correlation summarises the whole list and a funnel only uses its head. Measured on a real
    panel, docking's correlation was 0.108 while its enrichment of sub-10 nM compounds at the top 1%
    was 9.62: the correlation averaged strong selectivity at the head with noise in the bulk into a
    number describing neither.

    The intervals matter more than the values here. At 231 molecules the top 1% is two molecules, so an
    enrichment of 9.62 carries an interval from 1.82 to 17.43, and :attr:`EnrichmentPoint.better_than_
    random` is the only claim such a point supports.

    Args:
        active_below: The threshold that makes a molecule active, in the answers' own units. It is a
            decision rather than a property of the data, and it moves everything: on the panel measured
            here, enrichment at the top 1% was 9.62 for sub-10 nM actives, 1.78 for sub-100 nM and 0.86
            -- worse than random -- for sub-1000 nM.
    """

    from etalon.generate.audit import wilson

    pairs = [
        (float(score), float(answer))
        for score, answer in zip(scores, answers, strict=True)
        if score is not None and answer is not None
    ]
    if len(pairs) < 20:
        raise ValueError(
            f"{len(pairs)} molecule(s) with both a score and an answer. An enrichment curve needs "
            "enough that its top percentile is more than one molecule; below twenty it is arithmetic "
            "on single counts."
        )
    ordered = sorted(pairs, key=lambda pair: pair[0] if lower_is_stronger else -pair[0])
    active = [answer < active_below for _, answer in ordered]
    total_actives = sum(active)
    if not total_actives:
        raise ValueError(
            f"no molecule is active below {active_below}. An enrichment curve against an empty active "
            "set is a division by zero dressed as a measurement."
        )
    base = total_actives / len(ordered)

    out: list[EnrichmentPoint] = []
    for fraction in keep_fractions:
        count = max(1, int(round(len(ordered) * fraction)))
        hits = sum(active[:count])
        low, high = wilson(hits, count)
        out.append(
            EnrichmentPoint(
                keep_fraction=fraction,
                molecules=count,
                hits=hits,
                actives=total_actives,
                base_rate=base,
                low=low,
                high=high,
            )
        )
    return tuple(out)


def with_enrichment(template: Stage, points: Sequence[EnrichmentPoint]) -> Stage:
    """A stage carrying a measured enrichment curve, which the planner uses in place of a correlation.

    The planner will then hold the tier to the range these points cover, which is the behaviour that
    matters: a curve measured down to the top 1% says nothing about the top 0.01%, and a plan that
    operates there is resting on a clamp rather than on evidence.

    Only the curve is marked measured. This function used to set the stage-level ``evidence`` to
    ``MEASURED_HERE``, which left the placeholder ``spearman`` and the unattributed ``gpu_hours``
    sitting under a label that said somebody had measured them -- ``as_dict`` published
    ``spearman: 0.35`` beside ``evidence: measured_here``. What was measured here is the enrichment,
    and that is now the only thing this function claims.
    """

    from dataclasses import replace

    if not points:
        raise ValueError("an enrichment curve needs at least one point")
    curve = tuple(sorted((point.keep_fraction, point.retained) for point in points))
    best = max(points, key=lambda point: point.enrichment)
    return replace(
        template,
        enrichment=curve,
        enrichment_evidence=Evidence.MEASURED_HERE,
        source=(
            f"ENRICHMENT measured on this campaign's own panel over {points[0].actives} actives in "
            # Reconstructed from the base rate rather than from molecules/keep_fraction, which
            # rounded a 231-molecule panel to "200" because the top 1% of 231 is 2 molecules and
            # 2/0.01 is 200. actives/base_rate is the panel size exactly.
            f"{round(points[0].actives / points[0].base_rate) if points[0].base_rate else 0} "
            f"molecules. Best point: {best.enrichment:.2f}-fold at the top {best.keep_fraction:.0%} "
            f"(95% {best.low / best.base_rate:.2f} to {best.high / best.base_rate:.2f})"
            + (
                ", which excludes one."
                if best.better_than_random
                else ", whose interval includes one -- so this tier is not demonstrably better than "
                "random anywhere on the measured curve."
            )
            + f" Read that beside the counts: the best point rests on {best.hits} hit(s) in "
            f"{best.molecules} molecule(s). The planner will hold this tier to the keep fractions "
            "measured here. Every other number in this stage is unchanged -- "
            + template.source
        ),
    )


def as_stage(template: Stage, measured: Measured, *, gpu_hours: float | None = None) -> Stage:
    """A catalogue stage with the measured correlation substituted in, and the source rewritten.

    The source is replaced rather than appended to, because the placeholder's text explains why the
    number was a convention and keeping it beside a measurement would leave a reader unsure which
    applies.

    The correlation is marked measured, and the cost is marked measured only when one was supplied.
    Setting the stage-level ``evidence`` here used to relabel ``gpu_hours`` as measured while leaving
    the catalogue's own unattributed figure in place.
    """

    if template.answers not in (Answers.RANKING, Answers.ABSOLUTE_AFFINITY, Answers.RELATIVE_AFFINITY):
        raise ValueError(
            f"{template.id} answers {template.answers.value} rather than ranking, so a rank "
            "correlation is not a property it has. Measuring one would invite it to be compared with "
            "stages that do rank."
        )
    from dataclasses import replace

    return replace(
        template,
        spearman=measured.spearman,
        gpu_hours=template.gpu_hours if gpu_hours is None else gpu_hours,
        spearman_evidence=Evidence.MEASURED_HERE,
        gpu_hours_evidence=None if gpu_hours is None else Evidence.MEASURED_HERE,
        source=(
            f"Measured on this campaign's own panel: Spearman {measured.spearman:.3f} "
            f"(95% {measured.low:.3f} to {measured.high:.3f}) over {measured.molecules} molecules "
            f"against {measured.truth}"
            + (f", {measured.dropped} dropped for a missing score or answer" if measured.dropped else "")
            + f". The panel resolves differences of about {measured.resolvable:.3f}, so this number "
            "should be compared with another stage's only when they differ by more than that."
            + (
                ""
                if gpu_hours is not None
                # Named rather than quoted. The placeholder's own text explains why the
                # *correlation* was a convention, and that explanation stops applying the moment
                # one is measured -- but the cost was never part of this measurement, so its
                # provenance class has to survive the substitution that replaces the text.
                else f" The cost of {template.gpu_hours:g} GPU-hours per molecule is not part of "
                f"this measurement and remains {template.evidence_for('gpu_hours').value}."
            )
        ),
    )


__all__ = [
    "EnrichmentPoint",
    "Measured",
    "as_stage",
    "measure",
    "measure_enrichment",
    "with_enrichment",
]
