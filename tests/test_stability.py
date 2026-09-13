"""What 1000 ns of unbiased MD establishes, and the two tests that got it wrong first.

The three stability questions have three different consequences and a single phrase -- "is the pose
stable" -- hides all of them. A ligand that left makes every number from the trajectory a number
about a solvated ligand. A ligand still moving makes the average an average over a non-stationary
segment. A ligand that held the site and lost its contacts makes the free energy fine and the
comparison with the docking score meaningless.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.faults.preflight import blocking, unverifiable
from etalon.faults.stability import (
    CONTACT_PERSISTENCE,
    LEFT_THE_SITE_NM,
    Trajectory,
    check_stability,
    from_prism,
    necessary_not_sufficient,
)


def _series(start: float, rise: float, noise: float, frames: int, seed: int = 3) -> tuple[float, ...]:
    rng = random.Random(seed)
    return tuple(
        start + rise * index / frames + rng.gauss(0, noise) for index in range(frames)
    )


def _fired(trajectory: Trajectory, code: str) -> bool:
    entry = next(item for item in check_stability(trajectory) if item.code == code)
    return entry.fired and entry.evaluable


# -- did the ligand stay ---------------------------------------------------


def test_a_ligand_that_left_blocks_every_number_from_the_run() -> None:
    left = Trajectory(_series(0.1, 0.9, 0.02, 500), {}, (), replicas=5, nanoseconds=1000)

    assert _fired(left, "F_POSE_LEFT_THE_SITE")
    assert "F_POSE_LEFT_THE_SITE" in {entry.code for entry in blocking(check_stability(left))}


def test_a_ligand_that_left_and_returned_is_reported_as_such() -> None:
    """An RMSD is not a distance from the pocket, and the detail has to say so."""

    excursion = (*[0.12] * 100, *[0.8] * 50, *[0.13] * 350)
    trajectory = Trajectory(excursion, {}, (), replicas=5)

    entry = next(
        item for item in check_stability(trajectory) if item.code == "F_POSE_LEFT_THE_SITE"
    )

    assert entry.fired
    assert "left and returned" in entry.detail


def test_a_pose_inside_the_cutoff_does_not_fire() -> None:
    held = Trajectory(_series(0.12, 0.0, 0.02, 500), {}, (), replicas=5)

    assert max(held.ligand_rmsd_nm) < LEFT_THE_SITE_NM
    assert not _fired(held, "F_POSE_LEFT_THE_SITE")


# -- did it settle, and the two bugs in asking ----------------------------


def test_a_slow_creep_is_not_settled_whatever_the_sampling_rate() -> None:
    """Both bugs in one test.

    The first version compared the drift to a chosen 0.1 nm and reported a ligand creeping from
    0.15 to 0.40 nm over 1000 ns as settled. The second compared it to the noise multiplied by the
    square root of the frame count -- a half-remembered standard error that inverted the test, so
    more frames made a trend harder to detect. The same physical drift must read the same at any
    sampling rate.
    """

    dense = Trajectory(_series(0.15, 0.25, 0.01, 500), {"A": 0.9}, ("A",), replicas=5)
    sparse = Trajectory(_series(0.15, 0.25, 0.01, 50), {"A": 0.9}, ("A",), replicas=5)

    assert _fired(dense, "F_POSE_NOT_EQUILIBRATED")
    assert _fired(sparse, "F_POSE_NOT_EQUILIBRATED")
    ratios = [t.drift() / t.fluctuation() for t in (dense, sparse)]
    assert abs(ratios[0] - ratios[1]) < 0.2 * max(ratios)


def test_the_reported_ratio_is_the_one_the_test_actually_used() -> None:
    """The third bug in asking, which lived in the sentence rather than in the test.

    ``settled()`` was fixed to compare the drift against the noise directly; the detail string was
    not, and went on dividing by the square root of the frame count. So the flag was right and the
    sentence a person reads contradicted it -- and contradicted it *more* with more data. A
    trajectory at a true ratio of about 8 was reported as "0.6 times the noise" at 600 frames and
    "0.2 times the noise" at 6000, while firing at both.

    Nothing caught it because every test here read ``drift()`` and ``fluctuation()`` and none read
    the string that goes to the operator. This one reads the string.
    """

    for frames in (60, 600, 6000):
        creeping = Trajectory(_series(0.15, 0.25, 0.01, frames), {"A": 0.9}, ("A",), replicas=5)
        entry = next(
            item for item in check_stability(creeping) if item.code == "F_POSE_NOT_EQUILIBRATED"
        )
        stated = float(entry.detail.split("the trend is ")[1].split(" times")[0])
        computed = creeping.drift() / creeping.fluctuation()

        assert entry.fired
        assert abs(stated - computed) < 0.05, f"{frames} frames: said {stated}, tested {computed}"
        # A fired observation whose own sentence reads as comfortably below the threshold is worse
        # than no sentence: it invites the reader to overrule the flag.
        assert stated > 1.0


def test_a_noisy_pose_with_no_trend_is_settled() -> None:
    """Otherwise the check would refuse every flexible ligand for being flexible."""

    noisy = Trajectory(_series(0.20, 0.0, 0.08, 500), {"A": 0.9}, ("A",), replicas=5)

    assert not _fired(noisy, "F_POSE_NOT_EQUILIBRATED")
    assert noisy.fluctuation() > 0.05


def test_a_trend_is_found_even_under_heavy_noise() -> None:
    noisy_trend = Trajectory(_series(0.20, 0.30, 0.08, 500), {"A": 0.9}, ("A",), replicas=5)

    assert _fired(noisy_trend, "F_POSE_NOT_EQUILIBRATED")


def test_the_fluctuation_is_measured_about_the_trend_not_the_mean() -> None:
    """About the mean, a rising series has a large spread *because* of the trend.

    Using that as the noise scale would hide exactly the drift being looked for.
    """

    rising = Trajectory(_series(0.15, 0.30, 0.005, 400), {}, (), replicas=5)
    values = rising.final_third
    mean = sum(values) / len(values)
    about_the_mean = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5

    assert rising.fluctuation() < about_the_mean / 2


# -- is it the pose that was scored ---------------------------------------


def test_losing_the_scored_contacts_invalidates_the_comparison_not_the_number() -> None:
    """The free energy is about a real bound state; the screen's number was about another pose."""

    moved = Trajectory(
        _series(0.12, 0.0, 0.02, 500),
        {"TRP80": 0.9, "PHE7": 0.8},
        ("ASP118", "LEU15", "VAL23"),
        replicas=5,
    )
    observed = check_stability(moved)

    assert _fired(moved, "F_POSE_NOT_THE_SCORED_ONE")
    assert blocking(observed) == ()
    assert "F_POSE_NOT_THE_SCORED_ONE" in {entry.code for entry in unverifiable(observed)}


