"""What each stage of a funnel costs, and what it buys, with the source for both.

A cascade is a sequence of filters of increasing cost and increasing accuracy, and every
description of one says so. What none of them say is the exchange rate. Docking is "cheap and
approximate", FEP is "expensive and accurate", and somewhere between those two adjectives is a
decision about how to spend a month of GPU time that nobody is asked to justify.

This module makes the exchange rate explicit, in the same shape the fault taxonomy uses: every
number carries where it came from, a convention is labelled a convention, and a figure nobody
measured is never allowed to look like one that somebody did. The pattern earned its place in
``faults/taxonomy.py``, where three bands claimed a literature source and turned out to be
arguments; here the literature entries carry real citations, which is the rule working rather
than the rule being generous.

The numbers that matter most are the uncomfortable ones.

**MM-GBSA over 8 ns reaches Spearman 0.767, which is 0.087 below FEP** (Wang & Hou's end-point
review), at a small fraction of the cost. So the expensive tier's advantage over the mid tier is
about a tenth of a rank correlation, and on a panel of a few hundred molecules that gap is close
to what the panel can resolve at all.

**A single-trajectory MM-PBSA estimate is not reproducible to better than about 12 kcal/mol.**
Calculations started from the same structures varied by up to 12 kcal/mol for small molecules
bound to HIV-1 protease, and replicas within one ensemble by up to 15. The distributions are not
Gaussian -- skewness and excess kurtosis are definitively non-zero across 500-replica runs, and
normality is rejected for all nine systems tested. So the cost of a usable MM-PBSA number is the
cost of an ensemble, not of a run, and a pipeline that budgets for one run has budgeted for
something that cannot be ranked on.

**PMF by umbrella sampling costs roughly 2.1 microseconds of simulation per complex** -- 20 to 24
windows at 30 ns across three to five independent sets -- which is hundreds of GPU-hours per
ligand against tens for an FEP edge. It is an order of magnitude more expensive than the thing
usually placed after it, and its unbinding path is still chosen by trial and error. What it buys
is not a better affinity: a head-to-head on PARP1 found neither the physical nor the alchemical
route categorically more accurate. It buys mechanism -- intermediates, transient pockets, the
dissociation process in explicit water -- which is a different question from ranking, and a tier
that answers a different question does not belong in a funnel that is ranking.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar


class Evidence(StrEnum):
    """Where a stage's number came from. Never omitted, never inferred."""

    #: Measured in this project; ``source`` names the run or the findings file.
    MEASURED_HERE = "measured_here"
    #: A published value, cited in full.
    LITERATURE = "literature"
    #: Somebody chose it. Legitimate, and labelled.
    CONVENTION = "convention"
    #: Derived from published aggregates rather than reported directly -- a simulation length
    #: converted to GPU-hours, for instance. Separated from LITERATURE because the conversion is
    #: this project's and could be wrong in a way the citation is not.
    INFERRED = "inferred"


class Answers(StrEnum):
    """What question a stage actually answers, which decides where it can sit."""

    #: Produces a number meant to order molecules by affinity.
    RANKING = "ranking"
    #: Produces a pass or fail on physical validity, not an affinity.
    VALIDITY = "validity"
    #: Produces an absolute free energy.
    ABSOLUTE_AFFINITY = "absolute_affinity"
    #: Produces a relative free energy between two similar molecules.
    RELATIVE_AFFINITY = "relative_affinity"
    #: Produces a mechanism: a path, an intermediate, a residence time. Not a rank.
    MECHANISM = "mechanism"


