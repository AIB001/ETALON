"""Read a production trajectory as observations, and say what 1000 ns established.

A long unbiased simulation is usually described as checking whether a pose is stable, and the
description hides three different questions that have three different consequences.

**Did the ligand stay?** If it left, every number from the trajectory describes a solvated ligand
near a protein. That is a real system and not the one anybody asked about, so the number is about
the wrong subject and no divergence size redeems it.

**Did it settle?** A pose that is still moving at the end was averaged over a non-stationary
segment, so the average estimates a time-dependent quantity -- neither the starting state nor the
equilibrium one. Also the wrong subject, and the remedy is more time rather than more caution.

**Is it the pose that was scored?** A ligand can hold the site and lose every contact the docking
score was about. That does not make the free energy wrong; it makes the comparison between the
free energy and the docking score a comparison between two poses. So it invalidates a calibration
and not a measurement, which is why it is classified ``UNVERIFIABLE`` and why it withholds a
molecule from teaching the screen without withholding the molecule.

And one thing 1000 ns does not establish. A single trajectory samples one instance of a chaotic
process: neighbouring trajectories in phase space diverge exponentially, and replicas differing
only in their initial velocities have been observed to disagree by up to 15 kcal/mol on the same
system. So a run that loses the pose has not shown the pose is wrong, and a run that holds it has
not shown it is right. What a single long run gives is a necessary condition, cheaply, and this
module reports it as one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from etalon.faults.attribution import Observation

#: Ligand RMSD, in nm, beyond which the pose is treated as having left the site. A convention: 0.5
#: nm is roughly the width of a small-molecule binding site, so a ligand this far from where it
#: started is usually not making the same interactions. Weaker than it looks -- a ligand can leave
#: and return, and an RMSD is not a distance from the pocket -- which is why the fault it raises
#: is a THRESHOLD rather than an EXACT observable.
LEFT_THE_SITE_NM = 0.5

#: How large the trend across the final third may be, as a multiple of the fluctuation within it.
#:
#: A ratio rather than a distance, and the first version was a distance -- 0.1 nm -- which failed
#: on the case it existed for: a ligand creeping from 0.15 to 0.40 nm over 1000 ns and still
#: rising moved 0.085 nm across the final third and was reported settled. Picking a tighter
#: nanometre figure would have fixed that trajectory and broken a noisier one, because the
#: question is not how far the pose moved but whether the movement is a trend or the fluctuation
#: any bound ligand shows.
#:
#: So the test is dimensionless: a pose is settled when the systematic movement over the segment
#: is no larger than the random movement within it. One is the natural value and is still a
#: convention -- the statistic has no null distribution here, because the frames are serially
#: correlated and treating them as independent would overstate every such test.
NOT_SETTLED_RATIO = 1.0

#: A floor on the fluctuation used in that ratio, in nm. Without it a very quiet trajectory
#: divides a small trend by a smaller noise and reports drift that is numerically real and
#: physically nothing.
FLUCTUATION_FLOOR_NM = 0.01

#: What share of the docked pose's contacts must persist for the simulated pose to be the scored
#: one. A convention.
CONTACT_PERSISTENCE = 0.5

#: How often a contact must be present across frames to count as present at all.
CONTACT_PRESENT = 0.3


@dataclass(frozen=True, slots=True)
class Trajectory:
    """What ETALON needs from one production run, in its own vocabulary.

    Defined here rather than taking PRISM's analysis JSON directly so that a second engine can be
    read into the same shape, and so that a campaign resuming from files needs only these numbers
    rather than a trajectory. :func:`from_prism` does the translation.
    """

    #: Ligand RMSD per frame, in nm, after fitting on the protein. The series and not a summary,
    #: because a mean cannot tell a settled pose from one that left and came back.
    ligand_rmsd_nm: tuple[float, ...]
    #: Residue to the fraction of frames it was in contact with the ligand.
    contacts: Mapping[str, float] = ()  # type: ignore[assignment]
    #: Contacts the docked pose was scored for, if the campaign recorded them.
    scored_contacts: tuple[str, ...] = ()
    #: How many independent runs this trajectory is one of.
    replicas: int = 1
    nanoseconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.ligand_rmsd_nm:
            raise ValueError(
                "a trajectory with no RMSD series says nothing about stability; pass the series "
                "rather than a summary, because a mean cannot distinguish a settled pose from "
                "one that left and returned"
            )

    @property
    def final_third(self) -> tuple[float, ...]:
        cut = max(1, len(self.ligand_rmsd_nm) * 2 // 3)
        return self.ligand_rmsd_nm[cut:] or self.ligand_rmsd_nm[-1:]

    def drift(self) -> float:
        """Least-squares trend over the final third, scaled to the whole of that segment, in nm.

        A slope rather than a difference of endpoints, because a noisy series can end on a low
        frame and look settled. Scaled to the segment so the number is "how much it moved while we
        were watching the end" and does not depend on the frame rate.
        """

        series = self.final_third
        count = len(series)
        if count < 3:
            return 0.0
        mean_x = (count - 1) / 2.0
        mean_y = sum(series) / count
        numerator = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(series))
        denominator = sum((i - mean_x) ** 2 for i in range(count))
        slope = numerator / denominator if denominator else 0.0
        return abs(slope) * (count - 1)

    def fluctuation(self) -> float:
        """Residual standard deviation about that trend, in nm.

        About the trend and not about the mean: a steadily rising series has a large spread around
        its mean for a reason that is the trend itself, so using that spread as the noise scale
        would hide exactly the drift being looked for.
        """

        series = self.final_third
        count = len(series)
        if count < 3:
            return FLUCTUATION_FLOOR_NM
        mean_x = (count - 1) / 2.0
        mean_y = sum(series) / count
        denominator = sum((i - mean_x) ** 2 for i in range(count))
        slope = (
            sum((i - mean_x) * (y - mean_y) for i, y in enumerate(series)) / denominator
            if denominator
            else 0.0
        )
        residuals = [y - (mean_y + slope * (i - mean_x)) for i, y in enumerate(series)]
        variance = sum(r * r for r in residuals) / max(1, count - 2)
        return max(variance**0.5, FLUCTUATION_FLOOR_NM)

    def settled(self) -> bool:
        """Whether the trend over the final third is smaller than the noise within it.

        The ratio and nothing else. A first version multiplied the tolerance by the square root of
        the frame count, half-remembering a standard error, and inverted the test: the threshold
        grew with the number of frames, so more data made a trend harder to detect and a ligand
        creeping from 0.15 to 0.40 nm over 1000 ns came back settled at every sampling rate.

        The slope's nominal t-statistic would be the textbook test and it is not used here on
        purpose. Trajectory frames are strongly serially correlated, so the effective sample size
        is a small fraction of the frame count and a t computed from the frames is inflated by a
        factor nobody here has estimated -- on these series it reaches 30 where the direct ratio
        reaches 8. A weaker test whose weakness is understood beats a sharper one whose
        significance is fictional.
        """

        return self.drift() <= NOT_SETTLED_RATIO * self.fluctuation()

    def retained_fraction(self) -> float | None:
        """Share of the scored pose's contacts still present, or ``None`` if none were recorded."""

        if not self.scored_contacts:
            return None
        present = sum(
            1 for name in self.scored_contacts if float(dict(self.contacts).get(name, 0.0)) >= CONTACT_PRESENT
        )
        return present / len(self.scored_contacts)


