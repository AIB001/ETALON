"""Synthetic decision-theory fixtures only; no chemical or scientific performance claims."""
from dataclasses import replace
from itertools import product

import pytest

from etalon.research.diagnostics import JointModel, Probe, Resource, World, condition, exact_program


def xor_model(*, coupled=True, shared_cost=0.):
    resources = (Resource('a', .1, 'credit'), Resource('b', .1, 'credit'),
                 Resource('setup', shared_cost, 'credit'))
    probes = (Probe('x', 'measure', {'state': 'u'}, ('0', '1'), ('a', 'setup')),
              Probe('z', 'measure', {'state': 'u' if coupled else 'v'}, ('0', '1'), ('b', 'setup')))
    draws = list(product(range(2), repeat=2 if coupled else 3))
    worlds = tuple(World(str(i), 'theta-' + str(bits[0]), 1 / len(draws),
        {'x': str(bits[1]), 'z': str(bits[0] ^ (bits[1] if coupled else bits[2]))},
        {'keep': float(bits[0]), 'change': float(1 - bits[0])}) for i, bits in enumerate(draws))
    return JointModel({'theta-0': 'keep optimal', 'theta-1': 'change optimal'}, worlds, probes, resources,
        'synthetic_fixture', ('uniform binary nuisance; explicit shared state' if coupled else 'independent nuisance',),
        'unit loss for wrong binary decision', 'credit')


def test_exact_search_keeps_zero_single_query_value_prefix_for_xor():
    model = xor_model()
    greedy = exact_program(model, budget=1, max_depth=2, cost_weight=1, myopic=True)
    exact = exact_program(model, budget=1, max_depth=2, cost_weight=1)
    assert greedy['program']['kind'] == 'stop' and greedy['program']['objective'] == .5
    assert exact['program']['expected_loss'] == 0
    assert exact['program']['objective'] == pytest.approx(.2)
    assert exact['program']['worst_additional_cost'] == pytest.approx(.2)
    assert all(b['program']['kind'] == 'query' for b in exact['program']['branches'].values())


def test_uncoupled_nuisance_does_not_inherit_xor_information():
    result = exact_program(xor_model(coupled=False), budget=1, max_depth=2, cost_weight=1)
    assert result['program']['kind'] == 'stop' and result['program']['expected_loss'] == .5


def test_shared_setup_paid_once_hard_budget_and_explicit_reuse():
    model = xor_model(shared_cost=.2)
    result = exact_program(model, budget=.4, max_depth=2, cost_weight=1)
    assert result['program']['expected_additional_cost'] == pytest.approx(.4)
    assert exact_program(model, budget=.39, max_depth=2, cost_weight=1)['program']['kind'] == 'stop'
    reused = exact_program(model, budget=.2, max_depth=2, cost_weight=1, acquired_resources=('setup',))
    assert reused['program']['expected_additional_cost'] == pytest.approx(.2)


def test_zero_probability_joint_observation_is_external_model_challenge():
    model = xor_model()
    model = replace(model, worlds=tuple(replace(w, outcomes={'x': w.outcomes['x'], 'z': w.outcomes['x']}) for w in model.worlds))
    assert condition(model, {'x': '0'})['status'] == 'conditioned'
    result = exact_program(model, observed={'x': '0', 'z': '1'}, budget=1, max_depth=2, cost_weight=1)
    assert result['status'] == 'model_challenge' and result['program'] is None and result['posterior'] == {}


def test_missing_outcome_is_explicit_not_a_zero_value():
    model = xor_model()
    model = replace(model, probes=tuple(replace(p, outcomes=(*p.outcomes, 'missing')) for p in model.probes),
                    worlds=tuple(replace(w, outcomes={**w.outcomes, 'z': 'missing'}) for w in model.worlds))
    conditioned = condition(model, {'z': 'missing'})
    assert conditioned['posterior'] == {'theta-0': .5, 'theta-1': .5}
    assert exact_program(model, budget=1, max_depth=2, cost_weight=1)['program']['kind'] == 'stop'


def test_model_rejects_mixed_units_unpriced_resources_and_cycles():
    model = xor_model()
    with pytest.raises(ValueError, match='unit'): replace(model, resources=(replace(model.resources[0], unit='second'), *model.resources[1:]))
    with pytest.raises(ValueError, match='unpriced'): replace(model, resources=model.resources[:1])
    with pytest.raises(ValueError, match='cyclic'): replace(model, probes=tuple(replace(p, depends_on=(p.id,)) for p in model.probes))
    with pytest.raises(ValueError, match='original execution'): Resource('old', 1, 'credit', 'artifact-old')


def test_program_identity_changes_with_coupling_and_model_assumptions():
    a, b = xor_model(), xor_model(coupled=False)
    assert a.id != b.id
    assert exact_program(a, budget=1, max_depth=2, cost_weight=1)['program_id'] != exact_program(b, budget=1, max_depth=2, cost_weight=1)['program_id']
