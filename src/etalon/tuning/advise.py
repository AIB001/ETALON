"""Rank the knobs by what they do to the funnel, and refuse the ones not worth turning.

``findings/0004`` says improving the cheapest ranking tier's rank correlation by 0.15 outweighs ten
times the compute. :mod:`etalon.tuning.knob` says what is published to achieve that. This module
joins the two: it re-plans the whole funnel with each knob applied and reports the change in
expected actives delivered, so a campaign compares a week of engineering against a GPU-year in the
same units.

Two refusals, and the second is the one that saves a campaign from a published failure mode.

**A knob whose effect is smaller than the panel can resolve is refused.** Not because the effect is
not real -- a 1.07-fold EF1% improvement from sizing the docking box is a measured result -- but
because the campaign will not be able to tell whether it worked, and a change that cannot be
verified accumulates into a configuration nobody can account for. A cheap unverifiable knob is still
worth turning once on the strength of its publication; a campaign should simply not expect to
observe it, and ``Advice.below_resolution`` says which those are.

**A correlation is the wrong thing to rank knobs by, and this module did it.** Measured on the real
panel, a change that raised rank correlation from 0.121 to 0.352 destroyed enrichment of sub-10 nM
compounds at the top 1% from 9.58 to zero: a model fitted with squared error shrinks toward the mean and
never ranks an extreme first, so it orders the bulk well and puts nothing potent at the head. A funnel
uses only the head, so that change makes the first tier worse while every figure here says it improved.
So a knob acting on a tier whose head enrichment has been measured is not ranked until the knob's own
effect on enrichment is measured too.

**A negative gain is a finding rather than a verdict on the knob.** Improving a cheap tier can make an
expensive one statistically redundant: measured here, docking at 0.637 put MM-GBSA's 0.767 inside the
panel's 0.133 resolution, the planner dropped it as one tier at two prices, and the funnel lost its most
selective stage for a net loss of 0.003 retention. The refusal is defensible -- a tier you cannot justify
is a tier you cannot justify -- and the loss may be an artefact of panel size, since 0.767 probably is
better than 0.637 in truth. A bare "-2.4 actives" says none of that, so the explanation is carried.

**A knob whose precondition is not established is refused with the check named.** Consensus scoring
improves enrichment only if each member performs relatively well individually and the members are
appropriately diverse. That is the published condition, and the GPCR-Bench result is what it looks
like when the condition fails: MM/GBSA-containing combinations improved only 32% and 19% of all
combinations at EF1% and EF5%. On kinases, where it holds, Top-1% EF went from 6.4 to 23.5. An agent
that turns the knob because it is available has bet a campaign on which of those two it is in.

One conversion is done here and it is the least safe thing in the module. Several knobs are published
as EF1% multipliers rather than as rank correlations, and the two are not interchangeable without
knowing the active fraction. :func:`rho_for_multiplier` inverts the selection model numerically to
find the correlation that would produce the published enrichment multiple at this pool's active rate.
It makes consensus scoring comparable with rescoring, which is the comparison a campaign most needs,
and it inherits every assumption of the retention model -- Gaussian utility, Gaussian noise, and a
rank correlation standing in for a latent Pearson one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from etalon.economics.allocate import Funnel, plan, retention
from etalon.economics.stage import Stage
from etalon.tuning.knob import KNOBS, Knob, Lever

#: Which stage each lever acts on, by default. A campaign whose funnel names its tiers differently
#: passes its own mapping; getting this wrong would credit a scoring improvement to a tier that does
#: no scoring.
LEVER_TARGETS: dict[Lever, str] = {
    Lever.SCORING: "docking",
    Lever.SEARCH_SPACE: "docking",
    Lever.SAMPLING: "docking",
    Lever.FILTERS: "lilly_demerits",
    Lever.LIBRARY: "docking",
}


def rho_for_multiplier(
    base_rho: float,
    multiplier: float,
    *,
    keep_fraction: float = 0.01,
    active_fraction: float = 0.001,
) -> float:
    """The correlation that would multiply enrichment at ``keep_fraction`` by ``multiplier``.

    Enrichment at a cut is retention divided by the keep fraction, so multiplying enrichment means
    multiplying retention. This inverts :func:`etalon.economics.allocate.retention` for the
    correlation that does it.

    Returns ``1.0`` when the requested multiple is unreachable -- a multiplier large enough to demand
    retention above one is asking for more actives than the pool contains, and reporting a
    correlation above one would be worse than saturating.
    """

    from scipy import optimize

    if multiplier <= 1.0:
        return base_rho
    target = retention(base_rho, keep_fraction, active_fraction) * multiplier
    ceiling = retention(1.0, keep_fraction, active_fraction)
    if target >= ceiling:
        return 1.0

    def gap(rho: float) -> float:
        return retention(rho, keep_fraction, active_fraction) - target

    try:
        return float(optimize.brentq(gap, base_rho, 1.0, xtol=1e-4))
    except ValueError:
        return 1.0


@dataclass(frozen=True, slots=True)
class Option:
    """One knob, costed against the funnel it would change."""

    knob: Knob
    #: The stage the knob was applied to.
    target: str
    #: Correlation after the knob, however it was arrived at.
    rho_after: float
    rho_before: float
    #: Expected true actives delivered, before and after.
    actives_before: float
    actives_after: float
    added_gpu_hours: float
    #: Whether the correlation change came from a published EF multiplier rather than a published
    #: delta-Spearman. Flagged because the conversion is the least safe step in the module.
    converted: bool = False

    @property
    def gain(self) -> float:
        return self.actives_after - self.actives_before

    @property
    def actives_per_setup_hour(self) -> float:
        return self.gain / self.knob.setup_hours if self.knob.setup_hours else float("inf")

    def as_dict(self) -> dict[str, object]:
        return {
            "knob": self.knob.id,
            "lever": self.knob.lever.value,
            "target_stage": self.target,
            "rho_before": round(self.rho_before, 4),
            "rho_after": round(self.rho_after, 4),
            "rho_from_converted_ef_multiplier": self.converted,
            "actives_before": round(self.actives_before, 2),
            "actives_after": round(self.actives_after, 2),
            "gain_in_actives": round(self.gain, 2),
            "added_gpu_hours_total": round(self.added_gpu_hours, 1),
            "setup_hours": self.knob.setup_hours,
            "actives_per_setup_hour": round(self.actives_per_setup_hour, 3),
            "precondition": self.knob.precondition,
            "how_to_check": self.knob.how_to_check,
        }


@dataclass(frozen=True, slots=True)
class Advice:
    """Every knob, ranked, with the ones not worth turning separated out."""

    worth_turning: tuple[Option, ...]
    below_resolution: tuple[tuple[str, str], ...] = ()
    precondition_unmet: tuple[tuple[str, str], ...] = ()
    not_modelled: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "worth_turning": [option.as_dict() for option in self.worth_turning],
            "below_resolution": [{"knob": k, "why": w} for k, w in self.below_resolution],
            "precondition_unmet": [{"knob": k, "check": w} for k, w in self.precondition_unmet],
            "not_modelled": [{"knob": k, "why": w} for k, w in self.not_modelled],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        lines: list[str] = []
        if self.worth_turning:
            lines.append(
                f"  {'knob':<24}{'rho':>14}{'actives':>16}{'gain':>8}{'+GPU-h':>10}{'hours':>7}"
            )
            for option in self.worth_turning:
                lines.append(
                    f"  {option.knob.id:<24}"
                    f"{option.rho_before:.2f} -> {option.rho_after:.2f}{'*' if option.converted else ' '}"
                    f"{option.actives_before:>8.1f} -> {option.actives_after:<6.1f}"
                    f"{option.gain:>+8.1f}{option.added_gpu_hours:>10,.0f}{option.knob.setup_hours:>7.0f}"
                )
            if any(option.converted for option in self.worth_turning):
                lines.append(
                    "  * correlation derived from a published EF1% multiplier, not from a published "
                    "rank correlation"
                )
        for label, entries in (
            ("not worth turning -- the panel cannot resolve the effect", self.below_resolution),
            ("precondition not established", self.precondition_unmet),
            ("no effect size to model", self.not_modelled),
        ):
            if entries:
                lines.append("")
                lines.append(f"  {label}:")
                for knob, why in entries:
                    lines.append(f"    {knob}: {why[:160]}")
        for note in self.notes:
            lines.append("")
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def advise(
    pool: int,
    stages: Sequence[Stage],
    *,
    budget_gpu_hours: float,
    active_fraction: float = 0.001,
    final_count: int = 10,
    resolvable_spearman: float = 0.10,
    established: Iterable[str] = (),
    knobs: Sequence[Knob] = KNOBS,
    targets: Mapping[Lever, str] | None = None,
) -> Advice:
    """Rank the knobs by the actives they would add to this funnel.

    Args:
        established: Knob ids whose precondition the campaign has checked. A knob with a
            precondition and no entry here is refused with the check named, because the published
            effect does not transfer without it.
    """

    mapping = dict(targets or LEVER_TARGETS)
    known = set(established)
    baseline = plan(
        pool,
        stages,
        budget_gpu_hours=budget_gpu_hours,
        active_fraction=active_fraction,
        final_count=final_count,
        resolvable_spearman=resolvable_spearman,
    )
    actives = pool * active_fraction
    before = baseline.retention * actives
    if not baseline.feasible:
        # Every gain below is measured against this baseline, so an unaffordable one makes all of them
        # meaningless -- and when it happened the gains came out *negative*: a docking tier refused for
        # being indistinguishable from random left the end-point method screening 255,330 molecules at
        # 291 GPU-years, and that plan's retention was higher than anything a knob could reach
        # affordably. A ranking of negative improvements is worse than no ranking.
        return Advice(
            (),
            (),
            (),
            (),
            (
                "The baseline funnel is not affordable at this budget, so there is nothing to improve "
                "on: every gain would be measured against a plan that cannot be run. Read "
                "etalon_plan_campaign's refusals first -- a tier refused for being indistinguishable "
                "from random leaves the next tier screening the whole library, which is what turns an "
                "expensive plan into an impossible one.",
            ),
        )

    worth: list[Option] = []
    below: list[tuple[str, str]] = []
    unmet: list[tuple[str, str]] = []
    unmodelled: list[tuple[str, str]] = []
    notes: list[str] = []

    by_id = {stage.id: stage for stage in stages}
    for knob in knobs:
        target = mapping.get(knob.lever, "")
        if target not in by_id:
            unmodelled.append(
                (
                    knob.id,
                    f"it acts on {target or 'an unmapped stage'}, which is not in this funnel, so "
                    "its effect cannot be placed.",
                )
            )
            continue
        stage = by_id[target]
        if stage.spearman is None:
            unmodelled.append(
                (
                    knob.id,
                    f"{target} has no measured rank correlation, so there is no baseline to improve "
                    "on. Measure the tier before tuning it.",
                )
            )
            continue

        converted = False
        if knob.delta_spearman is not None:
            after = min(1.0, stage.spearman + knob.delta_spearman)
        elif knob.ef1_multiplier is not None:
            after = rho_for_multiplier(
                stage.spearman, knob.ef1_multiplier, active_fraction=active_fraction
            )
            converted = True
        else:
            unmodelled.append(
                (
                    knob.id,
                    "no published effect size in either unit, so its value cannot be compared with "
                    f"anything. {knob.source[:110]}",
                )
            )
            continue

        if stage.ranks_by_measurement:
            # The tier's head enrichment was measured and this knob's effect on it was not. Measured: a
            # knob worth +0.23 of rank correlation took enrichment at the top 1% from 9.58 to zero, and
            # every figure in this module would have called that an improvement.
            unmodelled.append(
                (
                    knob.id,
                    f"{target}'s enrichment at the head of its ranking is measured and this knob's "
                    "effect on that is not. A correlation describes the whole list; a funnel uses its "
                    "head. Measured on a real panel, a change worth +0.23 of correlation took "
                    "enrichment of the most potent compounds at the top 1% from 9.58 to zero, because a "
                    "model fitted with squared error shrinks toward the mean and never ranks an extreme "
                    "first. Measure this knob with etalon.economics.measure.measure_enrichment on the "
                    "same panel before acting on it.",
                )
            )
            continue

        if after - stage.spearman < resolvable_spearman:
            below.append(
                (
                    knob.id,
                    f"it moves {target}'s correlation by {after - stage.spearman:+.3f} against a "
                    f"panel that resolves {resolvable_spearman:.3f}. Turn it once on the strength "
                    "of its publication if it is cheap, and do not expect to observe it working.",
                )
            )
            continue
        if knob.precondition and knob.id not in known:
            unmet.append((knob.id, f"{knob.precondition} -- {knob.how_to_check}"))
            continue

        tuned = plan(
            pool,
            [
                replace(s, spearman=after, gpu_hours=s.gpu_hours + knob.added_gpu_hours)
                if s.id == target
                else s
                for s in stages
            ],
            budget_gpu_hours=budget_gpu_hours,
            active_fraction=active_fraction,
            final_count=final_count,
            resolvable_spearman=resolvable_spearman,
        )
        worth.append(
            Option(
                knob=knob,
                target=target,
                rho_before=stage.spearman,
                rho_after=after,
                actives_before=before,
                actives_after=tuned.retention * actives,
                added_gpu_hours=_added(baseline, tuned),
                converted=converted,
            )
        )

    # A knob whose modelled gain is negative is separated rather than ranked last, because the usual
    # cause is worth reading: improving a cheap tier can put an expensive one inside the panel's
    # resolution, and the planner then drops the expensive tier as one tier at two prices, losing real
    # selectivity it could not prove. Showing that as "-2.4 actives" invites the wrong conclusion.
    backfires = [option for option in worth if option.gain <= 0]
    worth = [option for option in worth if option.gain > 0]
    for option in backfires:
        crowded = sorted(
            stage.id
            for stage in stages
            if stage.spearman is not None
            and stage.id != option.target
            and abs(stage.spearman - option.rho_after) < resolvable_spearman
        )
        unmodelled.append(
            (
                option.knob.id,
                f"its modelled effect is {option.gain:+.1f} actives, which is not an argument against "
                f"the knob. Raising {option.target} to {option.rho_after:.3f} brings it within this "
                f"panel's {resolvable_spearman:.3f} resolution of "
                + (", ".join(crowded) if crowded else "a downstream tier")
                + ", so the planner drops that tier as one tier at two prices and the funnel loses its "
                "selectivity. The refusal is defensible and the loss may be an artefact of panel size: "
                "the dropped tier is probably better in truth and this panel cannot prove it. Enlarge "
                "the panel, or keep both tiers deliberately.",
            )
        )
    worth.sort(key=lambda option: (-option.gain, option.knob.setup_hours))
    if worth:
        best = worth[0]
        notes.append(
            f"{best.knob.id} is worth {best.gain:+.1f} actives for {best.knob.setup_hours:g} hours "
            f"of setup. For comparison, findings/0004 measured ten times the compute budget as "
            "worth about +3.7 on this funnel, so an afternoon of engineering and a GPU-year are the "
            "same order of magnitude here -- which is the reason this module exists."
        )
    if unmet:
        notes.append(
            f"{len(unmet)} knob(s) have unestablished preconditions. Consensus scoring is the one "
            "to read carefully: it improves enrichment only where each member is individually good "
            "and the members are diverse, and across GPCR-Bench -- where that failed -- "
            "MM/GBSA-containing combinations improved only 32% and 19% of combinations at EF1% and "
            "EF5%. On kinases, where it held, Top-1% EF went from 6.4 to 23.5."
        )
    notes.append(
        "Every gain here inherits the retention model's assumptions, including that consecutive "
        "tiers' errors are independent. A rescorer applied to docking's own poses is the least "
        "independent improvement imaginable, so its modelled gain is the most optimistic number in "
        "this table."
    )
    return Advice(tuple(worth), tuple(below), tuple(unmet), tuple(unmodelled), tuple(notes))


def _added(baseline: Funnel, tuned: Funnel) -> float:
    return tuned.gpu_hours - baseline.gpu_hours


__all__ = ["Advice", "LEVER_TARGETS", "Option", "advise", "rho_for_multiplier"]