@dataclass(frozen=True, slots=True)
class Stage:
    """One tier of a funnel, priced and scored."""

    id: str
    label: str
    answers: Answers
    #: GPU-hours for one molecule. For a relative method, one edge.
    gpu_hours: float
    #: Spearman rank correlation with experiment, where one is published. ``None`` where the
    #: stage does not rank -- a validity filter has no correlation with affinity and giving it
    #: one would invite it to be compared with stages that do.
    spearman: float | None
    #: Run-to-run spread in kcal/mol on repeating the stage from the same inputs. The number a
    #: campaign needs in order to know whether one run is a measurement. ``None`` for a
    #: deterministic stage.
    run_to_run_kcal_mol: float | None
    evidence: Evidence
    source: str
    #: What makes the stage's number wrong in a way more sampling cannot fix.
    systematic_caveat: str = ""
    #: A measurement this project took that disagrees with a number above, stated on the stage so
    #: that a reader of a default plan cannot miss it.
    #:
    #: The case this exists for: docking's ``spearman`` is 0.35, a placeholder, and this project
    #: measured 0.108 on its own panel -- about three times lower, with an interval including zero.
    #: Substituting 0.108 into the catalogue was rejected, because ``findings/0009`` is explicit that
    #: it is one engine, one receptor conformation, one box and shipped defaults, and generalising it
    #: to every campaign's docking tier would be the same over-reach as the convention it replaces.
    #: But shipping the convention in silence is worse: the default plan prints a funnel three times
    #: too optimistic and nothing on the page says the authors had measured otherwise. So the number
    #: stays and the contradiction travels with it.
    contradicted_by: str = ""
    #: For a validity tier: the share of decoys it rejects. A validity filter does not rank, so
    #: it cannot be given a keep fraction chosen to retain actives -- but it is not free of
    #: consequence either. It enriches by rejecting more decoys than actives, and modelling it as
    #: passing everything made MD stability look like a tier that cost GPU-days and did nothing.
    decoy_rejection: float = 0.0
    #: For a validity tier: the share of true actives it rejects as collateral. Above zero for
    #: every real filter, and the number that decides whether the tier is worth its place.
    active_rejection: float = 0.0
    #: A measured enrichment curve: ``((keep_fraction, retained_active_fraction), ...)``, ascending in
    #: keep fraction. When present it is used directly and the rank correlation is not consulted.
    #:
    #: This exists because the correlation turned out to be the wrong summary for a funnel. Measured on
    #: the real panel, docking's rank correlation was 0.108 while its enrichment of sub-10 nM compounds
    #: at the top 1% was 9.62 -- the Gaussian model behind :func:`etalon.economics.allocate.retention`
    #: predicts 1.72 from that correlation, which is the bottom of the measured 95% interval of 1.82 to
    #: 17.43. The two are not flatly contradictory and the central estimates differ by a factor of five,
    #: because a single correlation averages strong enrichment at the head of the list with noise in the
    #: bulk, and a funnel only ever uses the head.
    #:
    #: So a curve beats a correlation whenever one has been measured, and using a correlation is an
    #: assumption about the shape of the score-to-affinity relationship rather than a measurement of it.
    enrichment: tuple[tuple[float, float], ...] = ()

    # -- provenance per quantity, because one label over five numbers laundered three of them ----
    #
    # ``evidence`` above describes the stage, and a stage carries at least five numbers that came
    # from different places. That was not a cosmetic problem. ``measure.with_enrichment`` substituted
    # a measured enrichment curve and set ``evidence = MEASURED_HERE``, leaving ``spearman`` at the
    # catalogue's placeholder -- so ``as_dict`` published ``spearman: 0.35`` under
    # ``evidence: measured_here``, which is exactly the failure this project's taxonomy module says
    # it exists to prevent, committed by the module that prices it. ``as_stage`` did the same to
    # ``gpu_hours``.
    #
    # Compare ``Fault.evidence``, which describes one quantity (the magnitude band) and works. The
    # fix is to give the quantities that get substituted independently their own label. ``None``
    # means "whatever the stage says", so every existing catalogue entry keeps its meaning and only
    # a substitution has to be explicit about what it actually measured.
    spearman_evidence: Evidence | None = None
    gpu_hours_evidence: Evidence | None = None
    enrichment_evidence: Evidence | None = None

    #: The quantities that carry their own provenance, in the order a reader cares about them.
    QUANTITIES: ClassVar[tuple[str, ...]] = ("spearman", "gpu_hours", "enrichment")

    def evidence_for(self, quantity: str) -> Evidence:
        """Where one of this stage's numbers came from.

        Falls back to the stage's own label, which is the honest reading of a catalogue entry whose
        numbers all came from the same place.
        """

        if quantity not in self.QUANTITIES:
            raise ValueError(
                f"{quantity!r} carries no separate provenance; expected one of {self.QUANTITIES}"
            )
        return getattr(self, f"{quantity}_evidence") or self.evidence

    @property
    def evidence_by_quantity(self) -> dict[str, str]:
        return {name: self.evidence_for(name).value for name in self.QUANTITIES}

    def unmeasured_quantities(self) -> tuple[tuple[str, Evidence], ...]:
        """The numbers in this stage that nobody measured, named one at a time.

        A tier whose enrichment was measured and whose cost is a conversion is no longer either
        "measured" or "a convention", and reporting it as one of those hides which half a plan is
        resting on.
        """

        out: list[tuple[str, Evidence]] = []
        for name in self.QUANTITIES:
            if name == "spearman" and self.spearman is None:
                continue
            if name == "enrichment" and not self.enrichment:
                continue
            found = self.evidence_for(name)
            if found in (Evidence.CONVENTION, Evidence.INFERRED):
                out.append((name, found))
        return tuple(out)

    @property
    def ranks_by_measurement(self) -> bool:
        """Whether this stage's selectivity was measured rather than assumed from a correlation."""

        return bool(self.enrichment)

    @property
    def measured_range(self) -> tuple[float, float] | None:
        """The smallest and largest keep fractions the enrichment curve was measured at."""

        if not self.enrichment:
            return None
        keeps = [keep for keep, _ in self.enrichment]
        return min(keeps), max(keeps)

    def retained_at(self, keep_fraction: float) -> float | None:
        """Share of actives retained at a keep fraction, from the measured curve, or ``None``.

        Log-interpolated in the keep fraction, because a funnel's operating points span orders of
        magnitude and linear interpolation between 1% and 10% is dominated by the upper end.

        Outside the measured range it returns the nearest measured point, and that clamp is not a safe
        default -- it is the reason :func:`etalon.economics.allocate.plan` constrains a curve-carrying
        tier to its measured range. Clamping satisfies "do not extrapolate" in letter and breaks it in
        spirit: measured here, the optimiser chose a keep fraction of 0.0001 for a curve measured down
        to 0.01, then inherited the 1% retention figure at an operating point 125 times outside the
        evidence, and reported the resulting plan as affordable with a 100% hit rate. Nothing about
        that output looked like an extrapolation.
        """

        if not self.enrichment:
            return None
        points = sorted(self.enrichment)
        if keep_fraction <= points[0][0]:
            return points[0][1]
        if keep_fraction >= points[-1][0]:
            return points[-1][1]
        import math

        for (left_keep, left_value), (right_keep, right_value) in zip(points, points[1:], strict=False):
            if left_keep <= keep_fraction <= right_keep:
                span = math.log(right_keep) - math.log(left_keep)
                if span <= 0:
                    return left_value
                weight = (math.log(keep_fraction) - math.log(left_keep)) / span
                return left_value + weight * (right_value - left_value)
        return points[-1][1]

    @property
    def reproducible_from_one_run(self) -> bool:
        """Whether a single execution is a measurement of anything.

        A stage whose run-to-run spread exceeds 1 kcal/mol cannot be ranked on from one run: the
        threshold is RT*ln(10) at 310 K, which is the free energy of one log unit of affinity, so
        a spread above it means two molecules a factor of ten apart can swap places between runs.
        """

        return self.run_to_run_kcal_mol is None or self.run_to_run_kcal_mol <= 1.42

    def cost_for(self, molecules: int, *, replicas: int = 1) -> float:
        """GPU-hours to run this stage on a population, at a given replica count."""

        if molecules < 0 or replicas < 1:
            raise ValueError("molecules must be non-negative and replicas at least one")
        return self.gpu_hours * molecules * replicas

    def replicas_for_a_measurement(self) -> int:
        """How many runs it takes before this stage's output is a measurement.

        Five where the spread demands an ensemble, which is the replica count Coveney's TIES and
        ESMACS protocols settle on. Stated as a convention rather than a derivation, because the
        literature is explicit that no theoretical means exists to establish the number: the
        criterion is to find N such that N+1 changes nothing, which is a measurement per system
        rather than a constant.
        """

        return 1 if self.reproducible_from_one_run else 5

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "answers": self.answers.value,
            "gpu_hours_per_molecule": self.gpu_hours,
            "spearman": self.spearman,
            "run_to_run_kcal_mol": self.run_to_run_kcal_mol,
            "reproducible_from_one_run": self.reproducible_from_one_run,
            "replicas_for_a_measurement": self.replicas_for_a_measurement(),
            "evidence": self.evidence.value,
            # Published beside the scalar, never instead of it: a reader who wants to know where
            # ``spearman`` came from should not have to infer it from a label describing the stage.
            "evidence_by_quantity": self.evidence_by_quantity,
            "unmeasured_quantities": [name for name, _ in self.unmeasured_quantities()],
            "source": self.source,
            "systematic_caveat": self.systematic_caveat,
            "contradicted_by": self.contradicted_by,
            "measured_enrichment_curve": [list(point) for point in self.enrichment],
            "ranks_by_measurement": self.ranks_by_measurement,
        }


