"""Compile only the selected first program segment into the existing fixed runtime DAG."""
from __future__ import annotations

from etalon.active.schema import canonical, digest
from etalon.active.store import StateError
from etalon.runtime.schema import workflow, SCHEMA, references
from .diagnostics import JointModel
from .journal import ResearchJournal


def record_program(journal: ResearchJournal, model: JointModel, plan: dict) -> str:
    if plan.get('model_id') != model.id or plan.get('schema') != 'etalon-diagnostic-program/1':
        raise StateError('program must bind the explicit finite model')
    body = {k: v for k, v in plan.items() if k not in {'program_id', 'search_statistics'}}
    if plan['program_id'] != 'program-' + digest(body):
        raise StateError('program changed after planning')
    with journal.store.connection(write=True) as db:
        journal._artifacts(db, model.evidence, diagnostic_inputs=True)
        journal._artifacts(db, [r.existing_artifact_id for r in model.resources if r.existing_artifact_id],
                           diagnostic_inputs=True)
        journal._put(db, model.id, model.as_dict())
        return journal._put(db, plan['program_id'], body)


def compile_first_segment(journal: ResearchJournal, program_id: str, operation_bindings: dict) -> dict:
    """Caller supplies reviewed fixed operation bindings; no dynamic shell or unseen branches."""
    plan = journal.get(program_id)
    root = plan['program']
    if not isinstance(root, dict) or root.get('kind') != 'query':
        raise StateError('program has no authorized next query')
    binding = operation_bindings.get(root['probe_id'])
    if not isinstance(binding, dict) or set(binding) != {'operation', 'arguments'}:
        raise StateError('selected primitive has no explicit installed runtime binding')
    if references(binding['arguments']):
        raise StateError('first segment arguments must bind existing evidence, not future branches')
    unit = plan['cost_unit']
    cost = root['quoted_incremental_cost']
    compiled = workflow({'schema': SCHEMA, 'objective': 'Execute frozen diagnostic segment ' + program_id,
        'nodes': [{'id': 'diagnostic_step', **binding, 'resources': {unit: cost}}],
        'limits': {unit: cost}, 'controller': {'mode': 'ordered'}})
    receipt = {'program_id': program_id, 'probe_id': root['probe_id'], 'workflow': compiled,
               'workflow_sha256': digest(compiled), 'status': 'compiled_not_executed'}
    with journal.store.connection(write=True) as db:
        _program_artifacts(journal, db, plan)
        journal._put(db, 'segment-' + digest(receipt), receipt)
    return receipt


def _program_artifacts(journal, db, plan):
    """Recording a model does not freeze the bytes at its artifact paths."""
    model = journal._get(db, plan['model_id'])
    journal._artifacts(db, model['evidence'], diagnostic_inputs=True)
    journal._artifacts(db, [r['existing_artifact_id'] for r in model['resources']
                            if r.get('existing_artifact_id')], diagnostic_inputs=True)


def observed_branch(journal: ResearchJournal, program_id: str, observation_id: str, *, readout: str) -> dict:
    """Read a recorded discrete label under the selected probe's frozen bindings.

    Numeric values are not thresholded here. Use ``bind_outcome`` followed by
    ``condition`` or ``exact_program`` for a separately declared numeric rule.
    Only identities actually declared in the probe bindings can be checked.
    """
    plan = journal.get(program_id)
    root = plan['program']
    if not isinstance(root, dict) or root.get('kind') != 'query':
        raise StateError('program has no authorized next query')
    if not observation_id.startswith('observation-'):
        raise StateError('an admitted observation is required')
    observation = journal.get(observation_id)
    experiment = journal.get(observation['experiment_id'])
    if experiment['bindings'].get('probe_id') != root['probe_id'] or any(
            experiment['bindings'].get(key) != value for key, value in root['bindings'].items()):
        raise StateError('observation is not bound to the current program probe')
    if readout not in experiment['readouts'] or (
            observation['status'] == 'measured' and readout not in observation['values']):
        raise StateError('observation does not provide the declared readout')
    with journal.store.connection() as db:
        _program_artifacts(journal, db, plan)
        journal._artifacts(db, observation['evidence'], diagnostic_inputs=True)
    outcome = str(observation['values'][readout]) if observation['status'] == 'measured' else observation['status']
    branch = root['branches'].get(outcome)
    return {'status': 'observed_branch' if branch else 'model_challenge', 'observation_id': observation_id,
            'outcome': outcome, 'program': branch['program'] if branch else None,
            'requires_replanning': True, 'scope': 'no unexecuted branch is an observation'}
