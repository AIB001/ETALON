"""The whole campaign as one object, and a dry run that costs nothing.

Every module in ETALON answers one question. This one puts them in the order a campaign actually
runs them and renders the result before anything is spent, because the most expensive mistake in a
structure-based campaign is not a wrong number -- it is a funnel whose shape nobody examined until
the GPU-months were gone.

The order is the operator's scientific decision and this module does not reorder it. What it does
is say, for a given pool, budget and panel:

- which tiers survive, and why each refused tier was refused;
- how wide each tier should be, from the keep-fraction optimisation;
- what the whole thing costs, in GPU-years;
- how many of the pool's true actives are expected to reach the end, as an upper bound;
- which of those numbers rest on a convention nobody has measured.

That last line is the one worth the module. A plan that does not say which of its inputs is a guess
will be trusted further than it should be, and four of the inputs here are guesses: docking's rank
correlation, MD stability's two rejection rates, and the conversion from simulation microseconds to
GPU-hours for umbrella sampling.

:meth:`Pipeline.dry_run` touches nothing. It is meant to be read, argued with, and run again with
the campaign's own numbers -- which is two columns and a Spearman for a correlation, and an
afternoon on a panel with known answers for the rejection rates.
"""

from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from etalon.economics.allocate import Funnel, plan
from etalon.economics.stage import BY_ID, Stage
from etalon.generate.audit import Audit, GeneratorYield
from etalon.generate.audit import audit as audit_generators

#: The funnel the operator described, in order: cheap liability filters, docking, a co-folding
#: affinity model, an end-point free energy, unbiased MD for pose stability, then alchemical FEP on
#: what is left. PMF is deliberately absent -- it answers mechanism, and ADR 0004 says why that
#: cannot be a tier in a funnel that is ranking.
DEFAULT_FUNNEL: tuple[str, ...] = (
    "lilly_demerits",
    "docking",
    "boltz2_affinity",
    "mmgbsa_ensemble",
    "md_stability",
    "fep_edge",
)


