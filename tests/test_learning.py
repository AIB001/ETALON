"""The surrogate, its calibration, and the batch it chooses -- judged on what was measured.

The governing measurement is ``findings/0003``. On the real 231-molecule STK17B panel, conformal
marginal coverage came out 90.5% against a nominal 90% while the scaffold group holding the
panel's most potent compound was covered 37.5%: the forest regressed an unseen chemotype to the
panel mean, missed a 0.26 nM inhibitor by 3.13 pIC50, and gave it the narrowest interval in its
series. Most of what is pinned here exists because of that, and the tests run on synthetic data
that reproduces its shape rather than on the panel itself, so the suite needs no private file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.learn.acquire import DEFAULT_NOVELTY_BONUS, acquire
from etalon.learn.conformal import (
    SPREAD_FLOOR,
    Calibration,
    Interval,
    _quantile_index,
    intervals,
)
from etalon.learn.surrogate import DESCRIPTORS, MORGAN_BITS, Features, pic50


def _interval(name: str, mean: float, half: float) -> Interval:
    return Interval(name, mean, half / 2.0, mean - half, mean + half)


# -- the target scale ------------------------------------------------------


def test_affinity_becomes_pic50_because_free_energy_is_log_linear() -> None:
    assert pic50(1.0) == 9.0
    assert pic50(1000.0) == 6.0
    assert pic50(0.26) == pytest.approx(9.585, abs=0.001)
    with pytest.raises(ValueError, match="must be positive"):
        pic50(0.0)


# -- the quantile that is the guarantee ------------------------------------


def test_the_conformal_rank_is_the_one_the_theorem_asks_for() -> None:
    """ceil((n+1)(1-alpha)) and not a percentile.

    A percentile with linear interpolation gives a slightly smaller threshold and slightly
    under-covers, which no test that checks coverage is "about right" would ever catch.
    """

    # n=100, alpha=0.1 -> ceil(101 * 0.9) = 91st score, index 90.
    assert _quantile_index(100, 0.1) == 90
    assert _quantile_index(10, 0.1) == 9
    # Never past the end: with few scores the widest is the best available bound.
    assert _quantile_index(5, 0.01) == 4
    assert _quantile_index(5, 0.99) == 0


# -- the measurement that shapes the design --------------------------------


def test_coverage_is_reported_twice_and_the_worst_group_is_in_the_record() -> None:
    """A campaign reading the marginal figure alone is told an average whose variance is the point."""

    calibration = Calibration(
        alpha=0.1,
        q=1.4115,
        calibration_count=231,
        marginal_coverage=0.905,
        worst_group_coverage=0.375,
        worst_group="N=C1/C(=C2/C(=O)Nc3ccccc32)Nc2ccccc21",
        groups_measured=8,
        median_half_width=1.23,
        calibration_id="crossconf-a0.1-k5-n231",
    )

    recorded = calibration.as_dict()

    assert recorded["marginal_coverage"] == 0.905
    assert recorded["worst_group_coverage"] == 0.375
    assert recorded["nominal_coverage"] == 0.9
    # The half-width too: coverage without sharpness is satisfied by an infinite interval.
    assert recorded["median_half_width"] == 1.23
    assert "approximate here rather than exact" in str(recorded["guarantee"])


def test_an_interval_is_wider_where_the_model_disagrees_with_itself() -> None:
    calibration = Calibration(0.1, 2.0, 10, 0.9, 0.9, "g", 1, 1.0, "c")

    narrow, wide = intervals(["a", "b"], [7.0, 7.0], [0.0, 0.5], calibration)

    assert narrow.half_width == pytest.approx(2.0 * SPREAD_FLOOR)
    assert wide.half_width == pytest.approx(2.0 * (0.5 + SPREAD_FLOOR))
    assert narrow.mean == wide.mean


# -- ties ------------------------------------------------------------------


def test_overlapping_intervals_are_a_tie_and_the_batch_says_how_many() -> None:
    """Ranking a tie asserts a difference the interval says is not there."""

    candidates = [_interval("a", 8.0, 1.0), _interval("b", 7.6, 1.0), _interval("c", 2.0, 0.2)]

    batch = acquire(candidates, budget=2)

    assert batch.tied_with_best == 1
    assert any("not distinguishable" in note for note in batch.notes)


def test_a_clearly_better_candidate_is_not_reported_as_tied() -> None:
    batch = acquire([_interval("a", 9.0, 0.1), _interval("b", 5.0, 0.1)], budget=1)

    assert batch.tied_with_best == 0
    assert batch.parent_ids == ("a",)


# -- exploration -----------------------------------------------------------


def test_the_explore_share_is_reserved_before_anything_the_model_believes() -> None:
    """findings/0003: the forest was confident exactly where it was most wrong."""

    candidates = [_interval(f"seen{i}", 9.0 - i * 0.01, 0.05) for i in range(8)]
    candidates += [_interval(f"new{i}", 5.0, 0.05) for i in range(4)]
    scaffolds = {**{f"seen{i}": "known" for i in range(8)}, **{f"new{i}": f"novel{i}" for i in range(4)}}

    batch = acquire(
        candidates,
        budget=8,
        scaffolds=scaffolds,
        seen_scaffolds=frozenset({"known"}),
        explore_fraction=0.25,
    )

    unseen = [pick for pick in batch.picks if pick.reason == "unseen_scaffold"]
    assert len(unseen) == 2  # round(8 * 0.25)
    # The weak-but-novel picks are taken despite being 4 pIC50 below the best prediction.
    assert all(pick.parent_id.startswith("new") for pick in unseen)
    assert any("reserved for scaffolds the surrogate has never seen" in n for n in batch.notes)


def test_zero_exploration_spends_everything_on_what_the_model_believes() -> None:
    candidates = [_interval(f"seen{i}", 9.0 - i * 0.5, 0.05) for i in range(4)]
    candidates += [_interval("new", 5.0, 0.05)]
    scaffolds = {**{f"seen{i}": f"k{i}" for i in range(4)}, "new": "novel"}

    batch = acquire(
        candidates,
        budget=3,
        scaffolds=scaffolds,
        seen_scaffolds=frozenset(f"k{i}" for i in range(4)),
        explore_fraction=0.0,
    )

    assert "new" not in batch.parent_ids
    assert any("sharpen what the model knows and test none of it" in n for n in batch.notes)


def test_the_novelty_score_bonus_is_off_by_default() -> None:
    """It was 1.0 pIC50 and it took every place in the batch.

    A bonus comparable to the spread of the scores it is added to does not tilt a ranking, it
    replaces it -- measured as the sub-100 nM hit count falling from 13 to 4.
    """

    assert DEFAULT_NOVELTY_BONUS == 0.0


def test_reserving_places_for_novelty_that_does_not_exist_is_reported() -> None:
    candidates = [_interval(name, 8.0, 0.1) for name in "abcdefgh"]
    scaffolds = dict.fromkeys("abcdefgh", "known")

    batch = acquire(
        candidates, budget=8, scaffolds=scaffolds, seen_scaffolds=frozenset({"known"})
    )

    assert len(batch.picks) == 8
    assert any("the pool holds none" in note for note in batch.notes)


def test_an_explore_fraction_that_rounds_away_is_reported() -> None:
    """Found by a test that expected two places from a budget of two at a quarter.

    round() is banker's: round(0.5) is 0 while round(1.5) is 2, so a small budget silently got no
    exploration and a slightly larger one got two. Flooring is the honest reading, and the zero
    case says so instead of passing.
    """

    candidates = [_interval("a", 8.0, 0.1), _interval("b", 5.0, 0.1)]
    scaffolds = {"a": "known", "b": "novel"}

    batch = acquire(
        candidates,
        budget=2,
        scaffolds=scaffolds,
        seen_scaffolds=frozenset({"known"}),
        explore_fraction=0.25,
    )

    assert all(pick.reason != "unseen_scaffold" for pick in batch.picks)
    assert any("reserves no places" in note for note in batch.notes)


def test_no_scaffolds_degrades_to_a_confidence_bound_and_says_so() -> None:
    """The mode findings/0003 says is not good enough, so it may not pass silently."""

    batch = acquire([_interval("a", 8.0, 0.1)], budget=1)

    assert any("no novelty term and no diversity penalty" in note for note in batch.notes)


def test_an_empty_pool_and_an_impossible_budget_are_handled() -> None:
    assert acquire([], budget=5).picks == ()
    with pytest.raises(ValueError, match="selects nothing"):
        acquire([_interval("a", 1.0, 0.1)], budget=0)
    with pytest.raises(ValueError, match="explore_fraction must be in"):
        acquire([_interval("a", 1.0, 0.1)], budget=1, explore_fraction=1.5)


# -- alignment -------------------------------------------------------------


def test_a_dropped_row_realigns_rather_than_silently_truncating() -> None:
    """MolCascade's featuriser drops what it cannot featurise; a full target list then mispairs.

    Its reasoning is better than zero-filling: a zero vector is a plausible input a model will
    happily score. The consequence is that alignment becomes the caller's problem, and getting
    it wrong trains every molecule after the first failure against another's answer.
    """

    import numpy as np

    features = Features(matrix=np.zeros((3, 2)), names=("a", "b"), unparsed=(1, 4))

    assert features.align([10, 20, 30, 40, 50]) == [10, 30, 40]
    assert features.align([1, 2, 3]) == [1, 2, 3]  # already aligned
    with pytest.raises(ValueError, match="describes neither"):
        features.align([1, 2])


def test_the_representation_is_declared_in_molcascades_vocabulary() -> None:
    """So the manifest cannot describe a featurisation the model was not trained on.

    Skipped, loudly, where the vendored MolCascade cannot be imported. It needs pydantic v2 and an
    environment with v1 installed fails this one test at collection-time import, which is how the
    README's "277 tests, no GPU, no AmberTools, no network, no API key" came to describe a suite
    that was not green: 277 is what pytest collects and 276 is what passed. The dependency is real
    and undeclared rather than absent, so the honest report is a skip naming what is missing --
    the same three-valued distinction the faults layer makes between a check that passed and a check
    that could not run.
    """

    from etalon.boundary.infra import InfraError, load

    try:
        load("molcascade")
        from etalon.learn.surrogate import representation

        spec = representation()
    except (ImportError, InfraError) as error:  # pragma: no cover -- environment-dependent
        # The top-level package imports on pydantic v1; molcascade.chemistry.featurizers does not,
        # so the skip has to cover the call and not only the load.
        pytest.skip(f"vendored molcascade is not usable here ({error}); it needs pydantic v2")
    kinds = [block.kind for block in spec.blocks]

    assert kinds == ["descriptors", "morgan"]
    assert spec.width == len(DESCRIPTORS) + MORGAN_BITS
    # The manifest's n_features must equal this, and MolCascade validates that it does.
    assert spec.width == 1033