def from_prism(
    rmsd_result: Mapping[str, Any],
    contacts_result: Mapping[str, Any] | None = None,
    *,
    scored_contacts: Sequence[str] = (),
    replicas: int = 1,
    nanoseconds: float = 0.0,
) -> Trajectory:
    """Translate PRISM's analysis output into a :class:`Trajectory`.

    Raises:
        ValueError: If the RMSD result carries only summary statistics. PRISM's ``analyze_rmsd``
            returns the per-frame values alongside the mean; a caller that passes only the summary
            has discarded the thing the stability question is about, and accepting it would let a
            settled pose and a pose that left and returned produce the same verdict.
    """

    series = rmsd_result.get("values_nm") or rmsd_result.get("rmsd_nm") or rmsd_result.get("values")
    if not series:
        raise ValueError(
            "no per-frame RMSD in this result. PRISM's analyze_rmsd returns the series beside the "
            "statistics; a mean and a standard deviation cannot tell a settled pose from one that "
            "left the site and came back, which is the distinction this module exists to draw."
        )
    contacts: dict[str, float] = {}
    for entry in (contacts_result or {}).get("top_contacts", ()):
        if isinstance(entry, Mapping) and "residue" in entry:
            contacts[str(entry["residue"])] = float(entry.get("proportion", 0.0))
    return Trajectory(
        ligand_rmsd_nm=tuple(float(value) for value in series),
        contacts=contacts,
        scored_contacts=tuple(scored_contacts),
        replicas=replicas,
        nanoseconds=nanoseconds,
    )


