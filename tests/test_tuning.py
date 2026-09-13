"""Which screening knobs are worth turning, and the two reasons most of them are not.

The literature's ordering is not the one a configuration file suggests: the scoring function
dominates, the sampler is close to interchangeable, and consensus scoring is conditional on a
published precondition whose failure mode is also published. Most of this file pins that ordering
and the refusals that come with it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.economics.stage import BY_ID
from etalon.tuning.advise import advise, rho_for_multiplier
from etalon.tuning.knob import KNOBS, Effect, Lever, by_lever

FUNNEL = [BY_ID[i] for i in ("lilly_demerits", "docking", "mmgbsa_ensemble", "md_stability", "fep_edge")]


def _advise(**over: object):
    defaults: dict[str, object] = {
        "pool": 750_000,
        "stages": FUNNEL,
        "budget_gpu_hours": 8_760.0,
        "active_fraction": 0.001,
        "final_count": 10,
        "resolvable_spearman": 0.10,
    }
    defaults.update(over)
    pool = defaults.pop("pool")
    stages = defaults.pop("stages")
    return advise(pool, stages, **defaults)  # type: ignore[arg-type]


# -- the catalogue ---------------------------------------------------------


def test_every_knob_says_where_its_effect_size_came_from() -> None:
    import re

    for knob in KNOBS:
        assert knob.source.strip(), knob.id
        if knob.effect is Effect.BENCHMARKED:
            assert re.search(r"\b(19|20)\d{2}\b", knob.source), knob.id
        if knob.effect is Effect.ASSERTED:
            assert "asserted" in knob.source.lower(), knob.id


def test_sampling_knobs_carry_approximately_no_effect() -> None:
    """Recorded as zero rather than unknown, which is a claim and has to be sourced.

    DiffDock-L sampling scored with Gnina matched Vina sampling; DOCK3.x ranks on one pose.
    """

    exhaustiveness = next(k for k in by_lever(Lever.SAMPLING) if k.id == "exhaustiveness")

    assert exhaustiveness.delta_spearman == 0.0
    assert exhaustiveness.ef1_multiplier == 1.0
    assert "no benchmark separates from zero" in exhaustiveness.source
    # And it is not free: raising it quadruples the tier's cost.
    assert exhaustiveness.added_gpu_hours > BY_ID["docking"].gpu_hours


def test_the_conditional_knob_carries_its_condition_and_the_failure_mode() -> None:
    consensus = next(k for k in KNOBS if k.id == "consensus_three")

    assert "individually" in consensus.precondition or "on its own" in consensus.precondition
    assert "diverse" in consensus.precondition
    assert "GPCR" in consensus.source
    assert consensus.how_to_check.strip()


# -- the conversion -------------------------------------------------------


def test_an_ef_multiplier_inverts_into_a_correlation() -> None:
    """Not interchangeable without the active fraction, which is why it is computed and flagged."""

    assert rho_for_multiplier(0.35, 1.0) == 0.35
    modest = rho_for_multiplier(0.35, 1.07)
    large = rho_for_multiplier(0.35, 3.67)

    assert 0.35 < modest < large < 1.0
    assert large == pytest.approx(0.637, abs=0.01)


def test_an_unreachable_multiplier_saturates_rather_than_exceeding_one() -> None:
    """Asking for more actives than the pool contains must not report a correlation above one."""

    assert rho_for_multiplier(0.35, 10_000.0) == 1.0


def test_a_converted_correlation_is_flagged_in_the_output() -> None:
    advice = _advise(established={"consensus_three"})
    consensus = next(o for o in advice.worth_turning if o.knob.id == "consensus_three")

    assert consensus.converted is True
    assert "*" in advice.render()


# -- the two refusals -----------------------------------------------------


def test_a_knob_below_the_panels_resolution_is_separated_not_recommended() -> None:
    """Real effect, unverifiable change. The campaign should not expect to observe it."""

    advice = _advise(established={"rescore_ml", "consensus_three"})
    refused = dict(advice.below_resolution)

    assert "box_size" in refused
    assert "exhaustiveness" in refused
    assert "do not expect to observe it working" in refused["box_size"]


def test_an_unestablished_precondition_refuses_the_knob_and_names_the_check() -> None:
    advice = _advise()
    unmet = dict(advice.precondition_unmet)

    assert "consensus_three" in unmet
    assert "correlate their rankings" in unmet["consensus_three"]
    assert advice.worth_turning == ()


def test_establishing_the_precondition_unlocks_it() -> None:
    before = _advise()
    after = _advise(established={"rescore_ml", "consensus_three"})

    assert before.worth_turning == ()
    assert {o.knob.id for o in after.worth_turning} == {"rescore_ml", "consensus_three"}


def test_a_knob_with_no_effect_size_is_not_modelled_rather_than_guessed() -> None:
    advice = _advise(established={k.id for k in KNOBS})
    unmodelled = dict(advice.not_modelled)

    assert "pose_count" in unmodelled
    assert "qsar_prefilter" in unmodelled


def test_a_knob_acting_on_a_tier_with_no_correlation_says_measure_the_tier_first() -> None:
    advice = _advise(established={k.id for k in KNOBS})

    assert "widen_property_windows" in dict(advice.not_modelled)
    assert "Measure the tier before tuning it" in dict(advice.not_modelled)["widen_property_windows"]


# -- the result that justifies the module ---------------------------------


def test_engineering_hours_and_a_gpu_year_land_in_the_same_units() -> None:
    """findings/0006. The comparison a campaign faces and almost never makes."""

    advice = _advise(established={"rescore_ml", "consensus_three"})
    gains = {o.knob.id: o.gain for o in advice.worth_turning}

    # Ten times the compute was worth about +3.6 on this funnel (findings/0004).
    assert gains["rescore_ml"] == pytest.approx(3.7, abs=0.6)
    assert gains["consensus_three"] > gains["rescore_ml"]
    # And neither adds GPU-hours: they change what is done with the poses, not how many there are.
    assert all(o.added_gpu_hours < 1.0 for o in advice.worth_turning)


def test_the_ranking_puts_the_larger_gain_first() -> None:
    advice = _advise(established={"rescore_ml", "consensus_three"})
    gains = [o.gain for o in advice.worth_turning]

    assert gains == sorted(gains, reverse=True)


def test_the_optimism_of_a_rescorer_is_declared() -> None:
    """It reads the same poses from the same receptor conformation as the tier it improves."""

    advice = _advise(established={"rescore_ml"})

    assert any("least independent improvement" in note for note in advice.notes)


def test_the_tune_command_runs_and_respects_established_preconditions() -> None:
    from etalon.__main__ import main

    assert main(["tune", "--pool", "10000", "--budget", "100000"]) == 0
    assert main(["tune", "--established", "rescore_ml", "--json"]) == 0


def test_ranking_knobs_against_an_unaffordable_baseline_is_refused() -> None:
    """It produced negative gains: improving the screen appeared to deliver fewer actives.

    A ranking of negative improvements is worse than no ranking.
    """

    from dataclasses import replace as _replace

    funnel = [
        BY_ID["lilly_demerits"],
        _replace(BY_ID["docking"], spearman=0.108),
        BY_ID["mmgbsa_ensemble"],
    ]
    advice = advise(
        750_000,
        funnel,
        budget_gpu_hours=8_760.0,
        active_fraction=0.001,
        final_count=10,
        resolvable_spearman=0.133,
        established={"rescore_ml", "consensus_three"},
    )

    assert advice.worth_turning == ()
    assert any("not affordable at this budget" in note for note in advice.notes)


# -- the correlation is the wrong thing to rank by -------------------------

_CURVE = ((0.01, 1 / 12), (0.05, 2 / 12), (0.10, 3 / 12), (0.25, 4 / 12), (0.50, 8 / 12))


def test_a_knob_on_a_tier_with_measured_head_enrichment_is_not_ranked_by_correlation() -> None:
    """Measured: +0.23 of rank correlation took enrichment at the top 1% from 9.58 to zero.

    A model fitted with squared error shrinks toward the mean and never ranks an extreme first, so it
    orders the bulk well and puts nothing potent at the head. A funnel uses only the head, so that
    change makes the first tier worse while every figure in this module says it improved.
    """

    from dataclasses import replace as _replace

    funnel = [
        BY_ID["lilly_demerits"],
        _replace(BY_ID["docking"], spearman=0.121, enrichment=_CURVE),
        BY_ID["mmgbsa_ensemble"],
        BY_ID["fep_edge"],
    ]
    advice = advise(
        750_000,
        funnel,
        budget_gpu_hours=87_600.0,
        active_fraction=0.001,
        final_count=10,
        resolvable_spearman=0.133,
        established={"rescore_ml", "consensus_three"},
    )

    assert advice.worth_turning == ()
    unmodelled = dict(advice.not_modelled)
    assert "rescore_ml" in unmodelled
    assert "a funnel uses its head" in unmodelled["rescore_ml"]
    assert "measure_enrichment" in unmodelled["rescore_ml"]


def test_a_negative_gain_is_explained_rather_than_ranked_last() -> None:
    """Improving a cheap tier can make an expensive one statistically redundant.

    Measured: docking at 0.637 put MM-GBSA's 0.767 inside the panel's 0.133 resolution, the planner
    dropped it as one tier at two prices, and the funnel lost its most selective stage. A bare
    "-2.4 actives" invites the conclusion that the knob is bad, which is not what happened.
    """

    advice = advise(
        750_000,
        [BY_ID[i] for i in ("lilly_demerits", "docking", "mmgbsa_ensemble", "md_stability", "fep_edge")],
        budget_gpu_hours=87_600.0,
        active_fraction=0.001,
        final_count=10,
        resolvable_spearman=0.133,
        established={"rescore_ml", "consensus_three"},
    )

    assert all(option.gain > 0 for option in advice.worth_turning)
    explained = dict(advice.not_modelled)
    assert "consensus_three" in explained
    note = explained["consensus_three"]
    assert "not an argument against the knob" in note
    assert "mmgbsa_ensemble" in note
    assert "artefact of panel size" in note
