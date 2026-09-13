"""Whether a tier is worth its place, and the five ways the first planner got it wrong.

Most of this file is regression pressure on bugs found by running the planner rather than by
reading it, because every one of them produced a plan that looked entirely reasonable. A funnel
plan is a budget decision with no natural sanity check: nobody looks at "keep the top 0.3%" and
feels that something is off.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.economics.allocate import plan, retention
from etalon.economics.stage import BY_ID, STAGES, Answers, needs_an_ensemble

FUNNEL = [BY_ID[i] for i in ("lilly_demerits", "docking", "mmgbsa_ensemble", "md_stability", "fep_edge")]


# -- the selection model ---------------------------------------------------


def test_retention_matches_its_own_limits() -> None:
    """A worthless score keeps actives at the rate it keeps anything; a perfect one keeps all."""

    assert retention(0.0, 0.01, 0.01) == pytest.approx(0.01, abs=1e-6)
    assert retention(1.0, 0.01, 0.01) == pytest.approx(1.0)
    assert retention(1.0, 0.001, 0.01) == pytest.approx(0.1)
    assert retention(0.5, 1.0, 0.01) == 1.0


def test_the_number_that_justifies_permissive_early_tiers() -> None:
    """At docking's correlation, a hundred-fold cut discards most of the actives.

    This single figure is why the optimiser makes cheap tiers permissive, and why improving a
    cheap tier beats buying compute.
    """

    # The figure depends on the pool's active rate as well as the stage's correlation, and an
    # earlier draft of the ADR quoted the 1% number against a 0.1% setup.
    assert retention(0.35, 0.01, 0.01) == pytest.approx(0.070, abs=0.005)
    assert retention(0.35, 0.01, 0.001) == pytest.approx(0.111, abs=0.005)
    assert retention(0.767, 0.01, 0.01) == pytest.approx(0.336, abs=0.005)
    assert retention(0.854, 0.01, 0.01) == pytest.approx(0.456, abs=0.005)


def test_retention_rises_with_correlation_and_with_permissiveness() -> None:
    assert retention(0.2, 0.01, 0.01) < retention(0.6, 0.01, 0.01)
    assert retention(0.6, 0.001, 0.01) < retention(0.6, 0.1, 0.01)


# -- the bug that reversed the planner -------------------------------------


def test_more_budget_never_retains_fewer_actives() -> None:
    """The first planner's retention fell from 0.055 to 0.024 as the budget rose ten-fold.

    It spread the reduction geometrically across tiers, so a larger budget fed more molecules into
    the most expensive tier, which then cut harder to reach the same final count. Spreading a
    reduction is not allocating a budget, and nothing about the resulting plan looked wrong.
    """

    small = plan(750_000, FUNNEL, budget_gpu_hours=8_760.0, active_fraction=0.001, final_count=10)
    large = plan(750_000, FUNNEL, budget_gpu_hours=87_600.0, active_fraction=0.001, final_count=10)

    assert large.retention >= small.retention


def test_the_expensive_tiers_price_sets_how_hard_the_cheap_one_cuts() -> None:
    """The claim "permissive early tiers" needed its budget condition, and this is it.

    At a GPU-year an end-point method at 10 GPU-hours a molecule sees about 870 molecules, so
    docking must cut 255,000 down to 870 whatever anyone would prefer. Give the optimiser more and
    it does prefer a permissive cheap tier -- monotonically so.
    """

    two = [BY_ID["docking"], BY_ID["mmgbsa_ensemble"]]
    keeps = [
        next(
            tier.keep_fraction
            for tier in plan(
                750_000, two, budget_gpu_hours=budget, active_fraction=0.001, final_count=10
            ).tiers
            if tier.stage.id == "docking"
        )
        for budget in (8_760.0, 87_600.0, 876_000.0)
    ]

    assert keeps == sorted(keeps), keeps
    assert keeps[-1] > 10 * keeps[0]


def test_the_active_fraction_rises_down_the_funnel() -> None:
    """Enrichment, which a model holding it constant understates after the first tier."""

    funnel = plan(750_000, FUNNEL, budget_gpu_hours=8_760.0, active_fraction=0.001, final_count=10)
    fractions = [tier.active_fraction_in for tier in funnel.tiers]

    assert fractions == sorted(fractions)
    assert fractions[-1] > fractions[0]


# -- the three refusals ----------------------------------------------------


def test_a_mechanism_stage_is_not_a_tier() -> None:
    """PMF costs ten FEP edges and a PARP1 head-to-head found it no more accurate."""

    funnel = plan(
        10_000,
        [*FUNNEL, BY_ID["pmf_umbrella"]],
        budget_gpu_hours=50_000.0,
        active_fraction=0.01,
        final_count=5,
    )

    refused = dict(funnel.refused)
    assert "pmf_umbrella" in refused
    assert "mechanism rather than ranking" in refused["pmf_umbrella"]
    assert all(tier.stage.answers is not Answers.MECHANISM for tier in funnel.tiers)


def test_a_ranking_stage_with_no_measured_correlation_is_refused_not_charged_for() -> None:
    """Boltz-2 sat in the first plan at 52 GPU-hours, passing everything and changing nothing."""

    funnel = plan(
        10_000,
        [BY_ID["docking"], BY_ID["boltz2_affinity"], BY_ID["mmgbsa_ensemble"]],
        budget_gpu_hours=50_000.0,
        active_fraction=0.01,
        final_count=5,
    )

    refused = dict(funnel.refused)
    assert "boltz2_affinity" in refused
    assert "no rank correlation has been measured" in refused["boltz2_affinity"]
    assert all(tier.stage.id != "boltz2_affinity" for tier in funnel.tiers)


def test_an_indistinguishable_tier_is_one_tier_at_two_prices() -> None:
    cheap = replace(BY_ID["docking"], spearman=0.70)
    dear = replace(BY_ID["mmgbsa_ensemble"], spearman=0.74)

    funnel = plan(
        10_000, [cheap, dear], budget_gpu_hours=100_000.0, active_fraction=0.01, final_count=5,
        resolvable_spearman=0.10,
    )

    refused = dict(funnel.refused)
    assert dear.id in refused
    assert "one tier at two prices" in refused[dear.id]


def test_tiers_are_compared_only_against_the_same_question() -> None:
    """FEP was refused for being 0.087 above MM-GBSA on a rank correlation.

    That compares a relative free energy within a congeneric series against a rank order over a
    diverse library, which is the category error this project refuses everywhere else.
    """

    funnel = plan(
        10_000,
        [BY_ID["docking"], BY_ID["mmgbsa_ensemble"], BY_ID["fep_edge"]],
        budget_gpu_hours=200_000.0,
        active_fraction=0.01,
        final_count=5,
        resolvable_spearman=0.10,
    )

    assert "fep_edge" not in dict(funnel.refused)
    assert any(tier.stage.id == "fep_edge" for tier in funnel.tiers)


# -- pricing ---------------------------------------------------------------


def test_a_stage_whose_single_run_is_not_a_measurement_is_priced_as_an_ensemble() -> None:
    """Up to 12 kcal/mol between runs from the same structures, on HIV-1 protease."""

    single = BY_ID["mmgbsa_single"]

    assert single.reproducible_from_one_run is False
    assert single.replicas_for_a_measurement() == 5
    # The honest price is five times the advertised one.
    assert single.cost_for(100, replicas=5) == pytest.approx(5 * single.cost_for(100))
    assert single in needs_an_ensemble()


def test_the_reproducibility_threshold_is_one_log_unit_of_affinity() -> None:
    """RT*ln(10) at 310 K. Above it, two molecules a factor of ten apart can swap between runs."""

    borderline = replace(BY_ID["mmgbsa_single"], run_to_run_kcal_mol=1.4)
    over = replace(BY_ID["mmgbsa_single"], run_to_run_kcal_mol=1.5)

    assert borderline.reproducible_from_one_run is True
    assert over.reproducible_from_one_run is False


def test_a_validity_tier_rejects_more_decoys_than_actives() -> None:
    """Modelled as passing everything, MD stability cost GPU-days and did nothing."""

    for stage in STAGES:
        if stage.spearman is not None:
            continue
        if stage.decoy_rejection or stage.active_rejection:
            assert stage.decoy_rejection > stage.active_rejection, stage.id


def test_every_number_in_the_catalogue_says_where_it_came_from() -> None:
    """The rule the fault taxonomy earned, applied here: a convention may not look measured."""

    import re

    for stage in STAGES:
        assert stage.source.strip(), stage.id
        lowered = stage.source.lower()
        if stage.evidence.value == "convention":
            assert "no measurement" in lowered or "chosen" in lowered or "guess" in lowered, stage.id
        if stage.evidence.value == "literature":
            assert re.search(r"\b(19|20)\d{2}\b", stage.source), stage.id
        if stage.evidence.value == "inferred":
            assert "inference" in lowered or "conversion" in lowered or "arithmetic" in lowered, stage.id


def test_a_stage_that_does_not_rank_has_no_correlation() -> None:
    """Giving a validity filter one would invite it to be compared with stages that do."""

    for stage in STAGES:
        if stage.answers in (Answers.VALIDITY, Answers.MECHANISM):
            assert stage.spearman is None, stage.id


# -- the planner's own limits ----------------------------------------------


def test_an_unaffordable_funnel_says_so_rather_than_cutting_harder() -> None:
    funnel = plan(
        1_000_000,
        [BY_ID["docking"], BY_ID["mmgbsa_ensemble"], BY_ID["fep_edge"]],
        budget_gpu_hours=10.0,
        active_fraction=0.001,
        final_count=10,
    )

    assert funnel.feasible is False
    assert any("not a harsher cut" in note or "honest answers" in note for note in funnel.notes)


def test_the_independence_assumption_is_always_declared() -> None:
    """Every retention figure is an upper bound, and a reader must be told once per plan."""

    funnel = plan(1000, FUNNEL, budget_gpu_hours=100_000.0, active_fraction=0.01, final_count=5)

    assert any("upper bounds" in note for note in funnel.notes)
    assert any("share a pose" in note for note in funnel.notes)


def test_an_impossible_request_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot select"):
        plan(10, FUNNEL, budget_gpu_hours=100.0, final_count=100)
    with pytest.raises(ValueError, match="needs a budget"):
        plan(100, FUNNEL, budget_gpu_hours=0.0)


# -- edge cases found by auditing rather than by using ---------------------


def test_a_pool_of_all_actives_retains_the_keep_fraction() -> None:
    """It returned zero, and a reader would have concluded the funnel was broken.

    norm.isf(1.0) is negative infinity, so the integration interval was degenerate. With every
    molecule active, retaining a fraction of the molecules retains that fraction of the actives
    whatever the score is worth.
    """

    for rho in (0.0, 0.5, 1.0):
        assert retention(rho, 0.1, 1.0) == pytest.approx(0.1)

    funnel = plan(
        1000, [BY_ID["docking"]], budget_gpu_hours=1e6, active_fraction=1.0, final_count=10
    )
    assert funnel.retention == pytest.approx(0.01, abs=1e-4)


def test_the_end_of_funnel_hit_rate_is_reported() -> None:
    """Not how many actives survived but what share of the delivered molecules are real.

    The number a chemist spends attention against, and the one a retention figure does not give.
    """

    funnel = plan(
        750_000,
        [BY_ID[i] for i in ("lilly_demerits", "docking", "mmgbsa_ensemble", "fep_edge")],
        budget_gpu_hours=8_760.0,
        active_fraction=0.001,
        final_count=10,
    )

    assert 0.0 < funnel.final_active_fraction <= 1.0
    assert funnel.as_dict()["expected_active_fraction_at_the_end"] == pytest.approx(
        funnel.final_active_fraction, abs=1e-4
    )


def test_a_tiny_pool_still_plans() -> None:
    """A campaign with twenty molecules gets a plan, not an exception from the optimiser."""

    funnel = plan(
        20,
        [BY_ID["docking"], BY_ID["mmgbsa_ensemble"]],
        budget_gpu_hours=1e6,
        active_fraction=0.1,
        final_count=10,
    )

    assert funnel.feasible
    assert funnel.final_count == 10
    assert funnel.retention > 0.5


def test_a_funnel_of_validity_tiers_alone_says_its_output_is_unranked() -> None:
    funnel = plan(
        1000, [BY_ID["lilly_demerits"]], budget_gpu_hours=1e6, active_fraction=0.01, final_count=10
    )

    assert any("unranked" in note for note in funnel.notes)
    # It cannot reach the requested count, because a filter's keep fraction is its own.
    assert funnel.final_count > 10


# -- measuring a stage on your own panel -----------------------------------


def test_the_correlation_carries_its_own_uncertainty() -> None:
    """A measured number is not an exact one, and on a few hundred molecules it is barely a number.

    The Fisher standard error of a Spearman is about 1/sqrt(n-3): 0.066 at 231 molecules, so two
    stages 0.09 apart are not distinguishable however carefully each was measured.
    """

    from etalon.economics.measure import Measured

    panel = Measured(0.45, 231, 0.0, 0.0, "measured IC50")

    assert panel.standard_error == pytest.approx(0.066, abs=0.002)
    assert panel.resolvable == pytest.approx(0.132, abs=0.004)
    # Wider than the planner's 0.10 default, which came from an AUC's Hanley-McNeil error on the
    # same panel. The correlation's own interval is the more direct answer.
    assert panel.resolvable > 0.10


def test_the_resolution_tightens_with_the_panel() -> None:
    from etalon.economics.measure import Measured

    sizes = [Measured(0.45, n, 0.0, 0.0, "x").resolvable for n in (50, 231, 1000, 5000)]

    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] > 0.25  # fifty molecules resolve almost nothing
    assert sizes[-1] < 0.03


def test_getting_the_direction_wrong_flips_the_sign_rather_than_degrading_it() -> None:
    """Which is why the two direction flags are separate arguments rather than one.

    A flipped measurement looks like a terrible stage, not like a mistake, and a campaign would
    conclude its docking was worthless.
    """

    import numpy as np

    from etalon.economics.measure import measure

    rng = np.random.default_rng(0)
    nanomolar = np.exp(rng.normal(3, 2, 200))
    score = -9 - 0.5 * (-np.log10(nanomolar)) + rng.normal(0, 0.6, 200)

    right = measure(score.tolist(), nanomolar.tolist(), truth="measured IC50, nM")
    flipped = measure(
        score.tolist(), nanomolar.tolist(), truth="measured IC50, nM", lower_is_stronger=False
    )

    assert right.spearman > 0.4
    assert flipped.spearman == pytest.approx(-right.spearman, abs=1e-9)


def test_the_interval_stays_inside_minus_one_and_one() -> None:
    """Which the naive interval does not, near the ends."""


    from etalon.economics.measure import measure

    perfect = list(range(40))
    result = measure([float(v) for v in perfect], [float(v) for v in perfect], truth="x",
                     lower_is_stronger=False, answer_is_concentration=False)

    assert result.spearman == pytest.approx(1.0)
    assert result.high <= 1.0


def test_molecules_without_a_score_or_an_answer_are_counted_not_dropped_silently() -> None:
    from etalon.economics.measure import measure

    scores = [float(v) for v in range(20)] + [None, None]
    answers = [float(v) for v in range(20)] + [1.0, None]

    result = measure(scores, answers, truth="x", lower_is_stronger=False, answer_is_concentration=False)

    assert result.molecules == 20
    assert result.dropped == 2


def test_too_few_pairs_is_refused() -> None:
    """A correlation on nine molecules contains almost any value, and publishing one invites its use."""

    from etalon.economics.measure import measure

    with pytest.raises(ValueError, match="interval wide enough"):
        measure([1.0] * 5, [1.0, 2.0, 3.0, 4.0, 5.0], truth="x")


def test_a_measured_stage_replaces_the_placeholder_source() -> None:
    """Rather than appending to it: the old text explains why the number was a convention."""

    from etalon.economics.measure import Measured, as_stage

    measured = Measured(0.52, 231, 0.42, 0.61, "measured IC50, nM", dropped=3)
    stage = as_stage(BY_ID["docking"], measured)

    assert stage.spearman == 0.52
    assert "0.520" in stage.source
    assert "231 molecules" in stage.source
    assert "3 dropped" in stage.source
    assert "No measurement here" not in stage.source
    # The cost is untouched unless the caller supplies one.
    assert stage.gpu_hours == BY_ID["docking"].gpu_hours


def test_measuring_the_correlation_does_not_relabel_the_cost() -> None:
    """A stage carries five numbers from different places, and one label over all of them laundered
    three. ``as_stage`` measures a correlation; it has not measured what the tier costs, and used to
    say it had by setting the stage-level evidence."""

    from etalon.economics.measure import Measured, as_stage

    measured = Measured(0.52, 231, 0.42, 0.61, "measured IC50, nM")
    substituted = as_stage(BY_ID["docking"], measured)

    assert substituted.evidence_for("spearman").value == "measured_here"
    assert substituted.evidence_for("gpu_hours").value == "convention"
    assert substituted.as_dict()["evidence_by_quantity"]["gpu_hours"] == "convention"
    assert "gpu_hours" in substituted.as_dict()["unmeasured_quantities"]
    # And when the caller does supply a measured cost, that one quantity moves and no other.
    with_cost = as_stage(BY_ID["docking"], measured, gpu_hours=0.0004)
    assert with_cost.evidence_for("gpu_hours").value == "measured_here"


def test_measuring_an_enrichment_curve_does_not_relabel_the_correlation() -> None:
    """The exact case that shipped: ``as_dict`` published ``spearman: 0.35`` -- the catalogue's own
    placeholder -- under ``evidence: measured_here``, because ``with_enrichment`` set the stage-level
    label after substituting a curve."""

    from etalon.economics.measure import EnrichmentPoint, with_enrichment

    points = [
        EnrichmentPoint(0.01, 2, 1, 12, 12 / 231, 0.0182 * 12 / 231 / (12 / 231), 0.8),
        EnrichmentPoint(0.10, 23, 3, 12, 12 / 231, 0.05, 0.4),
    ]
    stage = with_enrichment(BY_ID["docking"], points)
    published = stage.as_dict()

    assert stage.evidence_for("enrichment").value == "measured_here"
    assert published["spearman"] == BY_ID["docking"].spearman
    assert published["evidence_by_quantity"]["spearman"] == "convention"
    assert "spearman" in published["unmeasured_quantities"]
    # The panel size is reconstructed from the base rate, not from molecules/keep_fraction, which
    # turned 231 into 200.
    assert "231 molecules" in stage.source
    # And the placeholder's own explanation survives instead of being overwritten.
    assert "placeholder" in stage.source


def test_measuring_a_stage_that_does_not_rank_is_refused() -> None:
    """Giving a validity filter a correlation invites it to be compared with stages that do."""

    from etalon.economics.measure import Measured, as_stage

    measured = Measured(0.5, 231, 0.4, 0.6, "x")

    with pytest.raises(ValueError, match="rather than ranking"):
        as_stage(BY_ID["md_stability"], measured)


def test_an_unaffordable_plans_retention_is_not_offered_for_comparison() -> None:
    """Read across plans without checking feasibility and the worst one wins.

    Measured: with docking at the correlation this project actually observed, the tier is refused for
    being indistinguishable from random, the end-point method screens 255,330 molecules at 292
    GPU-years, and that plan's retention comes out HIGHER than the affordable one's.
    """

    funnel = plan(
        750_000,
        [BY_ID["lilly_demerits"], replace(BY_ID["docking"], spearman=0.108), BY_ID["mmgbsa_ensemble"]],
        budget_gpu_hours=8_760.0,
        active_fraction=0.001,
        final_count=10,
        resolvable_spearman=0.133,
    )

    assert funnel.feasible is False
    assert funnel.achievable_retention is None
    assert funnel.as_dict()["achievable_active_retention"] is None
    # The raw figure is still there for a reader who wants what the arithmetic said.
    assert funnel.as_dict()["expected_active_retention"] > 0
    assert "docking" in dict(funnel.refused)


def test_a_tier_indistinguishable_from_random_is_refused() -> None:
    """The first ranking tier is compared against zero, which is the right baseline.

    A correlation that cannot be told from zero cannot be told from shuffling the library.
    """

    funnel = plan(
        10_000,
        [replace(BY_ID["docking"], spearman=0.10)],
        budget_gpu_hours=1e6,
        active_fraction=0.01,
        final_count=10,
        resolvable_spearman=0.133,
    )

    assert "docking" in dict(funnel.refused)
    assert "0.000 already reached" in dict(funnel.refused)["docking"]


def test_a_plan_that_misses_its_requested_delivery_says_so() -> None:
    funnel = plan(
        750_000,
        [BY_ID["lilly_demerits"], BY_ID["docking"], BY_ID["mmgbsa_ensemble"], BY_ID["fep_edge"]],
        budget_gpu_hours=8_760.0,
        active_fraction=0.001,
        final_count=10,
    )

    if funnel.final_count != 10:
        assert any("only comparable at equal delivery" in note for note in funnel.notes)


# -- a measured curve beats an assumed correlation --------------------------

_MEASURED_CURVE = ((0.01, 1 / 12), (0.05, 2 / 12), (0.10, 3 / 12), (0.25, 4 / 12), (0.50, 8 / 12))


def test_a_measured_curve_is_used_in_place_of_the_correlation() -> None:
    """Measured: the Gaussian route predicted EF@1% of 1.72 where the panel measured 9.62."""

    docking = replace(BY_ID["docking"], spearman=0.108, enrichment=_MEASURED_CURVE)

    assert docking.ranks_by_measurement is True
    assert docking.retained_at(0.10) == pytest.approx(3 / 12)
    # And the Gaussian model at the same correlation says much less.
    assert retention(0.108, 0.10, 0.052) < 3 / 12


def test_the_curve_is_interpolated_in_the_logarithm() -> None:
    """A funnel's operating points span orders of magnitude; linear interpolation favours the top."""

    docking = replace(BY_ID["docking"], enrichment=_MEASURED_CURVE)
    midpoint = docking.retained_at(0.03)

    assert 1 / 12 < midpoint < 3 / 12
    # Geometric midpoint of 0.01 and 0.10 is about 0.0316, so a log interpolation lands near halfway
    # between the two values; a linear one would land much closer to the 0.01 end.
    assert midpoint == pytest.approx((1 / 12 + 3 / 12) / 2, abs=0.03)


