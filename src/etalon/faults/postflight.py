"""Read a finished run as observations, from what it left on disk.

The preflight half refuses before a spend and is the valuable half. This is the other
half, and its job is narrower: a run has happened, something is on disk, and the question
is what may be said about it.

Everything here reads a manifest and a captured log rather than a live object, which is a
deliberate choice with two consequences worth the cost. A campaign resuming days later has
only the files, so a checker that needed the Python objects could not audit its own
history. And ``etalon.faults`` stays independent of ``etalon.boundary``: the fault layer
describes what can be wrong with a number, and it should not need to know which adapter
produced it.

Three of the four observables here exist because PRISM tells itself to ignore them.

Every ``grompp`` in the driver carries ``-maxwarn 999``, so a non-integer total charge --
which means the ligand's charges do not sum to a whole number and no amount of
neutralisation can fix it -- is printed and built over. The protonation step logs residues
it could not map and completes normally, so a predicted state that was never applied leaves
no trace in any output file. And the driver's exit code, measured in ``findings/0002``,
reports 0 for a re-driven directory in which every equilibration stage failed.

None of that is repaired here. This module reads, and the reading is what the campaign's
admissibility rule then acts on.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from etalon.faults.attribution import Observation

#: PROPKA computed a pKa for a residue whose name PRISM's mapper does not recognise, so the
#: computed state was not applied. Matched on the logged line because nothing in any output
#: file records it.
#:
#: The whole descriptor is captured, not the first token after "Residue". The real line is
#: ``Residue A:1 N+: N+ (unmapped)``, where ``A:1`` is the position and ``N+`` is the label
#: that says it is a terminus -- capturing only the first token classified a terminus as an
#: interior residue, which inverts the one distinction this check exists to draw.
_UNMAPPED = re.compile(r"Residue\s+(.{1,60}?)\s*\(unmapped\)", re.IGNORECASE)

#: Residue labels that are almost always a chain terminus, where the force field's default
#: is what the predictor would have said anyway. Separated so a report can distinguish the
#: routine case from a mapped-away histidine near the site, which is not routine at all.
_TERMINAL_LABELS = ("N+", "C-", "NTER", "CTER")


def check_build(
    build: Mapping[str, Any],
    log: str = "",
) -> tuple[Observation, ...]:
    """Observations from one build, given its manifest section and its captured output.

    Args:
        build: The ``build`` section of an ETALON run manifest.
        log: The captured build output. Without it the protonation check cannot be made
            and is reported unevaluable rather than clean -- the unmapped-residue lines
            exist only in that log, so an absent log is an unasked question.
    """

    observations: list[Observation] = [
        Observation(
            "F_BUILD_INCOMPLETE",
            fired=not bool(build.get("built")),
            detail=(
                str(build.get("detail") or "no topology in the build output")
                if not build.get("built")
                else "topol.top and solv_ions.gro are both present"
            ),
        )
    ]

    if not log:
        observations.append(
            Observation(
                "F_PROTONATION_NOT_APPLIED",
                fired=False,
                detail=(
                    "no build log supplied, and the unmapped-residue lines exist nowhere "
                    "else -- the build's own output files record nothing about a predicted "
                    "state that was dropped"
                ),
                evaluable=False,
            )
        )
        return tuple(observations)

    unmapped = [match.strip() for match in _UNMAPPED.findall(log)]
    terminal = [name for name in unmapped if any(tag in name.upper() for tag in _TERMINAL_LABELS)]
    interior = [name for name in unmapped if name not in terminal]
    observations.append(
        Observation(
            "F_PROTONATION_NOT_APPLIED",
            fired=bool(unmapped),
            detail=(
                f"{len(unmapped)} residue(s) unmapped"
                + (f", {len(terminal)} of them a terminus ({', '.join(terminal[:3])})" if terminal else "")
                + (
                    f", and {len(interior)} not ({', '.join(interior[:3])}) -- those are the "
                    "ones worth deciding by hand"
                    if interior
                    else ""
                )
                if unmapped
                else "the protonation step mapped every residue it predicted"
            ),
        )
    )
    return tuple(observations)


def check_drive(
    drive: Mapping[str, Any],
    *,
    requested: Sequence[str] | None = None,
) -> tuple[Observation, ...]:
    """Observations from one driver run, given its manifest section.

    Args:
        requested: Override the stages the record says were required. A campaign that
            wanted equilibration is not failed by an unrun production stage, and the
            record already holds the answer; this exists for re-reading an old run under a
            stricter requirement than it was driven with.
    """

    wanted = tuple(requested if requested is not None else drive.get("requested") or ())
    stages = {
        str(entry.get("stage")): str(entry.get("state"))
        for entry in (drive.get("stages") or ())
    }
    missing = {name: stages.get(name, "NO_RECORD") for name in wanted if stages.get(name) != "FINISHED"}

    observations = [
        Observation(
            "F_STAGE_NEVER_RAN",
            fired=bool(missing),
            detail=(
                ", ".join(f"{name}={state}" for name, state in missing.items())
                + (
                    f" (the driver reported exit {drive.get('exit_code')}"
                    + (", timed out" if drive.get("timed_out") else "")
                    + ")"
                )
                if missing
                else f"every requested stage finished: {', '.join(wanted) or 'none requested'}"
            ),
        )
    ]

    warnings = drive.get("warnings") or {}
    by_kind = warnings.get("by_kind") or {}
    if "grompp_warnings" not in warnings:
        observations.append(
            Observation(
                "F_TOPOLOGY_CHARGE_NOT_INTEGER",
                fired=False,
                detail=(
                    "no captured driver output in the record, so grompp's warnings were "
                    "never read. PRISM passes -maxwarn 999, so grompp does not refuse a "
                    "fractional charge -- it prints it and builds."
                ),
                evaluable=False,
            )
        )
    else:
        hits = int(by_kind.get("non_integer_charge") or 0)
        observations.append(
            Observation(
                "F_TOPOLOGY_CHARGE_NOT_INTEGER",
                fired=hits > 0,
                detail=(
                    f"grompp reported a non-integer total charge {hits} time(s), and built "
                    "anyway because the driver passes -maxwarn 999"
                    if hits
                    else f"grompp raised {warnings.get('grompp_warnings', 0)} warning(s), "
                    "none about a non-integer charge"
                ),
            )
        )
    return tuple(observations)


def check_run(
    manifest: Mapping[str, Any],
    *,
    build_log: str = "",
    requested: Sequence[str] | None = None,
) -> tuple[Observation, ...]:
    """Every postflight observation for one run, from its manifest.

    A manifest with no ``drive`` section is a build that was never driven. The stage
    observable is reported unevaluable rather than fired: nothing ran, which is different
    from something having failed, and reporting a stage as failed when it was never
    requested would make a half-finished campaign look like a broken one.
    """

    build = manifest.get("build") or {}
    observations = list(check_build(build, build_log))
    drive = manifest.get("drive")
    if drive is None:
        observations.append(
            Observation(
                "F_STAGE_NEVER_RAN",
                fired=False,
                detail="this run was built and never driven, so no stage has failed yet",
                evaluable=False,
            )
        )
        observations.append(
            Observation(
                "F_TOPOLOGY_CHARGE_NOT_INTEGER",
                fired=False,
                detail="grompp has not run, so it has not said anything about the charge",
                evaluable=False,
            )
        )
        return tuple(observations)
    observations.extend(check_drive(drive, requested=requested))
    return tuple(observations)


def blocking(observations: Iterable[Observation]) -> tuple[Observation, ...]:
    """The postflight observations that mean the run's number is about something else.

    The same rule as the preflight layer's, and deliberately the same function body,
    because a run that failed its equilibration and a molecule handed over as a drawing are
    the same kind of problem: there is no divergence size at which either becomes a number
    about the molecule.
    """

    from etalon.faults.preflight import blocking as _blocking

    return _blocking(observations)


__all__ = ["check_build", "check_drive", "check_run", "blocking"]
