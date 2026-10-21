"""Optional diagnostic SDK composition on synthetic persistent evidence only."""
from dataclasses import replace
from itertools import product
import json
from pathlib import Path

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Artifact, Coupling, Experiment, Observation, ResearchJournal
from etalon.research.calibration import OutcomeRule, bind_outcome
from etalon.research.diagnostics import JointModel, Probe, Resource, World, condition, exact_program
from etalon.research.runtime import record_program


def build_optional_fixture(root: Path):
    """New isolated Journal, no Campaign/Supervisor/policy fit or real execution."""
    root.mkdir(parents=True, exist_ok=False)
    store = CampaignStore(root / 'campaign.sqlite')
    store.configure(CampaignSpec('unmeasured', 4, 'fixture_credit', 'optional-method-fixture'), [
        Endpoint('unmeasured', 'test', 'not_measured', 'count', 'fixture/1', 1, queryable=False)])
    path = root / 'synthetic-readouts.json'
    path.write_text(json.dumps({'kind': 'synthetic_engineering_fixture', 'signal': 0.2}), encoding='utf-8')
    artifact = Artifact(str(path.absolute()), **file_identity(path), source_kind='fixture',
                        role='diagnostic', bindings={'condition': 'synthetic'})
    journal = ResearchJournal(store)
    journal.record(artifact)
    experiment = Experiment('fixture-task', 'file_audit', {'condition': 'synthetic'},
                            (artifact.id,), 'fixed-readout/1', 'fixture-instance', ('signal',), Coupling())
    journal.record(experiment)
    observations = {}
    for status in ('measured', 'failed', 'missing', 'unsupported', 'not_evaluated'):
        observation = Observation(experiment.id, status,
            {'signal': 0.2} if status == 'measured' else {},
            {'signal': 'count'} if status == 'measured' else {}, (artifact.id,),
            '' if status == 'measured' else 'synthetic fixture outcome; no scientific execution')
        journal.record(observation)
        observations[status] = observation.id
    # Ledger fixtures are explicitly synthetic; no worker has ever been launched.
    store.reserve_resource_execution('fixture-failed', 'a' * 64, .5,
                                     unit='fixture_credit', provenance={'kind': 'synthetic_fixture'})
    store.settle_resource_execution('fixture-failed', measured_cost=.25,
        receipt={'status': 'failed', 'kind': 'synthetic_fixture'})
    store.reserve_resource_execution('fixture-pending', 'b' * 64, 2,
                                     unit='fixture_credit', provenance={'kind': 'synthetic_fixture'})
    store.settle_resource_execution('fixture-pending', measured_cost=None,
        receipt={'status': 'outcome_unknown', 'kind': 'synthetic_fixture'})
    rule = OutcomeRule('x', 'fixed-readout/1', 'signal', 'count', .5, 'low', 'high')
    resources = (Resource('setup', .25, 'fixture_credit'), Resource('x', .1, 'fixture_credit'),
                 Resource('z', .1, 'fixture_credit'))
    probes = tuple(Probe(p, 'measure', {'condition': 'synthetic'}, ('low', 'high'), (p, 'setup'))
                   for p in ('x', 'z'))
    label = lambda bit: ('low', 'high')[bit]
    worlds = tuple(World(str(i), str(theta), .25,
                         {'x': label(nuisance), 'z': label(theta ^ nuisance)},
                         {'keep': float(theta), 'change': float(1 - theta)})
                   for i, (theta, nuisance) in enumerate(product(range(2), repeat=2)))
    model = JointModel({'0': 'keep optimal', '1': 'change optimal'}, worlds, probes, resources,
        'synthetic_fixture', ('declared correlated XOR outcomes; not measured probabilities',),
        'synthetic loss of wrong binary decision', 'fixture_credit', (artifact.id,))
    plan = exact_program(model, budget=.45, max_depth=2, cost_weight=1)
    record_program(journal, model, plan)  # Optional append; no compilation or submit.
    refs = {'database': str(store.path), 'artifact': artifact.id, 'artifact_path': str(path),
            'observations': observations, 'rule': vars(rule), 'model': model.as_dict(),
            'model_id': model.id, 'plan_id': plan['program_id'], 'task_id': 'fixture-task',
            'expected_bindings': {'condition': 'synthetic'}}
    (root / 'inputs.json').write_text(json.dumps(refs, indent=2), encoding='utf-8')
    return store, journal, refs, rule, model


def bind(journal, refs, rule, status='measured', **overrides):
    return bind_outcome(journal, refs['observations'][status], rule,
        **({'task_id': refs['task_id'], 'expected_bindings': refs['expected_bindings']} | overrides))


