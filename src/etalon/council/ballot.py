"""What one seat on the council may say, and the shapes a council's answer can take.

The vocabulary here is deliberately the one ``etalon.faults`` already uses, because the council
exists to act on that layer's leftovers rather than to build a second opinion beside it.

``etalon.faults.attribution.Observation`` carries two booleans, not one: ``fired`` says whether a
cause was seen, and ``evaluable`` says whether anybody could look. ``preflight.unchecked`` exists
because "0 blocking" printed over four unevaluated checks says the opposite of the truth. Those
unevaluated checks are the council's entire jurisdiction. A deterministic check that *ran* is a
measurement, and no vote may overturn a measurement.

So a seat's :class:`Vote` maps onto that pair rather than onto agreement:

``REFUSE``  the seat can see, from the evidence it holds, that the cause is present.
``ABSTAIN`` the seat's evidence does not decide it. This is ``evaluable=False`` said by an
            advisor, and it is **not** a vote in favour -- a record every seat abstained on is
            exactly as unchecked as it was before the council sat.
``CLEAR``   the seat believes the cause is absent. Recorded, reported, and -- see
            :mod:`etalon.council.convene` -- never sufficient to admit anything.

The asymmetry in that last line is the same one ``learn.admissible`` is built on: withholding a
good measurement costs one molecule's information, admitting a bad one costs a shift in the policy
applied to all of them. A council of language models is allowed to make the first mistake and not
the second.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Vote(StrEnum):
    """One seat's answer about one fault code."""

    #: The cause is present, on the evidence this seat holds.
    REFUSE = "refuse"
    #: This seat's evidence does not decide it. Not a vote in favour.
    ABSTAIN = "abstain"
    #: The cause is absent, on the evidence this seat holds. Never sufficient on its own.
    CLEAR = "clear"


class Outcome(StrEnum):
    """What the council as a body produced for one fault code."""

    #: Every seat that could see agreed the cause is present. The check becomes a refusal.
    REFUSED = "refused"
    #: Seats disagreed. Routed to a person, and the disagreement is the reason.
    SPLIT = "split"
    #: No seat's evidence decided it. The check stays exactly as unevaluable as it was.
    UNDECIDED = "undecided"
    #: Seats agreed the cause is absent. Recorded; the check stays unevaluable.
    #: A language model saying "I see no problem" is not a check having passed.
    CLEARED_BUT_STILL_UNCHECKED = "cleared_but_still_unchecked"
    #: The council did not sit, because it has not established that it is an instrument.
    COUNCIL_NOT_QUALIFIED = "council_not_qualified"


@dataclass(frozen=True, slots=True)
class Ballot:
    """One seat's vote on one code, with enough attached to re-ask the question later.

    ``prompt_sha256`` and ``response_sha256`` are carried for the reason ADR 0003 gives: a
    confidently wrong answer usually comes from a question that was asked badly, and a record
    holding only the conclusion cannot tell the two apart.
    """

    seat: str
    code: str
    vote: Vote
    #: One sentence. Read by people, acted on by nothing -- the same rule
    #: :class:`etalon.judgment.proposal.Proposal` applies to a rationale, for the same reason.
    reason: str = ""
    prompt_sha256: str = ""
    response_sha256: str = ""
    #: How many differently-shaped answers were refused before this one parsed.
    attempts: int = 1
    #: Set when the seat could not be reached or never answered in the shape asked for. A seat
    #: that errored is recorded as having abstained *and* as having failed, because an outage
    #: that silently reads as agreement is how a council shrinks without anybody noticing.
    error: str = ""

    @property
    def decided(self) -> bool:
        return self.vote is not Vote.ABSTAIN

    def as_dict(self) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "code": self.code,
            "vote": self.vote.value,
            "reason": self.reason,
            "prompt_sha256": self.prompt_sha256,
            "response_sha256": self.response_sha256,
            "attempts": self.attempts,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class Finding:
    """The council's answer on one fault code for one record.

    ``as_observation`` is the only way this object reaches the rest of ETALON, and it is
    deliberately narrow: see :func:`etalon.council.convene.adjudicate`.
    """

    code: str
    parent_id: str
    outcome: Outcome
    ballots: tuple[Ballot, ...] = ()
    #: Why the council reached this outcome, in the harness's own words rather than a seat's.
    note: str = ""
    #: Carried through from the reliability ruling in force when the council sat, so a reader of
    #: the ledger does not have to find out separately whether this council had been measured.
    qualification: dict[str, Any] = field(default_factory=dict)

    @property
    def refusing(self) -> bool:
        return self.outcome is Outcome.REFUSED

    @property
    def needs_a_person(self) -> bool:
        return self.outcome is Outcome.SPLIT

    def votes(self) -> dict[str, int]:
        counted: dict[str, int] = {vote.value: 0 for vote in Vote}
        for ballot in self.ballots:
            counted[ballot.vote.value] += 1
        return counted

    def dissent(self) -> tuple[Ballot, ...]:
        """The ballots that disagree with the majority direction, for a person to read first.

        Returned rather than summarised because the sentence a reader needs on a split is the
        minority's, and a count of three-to-one does not carry it.
        """

        if self.outcome is not Outcome.SPLIT:
            return ()
        refusing = [b for b in self.ballots if b.vote is Vote.REFUSE]
        clearing = [b for b in self.ballots if b.vote is Vote.CLEAR]
        return tuple(refusing if len(refusing) <= len(clearing) else clearing)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "parent_id": self.parent_id,
            "outcome": self.outcome.value,
            "votes": self.votes(),
            "ballots": [ballot.as_dict() for ballot in self.ballots],
            "note": self.note,
            "qualification": dict(self.qualification),
            "dissent": [ballot.as_dict() for ballot in self.dissent()],
        }


__all__ = ["Ballot", "Finding", "Outcome", "Vote"]
