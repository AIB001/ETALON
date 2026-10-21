"""V independent cases: distinct provenance files and actual advice boundaries."""
from dataclasses import replace
from pathlib import Path

import pytest

from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Coupling, Experiment, Observation
from etalon.research.calibration import empirical_model
from etalon.research.diagnostics import Resource, exact_program
from etalon.research.runtime import compile_first_segment, observed_branch, record_program
from test_research_use_methods import build_use_fixture
from test_research_optional_methods import build_optional_fixture


@pytest.mark.parametrize('changed', ['model', 'resource'])
def test_v_replay_checks_distinct_original_model_and_resource_not_only_observation(tmp_path, changed):
    store, journal, original, _, model, _, _, observation = build_use_fixture(tmp_path/'base')
    separate = {}
    for name in ('model', 'resource'):
        path = tmp_path / (name + '.json')
        path.write_text('{"separate": "' + name + '"}', encoding='utf8')
        artifact = replace(original, path=str(path), **file_identity(path))
        journal.record(artifact)
        separate[name] = artifact
    model = replace(model, evidence=(separate['model'].id,),
                    resources=(*model.resources, Resource('reused', 0, 'credit', separate['resource'].id)))
    plan = exact_program(model, budget=.1, max_depth=1, cost_weight=1)
    program = record_program(journal, model, plan)
    binding = {'q': {'operation': 'design.import', 'arguments': {
        'database': str(store.path), 'bundle_path': original.path, 'mode': 'index_only'}}}
    compiled = compile_first_segment(journal, program, binding)
    assert compiled['status'] == 'compiled_not_executed'
    assert observed_branch(journal, program, observation.id, readout='label')['status'] == 'observed_branch'
    before, balance = store.export(), store.balance()
    Path(separate[changed].path).write_text('different but observation bytes intact', encoding='utf8')
    assert file_identity(Path(original.path))['sha256'] == original.sha256
    # Even a previously compiled identical segment cannot bypass current evidence validation.
    with pytest.raises(StateError, match='artifact changed'):
        compile_first_segment(journal, program, binding)
    with pytest.raises(StateError, match='artifact changed'):
        observed_branch(journal, program, observation.id, readout='label')
    assert store.export() == before and store.balance() == balance
    assert balance['spent'] == .25 and balance['reserved'] == 2


def test_v_unequal_group_sizes_have_equal_mass_without_claiming_independent_rows(tmp_path):
    store, _, _, arguments, _, _, _, _ = build_use_fixture(tmp_path/'groups')
    rows = [dict(arguments['rows'][0], task_id='a' + str(i)) for i in range(3)]
    rows += [dict(arguments['rows'][2], task_id='b'),
             dict(arguments['rows'][2], task_id='c', independence_group='C')]
    args = arguments | {'rows': rows, 'development_tasks': tuple(r['task_id'] for r in rows)}
    before = store.export()
    balanced = empirical_model(**args)
    assert [w.probability for w in balanced.worlds] == pytest.approx([1/9]*3 + [1/3]*2)
    reordered = empirical_model(**(args | {'rows': list(reversed(rows))}))
    assert {w.id: w.probability for w in balanced.worlds} == {w.id: w.probability for w in reordered.worlds}
    assert any('held-out gain remain untested' in s for s in balanced.assumptions)
    with pytest.raises(ValueError, match='correlated'):
        empirical_model(**(args | {'group_balanced': False}))
    with pytest.raises(ValueError, match='independent development groups'):
        empirical_model(**(args | {'rows': [dict(r, independence_group='A') for r in rows]}))
    assert store.export() == before


def test_v_existing_resource_role_rejects_program_atomically_even_with_good_model_evidence(tmp_path):
    store, journal, artifact, _, model, _, _, _ = build_use_fixture(tmp_path/'roles')
    held = replace(artifact, role='independent_validation')
    journal.record(held)
    model = replace(model, resources=(*model.resources, Resource('held-reuse', 0, 'credit', held.id)))
    plan = exact_program(model, budget=.1, max_depth=1, cost_weight=1)
    before = store.export()
    with pytest.raises(StateError, match='independent/holdout'):
        record_program(journal, model, plan)
    assert store.export() == before
    with pytest.raises(StateError, match='unknown research record'):
        journal.get(model.id)
    with pytest.raises(StateError, match='unknown research record'):
        journal.get(plan['program_id'])


def test_v_feedback_can_return_future_query_but_never_dispatch_or_compile_it(tmp_path, monkeypatch):
    store, journal, refs, _, model = build_optional_fixture(tmp_path/'future')
    experiment = Experiment('explicit-fixture-task', 'file_audit',
        {'probe_id': 'x', 'condition': 'synthetic'}, (refs['artifact'],),
        'categorical/1', 'fixture', ('label',), Coupling())
    journal.record(experiment)
    obs = Observation(experiment.id, 'measured', {'label': 'low'}, {'label': 'category'}, (refs['artifact'],))
    journal.record(obs)
    from etalon.runtime import service
    import etalon.research.runtime as research_runtime
    def prohibited(*args, **kwargs):
        pytest.fail('reading branch dispatched or compiled future work')
    monkeypatch.setattr(service, 'submit', prohibited)
    monkeypatch.setattr(research_runtime, 'compile_first_segment', prohibited)
    before, balance = store.export(), store.balance()
    feedback = observed_branch(journal, refs['plan_id'], obs.id, readout='label')
    assert feedback['program']['kind'] == 'query' and feedback['program']['probe_id'] == 'z'
    assert feedback['requires_replanning'] is True
    assert store.export() == before and store.balance() == balance and not store.actions()
    # A numeric readout is not the threshold label "low" in this legacy categorical API.
    numeric = replace(obs, values={'label': .2}, units={'label': 'count'})
    journal.record(numeric)
    answer = observed_branch(journal, refs['plan_id'], numeric.id, readout='label')
    assert answer['status'] == 'model_challenge' and answer['outcome'] == '0.2'
    assert answer['program'] is None
