"""Synthetic fixture tests only: no benchmark labels or chemical performance."""
from copy import deepcopy
from dataclasses import replace
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

import pytest

from etalon.research.diagnostics import Probe, Resource
from etalon.research.transfer import (Catalog, GroupSplit, checked_rows, evaluate_policies,
    fit_policy, load_policy, next_action, probability_assessment, save_policy, calibrate_policy)


def dataset():
    roles = ['train'] * 4 + ['calibration'] * 2 + ['test'] * 2
    split = GroupSplit({f't{i}': {'role': role, 'group': f'g{i}', 'family': f'f{i}'}
        for i, role in enumerate(roles)}, 'synthetic reviewed families', '0' * 64)
    catalog = Catalog((Probe('x', 'measure', {}, ('0', '1', 'failed'), ('setup', 'x')),
                       Probe('z', 'measure', {}, ('0', '1'), ('setup', 'z'))),
        (Resource('setup', .1, 'fixture_credit'), Resource('x', .1, 'fixture_credit'),
         Resource('z', .1, 'fixture_credit'), Resource('terminal', .1, 'fixture_credit')),
        {'keep': ('terminal',), 'change': ('terminal',), 'abstain': ()},
        'synthetic binary task error', 'fixture_credit', 'abstain', 'keep')
    rows = []
    for i in range(8):
        x, z = (i % 4) // 2, i % 2
        outcome = x ^ z
        rows.append({'task_id': f't{i}', 'sample_unit': 'task', 'independence_group': f'g{i}',
            'source_kind': 'synthetic_fixture', 'evidence': ['fixture-evidence'],
            'outcomes': {'x': str(x), 'z': str(z)},
            'losses': {'keep': outcome, 'change': 1 - outcome, 'abstain': 1.},
            'resource_costs': {r.id: .05 for r in catalog.resources},
            'loss_definition': catalog.loss_definition, 'cost_unit': catalog.cost_unit,
            'outcome_evidence': {p.id: ['fixture-evidence'] for p in catalog.probes},
            'loss_evidence': {d: ['fixture-evidence'] for d in catalog.decision_resources},
            'cost_evidence': {r.id: ['fixture-evidence'] for r in catalog.resources}})
    return split, catalog, rows


def trained():
    split, catalog, rows = dataset()
    return fit_policy(rows[:4], split=split, catalog=catalog, budget=.4, max_depth=2,
                      cost_weight=1, static_order=('x', 'z')), rows


def test_group_isolation_and_no_pose_sample_or_missing_counterfactual():
    split, catalog, rows = dataset()
    for changed in [dict(role='test', group='g0', family='f0'),
                    dict(role='test', group='different', family='f0')]:
        tasks = deepcopy(split.tasks); tasks['t7'] = changed
        with pytest.raises(ValueError, match='group|family'): replace(split, tasks=tasks)
    with pytest.raises(ValueError, match='exactly'):
        checked_rows(rows[:5], split, 'train', catalog)
    bad = deepcopy(rows[:4]); bad[0]['sample_unit'] = 'pose'
    with pytest.raises(ValueError, match='poses'): checked_rows(bad, split, 'train', catalog)
    bad = deepcopy(rows[:4]); bad[0]['resource_costs'].pop('terminal')
    with pytest.raises(ValueError, match='costs'): checked_rows(bad, split, 'train', catalog)
    bad = deepcopy(rows[:4]); bad[0]['cost_evidence']['x'] = []
    with pytest.raises(ValueError, match='evidence'): checked_rows(bad, split, 'train', catalog)