def test_real_bound_output_selects_next_probe_without_dispatch_or_workflow(tmp_path):
    store, journal, refs, rule, model = build_optional_fixture(tmp_path / 'fixture')
    balance, labels = store.balance(), store.observations()
    bounded = bind(journal, refs, rule)
    assert bounded['outcome'] == 'low'
    assert bounded['likelihood'] is None and bounded['causal_hypothesis'] is None
    events = store.events()
    assert bind(journal, refs, rule) == bounded and store.events() == events
    observed = {bounded['probe_id']: bounded['outcome']}
    conditioned = condition(model, observed)
    assert conditioned['status'] == 'conditioned' and conditioned['posterior'] == {'0': .5, '1': .5}
    # Actual prior output feeds the optional finite suggestion. Host may decline it.
    suggestion = exact_program(model, observed=observed, budget=.1, max_depth=1, cost_weight=1)
    assert suggestion['program']['probe_id'] == 'z'
    assert suggestion['program']['expected_additional_cost'] == pytest.approx(.1)
    assert suggestion['initial_resources'] == ['setup', 'x']
    assert store.events() == events and store.balance() == balance and store.observations() == labels == []
    assert balance['spent'] == .25 and balance['reserved'] == 2


@pytest.mark.parametrize('status', ['failed', 'missing', 'unsupported', 'not_evaluated'])
def test_unavailable_observation_stops_outside_model_without_zero_imputation(tmp_path, status):
    store, journal, refs, rule, model = build_optional_fixture(tmp_path / status)
    balance = store.balance()
    bounded = bind(journal, refs, rule, status)
    assert bounded['outcome'] == status
    observed = {bounded['probe_id']: bounded['outcome']}
    result = exact_program(model, observed=observed, budget=.45, max_depth=2, cost_weight=1)
    assert result['status'] == 'model_challenge'
    assert result['posterior'] == {} and result['program'] is None and result['decision'] is None
    assert journal.get(refs['observations'][status])['values'] == {}
    assert store.balance() == balance and not store.observations()


def test_quotes_budget_and_optional_myopic_stop_do_not_spend(tmp_path):
    store, journal, refs, rule, model = build_optional_fixture(tmp_path / 'fixture')
    balance, events = store.balance(), store.events()
    exact = exact_program(model, budget=.45, max_depth=2, cost_weight=1)
    assert exact['program']['worst_additional_cost'] == pytest.approx(.45)
    assert exact['program']['expected_additional_cost'] == pytest.approx(.45)
    assert exact_program(model, budget=.44, max_depth=2, cost_weight=1)['program']['kind'] == 'stop'
    assert exact_program(model, budget=.45, max_depth=2, cost_weight=1, myopic=True)['program']['kind'] == 'stop'
    with pytest.raises(ValueError, match='no counterfactual imputation'):
        exact_program(model, budget=.45, max_depth=2, cost_weight=1,
                      world_resource_costs={world.id: {'x': .1} for world in model.worlds})
    assert store.events() == events and store.balance() == balance


def test_binding_refuses_wrong_task_condition_unit_method_role_and_changed_bytes(tmp_path):
    store, journal, refs, rule, model = build_optional_fixture(tmp_path / 'fixture')
    cases = [(rule, {'task_id': 'other'}, 'task/condition'),
             (rule, {'expected_bindings': {'condition': 'other'}}, 'task/condition'),
             (replace(rule, unit='angstrom'), {}, 'unit'),
             (replace(rule, measurement_method_id='other'), {}, 'measurement')]
    for proposed, overrides, message in cases:
        events, balance = store.events(), store.balance()
        with pytest.raises(StateError, match=message): bind(journal, refs, proposed, **overrides)
        assert store.events() == events and store.balance() == balance
    source = Artifact(**journal.get(refs['artifact']))
    for role in ('independent_validation', 'sealed_holdout'):
        held = replace(source, role=role)
        journal.record(held)
        old = journal.get(refs['observations']['measured'])
        observation = Observation(old['experiment_id'], 'measured', old['values'], old['units'], (held.id,))
        journal.record(observation)
        events, balance = store.events(), store.balance()
        with pytest.raises(StateError, match='independent/holdout'):
            bind_outcome(journal, observation.id, rule, task_id=refs['task_id'],
                         expected_bindings=refs['expected_bindings'])
        assert store.events() == events and store.balance() == balance
    Path(refs['artifact_path']).write_text('changed synthetic input', encoding='utf-8')
    events, balance = store.events(), store.balance()
    with pytest.raises(StateError, match='artifact changed'): bind(journal, refs, rule)
    assert store.events() == events and store.balance() == balance