@dataclass(frozen=True, slots=True)
class Plan:
    """A campaign's shape, its price, and what in it is a guess."""

    funnel: Funnel
    pool: int
    budget_gpu_hours: float
    active_fraction: float
    generators: Audit | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def conventions(self) -> tuple[tuple[str, str], ...]:
        """Every tier in the plan whose numbers nobody measured, and what they are.

        Collected rather than left to a reader to notice. A plan reads as a calculation, and a
        calculation over four guesses reads exactly like one over four measurements.

        Asked per quantity rather than per stage. A tier whose enrichment curve was measured on the
        real panel and whose cost is still a conversion from a published simulation length is not
        "measured" and not "a convention"; reading the stage's single label reported it as whichever
        of those was substituted last, and the half a plan actually turns on could be the other one.
        """

        out: list[tuple[str, str]] = []
        for tier in self.funnel.tiers:
            stage = tier.stage
            unmeasured = stage.unmeasured_quantities()
            if not unmeasured:
                continue
            named = ", ".join(f"{name} ({found.value})" for name, found in unmeasured)
            out.append((stage.id, f"[{named}] {stage.source}"))
        return tuple(out)

    @property
    def contradictions(self) -> tuple[tuple[str, str], ...]:
        """Tiers carrying a number this project measured and found wrong.

        Separate from :attr:`conventions` because they are different claims and the weaker one was
        swallowing the stronger. "Nobody measured this" invites a reader to supply their own number;
        "we measured this and got a third of it" says the plan on the page is wrong by a factor
        somebody here has already seen.
        """

        return tuple(
            (tier.stage.id, tier.stage.contradicted_by)
            for tier in self.funnel.tiers
            if tier.stage.contradicted_by
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "pool": self.pool,
            "budget_gpu_hours": self.budget_gpu_hours,
            "budget_gpu_years": round(self.budget_gpu_hours / 24 / 365, 3),
            "active_fraction": self.active_fraction,
            "true_actives_in_pool": int(self.pool * self.active_fraction),
            "funnel": self.funnel.as_dict(),
            "generators": None if self.generators is None else self.generators.as_dict(),
            "unmeasured_inputs": [{"stage": s, "source": src} for s, src in self.conventions],
            "contradicted_inputs": [
                {"stage": s, "measurement": detail} for s, detail in self.contradictions
            ],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        return "\n".join(_render(self))


def _wrap(text: str, *, width: int) -> list[str]:
    return textwrap.wrap(text, width=width) or [""]


def _render(plan_: Plan) -> list[str]:
    funnel = plan_.funnel
    actives = plan_.pool * plan_.active_fraction
    lines = [
        f"Pool {plan_.pool:,} molecules, {plan_.active_fraction:.2%} active "
        f"({int(actives):,} true actives), budget "
        f"{plan_.budget_gpu_hours:,.0f} GPU-hours ({plan_.budget_gpu_hours / 24 / 365:.2f} GPU-years)",
        "",
        f"  {'tier':<20}{'in':>11}{'out':>9}{'keep':>10}{'rep':>5}{'GPU-hours':>12}{'actives left':>14}",
    ]
    for tier in funnel.tiers:
        lines.append(
            f"  {tier.stage.id:<20}{tier.molecules_in:>11,}{tier.molecules_out:>9,}"
            f"{tier.keep_fraction:>10.4f}{tier.replicas:>5}{tier.gpu_hours:>12,.0f}"
            f"{tier.retained_actives * actives:>14.1f}"
        )
    lines.append(
        f"  {'':<20}{'':>11}{funnel.final_count:>9,}{'':>10}{'':>5}{funnel.gpu_hours:>12,.0f}"
        f"{funnel.retention * actives:>14.1f}"
    )
    if not funnel.feasible:
        lines.append("")
        lines.append("  NOT AFFORDABLE at this budget.")
    if funnel.refused:
        lines.append("")
        lines.append("  refused:")
        for stage, why in funnel.refused:
            lines.append(f"    {stage}")
            lines.append(f"      {why}")
    conventions = plan_.conventions
    if conventions:
        lines.append("")
        lines.append(
            f"  {len(conventions)} tier(s) in this plan rest on numbers nobody measured. A plan "
            "over guesses reads exactly like a plan over measurements:"
        )
        for stage, source in conventions:
            lines.append(f"    {stage}: {source[:150]}")
    contradicted = plan_.contradictions
    if contradicted:
        # Printed in full and never truncated. This is the project's own measurement disagreeing
        # with the number the plan above was computed from, and a reader who sees 150 characters of
        # it has seen the part that sounds like a caveat rather than the part that is a result.
        lines.append("")
        lines.append(
            f"  {len(contradicted)} tier(s) carry a number this project has measured and found "
            "wrong. The plan above was computed with the catalogue value:"
        )
        for stage, detail in contradicted:
            lines.append(f"    {stage}:")
            for chunk in _wrap(detail, width=92):
                lines.append(f"      {chunk}")
    for note in funnel.notes:
        lines.append("")
        lines.append(f"  note: {note}")
    for note in plan_.notes:
        lines.append("")
        lines.append(f"  note: {note}")
    return lines


@dataclass
class Pipeline:
    """A campaign's intended shape, renderable before anything is spent."""

    pool: int
    budget_gpu_hours: float
    active_fraction: float = 0.001
    final_count: int = 10
    #: The smallest rank-correlation difference the campaign's panel can resolve. Twice the
    #: Hanley-McNeil standard error of an AUC on it is the figure to use; on a 231-molecule panel
    #: with 40 actives that is about 0.10. Left at zero it admits every tier, which is only honest
    #: when the correlations are the campaign's own measurements.
    resolvable_spearman: float = 0.10
    stages: Sequence[str] = DEFAULT_FUNNEL

    def funnel_stages(self, overrides: Mapping[str, Stage] | None = None) -> list[Stage]:
        """The stage objects, with any the campaign has measured for itself substituted in."""

        chosen = dict(overrides or {})
        out: list[Stage] = []
        for name in self.stages:
            if name in chosen:
                out.append(chosen[name])
                continue
            if name not in BY_ID:
                raise KeyError(
                    f"no stage {name!r} in the catalogue; it holds {sorted(BY_ID)}. A campaign "
                    "with a tier of its own should build a Stage for it and pass it as an "
                    "override, so its cost and correlation are recorded beside everything else's."
                )
            out.append(BY_ID[name])
        return out

    def dry_run(
        self,
        *,
        overrides: Mapping[str, Stage] | None = None,
        generators: Sequence[GeneratorYield] = (),
        generator_stages: Sequence[str] = (),
    ) -> Plan:
        """Plan the campaign without running anything.

        Args:
            overrides: Stages the campaign has measured for itself, by id. This is the intended way
                to use the module: every published number in the catalogue is a placeholder for one
                of these.
            generators: Per-model counts, if a previous round produced them. Supplying them adds
                the generation audit, which says at which stage the models can still be told apart.
        """

        stages = self.funnel_stages(overrides)
        funnel = plan(
            self.pool,
            stages,
            budget_gpu_hours=self.budget_gpu_hours,
            active_fraction=self.active_fraction,
            final_count=self.final_count,
            resolvable_spearman=self.resolvable_spearman,
        )
        notes: list[str] = []
        if overrides:
            notes.append(
                "Measured overrides in use for: " + ", ".join(sorted(overrides)) + "."
            )
        else:
            notes.append(
                "No measured overrides: every correlation and rejection rate in this plan is the "
                "catalogue's placeholder. Replacing docking's is two columns and a Spearman on "
                "your own panel, and it is the single input this plan is most sensitive to -- "
                "findings/0004 shows 0.15 of rank correlation there outweighing ten times the "
                "compute."
            )

        report: Audit | None = None
        if generators:
            report = audit_generators(
                list(generators), list(generator_stages) or _stages_of(generators)
            )
            deepest = report.deepest_decidable_stage()
            notes.append(
                f"Generation feedback: reallocate on {deepest}."
                if deepest
                else "Generation feedback: no stage in this funnel can tell these models apart, so "
                "this round has not learned which to favour."
            )
        return Plan(
            funnel=funnel,
            pool=self.pool,
            budget_gpu_hours=self.budget_gpu_hours,
            active_fraction=self.active_fraction,
            generators=report,
            notes=tuple(notes),
        )

    def expensive_budget(self, plan_: Plan, stage_id: str = "mmgbsa_ensemble") -> int:
        """How many molecules the plan gives to one tier, for an acquisition budget.

        The link between the economics and the learning: :class:`etalon.campaign.Acquisition` takes
        a budget in molecules, and this is where that number should come from rather than from a
        round figure somebody liked.
        """

        for tier in plan_.funnel.tiers:
            if tier.stage.id == stage_id:
                return tier.molecules_in
        raise KeyError(
            f"{stage_id} is not in this plan -- it was refused or never included, so it has no "
            f"budget. Refusals: {[s for s, _ in plan_.funnel.refused]}"
        )


def _stages_of(generators: Sequence[GeneratorYield]) -> list[str]:
    """The stages every generator was counted at, in the first one's order."""

    if not generators:
        return []
    return [
        stage
        for stage in generators[0].survivors
        if all(stage in other.survivors for other in generators)
    ]


__all__ = ["DEFAULT_FUNNEL", "Pipeline", "Plan"]
