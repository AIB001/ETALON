"""Shape a funnel by optimisation, and refuse a tier that cannot pay for itself.

Every cascade is described as filters of increasing cost and increasing accuracy. The question
nobody is made to answer is whether a given tier is worth its place, and it has a concrete form:
*given a fixed budget, does this tier retain more true actives than spending the same GPU-hours
letting the tier above it cut less?* That is arithmetic once a stage's cost and rank correlation
are written down, which is what :mod:`etalon.economics.stage` does.

The model is the standard one for selection under noisy measurement. Let true utility be
``T ~ N(0,1)`` and a stage's score be ``S = rho*T + sqrt(1-rho^2)*E``. Keeping the top ``f`` by
``S`` retains ``R(rho, f, a) = P(S > s_f | T > t_a)`` of the actives -- a one-dimensional integral
that is ``min(1, f/a)`` at ``rho = 1`` and ``f`` at ``rho = 0``.

Two things about the arrangement matter more than the integral.

**The keep fractions are optimised, not spread evenly.** The first version of this module divided
the reduction geometrically across the tiers, and the result was backwards: a larger budget fed
more molecules into the most expensive tier, which then had to cut harder to reach the same final
count, and expected retention *fell* from 0.055 to 0.024 as the budget went from one GPU-month to
ten GPU-years. Spreading a reduction is not allocating a budget. With the final count fixed the
product of the keep fractions is fixed too, so the problem is a small constrained optimisation:
choose where in the funnel to be permissive.

Its answer is to be permissive early and selective late -- *when it can afford to be*, and the
qualifier is not decoration. Measured over four budgets on a 750,000-molecule pool, docking's keep
fraction rises monotonically with the budget (0.0011, 0.0116, 0.1168, 0.1900) while the end-point
tier's falls. At a realistic budget the optimiser has no such freedom: an end-point method at 10
GPU-hours a molecule can see about 870 of them in a GPU-year, so the cheap tier is forced to cut
to 870 out of 255,000 whatever anyone would prefer. **The expensive tier's price sets how hard the
cheap tier must cut**, which is the mechanism behind the result in ``findings/0004``.

**The active fraction rises as the funnel proceeds.** A tier sees an enriched population, and a
model that holds the active fraction constant understates every tier after the first. The
recursion is ``a_next = a * R / f``, which is the definition of enrichment, and it is why a late
tier with a mediocre correlation can still be worth its place.

A tier is compared only against tiers answering the same question. Judging an FEP edge by its
rank correlation against an end-point method's looks reasonable and is the error this project
refuses everywhere else: a relative free energy within a congeneric series and a rank order over
a diverse library are different quantities, and FEP's advantage is in absolute accuracy -- about
1 kcal/mol RMSE against 2 to 4 -- which a Spearman over a diverse panel does not see.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from etalon.economics.stage import Answers, Stage


def retention(rho: float, keep_fraction: float, active_fraction: float) -> float:
    """The fraction of true actives surviving a cut to the top ``keep_fraction`` by a score."""

    from scipy import integrate, stats

    if not 0.0 <= rho <= 1.0:
        raise ValueError(f"rho must be in [0, 1]; got {rho}")
    for name, value in (("keep_fraction", keep_fraction), ("active_fraction", active_fraction)):
        if not 0.0 < value <= 1.0:
            raise ValueError(f"{name} must be in (0, 1]; got {value}")

    if keep_fraction >= 1.0:
        return 1.0
    if active_fraction >= 1.0:
        # Every molecule is active, so retaining a fraction of the molecules retains that
        # fraction of the actives whatever the score is worth. Special-cased because the
        # integration below puts its lower limit at norm.isf(1.0), which is negative infinity,
        # and the degenerate interval returned 0.0 -- a reader would have seen "retention 0.000"
        # and concluded the funnel was broken rather than the edge case.
        return keep_fraction
    if rho <= 0.0:
        return keep_fraction
    if rho >= 1.0:
        return min(1.0, keep_fraction / active_fraction)

    threshold = stats.norm.isf(keep_fraction)
    active_cut = stats.norm.isf(active_fraction)
    spread = math.sqrt(1.0 - rho * rho)

    def integrand(t: float) -> float:
        return stats.norm.pdf(t) * stats.norm.sf((threshold - rho * t) / spread)

    joint, _ = integrate.quad(integrand, active_cut, active_cut + 12.0, limit=200)
    return float(min(1.0, joint / active_fraction))


@dataclass(frozen=True, slots=True)
class Tier:
    """One stage in a planned funnel, with the population it sees and passes."""

    stage: Stage
    molecules_in: int
    molecules_out: int
    replicas: int
    gpu_hours: float
    #: Share of the pool's actives still present after this tier.
    retained_actives: float
    #: Active fraction of the population entering this tier. Rises down the funnel.
    active_fraction_in: float

    @property
    def keep_fraction(self) -> float:
        return 0.0 if not self.molecules_in else self.molecules_out / self.molecules_in

    @property
    def enrichment(self) -> float:
        """How much more concentrated the actives are leaving than entering."""

        if not self.keep_fraction:
            return 0.0
        return (self.retained_actives / self.keep_fraction) if self.keep_fraction else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage.id,
            "answers": self.stage.answers.value,
            "molecules_in": self.molecules_in,
            "molecules_out": self.molecules_out,
            "keep_fraction": round(self.keep_fraction, 6),
            "replicas": self.replicas,
            "gpu_hours": round(self.gpu_hours, 2),
            "gpu_days": round(self.gpu_hours / 24.0, 2),
            "active_fraction_in": round(self.active_fraction_in, 6),
            "cumulative_active_retention": round(self.retained_actives, 4),
        }


@dataclass(frozen=True, slots=True)
class Funnel:
    """A planned funnel, its price, and what it is expected to keep."""

    tiers: tuple[Tier, ...]
    refused: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    feasible: bool = True

    @property
    def gpu_hours(self) -> float:
        return sum(tier.gpu_hours for tier in self.tiers)

    @property
    def retention(self) -> float:
        """Expected share of the pool's actives reaching the end, *if the plan is affordable*.

        Read :attr:`feasible` first. An infeasible plan still has a retention figure, because the
        arithmetic does not know about money, and comparing one across plans without checking
        feasibility is how a funnel costing 291 GPU-years gets read as the better option. That
        happened here: a docking tier refused for being indistinguishable from random left the
        end-point method screening 255,330 molecules, and its retention came out *higher* than the
        affordable plan's. :attr:`achievable_retention` is the figure to compare across plans.
        """

        return self.tiers[-1].retained_actives if self.tiers else 0.0

    @property
    def achievable_retention(self) -> float | None:
        """:attr:`retention` when the plan can be paid for, and ``None`` when it cannot."""

        return self.retention if self.feasible else None

    @property
    def final_count(self) -> int:
        return self.tiers[-1].molecules_out if self.tiers else 0

    @property
    def final_active_fraction(self) -> float:
        """Expected share of the surviving molecules that are truly active.

        The number a campaign actually cares about at the end of a funnel: not how many actives
        survived but what proportion of what it is about to spend a chemist's attention on is real.
        """

        if not self.tiers:
            return 0.0
        first, last = self.tiers[0], self.tiers[-1]
        if not last.molecules_out:
            return 0.0
        actives_left = self.retention * first.active_fraction_in * first.molecules_in
        return min(1.0, actives_left / last.molecules_out)

    def as_dict(self) -> dict[str, object]:
        return {
            "feasible": self.feasible,
            "tiers": [tier.as_dict() for tier in self.tiers],
            "total_gpu_hours": round(self.gpu_hours, 2),
            "total_gpu_days": round(self.gpu_hours / 24.0, 2),
            "total_gpu_years": round(self.gpu_hours / 24.0 / 365.0, 3),
            "expected_active_retention": round(self.retention, 4),
            # None when the plan cannot be paid for, so a caller cannot compare it with an affordable
            # plan's by accident. The raw figure stays above for a reader who wants the arithmetic.
            "achievable_active_retention": (None if not self.feasible else round(self.retention, 4)),
            "molecules_at_the_end": self.final_count,
            "molecules_requested": None,
            "expected_actives_at_the_end": round(
                self.retention
                * (self.tiers[0].active_fraction_in * self.tiers[0].molecules_in if self.tiers else 0.0),
                2,
            ),
            "expected_active_fraction_at_the_end": round(self.final_active_fraction, 4),
            "refused_tiers": [{"stage": s, "why": w} for s, w in self.refused],
            "notes": list(self.notes),
        }


def _walk(
    stages: Sequence[Stage],
    keeps: Sequence[float],
    pool: int,
    active_fraction: float,
) -> tuple[list[Tier], float]:
    """Run a funnel with given keep fractions, returning its tiers and total cost."""

    tiers: list[Tier] = []
    population = float(pool)
    active = active_fraction
    cumulative = 1.0
    spent = 0.0
    index = 0
    for stage in stages:
        replicas = stage.replicas_for_a_measurement()
        cost = stage.gpu_hours * population * replicas
        spent += cost
        if stage.spearman is None:
            # A validity tier: it rejects decoys and actives at its own two rates, so it changes
            # both the population and the enrichment without being given a keep fraction.
            kept_actives = 1.0 - stage.active_rejection
            kept_decoys = 1.0 - stage.decoy_rejection
            keep = active * kept_actives + (1.0 - active) * kept_decoys
            passed = max(1.0, population * keep)
            retained = kept_actives
        else:
            keep = keeps[index]
            index += 1
            passed = max(1.0, population * keep)
            # A measured curve beats a correlation. The correlation route assumes a Gaussian copula
            # between score and truth, and measured on the real panel that assumption understated
            # docking's enrichment at the head of the list by a factor of five -- which is the only
            # part of the list a funnel uses.
            from_curve = stage.retained_at(keep)
            retained = (
                from_curve if from_curve is not None else retention(stage.spearman, keep, active)
            )
        cumulative *= retained
        tiers.append(
            Tier(
                stage=stage,
                molecules_in=int(round(population)),
                molecules_out=int(round(passed)),
                replicas=replicas,
                gpu_hours=cost,
                retained_actives=cumulative,
                active_fraction_in=active,
            )
        )
        active = min(1.0, active * retained / keep) if keep > 0 else active
        population = passed
    return tiers, spent


def plan(
    pool: int,
    stages: Sequence[Stage],
    *,
    budget_gpu_hours: float,
    active_fraction: float = 0.01,
    final_count: int = 10,
    resolvable_spearman: float = 0.0,
) -> Funnel:
    """Shape a funnel over ``stages``, optimising where to be permissive.

    The tier order is the caller's -- it is a scientific decision and this function does not
    reorder it. What is optimised is the keep fraction at each ranking tier, subject to reaching
    ``final_count`` and staying inside the budget.

    Args:
        resolvable_spearman: The smallest correlation difference the campaign's panel can
            resolve, typically twice the Hanley-McNeil standard error of an AUC on it. A tier
            whose correlation is not better than the best already reached *among tiers answering
            the same question* by more than this is refused: two tiers the panel cannot tell
            apart are one tier at two prices.
    """

    import numpy as np
    from scipy import optimize

    if pool < 1 or final_count < 1:
        raise ValueError("a funnel needs at least one molecule in and one out")
    if final_count > pool:
        raise ValueError(f"cannot select {final_count} from a pool of {pool}")
    if budget_gpu_hours <= 0:
        raise ValueError("a funnel needs a budget")

    notes: list[str] = [
        "Retention figures are upper bounds. Consecutive tiers' errors are modelled as "
        "independent and they are not -- docking and an end-point method share a pose, a "
        "receptor conformation and a force field -- so each tier appears to bring more fresh "
        "information than it does. Measure the tier-to-tier rank correlation on your own panel "
        "and treat the gap as the size of this optimism."
    ]
    refused: list[tuple[str, str]] = []

    # -- which tiers survive -------------------------------------------------
    best_by_question: dict[Answers, float] = {}
    usable: list[Stage] = []
    for stage in stages:
        if stage.answers is Answers.MECHANISM:
            refused.append(
                (
                    stage.id,
                    "answers mechanism rather than ranking. It may be the most valuable "
                    "calculation in the campaign and it cannot be a tier in a funnel that is "
                    "ordering molecules: a head-to-head on PARP1 found the physical route no "
                    "more accurate than the alchemical one, so its cost does not buy a better "
                    "rank. Spend it on the few molecules whose mechanism is the question.",
                )
            )
            continue
        ranks = stage.answers in (
            Answers.RANKING,
            Answers.ABSOLUTE_AFFINITY,
            Answers.RELATIVE_AFFINITY,
        )
        if ranks and stage.ranks_by_measurement:
            # Its selectivity was measured, so there is nothing to assume and nothing to refuse on the
            # grounds of an unmeasured correlation. A measured curve is also how a tier that looked
            # indistinguishable from random by correlation gets back into a plan: docking's measured
            # rank correlation of 0.108 was refused for being within the panel's resolution of zero,
            # while its measured enrichment of sub-10 nM compounds at the top 1% had a 95% interval of
            # 1.82 to 17.43 -- entirely above 1.
            usable.append(stage)
            continue
        if ranks and stage.spearman is None:
            # The worst of both otherwise: a ranking stage with no measured correlation was
            # walked as if it were a validity filter with nothing to reject, so it passed its
            # whole population on, cost its full price, and changed no retention figure. Boltz-2
            # sat in a plan at 52 GPU-hours doing exactly that.
            refused.append(
                (
                    stage.id,
                    "it ranks molecules and no rank correlation has been measured for it, so its "
                    "place in a funnel cannot be planned. Treating it as a filter that rejects "
                    "nothing would charge the campaign its full price for no modelled benefit, "
                    "and assuming a correlation would put a number nobody measured at the centre "
                    "of a budget decision. Measure it on your panel -- it is two columns and a "
                    "Spearman -- and set Stage.spearman.",
                )
            )
            continue
        if stage.spearman is not None:
            # Compared only against stages answering the same question. An FEP edge judged by
            # its rank correlation against an end-point method's is the category error this
            # project refuses everywhere else.
            best = best_by_question.get(stage.answers, 0.0)
            if stage.spearman <= best + resolvable_spearman:
                refused.append(
                    (
                        stage.id,
                        f"its rank correlation of {stage.spearman:.3f} is not distinguishably "
                        f"better than the {best:.3f} already reached among tiers answering "
                        f"'{stage.answers.value}', against a panel that resolves "
                        f"{resolvable_spearman:.3f}. Two tiers the panel cannot tell apart are "
                        "one tier at two prices, and the cheap one wins.",
                    )
                )
                continue
            best_by_question[stage.answers] = stage.spearman
        usable.append(stage)

    if not usable:
        return Funnel((), tuple(refused), (*notes, "No tier survived."), feasible=False)

    ranking = [stage for stage in usable if stage.spearman is not None]
    if not ranking:
        tiers, spent = _walk(usable, [], pool, active_fraction)
        notes.append(
            "No ranking tier survived, so nothing in this funnel orders molecules: it filters "
            "on validity alone and its output is unranked."
        )
        return Funnel(tuple(tiers), tuple(refused), tuple(notes), feasible=spent <= budget_gpu_hours)

    # -- optimise the keep fractions ----------------------------------------
    # Parameterised as log-keeps so the product constraint is linear and the bounds keep every
    # fraction in (0, 1]. The validity tiers' own reduction is absorbed into the target product,
    # because their keep is a property of the filter rather than a variable.
    validity_keep = 1.0
    probe_tiers, _ = _walk(usable, [1.0] * len(ranking), pool, active_fraction)
    for tier in probe_tiers:
        if tier.stage.spearman is None:
            validity_keep *= tier.keep_fraction
    target_product = max(final_count / (pool * validity_keep), 1e-12)
    log_target = math.log(min(target_product, 1.0))

    count = len(ranking)
    floor = math.log(1.0 / pool)
    # A tier whose selectivity was measured may only operate where it was measured. Without this the
    # optimiser drove a curve measured down to 1% to a keep fraction of 0.0001 and inherited the 1%
    # retention figure there -- an extrapolation of 125-fold, reported as an affordable plan with a
    # 100% hit rate and nothing in the output to mark it. The bound is per tier, so a tier with no
    # curve keeps the full range.
    bounds: list[tuple[float, float]] = []
    clamped: list[str] = []
    for stage in ranking:
        window = stage.measured_range
        if window is None:
            bounds.append((floor, 0.0))
            continue
        low, high = window
        bounds.append((math.log(low), math.log(min(high, 1.0))))
        clamped.append(f"{stage.id} to [{low:.3g}, {high:.3g}]")
    if clamped:
        notes.append(
            "Keep fractions constrained to the range each measured enrichment curve covers: "
            + "; ".join(clamped)
            + ". Outside it the curve has nothing to say, and the clamp an interpolator would apply "
            "is an extrapolation wearing a measurement's clothes -- it produced a plan 125 times "
            "outside the evidence that reported a 100% hit rate."
        )

    def objective(logs: np.ndarray) -> float:
        keeps = [float(math.exp(v)) for v in logs]
        tiers, _ = _walk(usable, keeps, pool, active_fraction)
        value = tiers[-1].retained_actives if tiers else 0.0
        return -math.log(max(value, 1e-18))

    def budget_slack(logs: np.ndarray) -> float:
        keeps = [float(math.exp(v)) for v in logs]
        _, spent = _walk(usable, keeps, pool, active_fraction)
        return budget_gpu_hours - spent

    start = np.full(count, log_target / count)
    result = optimize.minimize(
        objective,
        start,
        method="SLSQP",
        bounds=bounds,
        constraints=[
            {"type": "eq", "fun": lambda v: float(np.sum(v)) - log_target},
            {"type": "ineq", "fun": budget_slack},
        ],
        options={"maxiter": 120, "ftol": 1e-8},
    )

    keeps = [float(math.exp(v)) for v in result.x]
    tiers, spent = _walk(usable, keeps, pool, active_fraction)
    feasible = spent <= budget_gpu_hours * 1.001

    if not feasible:
        notes.append(
            f"No arrangement of these tiers reaches {final_count} molecules within "
            f"{budget_gpu_hours:,.0f} GPU-hours; the cheapest found costs {spent:,.0f}. The "
            "honest answers are a smaller pool, a shorter funnel, or more compute -- not a "
            "harsher cut, which this optimiser has already taken as far as it goes."
        )
    if not result.success:
        notes.append(
            f"The keep-fraction optimisation did not converge ({result.message}). The plan below "
            "is the best point it reached and may not be the best there is."
        )
    permissive = [t.stage.id for t in tiers if t.stage.spearman is not None and t.keep_fraction > 0.5]
    if permissive:
        notes.append(
            "Permissive early tiers are the optimiser's answer and not a bug: "
            + ", ".join(permissive)
            + " pass most of what they see, because a cheap tier that cuts hard throws away "
            "actives it cannot identify. At a rank correlation of 0.35, keeping the top 1% "
            f"retains {retention(0.35, 0.01, active_fraction):.0%} of the actives at this pool's "
            "active rate."
        )
    if tiers and tiers[-1].molecules_out != final_count:
        notes.append(
            f"The funnel delivers {tiers[-1].molecules_out} molecules rather than the {final_count} "
            "requested. Two plans' actives-at-the-end are only comparable at equal delivery; compare "
            "expected_active_fraction_at_the_end, which is a rate, or compare retention after checking "
            "both plans are feasible."
        )
    for stage in (s for s in usable if not s.reproducible_from_one_run):
        notes.append(
            f"{stage.id} is priced at {stage.replicas_for_a_measurement()} replicas because one "
            f"run of it is not a measurement: its run-to-run spread is "
            f"{stage.run_to_run_kcal_mol:g} kcal/mol, larger than the free energy of one log "
            "unit of affinity. A plan that budgets one run has budgeted a number that cannot be "
            "ranked on."
        )
    for stage in (s for s in usable if s.spearman is None and s.active_rejection > 0):
        notes.append(
            f"{stage.id} rejects {stage.active_rejection:.0%} of actives as the price of "
            f"rejecting {stage.decoy_rejection:.0%} of decoys. Both numbers are conventions in "
            "this catalogue; measure them on a panel with known answers before trusting the "
            "retention figure above."
        )
    return Funnel(tuple(tiers), tuple(refused), tuple(notes), feasible=feasible)


__all__ = ["Funnel", "Tier", "plan", "retention"]
