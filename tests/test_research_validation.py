"""Fixture tests for immutable decision checks and empirical model admission."""
from dataclasses import replace
import pytest

from etalon.active.schema import digest
from etalon.active.store import StateError
from etalon.research import Observation
from etalon.research.validation import attach_decision_validation
from etalon.research.calibration import OutcomeRule, bind_outcome, empirical_model
from etalon.research.diagnostics import Probe, Resource, exact_program
from test_research_journal import setup, experiment


def test_validation_appends_without_mutation_promotion_or_charge(tmp_path):
    store, journal, artifact = setup(tmp_path)
    parent = {'task_id': 'dev', 'decision': {'delta': {}, 'status': 'unresolved'},
              'independent_validation_accessed': False}
    sha = digest(parent); parent['freeze_sha256'] = sha
    with store.connection(write=True) as db:
        journal._put(db, 'decision-' + sha, parent)
    released = replace(artifact, role='independent_validation', bindings={'decision_sha256': sha})
    journal.record(released)
    args = dict(decision_sha256=sha, evidence=(released.id,), release_evidence=released.id,
                protocol={'method': 'fixture'}, comparison={'value': 1})
    balance = store.balance()
    first = attach_decision_validation(journal, **args); events = store.events()
    assert attach_decision_validation(journal, **args) == first
    assert store.events() == events and store.balance() == balance
    assert journal.get('decision-' + sha) == parent
    assert first['phi_transfer'] == 'not_evaluated' and first['promotion'] == 'none'
    with pytest.raises(StateError, match='independent_validation'):
        attach_decision_validation(journal, **{**args, 'evidence': (artifact.id,)})
    wrong = replace(released, bindings={'decision_sha256': '0' * 64}); journal.record(wrong)
    with pytest.raises(StateError, match='exact decision'):
        attach_decision_validation(journal, **{**args, 'release_evidence': wrong.id})


def test_outcomes_require_actual_bound_observations_and_preserve_missing(tmp_path):
    _, journal, artifact = setup(tmp_path)
    ex = experiment('dev', artifact); journal.record(ex)
    obs = Observation(ex.id, 'measured', {'score': 2}, {'score': 'count'}, (artifact.id,)); journal.record(obs)
    rule = OutcomeRule('geometry', ex.method_id, 'score', 'count', 1, 'low', 'high')
    args = dict(task_id='dev', expected_bindings={'condition': '0'})
    assert bind_outcome(journal, obs.id, rule, **args)['outcome'] == 'high'
    with pytest.raises(StateError, match='unit'):
        bind_outcome(journal, obs.id, replace(rule, unit='angstrom'), **args)
    missing = replace(obs, status='missing', values={}, units={}, reason='not measured'); journal.record(missing)
    assert bind_outcome(journal, missing.id, rule, **args)['outcome'] == 'missing'
    held = replace(artifact, role='independent_validation'); journal.record(held)
    leaked = replace(obs, evidence=(held.id,)); journal.record(leaked)
    with pytest.raises(StateError, match='holdout'):
        bind_outcome(journal, leaked.id, rule, **args)


def test_empirical_joint_q_requires_independent_paired_tasks_and_no_leakage():
    rows = [dict(task_id=t, independence_group=t, evidence=['fixture-artifact'],
                 outcomes={'q': outcome}, losses={'keep': loss, 'change': 1-loss})
            for t, outcome, loss in [('a', 'low', 0), ('b', 'high', 1)]]
    args = dict(probes=(Probe('q', 'measure', {}, ('low', 'high'), ('cpu',)),),
                resources=(Resource('cpu', .1, 'credit'),), evidence=('fixture-artifact',),
                development_tasks=('a', 'b'), heldout_tasks=('c',),
                loss_definition='fixture independent loss', cost_unit='credit')
    model = empirical_model(rows=rows, **args)
    assert exact_program(model, budget=1, max_depth=1, cost_weight=1)['program']['kind'] == 'query'
    with pytest.raises(ValueError, match='insufficient'):
        empirical_model(rows=rows[:1], **args)
    with pytest.raises(ValueError, match='correlated'):
        empirical_model(rows=[rows[0], {**rows[1], 'independence_group': 'a'}], **args)
    with pytest.raises(ValueError, match='disjoint'):
        empirical_model(rows=rows, **{**args, 'heldout_tasks': ('a',)})
