"""Accepting a risk in writing, rather than switching off the check that found it.

The first real run of the preflight layer against real MolCascade output refused all
three molecules, every one for ``F_PROTONATION_UNDECIDED``. The refusal was correct --
nothing in MolCascade predicts a ligand protonation state, and the handoff record says
so honestly rather than inventing one. But a gate that refuses the whole population is
the failure ADR 0002 is about, re-created in a different field: a checker nobody can get
past is a checker that gets switched off, and then it protects nothing.

There are three ways out and only one of them is honest.

Weakening the fault -- deciding that an undecided protonation state is merely a
wrong-size problem -- would be writing the science to suit the tooling. A unit charge
difference is a different species.

Letting the operator disable checks by name produces a configuration in which the
absence of a finding means nothing, because no reader can tell a check that passed from
one that was turned off.

The third is a waiver: the fault still fires, the finding is still recorded, and a named
person accepts the consequence for a stated reason with a stated scope. The measurement
proceeds. The difference from a disabled check is that a waiver is *evidence* -- it
travels with the round, it appears in the attribution when that round's numbers later
disagree with something, and it expires.

That last part is what makes it more than paperwork. A waived cause is never "cleared";
it stays a standing candidate. So if a waived campaign later produces a divergence this
taxonomy cannot otherwise explain, the waiver is in the candidate list with the reason
somebody gave for accepting it -- which is exactly the information a reader needs and
exactly what a disabled check destroys.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from etalon.faults.taxonomy import BY_CODE, Consequence

# This module lives in ``judgment`` rather than in ``campaign``, and the move was forced by an
# import cycle that only appeared once ``etalon.learn`` was imported before
# ``etalon.campaign``: admissibility needs waivers, the campaign package needs admissibility,
# and a waiver defined inside the campaign closed the loop. The cycle was the symptom. A waiver
# is a person accepting a consequence, which is the same kind of object as an advisor's
# proposal and belongs beside it -- and with the layering as judgment -> learn -> campaign,
# each arrow points one way.


class WaiverError(ValueError):
    """Raised when a waiver would be indistinguishable from switching a check off."""


#: Substrings that mark a ``granted_by`` as a model rather than a person. A heuristic, and
#: labelled as one: it catches the obvious case of a model identifier pasted into the grantor
#: field and it cannot catch a model given a human-sounding name. The exact guard is
#: :meth:`Waiver.granted_by_advisor`, which sees the advisor's declared kind; this list is the
#: backstop for the string path, where the only thing available is the string.
_MODEL_NAME_HINTS = (
    "claude", "gpt-", "gpt4", "gpt5", "llama", "gemini", "mistral", "qwen", "deepseek",
    "o1-", "o3-", "-sonnet", "-opus", "-haiku", "language_model", "llm",
)


@dataclass(frozen=True, slots=True)
class Waiver:
    """One fault, accepted on the record.

    A waiver may only be granted by a person, and the reason is in ``docs/adr/0003``. Its
    entire value is that somebody accepted a named consequence and can be asked about it
    later; a language model writes that sentence better than most people and cannot be held
    to it. An advisor may recommend one -- see :meth:`recommend` -- and what is recorded then
    is a recommendation, which is a different object.
    """

    code: str
    #: Why this is acceptable *for this campaign*. Not a restatement of the fault.
    reason: str
    #: Who is accepting it. A waiver with no owner is a disabled check with extra steps.
    granted_by: str
    #: After this date the waiver stops applying and the fault blocks again. Required,
    #: because an unbounded waiver becomes the configuration and nobody revisits it.
    expires: date
    granted_at: str = ""

    def __post_init__(self) -> None:
        if self.code not in BY_CODE:
            raise WaiverError(
                f"{self.code} is not in the taxonomy, so this waiver accepts nothing "
                "identifiable. A typo here would read as a granted waiver."
            )
        if len(self.reason.strip()) < 24:
            raise WaiverError(
                f"the reason for waiving {self.code} is too short to be a reason. State "
                "what makes this acceptable for this campaign -- a reader a year from now "
                "has only this sentence."
            )
        if not self.granted_by.strip():
            raise WaiverError(
                f"a waiver for {self.code} must name who granted it. An unowned waiver "
                "is a disabled check that looks like a decision."
            )
        lowered = self.granted_by.lower()
        hit = next((hint for hint in _MODEL_NAME_HINTS if hint in lowered), None)
        if hit is not None:
            raise WaiverError(
                f"{self.granted_by!r} looks like a language model ({hit!r}), and a waiver can "
                "only be granted by a person. A model may recommend one -- use "
                "Waiver.recommend, which records a recommendation -- but the grantor field is "
                "an instrument of accountability, and accountability cannot be delegated to "
                "something that cannot be held to it. See docs/adr/0003. If this is a "
                "person's actual name, use a form that does not collide with a model id."
            )
        fault = BY_CODE[self.code]
        if fault.consequence is Consequence.UNVERIFIABLE:
            # Nothing to waive: these never blocked a spend in the first place, and
            # accepting one would imply a refusal that was never going to happen.
            raise WaiverError(
                f"{self.code} does not block a spend -- it invalidates a claim about the "
                "run -- so there is nothing for a waiver to release. Qualify the result "
                "instead; etalon.faults.unverifiable reports these separately for that "
                "reason."
            )
        if not self.granted_at:
            object.__setattr__(
                self, "granted_at", datetime.now(UTC).isoformat(timespec="seconds")
            )

    def active_on(self, when: date) -> bool:
        return when <= self.expires

    def as_dict(self) -> dict[str, object]:
        fault = BY_CODE[self.code]
        return {
            "code": self.code,
            "reason": self.reason,
            "granted_by": self.granted_by,
            "granted_at": self.granted_at,
            "expires": self.expires.isoformat(),
            # Carried so the record says what was accepted, not only which code. A
            # reader of the ledger should not have to have this source tree to hand.
            "accepted_consequence": fault.consequence.value,
            "accepted_summary": fault.summary,
        }

    @classmethod
    def granted_by_advisor(
        cls, advisor: Any, code: str, reason: str, expires: date
    ) -> Waiver:
        """Grant a waiver on an advisor's behalf, which succeeds only for a person.

        The exact guard, as opposed to the string heuristic in ``__post_init__``: an
        :class:`~etalon.judgment.proposal.Advisor` declares its own kind, so a model is refused
        on what it is rather than on what it is called.
        """

        if getattr(advisor, "is_model", False):
            raise WaiverError(
                f"{getattr(advisor, 'identifier', advisor)!r} is a language model and cannot "
                f"grant a waiver for {code}. Record its recommendation and have a person "
                "grant it; see docs/adr/0003 for why this line is where it is."
            )
        return cls(
            code=code,
            reason=reason,
            granted_by=str(getattr(advisor, "identifier", advisor)),
            expires=expires,
        )

    @staticmethod
    def recommend(advisor: Any, code: str, reason: str, expires: date) -> dict[str, object]:
        """What an advisor produces instead of a waiver: a recommendation, recorded as one.

        Returned as plain data rather than as a :class:`Waiver` on purpose. A half-granted
        waiver object would sit in a variable looking exactly like a granted one, and the one
        thing this boundary has to guarantee is that the two are never mistaken for each other.
        """

        return {
            "recommended_waiver": {
                "code": code,
                "reason": reason,
                "expires": expires.isoformat(),
                "recommended_by": str(getattr(advisor, "identifier", advisor)),
                "advisor_kind": str(getattr(getattr(advisor, "kind", ""), "value", "unknown")),
                "status": "AWAITING_A_PERSON",
                "note": (
                    "Not in force. A waiver lets a campaign spend past a fault that would "
                    "otherwise refuse the molecule, and its value is that a named person "
                    "accepted the consequence. Grant it with Waiver(...) if you agree."
                ),
            }
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Waiver:
        return cls(
            code=str(payload["code"]),
            reason=str(payload["reason"]),
            granted_by=str(payload["granted_by"]),
            expires=date.fromisoformat(str(payload["expires"])),
            granted_at=str(payload.get("granted_at", "")),
        )


@dataclass(frozen=True, slots=True)
class WaiverSet:
    """The waivers in force for a round, and the questions a caller asks of them."""

    waivers: tuple[Waiver, ...] = ()

    def active(self, when: date | None = None) -> tuple[Waiver, ...]:
        moment = when or datetime.now(UTC).date()
        return tuple(waiver for waiver in self.waivers if waiver.active_on(moment))

    def expired(self, when: date | None = None) -> tuple[Waiver, ...]:
        moment = when or datetime.now(UTC).date()
        return tuple(waiver for waiver in self.waivers if not waiver.active_on(moment))

    def for_code(self, code: str, when: date | None = None) -> Waiver | None:
        return next((w for w in self.active(when) if w.code == code), None)

    def codes(self, when: date | None = None) -> frozenset[str]:
        return frozenset(waiver.code for waiver in self.active(when))

    def as_dict(self) -> dict[str, object]:
        return {
            "active": [waiver.as_dict() for waiver in self.active()],
            "expired": [waiver.as_dict() for waiver in self.expired()],
        }


__all__ = ["Waiver", "WaiverError", "WaiverSet"]