def check_stability(trajectory: Trajectory) -> tuple[Observation, ...]:
    """Evaluate the three stability questions, and the one about replica count."""

    peak = max(trajectory.ligand_rmsd_nm)
    drift = trajectory.drift()
    retained = trajectory.retained_fraction()
    length = f"{trajectory.nanoseconds:g} ns" if trajectory.nanoseconds else f"{len(trajectory.ligand_rmsd_nm)} frames"

    observations = [
        Observation(
            "F_POSE_LEFT_THE_SITE",
            fired=peak > LEFT_THE_SITE_NM,
            detail=(
                f"peak ligand RMSD {peak:.2f} nm over {length}, against a {LEFT_THE_SITE_NM} nm "
                "cutoff"
                + (
                    f"; it ended at {trajectory.ligand_rmsd_nm[-1]:.2f} nm, so it may have left "
                    "and returned -- an RMSD is not a distance from the pocket"
                    if peak > LEFT_THE_SITE_NM and trajectory.ligand_rmsd_nm[-1] <= LEFT_THE_SITE_NM
                    else ""
                )
            ),
        ),
        Observation(
            "F_POSE_NOT_EQUILIBRATED",
            fired=not trajectory.settled(),
            detail=(
                f"ligand RMSD trended {drift:.3f} nm across the final third of {length} against a "
                f"residual fluctuation of {trajectory.fluctuation():.3f} nm over "
                f"{len(trajectory.final_third)} frames, so the trend is "
                # The ratio ``settled()`` actually tests, and nothing else. This sentence used to
                # divide by the square root of the frame count -- the same half-remembered standard
                # error the docstring of ``settled`` records having removed from the test itself,
                # left behind in the text. The flag was right and the sentence disagreed with it,
                # and it disagreed *more* the more frames there were: a trajectory at a true ratio
                # of 8 was reported as "0.2 times the noise" at 6000 frames. Nothing caught it
                # because the tests read drift() and fluctuation() and never read this string.
                f"{drift / trajectory.fluctuation():.1f} times the noise, against a "
                f"{NOT_SETTLED_RATIO:g} threshold"
            ),
        ),
    ]

    if retained is None:
        observations.append(
            Observation(
                "F_POSE_NOT_THE_SCORED_ONE",
                fired=False,
                detail=(
                    "the campaign recorded no contacts for the docked pose, so there is nothing to "
                    "compare the trajectory's against. The pose may have changed completely and "
                    "this check cannot see it."
                ),
                evaluable=False,
            )
        )
    else:
        observations.append(
            Observation(
                "F_POSE_NOT_THE_SCORED_ONE",
                fired=retained < CONTACT_PERSISTENCE,
                detail=(
                    f"{retained:.0%} of the docked pose's {len(trajectory.scored_contacts)} "
                    f"contacts persisted in at least {CONTACT_PRESENT:.0%} of frames"
                    + (
                        " -- the ligand is bound and the screen's number was about a different pose"
                        if retained < CONTACT_PERSISTENCE
                        else ""
                    )
                ),
            )
        )

    observations.append(
        Observation(
            "F_SINGLE_REPLICA_ESTIMATE",
            fired=trajectory.replicas < 2,
            detail=(
                f"{trajectory.replicas} replica(s). Neighbouring trajectories diverge "
                "exponentially, and replicas differing only in initial velocities have disagreed "
                "by up to 15 kcal/mol on one system, so this run is one draw rather than an "
                "estimate of a mean"
                if trajectory.replicas < 2
                else f"{trajectory.replicas} replicas, so the estimate is an ensemble average"
            ),
        )
    )
    return tuple(observations)


def necessary_not_sufficient() -> str:
    """What a single long run establishes, for a campaign to put in its record.

    Worth a function rather than a comment because it is the sentence most likely to be dropped
    when a result is summarised, and dropping it turns a screening filter into a claim about
    affinity.
    """

    return (
        "A single unbiased run is a necessary condition cheaply obtained, not a measurement. It "
        "can show that a pose does not survive, which is informative; it cannot show that one "
        "does, because the process is chaotic and one trajectory samples one instance of it. A "
        "pose that holds for 1000 ns is also not a tighter binder than one that holds for 200 -- "
        "residence in a pocket during an unbiased simulation is a validity filter and ranking on "
        "it confuses a filter with a predictor."
    )


__all__ = [
    "CONTACT_PERSISTENCE",
    "CONTACT_PRESENT",
    "FLUCTUATION_FLOOR_NM",
    "LEFT_THE_SITE_NM",
    "NOT_SETTLED_RATIO",
    "Trajectory",
    "check_stability",
    "from_prism",
    "necessary_not_sufficient",
]
