"""The agent: a screen that is allowed to learn from simulation, under stated conditions.

Two regimes, one ledger. :class:`~etalon.campaign.loop.Campaign` is for the expensive-stage-limited
one -- few measurements, each costing GPU-hours, learn between them. :class:`~etalon.campaign.sweep.Sweep`
is for the throughput-limited one -- a million cheap measurements, where nothing is worth authorising
individually and the irreversible act is a miscalibrated gate rather than a wasted hour. They share
the ledger, the screen adapter and the infrastructure pinning, and share no selection policy, because
a sweep selects nothing: every molecule in the pool is screened.
"""

from etalon.campaign.calibrate import Calibration, PanelMember, Separation, TierVerdict, calibrate
from etalon.campaign.drivers import MolCascadeProcessScreening, MolCascadeScreening, RevisionChanged
from etalon.campaign.expensive import PrismStage
from etalon.campaign.generation import (
    ChunkResult,
    Generator,
    Ingest,
    Pocket,
    PrismGeneration,
)
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
from etalon.campaign.supervisor import Supervisor, TickReport
from etalon.campaign.sweep import (
    DEFAULT_BATCH_SIZE,
    AdmitReport,
    Batch,
    Recovery,
    Sweep,
    SweepError,
)

__all__ = [
    "Acquirer",
    "Acquisition",
    "AdmitReport",
    "Batch",
    "Calibration",
    "Campaign",
    "ChunkResult",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_FUNNEL",
    "Entry",
    "ExpensiveStage",
    "Generator",
    "Ingest",
    "Ledger",
    "MolCascadeProcessScreening",
    "MolCascadeScreening",
    "Panel",
    "PanelMember",
    "ParameterChange",
    "Pipeline",
    "Plan",
    "Pocket",
    "PrismGeneration",
    "PrismStage",
    "Proposer",
    "Recovery",
    "RevisionChanged",
    "RoundOutcome",
    "Separation",
    "Supervisor",
    "Sweep",
    "SweepError",
    "TickReport",
    "TierVerdict",
    "as_loop_acquirer",
    "as_loop_proposer",
    "calibrate",
]
