"""A seat on the council, and the refusal that stops a council being one advisor in a wig.

This module exists because of a sentence already in this repository. ``tuning/knob.py`` carries the
published precondition for consensus *scoring*:

    Each member performs relatively well on its own AND the members are appropriately diverse.
    ... A member whose AUC is near chance contributes noise; two members correlating above about
    0.9 with each other contribute one opinion at two prices.

That is a claim about combining opinions. Nothing in it is about docking scores specifically. It is
the condition under which pooling several judgements beats taking one, and the GPCR-Bench result --
MM/GBSA-containing combinations improving only 32% and 19% of combinations -- is what it looks like
when the condition does not hold. ``tuning/advise.py`` refuses to recommend the consensus knob until
an operator has established that condition on their own panel.

Every published multi-agent system this project could find adds agents on the opposite assumption:
that more opinions are better, full stop. Supervisor-and-worker hierarchies, role pipelines,
debate, tournaments -- none of them measures whether its panel beats one member of it. Applying a
screening precondition to scoring functions and not to the agents scoring them is not a principled
distinction; it is where the analogy was not carried through.

So the council carries it through, and this module holds the half of the precondition that can be
checked before anything runs.

**Diversity is declared as evidence, not as a prompt.** A seat says which parts of the record it is
allowed to see. Two seats reading the same evidence through different personas are one opinion at
two prices however different their instructions sound, because the thing a persona cannot change is
what is in front of it. :func:`charter` refuses that arrangement at construction, which is cheaper
and more reliable than discovering it later from correlated votes -- though
:mod:`etalon.council.reliability` measures the votes too, because a declared difference in evidence
is necessary and not sufficient.

The other half of the precondition -- that each member is individually better than chance -- cannot
be checked from a declaration and is measured against labelled adjudications. See
:mod:`etalon.council.reliability`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Evidence(StrEnum):
    """A kind of thing a seat may be shown.

    The partition is by *provenance of the evidence*, not by topic, because that is what makes two
    seats independent. Two readers of the same handoff row fail together when the row is wrong; a
    reader of the row and a reader of the trajectory fail together only when both are wrong.
    """

    #: The ``md_system_input/v1`` row: coordinate origin, hydrogens, charge, stereochemistry,
    #: receptor id, protonation state.
    HANDOFF_RECORD = "handoff_record"
    #: The per-frame ligand RMSD series and the contact map from a finished run.
    TRAJECTORY = "trajectory"
    #: The screen's own number for this molecule, and what produced it.
    SCREEN_NUMBER = "screen_number"
    #: The expensive stage's number, its replica spread, and its units.
    EXPENSIVE_NUMBER = "expensive_number"
    #: The deterministic layer's observations: which causes fired, which could not be evaluated.
    FAULT_OBSERVATIONS = "fault_observations"
    #: Which MolCascade, which PRISM, which revision, which run, whether the toolchain was seeded.
    PROVENANCE = "provenance"
    #: The molecule itself: SMILES, scaffold, and how it compares to the panel.
    CHEMISTRY = "chemistry"


class SeatError(ValueError):
    """Raised when a council's composition would make its agreement uninformative."""