def test_a_tier_with_a_measured_curve_is_not_refused_for_its_correlation() -> None:
    """Docking's 0.108 is within the panel's resolution of zero, and its curve is not."""

    plain = plan(
        10_000,
        [replace(BY_ID["docking"], spearman=0.108)],
        budget_gpu_hours=1e6,
        active_fraction=0.01,
        final_count=10,
        resolvable_spearman=0.133,
    )
    measured = plan(
        10_000,
        [replace(BY_ID["docking"], spearman=0.108, enrichment=_MEASURED_CURVE)],
        budget_gpu_hours=1e6,
        active_fraction=0.01,
        final_count=10,
        resolvable_spearman=0.133,
    )

    assert "docking" in dict(plain.refused)
    assert "docking" not in dict(measured.refused)


def test_a_curve_carrying_tier_may_not_operate_outside_its_measured_range() -> None:
    """The bug, and it reported a 100% hit rate.

    Without this the optimiser chose a keep fraction of 0.0001 for a curve measured down to 0.01,
    inherited the 1% retention figure 125-fold outside the evidence, and called the plan affordable.
    Clamping satisfies "do not extrapolate" in letter and breaks it in spirit.
    """

    funnel = plan(
        750_000,
        [
            BY_ID["lilly_demerits"],
            replace(BY_ID["docking"], spearman=0.108, enrichment=_MEASURED_CURVE),
            BY_ID["mmgbsa_ensemble"],
        ],
        budget_gpu_hours=876_000.0,
        active_fraction=0.001,
        final_count=10,
        resolvable_spearman=0.133,
    )

    docking = next(tier for tier in funnel.tiers if tier.stage.id == "docking")
    low, high = docking.stage.measured_range or (0.0, 1.0)

    assert low <= docking.keep_fraction <= high
    assert any("constrained to the range" in note for note in funnel.notes)


