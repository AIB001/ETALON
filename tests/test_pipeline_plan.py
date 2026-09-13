"""The campaign as one object, and the line that makes a plan readable.

A plan reads as a calculation, and a calculation over four guesses reads exactly like one over four
measurements. Most of this file is about the parts that say which is which.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.campaign.pipeline import DEFAULT_FUNNEL, Pipeline
from etalon.economics.stage import BY_ID
from etalon.generate.audit import GeneratorYield


def _pipeline(**over: object) -> Pipeline:
    defaults: dict[str, object] = {
        "pool": 750_000,
        "budget_gpu_hours": 8_760.0,
        "active_fraction": 0.001,
        "final_count": 10,
    }
    defaults.update(over)
    return Pipeline(**defaults)  # type: ignore[arg-type]


def test_pmf_is_absent_from_the_default_funnel() -> None:
    """It answers mechanism. ADR 0004 is why that cannot be a tier in a ranking funnel."""

    assert "pmf_umbrella" not in DEFAULT_FUNNEL
    assert DEFAULT_FUNNEL[-1] == "fep_edge"


def test_a_plan_names_every_tier_resting_on_a_guess() -> None:
    """Collected rather than left to a reader to notice."""

    plan = _pipeline().dry_run()
    named = {stage for stage, _ in plan.conventions}

    assert "docking" in named
    assert all(source.strip() for _, source in plan.conventions)
    assert any("nobody measured" in line for line in plan.render().splitlines())


def test_a_measured_override_is_recorded_and_changes_the_outcome() -> None:
    """The intended use: every published number in the catalogue is a placeholder for one of these."""

    measured = replace(BY_ID["docking"], spearman=0.55, evidence=BY_ID["docking"].evidence)
    plain = _pipeline().dry_run()
    improved = _pipeline().dry_run(overrides={"docking": measured})

    assert improved.funnel.retention > plain.funnel.retention
    assert any("Measured overrides in use" in note for note in improved.notes)
    assert any("No measured overrides" in note for note in plain.notes)


def test_the_plan_says_where_most_of_the_actives_are_lost() -> None:
    """Not an assertion about a number, but about the plan being able to answer the question."""

    plan = _pipeline().dry_run()
    losses = []
    previous = 1.0
    for tier in plan.funnel.tiers:
        losses.append((tier.stage.id, previous - tier.retained_actives))
        previous = tier.retained_actives

    worst = max(losses, key=lambda pair: pair[1])[0]
    # The cheapest ranking tier, because at its correlation a hard cut discards what it cannot
    # identify -- which is the mechanism behind findings/0004.
    assert worst == "docking"


def test_an_unknown_stage_is_refused_with_the_remedy() -> None:
    with pytest.raises(KeyError, match="pass it as an override"):
        _pipeline(stages=("lilly_demerits", "something_invented")).dry_run()


def test_the_expensive_tiers_width_is_where_an_acquisition_budget_comes_from() -> None:
    """Rather than a round figure somebody liked."""

    plan = _pipeline().dry_run()
    budget = _pipeline().expensive_budget(plan)

    tier = next(t for t in plan.funnel.tiers if t.stage.id == "mmgbsa_ensemble")
    assert budget == tier.molecules_in
    assert budget > 0


def test_asking_for_the_budget_of_a_refused_tier_says_it_was_refused() -> None:
    plan = _pipeline().dry_run()

    with pytest.raises(KeyError, match="refused or never included"):
        _pipeline().expensive_budget(plan, "boltz2_affinity")


def test_the_generation_audit_is_attached_when_counts_exist() -> None:
    counts = {"qc_passed": 0, "docking_top": 0, "final": 0}
    generators = [
        GeneratorYield("a", 150_000, {**counts, "qc_passed": 91_000, "docking_top": 420, "final": 4}),
        GeneratorYield("b", 150_000, {**counts, "qc_passed": 128_000, "docking_top": 510, "final": 3}),
    ]

    plan = _pipeline().dry_run(generators=generators)

    assert plan.generators is not None
    assert plan.generators.deepest_decidable_stage() is not None
    assert any("Generation feedback" in note for note in plan.notes)


def test_a_dry_run_renders_without_a_funnel_it_can_afford() -> None:
    """A campaign with a month and a million molecules gets an answer, not an exception."""

    plan = _pipeline(pool=1_000_000, budget_gpu_hours=100.0).dry_run()
    rendered = plan.render()

    assert "NOT AFFORDABLE" in rendered or plan.funnel.feasible
    assert "Pool 1,000,000" in rendered


def test_the_plan_round_trips_as_json() -> None:
    import json

    payload = json.loads(json.dumps(_pipeline().dry_run().as_dict()))

    assert payload["true_actives_in_pool"] == 750
    assert payload["funnel"]["tiers"]
    assert payload["unmeasured_inputs"]


# -- the command line ------------------------------------------------------


def test_the_plan_command_renders_and_signals_affordability() -> None:
    """Exit 1 on an unaffordable plan so a shell can branch, and it is not an error."""

    from etalon.__main__ import main

    assert main(["plan", "--pool", "1000", "--budget", "1000000", "--deliver", "10"]) == 0
    assert main(["plan", "--pool", "1000000", "--budget", "50", "--deliver", "10"]) == 1


def test_a_measured_correlation_can_be_supplied_at_the_command_line() -> None:
    """The intended use: every figure in the catalogue is a placeholder for one of these."""

    from etalon.__main__ import main

    assert main(["plan", "--pool", "10000", "--budget", "100000", "--measured", "docking=0.52"]) == 0
    # A stage that is not in the catalogue, and a value that is not a number, are both refused.
    assert main(["plan", "--measured", "invented=0.5"]) == 2
    assert main(["plan", "--measured", "docking=high"]) == 2


def test_the_stage_catalogue_prints_as_json() -> None:
    from etalon.__main__ import main

    assert main(["stages", "--json"]) == 0
    assert main(["stages"]) == 0
