"""Evaluate the preflight observables, before a GPU-second is spent.

This is the cheap half of the fault layer and the valuable half. Every check here
reads a column of a handoff record or the state of the toolchain, so the whole pass
costs microseconds per molecule; and a campaign that refuses eleven molecules here has
saved eleven times three GPU-days and lost nothing, because the numbers those runs
would have produced would have been about something other than the molecule.

Two scopes, and keeping them apart matters.

A **per-record** check reads one ``md_system_input/v1`` row. Most of the taxonomy is
here, because the handoff contract was designed so that the facts a simulation stack
would otherwise assume are columns a producer had to fill.

A **population** check is about a record that is not there. ``F_HANDOFF_ABSENT`` cannot
be evaluated from a row by definition, so it is evaluated against the set of molecules
the caller expected. This is the check that fails closed, and it exists because a
missing row reads downstream as a molecule that was fine.

One rule throughout: a check that cannot be performed returns an observation marked
unevaluable rather than a ``False``. The difference is the whole reason the attribution
layer has three outcomes instead of two. A ``False`` says "this cause is cleared"; an
unevaluable observation says "this cause is still standing and I could not look", and
a verdict reached over one of those is provisional. Silently turning the second into
the first is how an audit becomes a reassurance.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from etalon.faults.attribution import Observation

#: Coordinate sources that are real coordinates. The other two enum values --
#: ``TWO_D_DEPICTION`` and ``NONE`` -- are legal in the contract precisely so a
#: producer can admit to them, and are exactly what this layer refuses.
_REAL_COORDINATES = frozenset({"DOCKED_POSE", "EMBEDDED_CONFORMER"})

#: Hydrogen states a force-field build can use. ``POLAR_ONLY`` is included because some
#: scoring functions expect exactly that; a caller whose consumer is a force field
#: should narrow it.
_USABLE_HYDROGENS = frozenset({"EXPLICIT_ALL", "POLAR_ONLY"})

#: What a producer writes when nothing computed a protonation state. Treated as the
#: fault firing rather than as a missing value, because it is an honest answer to a
#: question nobody asked: the state being simulated is whatever the standardizer left.
_UNDECIDED_PROTONATION = "INHERITED_FROM_STANDARDIZER"


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_record(
    row: Mapping[str, Any],
    *,
    receptor_path: Path | None = None,
    toolchain_active: bool | None = None,
) -> tuple[Observation, ...]:
    """Evaluate every per-record preflight observable against one handoff row.

    Args:
        row: One ``md_system_input/v1`` record, as a mapping.
        receptor_path: The receptor file the build will actually use. Supplying it
            turns ``F_RECEPTOR_NOT_THE_ONE_SCORED`` from an unevaluable check into a
            digest comparison, which is the only form in which that fault can be ruled
            out. Omitting it leaves the cause standing rather than cleared.
        toolchain_active: Whether ETALON's seed shim is on the path of the process
            that will run the build. ``None`` means the caller does not know, which is
            reported as unevaluable rather than assumed either way.

    Returns:
        One observation per per-record fault, in the order the taxonomy lists them.
        Observations that did not fire are returned too: that is how a reader knows
        the check ran.
    """

    status = str(row.get("status") or "")
    source = str(row.get("coordinate_source") or "")
    hydrogens = str(row.get("hydrogens") or "")
    hydrogen_count = row.get("hydrogen_count")
    receptor_id = row.get("receptor_id")
    stereo = row.get("stereo_smiles")
    parent = row.get("parent_smiles")
    protonation = str(row.get("protonation_state_id") or "")
    charge = row.get("formal_charge")

    observations: list[Observation] = [
        Observation(
            "F_STRUCTURE_UNREADABLE",
            fired=status != "OK",
            detail=(
                f"status={status!r}"
                + (f", {row['status_detail']}" if row.get("status_detail") else "")
            ),
        ),
        Observation(
            "F_COORDINATES_ARE_A_DEPICTION",
            fired=source == "TWO_D_DEPICTION",
            detail=f"coordinate_source={source!r}",
        ),
        Observation(
            "F_HYDROGENS_IMPLICIT",
            fired=hydrogens not in _USABLE_HYDROGENS,
            detail=f"hydrogens={hydrogens!r}, hydrogen_count={hydrogen_count!r}",
        ),
        Observation(
            "F_POSE_WITHOUT_RECEPTOR",
            fired=source == "DOCKED_POSE" and not receptor_id,
            detail=(
                f"coordinate_source={source!r}, receptor_id="
                f"{'absent' if not receptor_id else str(receptor_id)[:32] + '…'}"
            ),
        ),
    ]

    # The receptor comparison. Three states rather than two, because "I could not
    # check" and "it matches" must not look alike.
    if source != "DOCKED_POSE":
        observations.append(
            Observation(
                "F_RECEPTOR_NOT_THE_ONE_SCORED",
                fired=False,
                detail=(
                    "not applicable: these coordinates are not a pose, so there is no "
                    "receptor they were scored against"
                ),
            )
        )
    elif receptor_path is None or not receptor_id:
        observations.append(
            Observation(
                "F_RECEPTOR_NOT_THE_ONE_SCORED",
                fired=False,
                detail=(
                    "no receptor file supplied to compare against"
                    if receptor_path is None
                    else "the record names no receptor_id to compare"
                ),
                evaluable=False,
            )
        )
    else:
        # The digest is recomputed rather than trusted from anywhere: a receptor that
        # was re-protonated between docking and the build is the case this exists for,
        # and the only way to see it is to hash the file about to be used.
        try:
            observed = _digest(receptor_path)
        except OSError as error:
            observations.append(
                Observation(
                    "F_RECEPTOR_NOT_THE_ONE_SCORED",
                    fired=False,
                    detail=f"could not read {receptor_path}: {type(error).__name__}",
                    evaluable=False,
                )
            )
        else:
            recorded = str(receptor_id)
            # receptor_id is a prefixed digest; compare on the hex tail so the
            # comparison survives a change of prefix convention.
            matches = recorded.rsplit(":", 1)[-1] == observed
            observations.append(
                Observation(
                    "F_RECEPTOR_NOT_THE_ONE_SCORED",
                    fired=not matches,
                    detail=(
                        f"recorded {recorded[-16:]} against {observed[-16:]} of "
                        f"{receptor_path.name}"
                    ),
                )
            )

    observations.append(
        Observation(
            "F_PROTONATION_UNDECIDED",
            fired=protonation == _UNDECIDED_PROTONATION,
            detail=(
                f"protonation_state_id={protonation!r}, formal_charge={charge!r}"
                + (
                    "; a charged ligand whose state nobody decided is the expensive case"
                    if protonation == _UNDECIDED_PROTONATION and charge not in (0, None)
                    else ""
                )
            ),
        )
    )

    # Stereochemistry. Both strings are in the record so the comparison needs nothing
    # else, but a null on either side means it cannot be made.
    if not stereo or not parent:
        observations.append(
            Observation(
                "F_STEREO_CHOSEN_BY_EMBEDDING",
                fired=False,
                detail=(
                    "stereo_smiles is null, so what was built cannot be compared with "
                    "what it is filed under"
                ),
                evaluable=False,
            )
        )
    else:
        differs = str(stereo) != str(parent)
        observations.append(
            Observation(
                "F_STEREO_CHOSEN_BY_EMBEDDING",
                fired=differs,
                detail=(
                    f"built {str(stereo)[:56]} against name {str(parent)[:56]}"
                    if differs
                    else "the built configuration is the one the name designates"
                ),
            )
        )

    if toolchain_active is None:
        observations.append(
            Observation(
                "F_RUN_NOT_REPRODUCIBLE",
                fired=False,
                detail="the caller did not say whether the seed shim will be active",
                evaluable=False,
            )
        )
    else:
        observations.append(
            Observation(
                "F_RUN_NOT_REPRODUCIBLE",
                fired=not toolchain_active,
                detail=(
                    "the seed shim is on the path, so ion placement and velocities are "
                    "seeded and recorded"
                    if toolchain_active
                    else "no seed shim: genion and velocity generation will draw from "
                    "the clock, and no re-run will reproduce this one"
                ),
            )
        )

    # Declared unevaluable rather than omitted. The mapping quality of a relative FEP
    # edge is a property of a pair, and this function sees one record; a caller that
    # never supplies it should see the cause standing rather than absent.
    observations.append(
        Observation(
            "F_FEP_MAPPING_DEGENERATE",
            fired=False,
            detail=(
                "a mapping is a property of an edge between two molecules, and this "
                "check saw one record"
            ),
            evaluable=False,
        )
    )
    return tuple(observations)


def check_population(
    expected_parent_ids: Iterable[str],
    records: Mapping[str, Mapping[str, Any]],
) -> tuple[Observation, ...]:
    """Evaluate the one fault that is about a record's absence.

    Args:
        expected_parent_ids: The molecules the caller believes are being handed over.
        records: Handoff rows keyed by ``parent_id``.

    Returns:
        A single observation. It fires when any expected molecule has no record,
        because a molecule nothing has spoken about has not been shown to be simulable
        and a missing row reads downstream as a molecule that was fine.
    """

    expected = list(expected_parent_ids)
    missing = sorted(set(expected) - set(records))
    shown = ", ".join(identifier[:24] for identifier in missing[:3])
    return (
        Observation(
            "F_HANDOFF_ABSENT",
            fired=bool(missing),
            detail=(
                f"{len(missing)} of {len(expected)} expected molecules have no handoff "
                f"record" + (f" (e.g. {shown})" if shown else "")
                if missing
                else f"all {len(expected)} expected molecules have a record"
            ),
        ),
    )


def blocking(
    observations: Iterable[Observation],
    *,
    waived: Iterable[str] | None = None,
) -> tuple[Observation, ...]:
    """The observations that should stop a spend, as opposed to annotate one.

    The rule is the fault's consequence, not its band. That distinction was learned the
    hard way: this function first read "fired and unbounded", which is wrong and wrong in
    a direction that costs real work. ``F_RUN_NOT_REPRODUCIBLE`` is unbounded -- no band
    describes it -- and firing it means nobody can reproduce the run, which is a reason
    to refuse a *claim* and not a reason to refuse a molecule. Under the old rule every
    molecule in an unseeded campaign was blocked, which is both useless and the kind of
    uselessness that gets a checker switched off.

    So: ``WRONG_SUBJECT`` blocks, because the number would be about something else and
    no divergence size redeems that. ``WRONG_SIZE`` annotates, because how much error is
    tolerable belongs to the operator. ``UNVERIFIABLE`` annotates here and is surfaced
    by :func:`unverifiable` instead, where it belongs.

    ``waived`` releases named codes, and it has to exist here rather than only where a
    measurement becomes evidence. That was found by running the loop. A waiver granted
    for an undecided protonation state let such a measurement teach -- and the molecule
    was refused before any measurement existed, so the waiver released something that
    could never happen. A waiver that does not reach the decision to spend is not a
    waiver.

    What it does not do is hide anything. The observation still fired and is still in the
    record, and :func:`waived_blocking` returns exactly the causes a waiver released, so
    a round can say what it accepted rather than only how many molecules proceeded.
    """

    from etalon.faults.taxonomy import BY_CODE, Consequence

    released = frozenset(waived or ())
    return tuple(
        entry
        for entry in observations
        if entry.evaluable
        and entry.fired
        and entry.code not in released
        and BY_CODE[entry.code].consequence is Consequence.WRONG_SUBJECT
    )


def waived_blocking(
    observations: Iterable[Observation],
    waived: Iterable[str],
) -> tuple[Observation, ...]:
    """The causes that would have blocked and were released by a waiver.

    Separate from :func:`blocking` so "nothing blocked" and "three things blocked and
    were accepted" cannot be reported as the same sentence. A campaign that spends on a
    molecule under a waiver owes the record an account of what it accepted.
    """

    from etalon.faults.taxonomy import BY_CODE, Consequence

    released = frozenset(waived)
    return tuple(
        entry
        for entry in observations
        if entry.evaluable
        and entry.fired
        and entry.code in released
        and BY_CODE[entry.code].consequence is Consequence.WRONG_SUBJECT
    )


def unverifiable(observations: Iterable[Observation]) -> tuple[Observation, ...]:
    """The observations that invalidate a claim about the run rather than the run.

    Kept separate from :func:`blocking` so that the two cannot be conflated again. A
    campaign with these firing may produce perfectly good numbers; what it must not do
    is describe them as reproducible, or publish a convergence diagnostic it could not
    compute. The correct response is to qualify the result, not to refuse the spend.
    """

    from etalon.faults.taxonomy import BY_CODE, Consequence

    return tuple(
        entry
        for entry in observations
        if entry.evaluable
        and entry.fired
        and BY_CODE[entry.code].consequence is Consequence.UNVERIFIABLE
    )


def unchecked(observations: Iterable[Observation]) -> tuple[Observation, ...]:
    """The observations that could not be evaluated, which is not the same as clean.

    Worth a function of its own because the reporting mistake is so easy: a summary that
    prints "0 blocking" over a record where four checks could not run has told the
    operator the opposite of the truth. Every caller that reports a pass should report
    this count beside it.
    """

    return tuple(entry for entry in observations if not entry.evaluable)


__all__ = [
    "blocking",
    "check_population",
    "check_record",
    "unchecked",
    "unverifiable",
    "waived_blocking",
]
