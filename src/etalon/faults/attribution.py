"""Given a divergence of a known size, say which causes could have produced it.

Two estimates of the same quantity disagree. The conventional response is to average
them, or to trust the more expensive one, or to list the six things that might have
gone wrong. This module does the thing the lists cannot: it uses the *size* of the
disagreement to refuse causes.

A cause is refused when its band cannot reach the observed residual. That is the
direction that carries information. Confirming a cause because its flag fired is weak
-- on a real molecule several flags fire at once, and six possibilities ranked by
nothing is not a diagnosis. Refusing a cause because the arithmetic does not reach is
strong, and it needs only numbers the contracts already carry.

The output is deliberately shaped to make three states distinguishable, because
collapsing them is how an attribution becomes an assertion.

``ATTRIBUTED`` -- exactly one cause both fired and can account for the magnitude. The
strongest verdict available here, and still a hypothesis rather than a proof.

``AMBIGUOUS`` -- several survive. The list is the answer; narrowing it needs an
observable nobody has yet, and saying so is more useful than picking the first.

``UNEXPLAINED`` -- no cause that fired can account for the magnitude, which is the
most interesting result and the one most likely to be suppressed by a system that
insists on answering. A 6 kcal/mol residual with only a 3 kcal/mol stereochemistry
flag means something is wrong that this taxonomy does not contain, and the correct
report says that rather than blaming stereochemistry.

One asymmetry is worth stating because it is a design decision rather than an
oversight. An unbounded cause is never eliminated by magnitude, so a campaign whose
only flags are unbounded ones learns nothing from the size of its divergence and has
to decide on the exact comparisons alone. That is reported, not hidden: a verdict that
names how much of its own reasoning came from magnitude is a verdict a reader can
weigh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from etalon.faults.taxonomy import BY_CODE, Exactness, Fault


class Verdict(StrEnum):
    ATTRIBUTED = "attributed"
    AMBIGUOUS = "ambiguous"
    UNEXPLAINED = "unexplained"
    #: Nothing fired at all. Distinct from UNEXPLAINED: there a cause was found and
    #: could not account for the size, here no cause was found. The divergence may
    #: still be real -- two methods disagreeing within their own error bars is not a
    #: fault -- and that is the first thing to check.
    NO_FAULT_OBSERVED = "no_fault_observed"


@dataclass(frozen=True, slots=True)
class Observation:
    """One fault's observable, evaluated. Carried even when it did not fire.

    A non-firing observation is not noise: it is how a reader knows the check was
    performed. An attribution that reports only what fired cannot be distinguished
    from one whose checks were never run.
    """

    code: str
    fired: bool
    detail: str
    #: ``None`` when the observable could not be evaluated -- the column is absent,
    #: the tool's diagnostic is unavailable, the comparison needs a file nobody kept.
    #: Neither true nor false, and the difference matters: an unevaluable check leaves
    #: its cause standing rather than clearing it.
    evaluable: bool = True


@dataclass(frozen=True, slots=True)
class Attribution:
    """What the size of a divergence, plus what fired, jointly allow."""

    divergence_kcal_mol: float
    verdict: Verdict
    #: Causes that fired and could account for the magnitude, most exact first.
    candidates: tuple[Fault, ...] = ()
    #: Causes that fired and were refused because their band cannot reach.
    eliminated_by_magnitude: tuple[Fault, ...] = ()
    #: Causes whose observable could not be evaluated. They are neither candidates
    #: nor cleared, and a verdict reached while these are outstanding is provisional.
    not_assessable: tuple[Fault, ...] = ()
    #: Causes that fired, can account for the magnitude, and whose own floor is above
    #: the observed divergence. Candidates, not eliminations -- a cause that can produce
    #: a large error can produce a small one -- but worth separating, because a cause
    #: present and barely expressing is a different situation from one expressing fully.
    below_their_floor: tuple[Fault, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: Every observation as it was evaluated, so the record carries the evidence for
    #: each finding and not only the finding. Dropping these once meant a reader could
    #: see that the receptor digests differed but never which two digests.
    observed: tuple[Observation, ...] = ()

    @property
    def magnitude_eliminated(self) -> bool:
        """Whether the size of the divergence actually refused a cause."""

        return bool(self.eliminated_by_magnitude)

    @property
    def magnitude_was_available(self) -> bool:
        """Whether any cause under consideration had a band at all.

        Distinct from :attr:`magnitude_eliminated`, and the distinction is the one a
        reader needs. A bounded cause that survived was still *checked* against the
        magnitude and found consistent, which is information. A set of causes that are
        all unbounded was not checked against anything, and then the verdict rests
        entirely on the exact comparisons -- which is worth saying out loud, because the
        two cases look identical in a list of candidates.
        """

        considered = self.candidates + self.eliminated_by_magnitude
        return any(fault.upper_kcal_mol is not None for fault in considered)

    def detail_for(self, code: str) -> str:
        """The evidence sentence recorded for one fault, or an empty string."""

        for entry in self.observed:
            if entry.code == code:
                return entry.detail
        return ""

    def as_dict(self) -> dict[str, object]:
        return {
            "divergence_kcal_mol": self.divergence_kcal_mol,
            "verdict": self.verdict.value,
            "magnitude_eliminated": self.magnitude_eliminated,
            "magnitude_was_available": self.magnitude_was_available,
            "candidates": [
                {**fault.as_dict(), "detail": self.detail_for(fault.code)}
                for fault in self.candidates
            ],
            "below_their_floor": [
                {
                    "code": fault.code,
                    "band_lower_kcal_mol": fault.lower_kcal_mol,
                    "detail": self.detail_for(fault.code),
                    "why": (
                        f"its floor is {fault.lower_kcal_mol} kcal/mol and the observed "
                        f"divergence is {abs(self.divergence_kcal_mol)}, so it is a "
                        "candidate that is barely expressing rather than the obvious "
                        "explanation"
                    ),
                }
                for fault in self.below_their_floor
            ],
            "eliminated_by_magnitude": [
                {
                    "code": fault.code,
                    "band_upper_kcal_mol": fault.upper_kcal_mol,
                    "detail": self.detail_for(fault.code),
                    "why": (
                        f"its band tops out at {fault.upper_kcal_mol} kcal/mol, which "
                        f"cannot reach the observed {abs(self.divergence_kcal_mol)}"
                    ),
                }
                for fault in self.eliminated_by_magnitude
            ],
            "not_assessable": [
                {
                    "code": fault.code,
                    "observable": fault.observable,
                    "detail": self.detail_for(fault.code),
                }
                for fault in self.not_assessable
            ],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        return "\n".join(_render_lines(self))


#: Exact comparisons rank above thresholds, thresholds above derived diagnostics. Not
#: a confidence score: an ordering, so that a reader looking at an ambiguous list sees
#: the decidable causes first.
_EXACTNESS_RANK = {Exactness.EXACT: 0, Exactness.THRESHOLD: 1, Exactness.DERIVED: 2}


def attribute(
    divergence_kcal_mol: float,
    observations: tuple[Observation, ...],
) -> Attribution:
    """Rule on a divergence, eliminating by magnitude where the arithmetic allows.

    ``divergence_kcal_mol`` is the residual between two estimates of the same
    quantity -- a docking-derived estimate against a free energy, or a free energy
    against an experiment. Its sign is kept in the record and ignored in the
    arithmetic: a band describes a magnitude, and a cause that can shift an estimate
    by 3 kcal/mol can shift it in either direction.
    """

    unknown = [entry.code for entry in observations if entry.code not in BY_CODE]
    if unknown:
        raise KeyError(f"observation names a fault not in the taxonomy: {sorted(unknown)}")

    fired: list[Fault] = []
    unevaluable: list[Fault] = []
    for entry in observations:
        fault = BY_CODE[entry.code]
        if not entry.evaluable:
            unevaluable.append(fault)
        elif entry.fired:
            fired.append(fault)

    candidates = [fault for fault in fired if fault.can_account_for(divergence_kcal_mol)]
    eliminated = [fault for fault in fired if not fault.can_account_for(divergence_kcal_mol)]
    # A floor is not an elimination -- see Fault.can_account_for -- but a cause whose
    # own floor sits above the observed divergence is present and barely expressing,
    # which reads differently from one expressing fully. Separating them is the only
    # use this catalogue makes of the lower bound, and without it the field is decoration.
    below_floor = [
        fault
        for fault in candidates
        if fault.lower_kcal_mol > 0.0 and abs(divergence_kcal_mol) < fault.lower_kcal_mol
    ]
    candidates.sort(key=lambda fault: (_EXACTNESS_RANK[fault.exactness], fault.code))
    below_floor.sort(key=lambda fault: fault.code)
    eliminated.sort(key=lambda fault: fault.code)
    unevaluable.sort(key=lambda fault: fault.code)

    notes: list[str] = []
    if not fired and not unevaluable:
        verdict = Verdict.NO_FAULT_OBSERVED
        notes.append(
            "No fault observable fired. Before treating the divergence as a defect, "
            "check whether the two estimates disagree within their own reported "
            "uncertainties -- two methods differing by less than their error bars is "
            "not a fault."
        )
    elif not candidates:
        verdict = Verdict.UNEXPLAINED
        if eliminated:
            widest = max(
                (fault.upper_kcal_mol or 0.0) for fault in eliminated
            )
            notes.append(
                f"Every cause that fired was refused by magnitude: the widest band "
                f"among them reaches {widest} kcal/mol and the observed divergence is "
                f"{abs(divergence_kcal_mol)}. Something outside this taxonomy is "
                "acting, and naming one of the refused causes anyway would be the "
                "wrong answer confidently given."
            )
        else:
            notes.append(
                "No cause could be evaluated, so nothing has been ruled in or out."
            )
    elif len(candidates) == 1:
        verdict = Verdict.ATTRIBUTED
    else:
        verdict = Verdict.AMBIGUOUS
        notes.append(
            f"{len(candidates)} causes survive both their observable and the "
            "magnitude. Narrowing further needs an observable that distinguishes "
            "them, and the list is the honest answer until one exists."
        )

    if below_floor and len(below_floor) == len(candidates):
        notes.append(
            f"Every surviving cause has a floor above the observed "
            f"{abs(divergence_kcal_mol)} kcal/mol: each is a cause that would normally "
            "show up larger than this. That is not a reason to dismiss them, but it is a "
            "reason to check the divergence itself before acting on the attribution."
        )
    if unevaluable:
        notes.append(
            f"{len(unevaluable)} cause(s) could not be assessed, so they are neither "
            "candidates nor cleared. Any verdict above is provisional on them."
        )
    if fired and all(fault.upper_kcal_mol is None for fault in fired):
        notes.append(
            "No cause that fired carries a band, so the size of the divergence could "
            "not narrow anything and this verdict rests entirely on the exact "
            "comparisons. That is a property of these causes rather than of this "
            "divergence: most of them mean the estimate is about the wrong thing "
            "rather than wrong by an amount."
        )
    return Attribution(
        divergence_kcal_mol=divergence_kcal_mol,
        verdict=verdict,
        candidates=tuple(candidates),
        eliminated_by_magnitude=tuple(eliminated),
        not_assessable=tuple(unevaluable),
        below_their_floor=tuple(below_floor),
        notes=tuple(notes),
        observed=tuple(observations),
    )


def _render_lines(attribution: Attribution) -> list[str]:
    headline = (
        f"Divergence {attribution.divergence_kcal_mol:+.2f} kcal/mol — "
        f"{attribution.verdict.value.upper()}"
    )
    lines = [headline]
    if attribution.candidates:
        lines.append("")
        lines.append("  could account for it:")
        for fault in attribution.candidates:
            upper = "unbounded" if fault.upper_kcal_mol is None else f"{fault.upper_kcal_mol}"
            lines.append(
                f"    {fault.code:<34}{fault.exactness.value:<11}"
                f"band {fault.lower_kcal_mol}–{upper}"
            )
            lines.append(f"      {fault.summary}")
            detail = attribution.detail_for(fault.code)
            if detail:
                lines.append(f"      observed: {detail}")
    if attribution.eliminated_by_magnitude:
        lines.append("")
        lines.append("  refused — the band cannot reach:")
        for fault in attribution.eliminated_by_magnitude:
            lines.append(
                f"    {fault.code:<34}tops out at {fault.upper_kcal_mol} kcal/mol"
            )
            detail = attribution.detail_for(fault.code)
            if detail:
                lines.append(f"      observed: {detail}")
    if attribution.not_assessable:
        lines.append("")
        lines.append("  could not be assessed:")
        for fault in attribution.not_assessable:
            lines.append(f"    {fault.code:<34}{fault.observable}")
    for note in attribution.notes:
        lines.append("")
        lines.append(f"  note: {note}")
    return lines


__all__ = ["Attribution", "Observation", "Verdict", "attribute"]
