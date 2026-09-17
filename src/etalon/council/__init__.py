"""A council of advisors that must prove it is an instrument before it may sit.

``tuning/knob.py`` carries the published precondition for consensus scoring -- each member good on
its own, and the members diverse -- and ``tuning/advise.py`` refuses to recommend the knob until an
operator has established it. This package applies the same condition to advisors, which is the
place the analogy had not been carried through: every multi-agent system this project surveyed adds
agents on the assumption that more opinions are better, and that is the assumption the consensus
literature refutes.

Four modules and one direction of travel.

``seat.py``         a seat declares what evidence it sees; identical scopes are refused
``reliability.py``  Youden's J per seat, kappa per pair, and the refusal below chance
``convene.py``      the sitting, bounded so a council can only ever add a refusal
``ballot.py``       votes, with abstention as a first-class answer rather than a missing one
"""

from etalon.council.ballot import Ballot, Finding, Outcome, Vote
from etalon.council.convene import adjudicate, as_observations, for_a_person, report
from etalon.council.reliability import (
    REDUNDANT_ABOVE,
    Reliability,
    Skill,
    cohen_kappa,
    effective_votes,
    fleiss_kappa,
    rule,
)
from etalon.council.seat import Evidence, Seat, SeatError, charter, composition

__all__ = [
    "REDUNDANT_ABOVE",
    "Ballot",
    "Evidence",
    "Finding",
    "Outcome",
    "Reliability",
    "Seat",
    "SeatError",
    "Skill",
    "Vote",
    "adjudicate",
    "as_observations",
    "charter",
    "cohen_kappa",
    "composition",
    "effective_votes",
    "fleiss_kappa",
    "for_a_person",
    "report",
    "rule",
]