def test_the_measured_range_is_reported() -> None:
    docking = replace(BY_ID["docking"], enrichment=_MEASURED_CURVE)

    assert docking.measured_range == (0.01, 0.50)
    assert BY_ID["docking"].measured_range is None


def test_more_budget_stops_helping_once_the_evidence_binds() -> None:
    """The actionable consequence: beyond a point the constraint is the panel, not the money."""

    funnel = [
        BY_ID["lilly_demerits"],
        replace(BY_ID["docking"], spearman=0.108, enrichment=_MEASURED_CURVE),
        BY_ID["mmgbsa_ensemble"],
        BY_ID["md_stability"],
        BY_ID["fep_edge"],
    ]
    ten = plan(750_000, funnel, budget_gpu_hours=87_600.0, active_fraction=0.001, final_count=10,
               resolvable_spearman=0.133)
    hundred = plan(750_000, funnel, budget_gpu_hours=876_000.0, active_fraction=0.001, final_count=10,
                   resolvable_spearman=0.133)

    assert ten.feasible and hundred.feasible
    assert hundred.retention == pytest.approx(ten.retention, abs=0.002)
    # Ten times the budget is barely spent, because the evidence will not let the funnel cut harder.
    assert hundred.gpu_hours < 1.3 * ten.gpu_hours
