"""The agent: a screen that is allowed to learn from simulation, under stated conditions."""

from etalon.campaign.expensive import PrismStage
from etalon.campaign.ledger import Entry, Ledger
from etalon.campaign.loop import Acquirer, Campaign, ExpensiveStage, Proposer, RoundOutcome
from etalon.campaign.pipeline import DEFAULT_FUNNEL, Pipeline, Plan
from etalon.campaign.propose import (
    Acquisition,
    Panel,
    ParameterChange,
    as_loop_acquirer,
    as_loop_proposer,
)

__all__ = [
    "DEFAULT_FUNNEL",
    "Acquirer",
    "Acquisition",
    "Campaign",
    "Entry",
    "ExpensiveStage",
    "Ledger",
    "Panel",
    "ParameterChange",
    "Pipeline",
    "Plan",
    "PrismStage",
    "Proposer",
    "RoundOutcome",
    "as_loop_acquirer",
    "as_loop_proposer",
]