# -- the catalogue ---------------------------------------------------------
#
# Ordered by cost. Every GPU-hour figure is for one molecule on one modern GPU, and the ones
# marked INFERRED are conversions this project made from published simulation lengths -- the
# conversion could be wrong in a way the citation is not, which is why it is a separate class.

_LILLY = Stage(
    id="lilly_demerits",
    label="Lilly medchem rules",
    answers=Answers.VALIDITY,
    gpu_hours=3e-7,
    spearman=None,
    run_to_run_kcal_mol=None,
    evidence=Evidence.MEASURED_HERE,
    source=(
        "Measured in this project on the STK17B set: enrichment factor 4.66, and 17 of 77 "
        "approved oral drugs rejected -- which is the cost of the filter, not a defect of it. "
        "The 0.22 active rejection is that 17-of-77 figure used as a proxy for how often a "
        "real drug-like active is refused; the decoy rejection is inferred from the enrichment."
    ),
    decoy_rejection=0.66,
    active_rejection=0.22,
    systematic_caveat=(
        "It is a liability filter, not an affinity predictor. Using its demerit total to rank "
        "binding is a category error; measured here, it is the only derived metric the default "
        "funnel produces, which is why Screen.metrics refuses to choose it for you."
    ),
)

_DOCKING = Stage(
    id="docking",
    label="Docking (Vina/Uni-Dock class)",
    answers=Answers.RANKING,
    gpu_hours=3e-4,
    spearman=0.35,
    run_to_run_kcal_mol=None,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement here and no single citation: published docking-vs-experiment rank "
        "correlations scatter roughly 0.2 to 0.5 across targets, and 0.35 is the middle of that "
        "range chosen as a placeholder. A campaign should replace it with its own number on its "
        "own panel, which is one line of etalon.learn.calibrate."
    ),
    systematic_caveat=(
        "Scores a pose against one receptor conformation, so it is blind to induced fit and to "
        "anything its scoring function does not model. PoseBusters found AI docking methods "
        "routinely produce physically invalid poses that pass an RMSD criterion."
    ),
    contradicted_by=(
        "findings/0009: measured on this project's own 231-molecule STK17B panel, Uni-Dock at "
        "shipped defaults reached Spearman 0.108 with a 95% interval of -0.021 to 0.234 -- an "
        "interval that includes zero, and about three times below the 0.35 above. That measurement "
        "is one engine, one receptor conformation, one box and one target, so it does not replace "
        "the convention; it does mean a plan built on 0.35 is optimistic by an unknown factor that "
        "was three on the only panel anyone here has checked. Pass --measured docking=<your number> "
        "before believing a funnel that rests on this tier."
    ),
)

