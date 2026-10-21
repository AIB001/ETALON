"""Auditable diagnostic research records and finite-model reference solvers.

These tools do not certify scientific truth, execute simulations or demonstrate RSI transfer.
"""

from .journal import ResearchJournal
from .schema import (
    Artifact, Change, Contrast, Coupling, Experiment, MethodEvaluation, MethodUse, MethodVersion,
    Observation, Revision, TaskSplit, method_record_from_dict,
)

__all__ = ['Artifact', 'Change', 'Contrast', 'Coupling', 'Experiment', 'Observation',
           'ResearchJournal', 'Revision', 'TaskSplit', 'MethodVersion', 'MethodUse',
           'MethodEvaluation', 'method_record_from_dict']
