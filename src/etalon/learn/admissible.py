"""Which measurements are allowed to teach the screen anything.

Putting machine learning inside a CADD loop is old and well done. DeepDriveMD drives
adaptive MD from a learned latent space; IMPECCABLE runs surrogate, docking, MD and
binding free energy as one campaign with the surrogate retrained from downstream results;
Colmena steers ensembles of simulations from a thinker process. None of what follows
claims novelty over any of that, and an earlier draft of this project did claim it and
was wrong.

What those loops share is an assumption, stated nowhere because it is too obvious to
state: that a number arriving from the expensive stage is a measurement of the molecule
it is filed under. That assumption is false in a way that has been measured here. The
shortlist exporter rebuilds geometry from SMILES -- 19 heavy atoms, zero explicit
hydrogens, every z exactly 0.00 -- and PRISM's ligand validator accepts it, because it
checks existence, size, suffix and a positive atom count. Across 41 gaff2 builds from
hydrogen-free input the topology carried zero hydrogens in 41 of 41, with no warning at
any stage -- that last count measured in the vendored PRISM rather than by ETALON, and
recorded at ``asset/prism/prism/generation/handoff.py``; the exporter's own output above
is what was measured here. A loop without a gate here does not merely record one bad number: it trains
on a label generated from a molecule that was never simulated, and that label then moves
the thresholds applied to every molecule afterwards.

So this module answers one question per measurement -- *may this update the screen* --
and the asymmetry in the answer is the entire argument. Withholding a good measurement
costs one molecule's worth of information. Admitting a bad one costs a shift in the
policy applied to all of them. Those are not comparable, so the gate is deliberately
biased toward withholding, and every withholding is recorded with its reason so the
bias is auditable rather than invisible.

Three rules follow from the fault layer and one from honesty.

A ``WRONG_SUBJECT`` fault that fired, and is not waived, withholds the measurement. There
is no divergence size at which a number about a different species becomes a label about
this one.

An ``UNVERIFIABLE`` fault does not withhold. The number may be perfectly good; what is
missing is a way to check a claim about it. The measurement is admitted and the round is
marked as not reproducible, which is a fact about the campaign rather than about the
molecule.

A waived fault admits the measurement and keeps the cause standing. A waiver is evidence,
not an erasure: it rides along in the record and reappears as a candidate if this round's
numbers later disagree with something. That is precisely what disabling a check destroys.

And the honest part, which is the admission rate itself. A loop learning only from clean
measurements is learning from a biased sample -- the molecules that survive preflight are
not a random draw from the pool, and on a congeneric series they are systematically the
ones whose geometry was easy. The report says so when the rate is low, because a
selection effect that nobody writes down becomes a property of the model nobody can
find.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum

from etalon.faults.attribution import Observation
from etalon.faults.taxonomy import BY_CODE, Consequence
from etalon.judgment.waiver import WaiverSet

#: Below this fraction of measurements admitted, the surviving set is reported as a
#: selection rather than a sample. A convention, not a measurement: two thirds is the
#: point at which a withheld third can plausibly carry a systematic difference rather
#: than noise, and nothing here has measured where the real boundary is.
_SELECTION_WARNING_BELOW = 2 / 3


class Admission(StrEnum):
    ADMITTED = "admitted"
    #: Admitted because a named person accepted a named risk, for a stated reason.
    ADMITTED_UNDER_WAIVER = "admitted_under_waiver"
    #: Not allowed to teach anything. The measurement is still recorded.
    WITHHELD = "withheld"


@dataclass(frozen=True, slots=True)
class Measurement:
    """One molecule measured twice: cheaply by the screen, expensively by simulation."""

    parent_id: str
    #: The screen's number -- a docking score, a predicted affinity. The thing being
    #: calibrated.
    cheap_value: float | None
    #: The expensive number, in kcal/mol. The thing being calibrated against.
    expensive_value: float | None
    #: What the preflight and postflight checks saw. Non-firing observations included:
    #: that is how a reader knows the checks ran.
    observations: tuple[Observation, ...] = ()
    units: str = "kcal/mol"
    #: Infra commits, run ids, revision id. Carried so a ruling can be re-derived.
    provenance: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        """Keep the labels and evidence, including invalid numeric results, in the journal."""

        record = asdict(self)
        for name in ("cheap_value", "expensive_value"):
            value = record[name]
            if value is not None and not math.isfinite(value):
                record[name] = str(value)
        return record


@dataclass(frozen=True, slots=True)
class Ruling:
    """Whether one measurement may teach, and everything that decided it."""

    parent_id: str
    admission: Admission
    #: Fired, unwaived, and about the wrong subject. Why this was withheld.
    blocking: tuple[str, ...] = ()
    #: Fired, about the wrong subject, and accepted on the record. Still standing.
    waived: tuple[str, ...] = ()
    #: Fired and bounded: the number is about the right thing and may be off by the band.
    bounded: tuple[str, ...] = ()
    #: Fired and unverifiable: no claim about this run can be checked.
    unverifiable: tuple[str, ...] = ()
    #: Could not be evaluated. Neither clean nor dirty, and not a pass.
    unchecked: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def teaches(self) -> bool:
        return self.admission is not Admission.WITHHELD

    @property
    def reproducible(self) -> bool:
        """Whether a claim about this measurement can be checked by re-running it."""

        return not self.unverifiable

    def as_dict(self) -> dict[str, object]:
        return {
            "parent_id": self.parent_id,
            "admission": self.admission.value,
            "teaches": self.teaches,
            "reproducible": self.reproducible,
            "blocking": list(self.blocking),
            "waived": list(self.waived),
            "bounded": list(self.bounded),
            "unverifiable": list(self.unverifiable),
            "unchecked": list(self.unchecked),
            "notes": list(self.notes),
        }


def rule(
    measurement: Measurement,
    waivers: WaiverSet | None = None,
    *,
    when: date | None = None,
    require_comparator: bool = True,
) -> Ruling:
    """Decide whether one measurement may update the screen."""

    active = (waivers or WaiverSet()).codes(when)
    blocking: list[str] = []
    waived: list[str] = []
    bounded: list[str] = []
    unverifiable: list[str] = []
    unchecked: list[str] = []
    notes: list[str] = []

    for name in ("cheap_value", "expensive_value"):
        value = getattr(measurement, name)
        if value is not None and not math.isfinite(value):
            blocking.append(f"NONFINITE_{name.upper()}")

    for entry in measurement.observations:
        fault = BY_CODE.get(entry.code)
        if fault is None:
            # Refused rather than ignored: an unknown code means the caller and this
            # layer disagree about what was checked, and guessing would hide that.
            raise KeyError(
                f"observation names a fault not in the taxonomy: {entry.code!r}"
            )
        if not entry.evaluable:
            unchecked.append(entry.code)
            continue
        if not entry.fired:
            continue
        if fault.consequence is Consequence.UNVERIFIABLE:
            unverifiable.append(entry.code)
        elif fault.consequence is Consequence.WRONG_SIZE:
            bounded.append(entry.code)
        elif entry.code in active:
            waived.append(entry.code)
        else:
            blocking.append(entry.code)

    if measurement.expensive_value is None:
        blocking.append("NO_EXPENSIVE_VALUE")
        notes.append(
            "No expensive number, so there is nothing for the screen to learn from. "
            "Recorded rather than dropped: a molecule that was sent for simulation and "
            "came back without a result is a fact about the campaign."
        )
    if require_comparator and measurement.cheap_value is None:
        blocking.append("NO_CHEAP_VALUE")
        notes.append(
            "No screen number to calibrate against. The expensive measurement stands on "
            "its own but cannot tell the screen it was wrong about anything."
        )

    if blocking:
        admission = Admission.WITHHELD
        real = [code for code in blocking if code in BY_CODE]
        if real:
            notes.append(
                "Withheld: "
                + ", ".join(real)
                + ". These mean the number would be about something other than the "
                "molecule it is filed under, so using it as a label would move the "
                "screen's thresholds on evidence from a different species. Withholding "
                "costs one molecule's information; admitting costs a shift applied to "
                "every molecule after it."
            )
    elif waived:
        admission = Admission.ADMITTED_UNDER_WAIVER
        notes.append(
            "Admitted under waiver for "
            + ", ".join(waived)
            + ". The cause is not cleared -- it stays a standing candidate, so a later "
            "divergence this taxonomy cannot otherwise explain will name it with the "
            "reason somebody gave for accepting it."
        )
    else:
        admission = Admission.ADMITTED

    if unverifiable:
        notes.append(
            "Admitted but not reproducible ("
            + ", ".join(unverifiable)
            + "): the number may be right and no re-run will confirm it. This qualifies "
            "the claim, not the molecule."
        )
    if unchecked:
        notes.append(
            f"{len(unchecked)} check(s) could not be evaluated, so this measurement is "
            "clean only as far as anyone looked."
        )
    return Ruling(
        parent_id=measurement.parent_id,
        admission=admission,
        blocking=tuple(blocking),
        waived=tuple(waived),
        bounded=tuple(bounded),
        unverifiable=tuple(unverifiable),
        unchecked=tuple(unchecked),
        notes=tuple(notes),
    )


@dataclass(frozen=True, slots=True)
class AdmissionReport:
    """What a round's measurements are collectively allowed to teach."""

    rulings: tuple[Ruling, ...]
    notes: tuple[str, ...] = ()

    @property
    def admitted(self) -> tuple[Ruling, ...]:
        return tuple(ruling for ruling in self.rulings if ruling.teaches)

    @property
    def withheld(self) -> tuple[Ruling, ...]:
        return tuple(ruling for ruling in self.rulings if not ruling.teaches)

    @property
    def rate(self) -> float:
        return 0.0 if not self.rulings else len(self.admitted) / len(self.rulings)

    @property
    def reproducible(self) -> bool:
        """Whether every admitted measurement can be checked by re-running it."""

        return all(ruling.reproducible for ruling in self.admitted)

    def reasons(self) -> dict[str, int]:
        """Withholding reasons by count, so a campaign can see what is costing it."""

        counts: dict[str, int] = {}
        for ruling in self.withheld:
            for code in ruling.blocking:
                counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def as_dict(self) -> dict[str, object]:
        return {
            "measured": len(self.rulings),
            "admitted": len(self.admitted),
            "withheld": len(self.withheld),
            "admission_rate": round(self.rate, 4),
            "all_admitted_are_reproducible": self.reproducible,
            "withholding_reasons": self.reasons(),
            "rulings": [ruling.as_dict() for ruling in self.rulings],
            "notes": list(self.notes),
        }


