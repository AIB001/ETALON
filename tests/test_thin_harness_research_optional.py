"""Independent V cases for optional finite-model methods; no scientific gain claims."""
from dataclasses import replace
import copy

import pytest

from etalon.active.store import StateError
from etalon.research import Observation
from etalon.research.calibration import bind_outcome
from etalon.research.diagnostics import condition, exact_program
from test_research_optional_methods import build_optional_fixture


def bind(journal, refs, rule, observation_id):
    return bind_outcome(journal, observation_id, rule, task_id=refs['task_id'],
                        expected_bindings=refs['expected_bindings'])


def test_v_joint_challenge_after_two_real_bindings_not_marginal_attribution(tmp_path):
    store, journal, refs, rule, model = build_optional_fixture(tmp_path / 'joint')
    worlds = tuple(replace(w, probability=.5) for w in model.worlds
                   if w.outcomes['x'] == w.outcomes['z'])
    restricted = replace(model, worlds=worlds)
    original = journal.get(refs['observations']['measured'])
    high = Observation(original['experiment_id'], 'measured', {'signal': .8},
                       {'signal': 'count'}, tuple(original['evidence']))
    journal.record(high)
    x = bind(journal, refs, rule, refs['observations']['measured'])
    z = bind(journal, refs, replace(rule, probe_id='z'), high.id)
    observed = {x['probe_id']: x['outcome'], z['probe_id']: z['outcome']}
    assert condition(restricted, {'x': observed['x']})['status'] == 'conditioned'
    assert condition(restricted, {'z': observed['z']})['status'] == 'conditioned'
    before, balance = store.export(), store.balance()
    result = condition(restricted, observed)
    assert result['status'] == 'model_challenge' and result['posterior'] == {}
    advice = exact_program(restricted, observed=observed, budget=.45,
                           max_depth=2, cost_weight=1)
    assert advice['program'] is None and advice['decision'] is None
    assert store.export() == before and store.balance() == balance
    assert x['likelihood'] is None and z['causal_hypothesis'] is None


def test_v_acquired_setup_and_joint_measured_costs_do_not_debit_ledger(tmp_path):
    store, _, _, _, model = build_optional_fixture(tmp_path / 'cost')
    before, balance = store.export(), store.balance()
    costs = {w.id: {'setup': .25, 'x': .02 if w.outcomes['x'] == 'low' else .08,
                    'z': .03} for w in model.worlds}
    quoted = exact_program(model, budget=.2, max_depth=2, cost_weight=1,
                           acquired_resources=('setup',))
    measured = exact_program(model, budget=.2, max_depth=2, cost_weight=1,
                             acquired_resources=('setup',), world_resource_costs=costs)
    assert quoted['program']['kind'] == measured['program']['kind'] == 'query'
    assert quoted['program']['expected_additional_cost'] == pytest.approx(.2)
    assert measured['program']['expected_additional_cost'] == pytest.approx(.08)
    assert measured['program']['worst_additional_cost'] == pytest.approx(.2)
    assert 'measured joint' in measured['cost_basis']
    assert quoted['initial_resources'] == measured['initial_resources'] == ['setup']
    invalid = copy.deepcopy(costs)
    invalid[model.worlds[0].id]['x'] = .11
    with pytest.raises(ValueError, match='exceeds reservation'):
        exact_program(model, budget=.2, max_depth=2, cost_weight=1,
                      acquired_resources=('setup',), world_resource_costs=invalid)
    assert store.export() == before and store.balance() == balance
    assert balance['spent'] == .25 and balance['reserved'] == 2


def test_v_equality_rule_and_replay_revalidate_evidence_after_binding(tmp_path):
    store, journal, refs, rule, _ = build_optional_fixture(tmp_path / 'binding')
    observation = refs['observations']['measured']
    low = bind(journal, refs, rule, observation)
    original_events = store.events()
    high = bind(journal, refs, replace(rule, threshold=.2), observation)
    assert low['outcome'] == 'low' and high['outcome'] == 'high'
    assert len(store.events()) == len(original_events) + 1
    assert store.events()[:len(original_events)] == original_events
    # This is a different rule identity, not proof of prospective threshold selection.
    assert high['rule'] != low['rule']
    before = store.export()
    from pathlib import Path
    Path(refs['artifact_path']).write_text('modified after a valid bind', encoding='utf8')
    with pytest.raises(StateError, match='artifact changed'):
        bind(journal, refs, rule, observation)
    assert store.export() == before  # no cached success bypasses current source validation


def test_v_direct_suggestions_skip_unrelated_policy_and_enforce_probe_dependencies(tmp_path, monkeypatch):
    store, _, _, _, model = build_optional_fixture(tmp_path / 'direct')
    from etalon.campaign.loop import Campaign
    from etalon.campaign.supervisor import Supervisor
    from etalon.research import transfer
    from etalon.runtime import api

    def forbidden(*args, **kwargs):
        pytest.fail('optional numerical method invoked orchestration or execution')

    monkeypatch.setattr(Campaign, '__init__', forbidden)
    monkeypatch.setattr(Supervisor, 'tick', forbidden)
    monkeypatch.setattr(transfer, 'fit_policy', forbidden)
    monkeypatch.setattr(api, 'submit_action', forbidden)
    before = store.export()
    assert condition(model, {})['status'] == 'conditioned'
    assert exact_program(model, budget=0, max_depth=0, cost_weight=1)['program']['kind'] == 'stop'
    dependent = replace(model, probes=tuple(replace(p, depends_on=('x',)) if p.id == 'z'
                                          else p for p in model.probes))
    with pytest.raises(ValueError, match='lacks its dependency'):
        exact_program(dependent, budget=.45, max_depth=2, cost_weight=1,
                      observed={'z': 'low'})
    with pytest.raises(ValueError, match='unknown acquired resource'):
        exact_program(model, budget=.45, max_depth=2, cost_weight=1,
                      acquired_resources=('fabricated',))
    assert store.export() == before


def test_v_failed_bind_keeps_unknown_and_no_method_gate_or_false_label(tmp_path):
    store, journal, refs, rule, model = build_optional_fixture(tmp_path / 'failure')
    original = store.export()
    before_balance = store.balance()
    for bad in (refs['artifact'], 'observation-' + '0' * 64):
        with pytest.raises(StateError):
            bind(journal, refs, rule, bad)
        assert store.export() == original
    result = bind(journal, refs, rule, refs['observations']['unsupported'])
    assert result['outcome'] == 'unsupported' and result['likelihood'] is None
    observed = {result['probe_id']: result['outcome']}
    assert condition(model, observed)['status'] == 'model_challenge'
    assert not store.actions() and not store.observations()
    assert store.balance() == before_balance
    assert all(not event['body'].get('record', {}).get('schema', '').startswith('etalon-method-')
               for event in store.events())
