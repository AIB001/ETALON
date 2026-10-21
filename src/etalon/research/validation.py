"""Append a released independent check to a frozen decision, never to phi transfer."""
from __future__ import annotations

from etalon.active.schema import digest
from etalon.active.store import StateError
from .journal import ResearchJournal


def attach_decision_validation(journal: ResearchJournal, *, decision_sha256: str,
                               evidence: tuple[str, ...], release_evidence: str,
                               protocol: dict, comparison: dict) -> dict:
    """Release is an auditable coordinator assertion, not proof of pretraining isolation.

    The parent decision is checked in the same transaction and remains immutable.
    Evidence is thereafter seen feedback; it cannot validate a later revision unseen.
    """
    with journal.store.connection(write=True) as db:
        parent = journal._get(db, 'decision-' + decision_sha256)
        body = {k: v for k, v in parent.items() if k != 'freeze_sha256'}
        if parent.get('freeze_sha256') != decision_sha256 or digest(body) != decision_sha256:
            raise StateError('decision freeze hash mismatch')
        if parent.get('independent_validation_accessed') is not False:
            raise StateError('decision does not declare an unexposed freeze')
        artifacts = journal._artifacts(db, (*evidence, release_evidence))
        if not evidence or any(a.role != 'independent_validation' for a in artifacts):
            raise StateError('released validation requires independent_validation evidence')
        if any(a.bindings.get('decision_sha256') != decision_sha256 for a in artifacts):
            raise StateError('release and evidence must bind this exact decision')
        if not protocol or not comparison:
            raise ValueError('explicit validation method and comparison required')
        receipt = {
            'schema': 'etalon-decision-validation/1', 'decision_sha256': decision_sha256,
            'task_id': parent['task_id'], 'evidence': list(evidence),
            'release_evidence': release_evidence, 'protocol': protocol,
            'protocol_sha256': digest(protocol), 'comparison': comparison,
            'parent_decision_unchanged': True, 'promotion': 'none',
            'phi_transfer': 'not_evaluated',
            'future_use': 'seen_development_feedback_only',
            'independence_scope': 'coordinator-released after decision freeze; retrospective',
            'incremental_producer_execution_cost': 0,
            'local_attachment_compute_cost': None,
        }
        identifier = 'decisionvalidation-' + digest(receipt)
        journal._put(db, identifier, receipt)
    return {'id': identifier, **receipt}
