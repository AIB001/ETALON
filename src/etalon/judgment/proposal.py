"""What an advisor may propose, and what the harness will do about it.

ETALON's design already separates proposing from admitting everywhere: the screen proposes a
shortlist and preflight admits it, a simulation proposes a number and the admissibility rule
admits it, a parameter change is admitted only if it beats the panel's resolution. An advisor
-- a language model, a person, or an algorithm -- goes in the first half of those pairs and
in none of the second.

The gradation that makes this operational is not about confidence. It is about what being
wrong costs, and :data:`AUTONOMY` states it once so that no caller has to decide case by
case. Choosing the wrong twenty molecules to measure wastes a week and nothing it records
becomes untrue, so an advisor's acquisition decision is acted on directly. Granting a waiver
for a fault that means the number is about a different species costs the validity of a
recorded result, unrecoverably, so an advisor cannot do it at all -- it recommends, and a
person grants.

``docs/adr/0003`` carries the reasoning and the citations. The short version is that the
characteristic failure of these systems is not a wrong answer but a confident, well-formatted
one, and a waiver reason is pure format: a plausible justification and a sound one are
indistinguishable at the point of reading, which is the only point at which anyone reads it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Act(StrEnum):
    """The kinds of thing an advisor can put forward."""

    #: Which molecules to spend the next batch of compute on.
    SPEND = "spend"
    #: A change to the screen's parameters.
    PARAMETER_CHANGE = "parameter_change"
    #: Which of the screen's metrics a binding free energy should be calibrated against.
    COMPARATOR = "comparator"
    #: Accepting a fault that would otherwise refuse a molecule.
    WAIVER = "waiver"
    #: An explanation for a divergence the fault taxonomy could not account for.
    HYPOTHESIS = "hypothesis"


class Autonomy(StrEnum):
    """How far a proposal travels on its own."""

    #: Applied directly. Being wrong costs compute, and the next round shows it.
    ACTED_ON = "acted_on"
    #: Applied only if an existing statistical gate admits it.
    GATED = "gated"
    #: Recorded, and a person decides. The harness will not apply it.
    NEEDS_A_PERSON = "needs_a_person"
    #: Recorded and never applied by anything. A hypothesis is not an instruction.
    RECORDED_ONLY = "recorded_only"


#: The rule, stated once. See ADR 0003 for why each row is where it is.
AUTONOMY: dict[Act, Autonomy] = {
    Act.SPEND: Autonomy.ACTED_ON,
    Act.PARAMETER_CHANGE: Autonomy.GATED,
    Act.COMPARATOR: Autonomy.NEEDS_A_PERSON,
    Act.WAIVER: Autonomy.NEEDS_A_PERSON,
    Act.HYPOTHESIS: Autonomy.RECORDED_ONLY,
}


class AdvisorKind(StrEnum):
    LANGUAGE_MODEL = "language_model"
    PERSON = "person"
    #: A deterministic procedure -- an acquisition function, a regression. Distinguished
    #: from a model because its output is reproducible from its inputs, which changes what a
    #: reader can check.
    ALGORITHM = "algorithm"


@dataclass(frozen=True, slots=True)
class Advisor:
    """Who is proposing, in enough detail to re-ask the question later.

    The prompt digest is the part that earns its place. Long-horizon memory degradation means
    an advisor's reasoning in round seven can contradict round two, and a campaign that keeps
    only conclusions cannot see that. More immediately: a confidently wrong answer usually
    comes from a question that was asked badly, and without the prompt a reader cannot tell
    the two apart.
    """

    kind: AdvisorKind
    #: ``claude-sonnet-5``, a person's name, ``conformal-acquisition@0.1.0``.
    identifier: str
    #: How it was reached: ``claude-cli``, ``https-api``, ``in-process``, ``keyboard``.
    transport: str = "in-process"
    prompt_sha256: str = ""
    response_sha256: str = ""
    at: str = ""

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("an advisor must be identifiable; an anonymous proposal cannot be weighed")
        if not self.at:
            object.__setattr__(
                self, "at", datetime.now(UTC).isoformat(timespec="seconds")
            )

    @property
    def is_model(self) -> bool:
        return self.kind is AdvisorKind.LANGUAGE_MODEL

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "identifier": self.identifier,
            "transport": self.transport,
            "prompt_sha256": self.prompt_sha256,
            "response_sha256": self.response_sha256,
            "at": self.at,
        }


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Proposal:
    """One thing an advisor put forward, and how far it is allowed to travel."""

    act: Act
    advisor: Advisor
    #: The content. Shape depends on ``act`` and is the caller's contract with itself; the
    #: harness only requires that it serialise, because it goes in the ledger.
    payload: dict[str, Any] = field(default_factory=dict)
    #: Why. Recorded for every act, and load-bearing for none of them: a rationale is read by
    #: people and acted on by nothing, which is deliberate given that a plausible rationale
    #: and a sound one are indistinguishable in writing.
    rationale: str = ""

    def __post_init__(self) -> None:
        try:
            json.dumps(self.payload)
        except TypeError as error:
            raise ValueError(
                f"a {self.act.value} proposal's payload must serialise, because it is "
                f"recorded in the ledger: {error}"
            ) from error

    @property
    def autonomy(self) -> Autonomy:
        return AUTONOMY[self.act]

    @property
    def may_be_applied_without_a_person(self) -> bool:
        return self.autonomy in (Autonomy.ACTED_ON, Autonomy.GATED)

    def as_dict(self) -> dict[str, object]:
        return {
            "act": self.act.value,
            "autonomy": self.autonomy.value,
            "advisor": self.advisor.as_dict(),
            "payload": dict(self.payload),
            "rationale": self.rationale,
        }


class NotAnAdvisorsDecision(PermissionError):
    """Raised when a proposal is used past the point its act is allowed to travel."""


def refuse_if_not_an_advisors_decision(proposal: Proposal) -> None:
    """Guard the two acts an advisor may put forward and may not settle.

    Called at the point of application rather than of proposal, because a recommendation is
    always legitimate -- it is acting on one unilaterally that is not.
    """

    if proposal.autonomy is Autonomy.NEEDS_A_PERSON:
        raise NotAnAdvisorsDecision(
            f"a {proposal.act.value} proposal from {proposal.advisor.identifier} is a "
            "recommendation and not a decision. Being wrong here costs the validity of a "
            "recorded result rather than some compute: a silently wrong comparator "
            "invalidates every later round, and a waiver's whole value is that a named "
            "person accepted a named consequence. Record it, show it to somebody, and have "
            "them grant it. See docs/adr/0003."
        )
    if proposal.autonomy is Autonomy.RECORDED_ONLY:
        raise NotAnAdvisorsDecision(
            f"a {proposal.act.value} is recorded and acted on by nothing. It is an "
            "explanation offered for a divergence, which is a different kind of object from "
            "an instruction, and treating one as the other is how a guess becomes a finding."
        )


__all__ = [
    "AUTONOMY",
    "Act",
    "Advisor",
    "AdvisorKind",
    "Autonomy",
    "NotAnAdvisorsDecision",
    "Proposal",
    "digest",
    "refuse_if_not_an_advisors_decision",
]
