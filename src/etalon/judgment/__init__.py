"""Where an advisor may act, and where it may not.

A language model can answer every question ETALON currently stops and asks a person. The
design question is which of its answers may be acted on, and ``docs/adr/0003`` settles it on
the documented failure modes of autonomous research agents rather than on taste: the
characteristic failure is not a wrong answer but a confident, well-formatted one, produced most
readily under extended reasoning and most dangerous where a reader cannot tell it from a
correct one.

So an advisor proposes and never grants, and the gradation is by what being wrong costs.
Choosing the wrong molecules to measure wastes compute and nothing recorded becomes untrue, so
that is applied directly. A parameter change goes through the noise floor that already refuses
changes smaller than the panel can resolve. A comparator and a waiver need a person, because a
silently wrong comparator invalidates every later round and a waiver's whole value is that
somebody accepted a consequence and can be asked about it.
"""

from etalon.judgment.advisor import AdvisorError, Advisory, ClaudeCli, Scripted, Transport
from etalon.judgment.proposal import (
    AUTONOMY,
    Act,
    Advisor,
    AdvisorKind,
    Autonomy,
    NotAnAdvisorsDecision,
    Proposal,
    digest,
    refuse_if_not_an_advisors_decision,
)
from etalon.judgment.waiver import Waiver, WaiverError, WaiverSet

__all__ = [
    "AUTONOMY",
    "Act",
    "Advisor",
    "AdvisorError",
    "AdvisorKind",
    "Advisory",
    "Autonomy",
    "ClaudeCli",
    "NotAnAdvisorsDecision",
    "Proposal",
    "Scripted",
    "Waiver",
    "WaiverError",
    "WaiverSet",
    "Transport",
    "digest",
    "refuse_if_not_an_advisors_decision",
]