def admissible(
    measurements: Iterable[Measurement],
    waivers: WaiverSet | None = None,
    *,
    when: date | None = None,
) -> AdmissionReport:
    """Rule on a round's measurements, and say what the surviving set is.

    The note about a low admission rate is the part worth reading. A loop that learns
    only from measurements it could verify is learning from a selected subpopulation, and
    on a congeneric series the selection is not neutral -- the molecules whose geometry
    was easy are over-represented, and those are not the ones a screen is getting wrong.
    Saying so does not fix it. Not saying so turns it into a property of the model that
    nobody can find afterwards.
    """

    rulings = tuple(rule(measurement, waivers, when=when) for measurement in measurements)
    notes: list[str] = []
    if not rulings:
        notes.append("No measurements, so the screen learns nothing this round.")
    else:
        admitted = [ruling for ruling in rulings if ruling.teaches]
        rate = len(admitted) / len(rulings)
        if rate < _SELECTION_WARNING_BELOW:
            notes.append(
                f"Only {len(admitted)} of {len(rulings)} measurements are admissible "
                f"({rate:.0%}). What survives is a selection rather than a sample: the "
                "molecules that pass preflight are not a random draw, and on a "
                "congeneric series they are systematically the ones whose geometry was "
                "easy -- which are not the ones a screen is getting wrong. Treat an "
                "update fitted on this set as provisional, and read the withholding "
                "reasons before accepting it."
            )
        if admitted and not all(ruling.reproducible for ruling in admitted):
            count = sum(1 for ruling in admitted if not ruling.reproducible)
            notes.append(
                f"{count} admitted measurement(s) cannot be reproduced. The update they "
                "support may be sound; the claim that it can be re-derived is not."
            )
        waived_codes = sorted({code for ruling in admitted for code in ruling.waived})
        if waived_codes:
            notes.append(
                "Waivers in force for this round: "
                + ", ".join(waived_codes)
                + ". Every update fitted on these measurements inherits them."
            )
    return AdmissionReport(rulings=rulings, notes=tuple(notes))


def teachable(report: AdmissionReport, measurements: Sequence[Measurement]) -> list[Measurement]:
    """The measurements the report admits, in input order."""

    allowed = {ruling.parent_id for ruling in report.admitted}
    return [m for m in measurements if m.parent_id in allowed]


__all__ = [
    "Admission",
    "AdmissionReport",
    "Measurement",
    "Ruling",
    "admissible",
    "rule",
    "teachable",
]
