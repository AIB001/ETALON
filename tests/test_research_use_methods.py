"""Synthetic paired-row model and optional discrete-feedback boundaries."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Coupling, Experiment, Observation
from etalon.research.calibration import empirical_model
from etalon.research.diagnostics import Probe, Resource, exact_program
from etalon.research.runtime import compile_first_segment, observed_branch, record_program
from test_research_journal import setup


def build_use_fixture(root):
    """New synthetic task groups only; no old scientific evidence or execution."""
    root.mkdir(parents=True, exist_ok=False)
    store, journal, artifact = setup(root)
    rows = [dict(task_id=task, independence_group=group, evidence=[artifact.id],
                 outcomes={'q': outcome}, losses={'keep': loss, 'change': 1 - loss})
            for task, group, outcome, loss in [('a1', 'A', 'low', 0),
                                              ('a2', 'A', 'low', 0),
                                              ('b', 'B', 'high', 1)]]
    arguments = dict(rows=rows,
        probes=(Probe('q', 'measure', {'condition': 'synthetic-fixed'},
                      ('low', 'high'), ('cpu',)),),
        resources=(Resource('cpu', .1, 'credit'),), evidence=(artifact.id,),
        development_tasks=('a1', 'a2', 'b'), heldout_tasks=('heldout-unseen-fixture',),
        loss_definition='synthetic wrong-decision unit loss; no measured benefit',
        cost_unit='credit', group_balanced=True)
    model = empirical_model(**arguments)
    plan = exact_program(model, budget=.1, max_depth=1, cost_weight=1)
    program_id = record_program(journal, model, plan)
    experiment = Experiment('new-synthetic-task', 'file_audit',
        {'probe_id': 'q', 'condition': 'synthetic-fixed'}, (artifact.id,),
        'synthetic-label/1', 'new-fixture-instance', ('label',), Coupling())
    journal.record(experiment)
    observation = Observation(experiment.id, 'measured', {'label': 'low'},
                              {'label': 'category'}, (artifact.id,))
    journal.record(observation)
    store.reserve_resource_execution('failed-fixture', 'a' * 64, 1,
        unit='credit', provenance={'kind': 'synthetic_fixture'})
    store.settle_resource_execution('failed-fixture', measured_cost=.25,
        receipt={'status': 'failed', 'kind': 'synthetic_fixture'})
    store.reserve_resource_execution('pending-fixture', 'b' * 64, 2,
        unit='credit', provenance={'kind': 'synthetic_fixture'})
    store.settle_resource_execution('pending-fixture', measured_cost=None,
        receipt={'status': 'outcome_unknown', 'kind': 'synthetic_fixture'})
    (root / 'inputs.json').write_text(json.dumps({
        'scope': 'synthetic_engineering_fixture', 'database': str(store.path),
        'rows': rows, 'model': model.as_dict(), 'program_id': program_id,
        'observation_id': observation.id}, indent=2), encoding='utf-8')
    return store, journal, artifact, arguments, model, program_id, experiment, observation


def test_empirical_group_weights_are_explicit_and_model_recording_is_optional(tmp_path):
    store, journal, _, arguments, model, program, _, observation = build_use_fixture(tmp_path / 'fixture')
    events, balance = store.events(), store.balance()
    repeated = empirical_model(**arguments)
    assert repeated == model
    assert [world.probability for world in model.worlds] == [.25, .25, .5]
    assert any('equal group weights' in text for text in model.assumptions)
    assert any('remain untested' in text for text in model.assumptions)
    assert exact_program(model, budget=.1, max_depth=1, cost_weight=1)['program']['kind'] == 'query'
    feedback = observed_branch(journal, program, observation.id, readout='label')
    assert feedback['status'] == 'observed_branch'
    assert feedback['program']['kind'] == 'stop' and feedback['program']['decision'] == 'keep'
    assert feedback['requires_replanning'] is True
    assert store.events() == events and store.balance() == balance
    assert balance['spent'] == .25 and balance['reserved'] == 2 and not store.observations()


def test_empirical_rows_refuse_replication_leakage_and_missing_pairs(tmp_path):
    store, _, _, arguments, _, _, _, _ = build_use_fixture(tmp_path / 'fixture')
    events, balance = store.events(), store.balance()
    cases = [({'group_balanced': False}, 'correlated'),
             ({'heldout_tasks': ('a1',)}, 'disjoint'),
             ({'heldout_tasks': ()}, 'disjoint'),
             ({'rows': arguments['rows'][:2]}, 'independent development groups'),
             ({'rows': [arguments['rows'][0], arguments['rows'][0]]}, 'correlated'),
             ({'rows': [arguments['rows'][0], {**arguments['rows'][2], 'outcomes': {}}]}, 'full joint'),
             ({'rows': [arguments['rows'][0], {**arguments['rows'][2], 'losses': {'keep': 1}}]}, 'full joint'),
             ({'rows': [arguments['rows'][0], {**arguments['rows'][2], 'evidence': ['undeclared']}]}, 'declared evidence')]
    for changes, message in cases:
        with pytest.raises(ValueError, match=message): empirical_model(**(arguments | changes))
    assert store.events() == events and store.balance() == balance


def test_empirical_pure_function_does_not_claim_artifact_admission(tmp_path):
    store, journal, artifact, arguments, _, _, _, _ = build_use_fixture(tmp_path / 'fixture')
    for role in ('independent_validation', 'sealed_holdout'):
        held = replace(artifact, role=role)
        journal.record(held)
        args = arguments | {'evidence': (held.id,), 'rows': [dict(r, evidence=[held.id]) for r in arguments['rows']]}
        model = empirical_model(**args)  # Pure constructor has no Journal access.
        plan = exact_program(model, budget=.1, max_depth=1, cost_weight=1)
        events, balance = store.events(), store.balance()
        with pytest.raises(StateError, match='independent/holdout'): record_program(journal, model, plan)
        assert store.events() == events and store.balance() == balance
        with pytest.raises(StateError, match='unknown research record'): journal.get(model.id)


def test_observed_branch_rejects_mismatched_declared_conditions_without_writes(tmp_path):
    store, journal, _, _, _, program, experiment, observation = build_use_fixture(tmp_path / 'fixture')
    wrong = replace(experiment, bindings={'probe_id': 'q', 'condition': 'different'})
    journal.record(wrong)
    wrong_observation = replace(observation, experiment_id=wrong.id)
    journal.record(wrong_observation)
    events, balance = store.events(), store.balance()
    with pytest.raises(StateError, match='current program probe'):
        observed_branch(journal, program, wrong_observation.id, readout='label')
    with pytest.raises(StateError, match='declared readout'):
        observed_branch(journal, program, observation.id, readout='absent')
    with pytest.raises(StateError, match='admitted observation'):
        observed_branch(journal, program, experiment.id, readout='label')
    assert store.events() == events and store.balance() == balance


def test_discrete_branch_preserves_failure_and_never_invents_numeric_threshold(tmp_path):
    store, journal, _, _, _, program, _, observation = build_use_fixture(tmp_path / 'fixture')
    candidates = [replace(observation, status='failed', values={}, units={}, reason='fixture failure'),
                  replace(observation, values={'label': .2}, units={'label': 'count'})]
    for candidate, expected in zip(candidates, ('failed', '0.2')):
        journal.record(candidate)
        events, balance = store.events(), store.balance()
        result = observed_branch(journal, program, candidate.id, readout='label')
        assert result['status'] == 'model_challenge' and result['program'] is None
        assert result['outcome'] == expected and result['requires_replanning']
        assert store.events() == events and store.balance() == balance


def test_stop_program_cannot_compile_or_consume_feedback(tmp_path):
    store, journal, _, _, model, _, _, observation = build_use_fixture(tmp_path / 'fixture')
    plan = exact_program(model, budget=0, max_depth=0, cost_weight=1)
    identifier = record_program(journal, model, plan)
    events, balance = store.events(), store.balance()
    with pytest.raises(StateError, match='no authorized next query'):
        compile_first_segment(journal, identifier, {})
    with pytest.raises(StateError, match='no authorized next query'):
        observed_branch(journal, identifier, observation.id, readout='label')
    assert store.events() == events and store.balance() == balance


@pytest.mark.parametrize('source', ['model_evidence', 'existing_resource'])
def test_saved_model_and_reused_resource_bytes_are_rechecked(tmp_path, source):
    store, journal, artifact, _, model, program, _, observation = build_use_fixture(tmp_path / 'fixture')
    changed_path = Path(artifact.path)
    if source == 'existing_resource':
        changed_path = tmp_path / 'cached-resource.json'
        changed_path.write_text('{"source": "synthetic cached result"}', encoding='utf-8')
        cached = replace(artifact, path=str(changed_path), **file_identity(changed_path))
        journal.record(cached)
        model = replace(model, resources=(*model.resources, Resource('cached', 0, 'credit', cached.id)))
        program = record_program(journal, model, exact_program(model, budget=.1, max_depth=1, cost_weight=1))
    binding = {'q': {'operation': 'design.import', 'arguments': {
        'database': str(store.path), 'bundle_path': artifact.path, 'mode': 'index_only'}}}
    changed_path.write_text('changed fixture bytes', encoding='utf-8')
    events, balance = store.events(), store.balance()
    with pytest.raises(StateError, match='artifact changed'):
        compile_first_segment(journal, program, binding)
    with pytest.raises(StateError, match='artifact changed'):
        observed_branch(journal, program, observation.id, readout='label')
    assert store.events() == events and store.balance() == balance
