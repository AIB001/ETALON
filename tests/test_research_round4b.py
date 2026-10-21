"""Synthetic R4b support/cost/lineage tests; no scientific producer execution."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest
from etalon.active.schema import digest,canonical
from etalon.research.transfer import unpack,next_action,evaluate_policies,GroupSplit
from etalon.research.support import support_report,loss_interval,bounded_panel,fallback_action
from etalon.research.online import execute_visible_task
from etalon.research.workflows import legacy_bindings,validate_bindings,validate_receptor_binding
from etalon.research.transfer_checks import outcome_improvement_diagnostic
from test_research_online import policy_fixture,callbacks


def guarded(constant=False):
    p=deepcopy(policy_fixture(constant));_,c,_=unpack(p)
    p['support_guard']={'fallback_decision':c.initial_decision,'unknown_world_probability':None,
        'unknown_loss_bounds':{d:[0,1] for d in c.decision_resources}}
    p['evaluation_cost_weight']=.001
    p['policy_sha256']=digest({k:v for k,v in p.items() if k!='policy_sha256'})
    return p


def test_visible_failure_falls_back_without_future_query_and_preserves_missing_score():
    p=guarded();log,acquire,builder=callbacks(p,{'q1':'failed'})
    def partial(rid,reason):
        a=acquire(rid,reason)
        if rid=='r0':a['slots'][0]['score']=None
        return a
    result=execute_visible_task(p,task_id='t5',candidate_ids=[f'c{i}' for i in range(6)],acquire=partial,receipt_builder=builder)
    assert result['decision']=='fixed_pool_vina' and result['actual_path_cost']==pytest.approx(.2)
    assert result['terminal_score_state']=='partial' and result['candidate_scores']['c0'] is None
    assert [r['probe_id'] for r in result['query_history']]==['q1'] and 'r2' not in result['resources']
    action=result['actions'][-1]['action']
    assert action['scientific_loss'] is None and action['model_challenge']['unknown_losses']['fixed_pool_vina']['upper']==1


def test_joint_failure_despite_nonzero_marginals_is_not_visible_at_prefix():
    p=guarded();_,_,m=unpack(p)
    worlds=[w for w in m.worlds if w.outcomes!={'q1':'changed','q2':'changed'}]
    m=replace(m,worlds=tuple(replace(w,probability=1/3) for w in worlds))
    prefix=support_report(m,{'q1':'changed'},p['support_guard']['unknown_loss_bounds'])
    full=support_report(m,{'q1':'changed','q2':'changed'},p['support_guard']['unknown_loss_bounds'])
    assert not prefix['model_challenge'] and prefix['unobserved_probes']==['q2']
    assert full['model_challenge'] and all(full['marginal_support'].values())
    assert full['external_world_probability'] is None and full['new_worlds_created']==0


def test_unknown_loss_is_an_interval_not_a_training_point_and_fallback_pays_quote():
    assert loss_interval(None,[0,1])['value'] is None
    with pytest.raises(ValueError,match='imputed'):loss_interval(.5,[0,1])
    with pytest.raises(ValueError,match='missing'):loss_interval(None,[0,1],state='measured')
    panel=bounded_panel({'task_id':'x','losses':{'B':.2,'P':None},'loss_states':{'B':'measured','P':'unavailable_label'}},
        decisions=('B','P'),loss_bounds={'B':[0,1],'P':[0,1]})
    assert not panel['eligible_for_existing_complete_row_learner'] and panel['losses']['P']['upper']==1
    p=guarded();_,c,m=unpack(p);p['budget']=200
    stop=fallback_action(p,c,m,{'q1':'failed'},set())
    assert stop['decision']=='abstain' and stop['fallback_reason']=='baseline_unaffordable'


def test_common_cost_objective_and_cold_copy_best_do_not_get_free_terminal_scores():
    p=guarded(constant=True);_,c,m=unpack(p);p['cost_weight']=0.
    b=legacy_bindings();b['cold_start_decision_resources']={'fixed_pool_vina':['r0'],'fixed_pool_vinardo':['r1','r2'],'abstain':[]}
    p['workflow_bindings']=b;p['policy_sha256']=digest({k:v for k,v in p.items() if k!='policy_sha256'})
    row=dict(task_id='t5',sample_unit='task',independence_group='g5',source_kind='synthetic_fixture',
        loss_definition=c.loss_definition,cost_unit=c.cost_unit,outcomes={'q1':'same','q2':'same'},
        losses={'fixed_pool_vina':1.,'fixed_pool_vinardo':0.,'abstain':1.},resource_costs={'r0':.1,'r1':.1,'r2':.1},
        evidence=['fixture'],outcome_evidence={q.id:['fixture'] for q in c.probes},
        loss_evidence={d:['fixture'] for d in c.decision_resources},cost_evidence={r.id:['fixture'] for r in c.resources})
    report=evaluate_policies(p,[row],role='test',release={'policy_sha256':p['policy_sha256'],'split_sha256':p['split_sha256'],'evidence':['fixture']})
    phi=next(r for r in report['results'] if r['mode']=='phi')
    cold=next(r for r in report['cold_start_comparators'] if r['mode']=='cold_copy_best')
    assert phi['objective']==pytest.approx(.0003) and cold['objective']==pytest.approx(.0002)
    assert cold['paid_resources']==['r1','r2'] and cold['selection_development_cost'] is None


def test_receptor_bytes_are_pinned_and_cross_group_relation_failure_is_retained():
    p=guarded();_,c,_=unpack(p);b=legacy_bindings();validate_bindings(b,c)
    b['decision_scores']['fixed_pool_vinardo']=['r1','r1']
    with pytest.raises(ValueError):validate_bindings(b,c)
    state={'availability':'reviewed','coupling':'fixed ligand','preparation_evidence':['fixture'],
        'input_identity':{'receptor_state_id':'P','coordinate_frame_id':'frame'},'grid':{'center_A':[0,0,0],'size_A':[22,22,22],'spacing_A':.375},
        'inputs':{k:{'sha256':'a'*64,'bytes':1} for k in ('receptor_source','scorer_receptor','scorer_manifest')}}
    request={'input_identity':state['input_identity'],'evaluation':state['grid'],'inputs':deepcopy(state['inputs'])}
    validate_receptor_binding(request,{'receptor_key':'P'},{'receptor_states':{'P':state}})
    request['inputs']['scorer_receptor']['sha256']='b'*64
    with pytest.raises(ValueError,match='bytes'):validate_receptor_binding(request,{'receptor_key':'P'},{'receptor_states':{'P':state}})
    rows=[{'task_id':str(i),'independence_group':str(i),'outcomes':{'q1':'changed','q2':'same'},
        'losses':{'fixed_pool_vina':.5,'fixed_pool_vinardo':v,'abstain':1},'resource_costs':{'r0':1,'r1':1,'r2':1}} for i,v in enumerate([0.,1.,None])]
    diagnostic=outcome_improvement_diagnostic(rows,c)
    summary=next(s for s in diagnostic['conditional_group_summary'] if s['probe_id']=='q1')
    assert summary['positive_groups']==summary['negative_groups']==summary['missing_cells']==1
    assert any(r['sign_generalized'] is False for r in diagnostic['leave_group_out_relation'])


def test_seen_release_preserves_old_role_and_requires_exact_original_labels(tmp_path):
    from etalon.active import CampaignSpec,CampaignStore,Endpoint
    from etalon.research import ResearchJournal,Artifact
    from etalon.research.seen import register_seen_labels
    from etalon.boundary.prism_v1 import file_identity
    from etalon.active.store import StateError
    store=CampaignStore(tmp_path/'fixture.sqlite')
    store.configure(CampaignSpec('unmeasured',1200,'wall_second','fixture'),
        [Endpoint('unmeasured','fixture','not_measured','count','fixture',0,queryable=False)])
    journal=ResearchJournal(store)
    original={'tasks':{'oldtest':{'pairs':[{'a':'a','b':'b','better':'a'}]}}}
    path=tmp_path/'original.json';path.write_text(canonical(original))
    artifact=Artifact(str(path),**file_identity(path),source_kind='fixture',role='independent_validation',bindings={})
    journal.record(artifact);original_record=journal.get(artifact.id)
    with store.connection(write=True) as db:
        journal._put(db,'taskrows-old',{'role':'test','executions':{'oldtest':{'r0':'original-execution'}},'evidence':[artifact.id]})
    split=GroupSplit({t:{'role':r,'group':t,'family':t} for t,r in [('oldtest','train'),('train','train'),('cal','calibration'),('new','test')]},'fixture','0'*64)
    labels={'tasks':original['tasks'],'seen_development_release':{'purpose':'former_independent_validation_now_seen',
        'new_split_sha256':split.sha256,'tasks':{'oldtest':{'source_taskrows_id':'taskrows-old','source_label_sha256':artifact.sha256}}}}
    new=tmp_path/'seen.json';new.write_text(canonical(labels))
    identifier,lineage=register_seen_labels(journal,new,labels,split)
    derived=journal.get(identifier)
    assert derived['parents']==[artifact.id] and derived['bindings']['development_split_sha256']==split.sha256
    assert journal.get(artifact.id)==original_record and lineage['oldtest']['original_role']=='test'
    with store.connection() as db:
        with pytest.raises(StateError,match='independent'):journal._artifacts(db,[artifact.id],diagnostic_inputs=True)
    bad=deepcopy(labels);bad['tasks']['oldtest']['pairs'][0]['better']='b'
    wrong=tmp_path/'wrong.json';wrong.write_text(canonical(bad))
    with pytest.raises(StateError,match='changes scientific'):register_seen_labels(journal,wrong,bad,split)