def test_learned_xor_policy_persistence_and_common_cost_controls(tmp_path):
    policy, rows = trained()
    assert policy['program']['kind'] == 'query'
    assert policy['program']['expected_loss'] == 0
    assert policy['program']['expected_additional_cost'] == pytest.approx(.2)
    file = tmp_path / 'policy.json'; save_policy(policy, file)
    restored = load_policy(file)
    report = evaluate_policies(restored, rows[4:6], role='calibration', static_order=('x', 'z'))
    grouped = {mode: [r for r in report['results'] if r['mode'] == mode]
               for mode in ('phi', 'copy_best', 'static', 'myopic', 'nonmyopic')}
    assert all(r['scientific_loss'] == 0 and r['actual_resource_cost'] == pytest.approx(.2)
               for r in grouped['phi'])
    assert all(r['actual_resource_cost'] == pytest.approx(.05) and r['queries'] == []
               for r in grouped['myopic'] + grouped['copy_best'])
    assert [{k: v for k, v in r.items() if k != 'mode'} for r in grouped['phi']] == [
        {k: v for k, v in r.items() if k != 'mode'} for r in grouped['nonmyopic']]
    with pytest.raises(FileExistsError): save_policy(policy, file)
    tampered = deepcopy(policy); tampered['budget'] = 900
    with pytest.raises(ValueError, match='hash'): next_action(tampered, [])


def test_learning_changes_with_training_loss_and_actual_cost_not_handwritten_rule():
    split, catalog, rows = dataset()
    a = fit_policy(rows[:4], split=split, catalog=catalog, budget=.4, max_depth=2, cost_weight=1)
    changed = deepcopy(rows[:4])
    for r in changed: r['losses'] = {'keep': 0., 'change': 1., 'abstain': 1.}
    b = fit_policy(changed, split=split, catalog=catalog, budget=.4, max_depth=2, cost_weight=1)
    assert a['program']['kind'] == 'query' and b['program']['decision'] == 'keep'
    # Same quotes, outcomes and loss; high actual query costs reverse the choice.
    expensive = replace(catalog, resources=tuple(replace(r, quoted_cost=2.) for r in catalog.resources))
    low = fit_policy(rows[:4], split=split, catalog=expensive, budget=8., max_depth=2, cost_weight=1)
    costs = deepcopy(rows[:4])
    for r in costs: r['resource_costs'].update(x=1., z=1.)
    high = fit_policy(costs, split=split, catalog=expensive, budget=8., max_depth=2, cost_weight=1)
    assert low['program']['kind'] == 'query' and high['program']['kind'] == 'stop'


def test_group_balancing_and_calibration_support_failures_are_visible():
    split, catalog, rows = dataset()
    tasks = deepcopy(split.tasks); tasks['replicate-task'] = tasks['t0'].copy()
    more = [*rows[:4], {**rows[0], 'task_id': 'replicate-task'}]
    policy = fit_policy(more, split=replace(split, tasks=tasks), catalog=catalog,
                       budget=.4, max_depth=2, cost_weight=1)
    assert sorted(w['probability'] for w in policy['model']['worlds']) == [.125, .125, .25, .25, .25]
    heldout = deepcopy(rows[4:6]); heldout[0]['outcomes']['x'] = 'failed'
    before = deepcopy(policy)
    assessment = probability_assessment(policy, heldout)
    zero = next(r for r in assessment['predictions'] if r['task_id'] == 't4' and r['probe_id'] == 'x')
    assert zero['support_failure'] and zero['infinite_log_loss'] and zero['log_loss'] is None
    assert zero['brier'] == pytest.approx(1.5)
    with pytest.raises(ValueError, match='frozen'):
        probability_assessment(policy, heldout, visible_prefix=('x',))
    prefixed = fit_policy(more, split=replace(split, tasks=tasks), catalog=catalog,
        budget=.4, max_depth=2, cost_weight=1, calibration_prefix=('x',))
    unsupported = probability_assessment(prefixed, heldout)
    assert unsupported['predictions'][0]['probabilities'] is None
    assert policy == before


def test_independent_test_release_must_bind_policy_and_split():
    policy, rows = trained()
    with pytest.raises(ValueError, match='release'):
        evaluate_policies(policy, rows[6:], role='test', static_order=('x', 'z'))
    release = {'policy_sha256': policy['policy_sha256'], 'split_sha256': policy['split_sha256'],
               'evidence': ['synthetic release']}
    report = evaluate_policies(policy, rows[6:], role='test', static_order=('x', 'z'), release=release)
    assert len(report['results']) == 12 and report['promotion'] == 'none'
    # Outcomes are supplied only when requested; changing hidden loss cannot change decisions.
    altered = deepcopy(rows[6:])
    for r in altered: r['losses'] = {d: 20. for d in r['losses']}
    other = evaluate_policies(policy, altered, role='test', static_order=('x', 'z'), release=release)
    assert [(r['queries'], r['decision']) for r in report['results']] == [
        (r['queries'], r['decision']) for r in other['results']]