_BOLTZ2 = Stage(
    id="boltz2_affinity",
    label="Boltz-2 co-folded affinity",
    answers=Answers.RANKING,
    gpu_hours=0.006,
    spearman=None,
    run_to_run_kcal_mol=None,
    evidence=Evidence.MEASURED_HERE,
    source=(
        "Cost measured in this project: tens of seconds per molecule on an RTX 4090, which is "
        "why MolCascade's catalogue puts it last among the ranking tiers. No rank correlation "
        "is asserted here because this project has not measured one and a campaign's own panel "
        "is the only place to get it."
    ),
    systematic_caveat=(
        "Rebuilds the complex from sequence and SMILES, so its opinion is independent of every "
        "pose above it -- which is its value and also means it cannot confirm one."
    ),
)

_MMGBSA_ONE = Stage(
    id="mmgbsa_single",
    label="MM-GBSA, one 8 ns replica",
    answers=Answers.RANKING,
    gpu_hours=2.0,
    spearman=0.767,
    run_to_run_kcal_mol=12.0,
    evidence=Evidence.LITERATURE,
    source=(
        "Spearman 0.767 from 8 ns sampling, 0.087 below FEP on the same comparison: Wang E, "
        "Sun H, Wang J, et al. End-point binding free energy calculation with MM/PBSA and "
        "MM/GBSA. Chem Rev. 2019;119(16):9478-9508. Run-to-run spread: Wan S, Bhati AP, Zasada "
        "SJ, Coveney PV, and related ensemble work -- MM-PBSA from single simulations started "
        "from the same structures varied by up to 12 kcal/mol for small molecules bound to "
        "HIV-1 protease, and replicas within one ensemble by up to 15 kcal/mol."
    ),
    systematic_caveat=(
        "The 0.767 is a rank correlation over a set, where the per-molecule noise partly "
        "averages out. It is not a statement that one molecule's number is good to 0.087 of "
        "anything. And MM-GBSA ranks better than MM-PBSA while predicting absolute values "
        "worse, so the choice between them depends on which you need."
    ),
)