@dataclass(frozen=True, slots=True)
class Seat:
    """One adjudicator, and the evidence it is allowed to decide on.

    ``advisory`` is an :class:`etalon.judgment.advisor.Advisory` or anything with the same ``ask``.
    Held as ``Any`` rather than imported, so that this module -- which is the part worth reading --
    does not drag in a subprocess transport.
    """

    #: Short, stable, and recorded in every ballot. Not a persona name: a seat is identified by
    #: what it sees, so ``record-reader`` is a better name than ``the sceptical chemist``.
    name: str
    advisory: Any
    sees: frozenset[Evidence]
    #: What this seat is being asked to look for, in one sentence, appended to every question it is
    #: put. Deliberately short: hallucination rates in the drug-discovery benchmarks were highest
    #: under extended reasoning, and ADR 0003 is why the questions here ask for a decision rather
    #: than for an argument.
    brief: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SeatError("a seat must be named; an anonymous ballot cannot be weighed")
        # Coerced and validated here rather than trusted. A scope holding the *string*
        # "handoff_record" instead of the enum member constructs without complaint, charters
        # without complaint, and then fails inside evidence_for at the moment the council sits --
        # which is after the reliability measurement has been quoted and, in a real campaign,
        # after the deterministic layer has already refused everything it was going to refuse.
        # An unknown name is named against the ones that exist, because the usual cause is a
        # typo and the usual symptom would otherwise be a seat that silently sees nothing.
        coerced = []
        for item in self.sees:
            if isinstance(item, Evidence):
                coerced.append(item)
                continue
            try:
                coerced.append(Evidence(str(item)))
            except ValueError as error:
                known = ", ".join(sorted(member.value for member in Evidence))
                raise SeatError(
                    f"seat {self.name!r} declares evidence {item!r}, which is not a kind this "
                    f"council knows. The kinds are: {known}."
                ) from error
        object.__setattr__(self, "sees", frozenset(coerced))
        if not self.sees:
            raise SeatError(
                f"seat {self.name!r} is shown no evidence, so every vote it casts is a prior "
                "rather than a reading. Give it an Evidence scope or leave it off the council."
            )
        if not hasattr(self.advisory, "ask"):
            raise SeatError(f"seat {self.name!r} has no advisory that can be asked a question")

    def evidence_for(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """The subset of a record this seat is permitted to see.

        Enforced here rather than trusted to the prompt. A seat whose independence is a sentence in
        its instructions is independent until a model reads past the sentence; a seat whose
        independence is the dictionary it was handed is independent because the other evidence is
        not in the process.
        """

        return {key.value: record[key.value] for key in self.sees if key.value in record}


def charter(seats: Sequence[Seat]) -> tuple[Seat, ...]:
    """Admit a council, or refuse its composition.

    Three refusals, and each is the structural half of a condition
    :mod:`etalon.council.reliability` measures afterwards.

    A council of one is refused because there is nothing to disagree with, and the whole mechanism
    here is that disagreement is the signal. Use the advisor directly instead -- ``judgment/``
    already supports one, and pretending it is a council would put a quorum's weight behind one
    opinion.

    Two seats with the same evidence scope are refused. This is the sentence from ``tuning/knob.py``
    applied to advisors: whatever their briefs say, they read the same thing and will be wrong
    together on exactly the inputs where being wrong together matters -- a handoff row that is
    itself misleading, which is the case the deterministic layer already failed to catch and the
    council was convened for.

    A duplicated name is refused because ballots are keyed by it and two seats sharing one would
    have their votes counted as one seat changing its mind.

    Returns the seats in a fixed order, so a council's composition digest is a function of its
    membership rather than of the order somebody happened to list them in.
    """

    if len(seats) < 2:
        raise SeatError(
            f"a council needs at least two seats and got {len(seats)}. A single adjudicator is an "
            "advisor -- use etalon.judgment.advisor.Advisory, which records one opinion as one "
            "opinion. Convening it as a council would put a quorum's weight behind it, and the "
            "one thing this layer is for is that a split is visible."
        )
    by_name: dict[str, Seat] = {}
    for seat in seats:
        if seat.name in by_name:
            raise SeatError(
                f"two seats are both named {seat.name!r}. Ballots are keyed by seat name, so this "
                "would record two independent votes as one seat voting twice."
            )
        by_name[seat.name] = seat

    by_scope: dict[frozenset[Evidence], str] = {}
    for seat in sorted(by_name.values(), key=lambda s: s.name):
        clash = by_scope.get(seat.sees)
        if clash is not None:
            shown = ", ".join(sorted(item.value for item in seat.sees))
            raise SeatError(
                f"seats {clash!r} and {seat.name!r} are shown exactly the same evidence "
                f"({shown}), so they are one opinion at two prices -- the phrase is "
                "tuning/knob.py's, about consensus scoring, and the condition is the same one. "
                "Differing briefs do not make them independent: a persona cannot change what is "
                "in front of it, and both will be wrong together on precisely the record that is "
                "itself misleading, which is the case a council is convened for. Give one of them "
                "a different Evidence scope, or seat only one."
            )
        by_scope[seat.sees] = seat.name
    return tuple(sorted(by_name.values(), key=lambda s: s.name))


def composition(seats: Sequence[Seat]) -> dict[str, Any]:
    """The council's membership as data, for the ledger and for a reliability record.

    ``overlap`` is reported per pair because the structural refusal above only catches *identical*
    scopes, and two seats sharing three of four evidence kinds are nearly the refused case without
    triggering it. Reported rather than refused: where the line falls is not something this project
    has measured, and a threshold invented here would read like one that had been.
    """

    ordered = sorted(seats, key=lambda s: s.name)
    pairs = []
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            shared = left.sees & right.sees
            union = left.sees | right.sees
            pairs.append(
                {
                    "seats": [left.name, right.name],
                    "shared_evidence": sorted(item.value for item in shared),
                    "jaccard": round(len(shared) / len(union), 4) if union else 0.0,
                }
            )
    return {
        "seats": [
            {
                "name": seat.name,
                "sees": sorted(item.value for item in seat.sees),
                "brief": seat.brief,
            }
            for seat in ordered
        ],
        "pairs": pairs,
        "note": (
            "Evidence overlap is the structural half of the consensus precondition and is "
            "necessary rather than sufficient. Two seats with disjoint evidence can still agree "
            "for a shared reason -- the same pretraining, most obviously -- which is why "
            "etalon.council.reliability measures the votes as well as the scopes."
        ),
    }


__all__ = ["Evidence", "Seat", "SeatError", "charter", "composition"]
