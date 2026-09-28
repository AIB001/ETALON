"""The agent: a screen that is allowed to learn from simulation, under stated conditions.

Two regimes, one ledger. :class:`~etalon.campaign.loop.Campaign` is for the expensive-stage-limited
one -- few measurements, each costing GPU-hours, learn between them. :class:`~etalon.campaign.sweep.Sweep`
is for the throughput-limited one -- a million cheap measurements, where nothing is worth authorising
individually and the irreversible act is a miscalibrated gate rather than a wasted hour. They share
the ledger, the screen adapter and the infrastructure pinning, and share no selection policy, because
a sweep selects nothing: every molecule in the pool is screened.
"""

from etalon.campaign.calibrate import Calibration, PanelMember, Separation, TierVerdict, calibrate
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
from etalon.campaign.sweep import (
    DEFAULT_BATCH_SIZE,
    AdmitReport,
    Batch,
    Recovery,
    Sweep,
    SweepError,
)

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_FUNNEL",
    "Acquirer",
    "Acquisition",
    "AdmitReport",
    "Batch",
    "Calibration",
    "Campaign",
    "Entry",
    "ExpensiveStage",
    "Ledger",
    "Panel",
    "PanelMember",
    "ParameterChange",
    "Pipeline",
    "Plan",
    "PrismStage",
    "Proposer",
    "Recovery",
    "RoundOutcome",
    "Separation",
    "Sweep",
    "SweepError",
    "TierVerdict",
    "as_loop_acquirer",
    "as_loop_proposer",
    "calibrate",
]
