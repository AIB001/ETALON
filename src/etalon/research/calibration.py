"""Bind recorded readouts to finite q outcomes without inventing likelihoods."""
from __future__ import annotations

from dataclasses import dataclass
from etalon.active.schema import digest, finite
from etalon.active.store import StateError
from .diagnostics import JointModel, Probe, Resource, World
from .journal import ResearchJournal


@dataclass(frozen=True)
class OutcomeRule:
    probe_id: str
    measurement_method_id: str
    readout: str
    unit: str
    threshold: float
    below: str
    at_or_above: str

    def __post_init__(self):
        finite(self.threshold, 'outcome threshold')
        if not all((self.probe_id, self.measurement_method_id, self.readout, self.unit,
                    self.below, self.at_or_above)) or self.below == self.at_or_above:
            raise ValueError('explicit fixed outcome rule required')


def bind_outcome(journal: ResearchJournal, observation_id: str, rule: OutcomeRule,
                 *, task_id: str, expected_bindings: dict[str, str]) -> dict:
    """Call only with a rule frozen before the next task. No model is fitted here."""
    with journal.store.connection(write=True) as db:
        obs = journal._get(db, observation_id)
        if not observation_id.startswith('observation-'):
            raise StateError('an admitted observation is required')
        ex = journal._get(db, obs['experiment_id'])
        if ex['task_id'] != task_id or not expected_bindings or any(
                ex['bindings'].get(k) != v for k, v in expected_bindings.items()):
            raise StateError('task/condition identity mismatch')
        if (obs.get('measurement_method_id') or ex['method_id']) != rule.measurement_method_id:
            raise StateError('measurement definition mismatch')
        journal._artifacts(db, obs['evidence'], diagnostic_inputs=True)
        if obs['status'] == 'measured':
            if obs['units'].get(rule.readout) != rule.unit:
                raise StateError('readout unit mismatch')
            value = obs['values'][rule.readout]
            finite(value, 'recorded readout')
            outcome = rule.below if value < rule.threshold else rule.at_or_above
        else:
            outcome = obs['status']
        result = {'schema': 'etalon-recorded-probe-outcome/1',
                  'observation_id': observation_id, 'probe_id': rule.probe_id,
                  'task_id': task_id, 'rule': vars(rule), 'expected_bindings': expected_bindings,
                  'outcome': outcome, 'source': 'admitted_observation',
                  'likelihood': None, 'causal_hypothesis': None}
        journal._put(db, 'probeoutcome-' + digest(result), result)
        return result


def empirical_model(*, rows: list[dict], probes: tuple[Probe, ...],
                    resources: tuple[Resource, ...], evidence: tuple[str, ...],
                    development_tasks: tuple[str, ...], heldout_tasks: tuple[str, ...],
                    loss_definition: str, cost_unit: str, group_balanced: bool = False) -> JointModel:
    """An explicit empirical JOINT distribution over complete development tasks.

    One row per independent task/group, never per correlated pose. Caller supplies
    measured paired outcomes and independently defined losses for every decision.
    Equal task weights are a declared model assumption, not calibrated confidence.
    Evidence is hash-checked when record_program admits this model to the journal.
    """
    if set(development_tasks) & set(heldout_tasks) or not heldout_tasks:
        raise ValueError('a disjoint held-out task set must be frozen')
    if len(rows) < 2:
        raise ValueError('insufficient independent development tasks; no fitted model')
    tasks = [r['task_id'] for r in rows]
    groups = [r['independence_group'] for r in rows]
    if len(set(tasks)) != len(rows) or (not group_balanced and len(set(groups)) != len(rows)):
        raise ValueError('correlated slots/tasks cannot be counted as independent rows')
    if len(set(groups)) < 2:
        raise ValueError('insufficient independent development groups; no fitted model')
    if not set(tasks) <= set(development_tasks) or not all(groups):
        raise ValueError('rows must belong to declared development tasks/groups')
    if not evidence or any(not r.get('evidence') or not set(r['evidence']) <= set(evidence) for r in rows):
        raise ValueError('every paired task row needs declared evidence')
    return JointModel(
        hypotheses={'empirical_task': 'empirical task variability; no HD/HP/HE attribution'},
        worlds=tuple(World(digest(r), 'empirical_task',
                           1 / (len(set(groups)) * groups.count(r['independence_group'])) if group_balanced else 1 / len(rows),
                           r['outcomes'], r['losses']) for r in rows),
        probes=probes, resources=resources, probability_basis='estimated_from_evidence',
        assumptions=(('equal group weights, equal task weights within group; no pose replication' if group_balanced
                      else 'equal independent-task weights; complete paired observations only'),
                     'empirical support is finite; unseen outcomes challenge the model',
                     'predictive calibration and held-out gain remain untested',
                     'split_sha256=' + digest({'development': development_tasks, 'heldout': heldout_tasks})),
        independent_loss_definition=loss_definition, cost_unit=cost_unit, evidence=evidence)