def test_public_cli_fits_reloads_and_refuses_unreleased_test_before_opening(tmp_path):
    split, catalog, rows = dataset()
    config = {'split': asdict(split), 'catalog': asdict(catalog),
        'learner': dict(budget=.4, max_depth=2, cost_weight=1, static_order=['x','z'])}
    for name, value in [('config.json', config), ('training.json', rows[:4]), ('history.json', [])]:
        (tmp_path/name).write_text(json.dumps(value))
    script = Path(__file__).parents[1]/'examples/learn_policy_round4.py'
    def run(*args):
        return subprocess.run([sys.executable,str(script),*map(str,args)],capture_output=True,text=True)
    result = run('fit','--config',tmp_path/'config.json','--training',tmp_path/'training.json',
                 '--output',tmp_path/'policy.json')
    assert result.returncode == 0, result.stderr
    result = run('act','--policy',tmp_path/'policy.json','--history',tmp_path/'history.json',
                 '--output',tmp_path/'next.json')
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path/'next.json').read_text())['kind'] == 'query'
    result = run('evaluate','--policy',tmp_path/'policy.json','--role','test',
                 '--rows',tmp_path/'MUST_NOT_OPEN.json','--output',tmp_path/'forbidden.json')
    assert result.returncode != 0 and 'independent release required' in result.stderr
    assert 'FileNotFoundError' not in result.stderr and not (tmp_path/'forbidden.json').exists()


def test_terminal_cost_quote_and_unsupported_branch_abstention():
    policy, rows = trained()
    receipt = {'policy_sha256': policy['policy_sha256'], 'probe_id': 'x', 'outcome': 'failed',
               'resource_costs': {'setup': .05, 'x': .05}, 'evidence': ['fixture-evidence']}
    assert next_action(policy, [receipt])['status'] == 'unsupported_history'
    split, catalog, all_rows = dataset()
    limited = fit_policy(all_rows[:4], split=split, catalog=catalog,
                         budget=.05, max_depth=2, cost_weight=1)
    assert next_action(limited, [])['decision'] == 'abstain'
    assert next_action(limited, [], mode='phi0')['decision'] == 'abstain'
    with pytest.raises(ValueError, match='frozen'):
        evaluate_policies(policy,rows[4:6],role='calibration',static_order=('z','x'))


def test_common_prefix_is_required_and_charged_for_every_control():
    split, catalog, rows = dataset()
    catalog = replace(catalog, initial_resources=('setup',))
    policy = fit_policy(rows[:4],split=split,catalog=catalog,budget=.4,max_depth=2,cost_weight=1,
                        static_order=('x','z'))
    assert policy['training_expected_initial_cost'] == pytest.approx(.05)
    with pytest.raises(ValueError,match='initial resource receipt'):
        next_action(policy,[])
    report=evaluate_policies(policy,rows[4:6],role='calibration')
    for r in report['results']:
        assert r['common_initial_cost']==pytest.approx(.05)
        assert r['actual_resource_cost']>=.1 and 'setup' in r['paid_resources']