_MMGBSA_ENSEMBLE = Stage(
    id="mmgbsa_ensemble",
    label="MM-GBSA, 5-replica ensemble",
    answers=Answers.RANKING,
    gpu_hours=10.0,
    spearman=0.767,
    run_to_run_kcal_mol=1.0,
    evidence=Evidence.INFERRED,
    source=(
        "Cost is five times the single replica. The spread is this project's inference, not a "
        "published figure: averaging five draws from a distribution with a 12 kcal/mol range "
        "narrows the mean's spread by roughly sqrt(5), and the literature is explicit that these "
        "distributions are non-Gaussian -- skewness and excess kurtosis definitively non-zero "
        "over 500 replicas, normality rejected for all nine systems tested -- so the square-root "
        "law is an approximation here rather than a result."
    ),
    systematic_caveat=(
        "An ensemble fixes stochastic error and not systematic error. Force field and starting "
        "structure are unchanged by running five of them, and the BRD4 work found robust "
        "rankings needed ensembles *plus* multiple trajectories plus explicit solvation."
    ),
)

_MD_STABILITY = Stage(
    id="md_stability",
    label="Unbiased MD, pose stability",
    answers=Answers.VALIDITY,
    gpu_hours=24.0,
    spearman=None,
    run_to_run_kcal_mol=None,
    evidence=Evidence.INFERRED,
    source=(
        "Roughly 1 GPU-day per 1000 ns of a solvated protein-ligand system of this size on a "
        "current card; this project measured the build path rather than the production rate, so "
        "the figure is a conversion and not a benchmark taken here. The rejection rates below "
        "are conventions and nobody measured them."
    ),
    # A pose that leaves the site in 1000 ns was usually never a binder, and a pose that holds
    # usually was -- so the filter is asymmetric, which is the whole argument for it. Half the
    # decoys and a tenth of the actives is a guess, stated as one: a campaign that has run this
    # tier on a panel with known answers can measure both numbers in an afternoon, and until it
    # does, every retention figure downstream of here inherits a guess.
    decoy_rejection=0.5,
    active_rejection=0.1,
    systematic_caveat=(
        "Answers validity, not affinity: a pose that holds for 1000 ns is not a tighter binder "
        "than one that holds for 200 ns, and ranking on residence-in-the-pocket confuses a "
        "filter with a predictor. One trajectory also samples one instance of a chaotic "
        "process, so a single run that loses the pose has not shown the pose is wrong."
    ),
)

