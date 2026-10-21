"""Synthetic action-order tests; no producer computation or scientific inference."""
from copy import deepcopy
import pytest
from etalon.research.fixed_pool import two_chunk_catalog
from etalon.research.transfer import GroupSplit,fit_policy
from etalon.research.online import execute_visible_task,_write_once,complete_counterfactual
from etalon.active.store import StateError


def policy_fixture(constant=False):
    split=GroupSplit({f't{i}':{'role':'train' if i<4 else 'calibration' if i==4 else 'test',
        'group':f'g{i}','family':f'g{i}'} for i in range(6)},'synthetic','0'*64)
    catalog=two_chunk_catalog(initial_quote=300,first_quote=300,second_quote=300,loss_definition='synthetic loss')
    rows=[]
    for i in range(4):
        x,z=i//2,i%2;loss=1 if constant else x^z
        rows.append(dict(task_id=f't{i}',sample_unit='task',independence_group=f'g{i}',source_kind='synthetic_fixture',
            loss_definition=catalog.loss_definition,cost_unit='wall_second',outcomes={'q1':['same','changed'][x],'q2':['same','changed'][z]},
            losses={'fixed_pool_vina':loss,'fixed_pool_vinardo':1-loss,'abstain':1.},resource_costs={'r0':.1,'r1':.1,'r2':.1},
            evidence=['fixture'],outcome_evidence={'q1':['fixture'],'q2':['fixture']},
            loss_evidence={d:['fixture'] for d in catalog.decision_resources},cost_evidence={r.id:['fixture'] for r in catalog.resources}))
    return fit_policy(rows,split=split,catalog=catalog,budget=900,max_depth=2,cost_weight=.001,static_order=('q1','q2'))


def callbacks(policy,outcomes):
    log=[]
    def acquire(rid,reason):
        log.append(('acquire',rid,reason))
        indices=range(6) if rid=='r0' else range(3) if rid=='r1' else range(3,6)
        return {'execution_id':'fixture-'+rid,'result_sha256':rid,'result_artifact_id':'fixture-'+rid,
                'cost':{'wall_second':.1},'slots':[{'candidate_id':f'c{i}','score':-float(i)} for i in indices]}
    def receipt(admissions,probe):
        log.append(('visible',probe))
        result={'policy_sha256':policy['policy_sha256'],'evidence':['fixture'],
                'resource_costs':{r:.1 for r in admissions if probe is None or r!='r0'}}
        if probe:result.update(probe_id=probe,outcome=outcomes[probe])
        return result
    return log,acquire,receipt


def test_terminal_vinardo_acquires_both_chunks_after_stop_and_pays_prefix():
    policy=policy_fixture(constant=True);log,acquire,receipt=callbacks(policy,{})
    result=execute_visible_task(policy,task_id='t5',candidate_ids=[f'c{i}' for i in range(6)],
                               acquire=acquire,receipt_builder=receipt)
    assert result['decision']=='fixed_pool_vinardo' and result['query_history']==[]
    assert [event for event in log if event[0]=='acquire']==[
        ('acquire','r0','common_initial_prefix'),('acquire','r1','terminal:fixed_pool_vinardo'),('acquire','r2','terminal:fixed_pool_vinardo')]
    assert result['actual_path_cost']==pytest.approx(.3) and len(result['candidate_scores'])==6
    assert result['actions'][1]['kind']=='decision'  # terminal resource acquisition follows the decision


def test_queries_are_paid_in_visible_order_and_unsupported_stops_early():
    policy=policy_fixture();log,acquire,receipt=callbacks(policy,{'q1':'same','q2':'changed'})
    result=execute_visible_task(policy,task_id='t5',candidate_ids=[f'c{i}' for i in range(6)],
                               acquire=acquire,receipt_builder=receipt)
    assert result['decision']=='fixed_pool_vinardo'
    assert log.index(('visible','q1'))<log.index(('acquire','r2','query:q2'))
    log,acquire,receipt=callbacks(policy,{'q1':'failed'})
    failed=execute_visible_task(policy,task_id='t5',candidate_ids=[f'c{i}' for i in range(6)],
                               acquire=acquire,receipt_builder=receipt)
    assert failed['decision']=='abstain' and 'r2' not in failed['resources']
    assert all(v is None for v in failed['candidate_scores'].values())


def test_atomic_immutable_output_and_counterfactual_gate(tmp_path):
    path=tmp_path/'frozen.json';_write_once(path,{'x':1});before=path.read_bytes();_write_once(path,{'x':1})
    assert path.read_bytes()==before and not list(tmp_path.glob('.online-*.tmp'))
    with pytest.raises(StateError,match='immutable'):_write_once(path,{'x':2})
    with pytest.raises(StateError,match='freeze online predictions'):
        complete_counterfactual(None,None,None,manifest_path=None,input_freeze_path=None,root=None,output_dir=tmp_path)