def test_chunk_outcome_uses_only_paid_subset_and_respects_ties_failures():
    from etalon.research.fixed_pool import subset_rank_outcome, two_chunk_catalog
    ids=('a','b','c'); vina=dict(a=-3.,b=-2.,c=-1.)
    assert subset_rank_outcome(candidate_ids=ids,vina=vina,vinardo=dict(a=-30.,b=-20.,c=-10.))['outcome']=='same'
    assert subset_rank_outcome(candidate_ids=ids,vina=vina,vinardo=dict(a=-3.,b=-1.,c=-2.))['outcome']=='changed'
    assert subset_rank_outcome(candidate_ids=ids,vina=vina,vinardo=dict(a=-3.,b=-3.,c=-2.))['outcome']=='changed'
    assert subset_rank_outcome(candidate_ids=ids,vina=vina,vinardo=dict(a=-3.,b=None,c=-2.))['outcome']=='failed'
    with pytest.raises(ValueError,match='paid subset'):
        subset_rank_outcome(candidate_ids=ids,vina=vina,vinardo={**vina,'unpaid':-10.})
    catalog=two_chunk_catalog(initial_quote=300,first_quote=300,second_quote=300,loss_definition='frozen assay order')
    assert catalog.initial_resources==('r0',)
    assert catalog.decision_resources['fixed_pool_vinardo']==('r0','r1','r2')


def test_calibration_selects_frozen_mixture_without_inventing_worlds_or_train_losses():
    split,catalog,rows=dataset()
    grid={'mixture_weights':[0.,.25,.5,.75],'cost_weights':[0.,.001,1.],
          'selection_cost_weight':.001,'probability_selection_metric':'group_mean_brier'}
    train=fit_policy(rows[:4],split=split,catalog=catalog,budget=.4,max_depth=2,cost_weight=1,
                     static_order=('x','z'),calibration_selection=grid)
    final,report=calibrate_policy(train,rows[4:6])
    assert report['selected_mixture_weight']==.75 and report['new_worlds_created']==0
    assert {w['id']:w['losses'] for w in train['model']['worlds']}=={
        w['id']:w['losses'] for w in final['model']['worlds']}
    assert all(w['probability']>0 for w in final['model']['worlds'])
    assert report['probability_candidates'][-1]['group_mean_brier']<report['probability_candidates'][0]['group_mean_brier']
    assert len(report['loss_cost_curve'])==3
    assert final['training_model']==train['model']
    with pytest.raises(ValueError,match='repeated'):calibrate_policy(final,rows[4:6])
    outside=deepcopy(rows[4:6])
    for r in outside:r['outcomes']['x']='failed'
    unsupported,diagnostic=calibrate_policy(train,outside)
    assert diagnostic['unsupported_joint_mass']==1 and diagnostic['selected_mixture_weight']==0
    assert [w['probability'] for w in unsupported['model']['worlds']]==[w['probability'] for w in train['model']['worlds']]


def test_train_only_baselines_keep_training_cost_weight_after_calibration():
    split,catalog,rows=dataset()
    catalog=replace(catalog,decision_resources={'keep':(),'change':('terminal',),'abstain':()})
    for r in rows:r['losses']={'keep':.2,'change':0.,'abstain':1.}
    grid={'mixture_weights':[0.],'cost_weights':[0.],'selection_cost_weight':.001,
          'probability_selection_metric':'group_mean_brier'}
    train=fit_policy(rows[:4],split=split,catalog=catalog,budget=.4,max_depth=2,cost_weight=10.,
                     calibration_selection=grid)
    calibrated,_=calibrate_policy(train,rows[4:6])
    assert calibrated['cost_weight']==0. and calibrated['training_cost_weight']==10.
    assert next_action(calibrated,[],mode='phi')['decision']=='change'
    for mode in ('copy_best','static'):
        assert next_action(train,[],mode=mode)['decision']==next_action(calibrated,[],mode=mode)['decision']=='keep'


def test_static_keeps_fixed_paid_schedule_even_after_unsupported_first_outcome():
    policy,_=trained()
    first={'policy_sha256':policy['policy_sha256'],'probe_id':'x','outcome':'failed',
           'resource_costs':{'setup':.05,'x':.05},'evidence':['fixture']}
    action=next_action(policy,[first],mode='static')
    assert action['kind']=='query' and action['probe_id']=='z'
    second={'policy_sha256':policy['policy_sha256'],'probe_id':'z','outcome':'1',
            'resource_costs':{'z':.05},'evidence':['fixture']}
    final=next_action(policy,[first,second],mode='static')
    assert final['kind']=='stop' and final['decision']=='abstain' and final['status']=='unsupported_history'