_FEP = Stage(
    id="fep_edge",
    label="Alchemical FEP, one edge",
    answers=Answers.RELATIVE_AFFINITY,
    gpu_hours=12.0,
    spearman=0.854,
    run_to_run_kcal_mol=1.0,
    evidence=Evidence.LITERATURE,
    source=(
        "12+ GPU-hours per ligand with r around 0.65 and RMSE just under 1 kcal/mol is the "
        "figure reported for this class; the 0.854 Spearman is the MM-GBSA comparison read the "
        "other way -- 0.767 was stated as 0.087 below FEP on the same set (Wang et al., Chem "
        "Rev 2019), so the FEP figure is arithmetic on a published difference rather than a "
        "directly cited number."
    ),
    systematic_caveat=(
        "Relative, so it needs a congeneric series and a reference compound with a measured "
        "affinity. A mapping between two analogues that shares too few atoms turns a relative "
        "calculation into a near-total double annihilation -- this project has a fault code for "
        "it, and PRISM's mapper is Cartesian distance with no quality gate."
    ),
)

_PMF = Stage(
    id="pmf_umbrella",
    label="PMF by umbrella sampling",
    answers=Answers.MECHANISM,
    gpu_hours=200.0,
    spearman=None,
    run_to_run_kcal_mol=None,
    evidence=Evidence.INFERRED,
    source=(
        "About 2.1 microseconds of simulation per complex -- 20 to 24 windows at 30 ns across "
        "three to five independent sets -- reported in the geometrical-route literature. "
        "Converting that to roughly 200 GPU-hours is this project's arithmetic and the one "
        "figure here most likely to be wrong: no published apples-to-apples GPU-hour benchmark "
        "of umbrella sampling against FEP on the same hardware was found."
    ),
    systematic_caveat=(
        "It answers a different question. A PARP1 head-to-head found neither the physical nor "
        "the alchemical route categorically more accurate, so the ten-fold cost over an FEP edge "
        "does not buy a better affinity -- it buys intermediates, transient pockets and the "
        "dissociation process in explicit water. Unidimensional PMF also under-samples "
        "orthogonal degrees of freedom, notably ligand orientation, and the unbinding path is "
        "still chosen by trial and error, which is the obstacle to putting it in a pipeline."
    ),
)

STAGES: tuple[Stage, ...] = (
    _LILLY,
    _DOCKING,
    _BOLTZ2,
    _MMGBSA_ONE,
    _MMGBSA_ENSEMBLE,
    _MD_STABILITY,
    _FEP,
    _PMF,
)

BY_ID: dict[str, Stage] = {stage.id: stage for stage in STAGES}


def ranking_stages() -> tuple[Stage, ...]:
    """Stages whose output can order molecules. The only ones a funnel may compare."""

    return tuple(
        stage
        for stage in STAGES
        if stage.answers
        in (Answers.RANKING, Answers.ABSOLUTE_AFFINITY, Answers.RELATIVE_AFFINITY)
    )


def needs_an_ensemble() -> tuple[Stage, ...]:
    """Stages a single run of which is not a measurement."""

    return tuple(stage for stage in STAGES if not stage.reproducible_from_one_run)


__all__ = [
    "BY_ID",
    "STAGES",
    "Answers",
    "Evidence",
    "Stage",
    "needs_an_ensemble",
    "ranking_stages",
]