def test_retained_contacts_do_not_fire() -> None:
    held = Trajectory(
        _series(0.12, 0.0, 0.02, 500),
        {"ASP118": 0.9, "LEU15": 0.8, "VAL23": 0.7},
        ("ASP118", "LEU15", "VAL23"),
        replicas=5,
    )

    assert held.retained_fraction() == 1.0
    assert not _fired(held, "F_POSE_NOT_THE_SCORED_ONE")
    assert CONTACT_PERSISTENCE < 1.0


def test_no_recorded_pose_makes_the_check_unevaluable_rather_than_clean() -> None:
    """The pose may have changed completely and this check cannot see it."""

    unknown = Trajectory(_series(0.12, 0.0, 0.02, 500), {"A": 0.9}, (), replicas=5)
    entry = next(
        item for item in check_stability(unknown) if item.code == "F_POSE_NOT_THE_SCORED_ONE"
    )

    assert entry.evaluable is False
    assert "recorded no contacts" in entry.detail


# -- what one run is ------------------------------------------------------


def test_one_replica_fires_and_five_do_not() -> None:
    """Up to 15 kcal/mol between replicas differing only in their initial velocities."""

    one = Trajectory(_series(0.12, 0.0, 0.02, 500), {}, (), replicas=1)
    five = Trajectory(_series(0.12, 0.0, 0.02, 500), {}, (), replicas=5)

    assert _fired(one, "F_SINGLE_REPLICA_ESTIMATE")
    assert not _fired(five, "F_SINGLE_REPLICA_ESTIMATE")


def test_the_single_replica_band_explains_a_plausible_divergence_but_not_any() -> None:
    """Its width is the point: a 4 kcal/mol disagreement needs no further explanation."""

    from etalon.faults import BY_CODE

    fault = BY_CODE["F_SINGLE_REPLICA_ESTIMATE"]

    assert fault.can_account_for(4.0)
    assert not fault.can_account_for(15.0)
    assert fault.upper_kcal_mol == 12.0


def test_a_summary_without_the_series_is_refused() -> None:
    """A mean cannot distinguish a settled pose from one that left and came back."""

    with pytest.raises(ValueError, match="no per-frame RMSD"):
        from_prism({"mean_nm": 0.12, "std_nm": 0.02})
    with pytest.raises(ValueError, match="says nothing about stability"):
        Trajectory(())


def test_prism_analysis_output_translates() -> None:
    trajectory = from_prism(
        {"values_nm": [0.10, 0.12, 0.11, 0.12, 0.13, 0.12]},
        {"top_contacts": [{"residue": "ASP118", "proportion": 0.91}]},
        scored_contacts=("ASP118",),
        replicas=5,
        nanoseconds=1000,
    )

    assert len(trajectory.ligand_rmsd_nm) == 6
    assert trajectory.retained_fraction() == 1.0
    assert trajectory.nanoseconds == 1000


def test_the_necessary_not_sufficient_sentence_is_available_to_a_record() -> None:
    """The sentence most likely to be dropped when a result is summarised."""

    text = necessary_not_sufficient()

    assert "necessary condition" in text
    assert "cannot show that one does" in text
    assert "1000 ns is also not a tighter binder" in text
