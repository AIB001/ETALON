"""Synthetic independent-evaluation tests; no actual scientific endpoint claim."""
from contextlib import contextmanager
from pathlib import Path
import pytest
from etalon.active.schema import canonical,digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from etalon.research.baseline_feedback import evaluate_retained,attach_retained_feedback


def test_NA_raw_loss_stays_unknown_but_effective_fallback_and_preparation_are_paid(tmp_path,monkeypatch):
    def write(name,value):
        p=tmp_path/name;p.write_text(canonical(value));return p
    policy={'policy_sha256':'f'*64,'split_sha256':'e'*64,'input_freeze_sha256':'d'*64,
        'split':{'tasks':{t:{'role':'test','group':t} for t in ('na','ready')}}}
    base_scores={c:float(i) for i,c in enumerate('abcdef')};p_scores={**base_scores,'a':1.,'b':0.}
    frozen={'run_id':'fixture','tasks':{t:{'preparation_admission_id':t+'p0','candidate_scores':base_scores,
        'score_admissions':{'r0':t+'r0'},'attributed_path_cost':5.} for t in ('na','ready')}}
    pred=write('pred.json',frozen)
    diagnostics={'prediction_sha256':file_identity(pred)['sha256'],'tasks':{
        'na':{'preparation_admission_id':'nap1','score_admissions':{},'raw_P_scores':None,'score_cost':None},
        'ready':{'preparation_admission_id':'readyp1','score_admissions':{'r1':'readyr1','r2':'readyr2'},'raw_P_scores':p_scores,'score_cost':6.}}}
    preps={t+p:{'status':'NOT_APPLICABLE' if t=='na' and p=='p1' else 'PREPARED','cost':{'wall_second':.01}}
        for t in ('na','ready') for p in ('p0','p1')}
    scores={t+'r0':{'cost':{'wall_second':3.},'slots':[{'candidate_id':c,'score':v} for c,v in base_scores.items()]} for t in ('na','ready')}
    scores.update({rid:{'cost':{'wall_second':3.},'slots':[{'candidate_id':c,'score':p_scores[c]} for c in chars]}
                   for rid,chars in [('readyr1','abc'),('readyr2','def')]})
    monkeypatch.setattr('etalon.research.baseline_feedback.paid_preparation',lambda j,i:preps[i])
    monkeypatch.setattr('etalon.research.baseline_feedback.preparation_cost_attribution',lambda j,i:{'wall_second':2.,'execution_costs':[]})
    monkeypatch.setattr('etalon.research.baseline_feedback.paid_score',lambda j,i:scores[i])
    roles={}
    def artifact(j,p,**kwargs):roles[p.name]=kwargs.get('role','diagnostic');return 'fixture-'+p.name
    monkeypatch.setattr('etalon.research.baseline_feedback._file_artifact',artifact)
    class Journal:
        def __init__(self):
            self.store=self;self.records={'baselinepolicy-'+policy['policy_sha256']:policy,
                'onlinepredictions-'+digest('fixture'):frozen,'baselinediagnostics-'+digest('fixture'):diagnostics}
        def get(self,i):return self.records[i]
        def _put(self,db,i,v):self.records[i]=v
        def balance(self):return {'spent':100,'reserved':0}
        @contextmanager
        def connection(self,**kwargs):yield None
    journal=Journal();oracle=tmp_path/'oracle.py'
    oracle.write_text("def evaluate(scores,task):\n return {'loss':float(scores['a']>scores['b']),'candidate_scores':scores}\n")
    labels=write('labels.json',{'role':'test','tasks':{'na':{},'ready':{}},'input_freeze_sha256':'d'*64,'script_sha256':file_identity(oracle)['sha256']})
    release=write('release.json',{'policy_sha256':'f'*64,'split_sha256':'e'*64,'predictions_sha256':file_identity(pred)['sha256'],'evidence':['fixture']})
    result=evaluate_retained(journal,policy_path=write('policy.json',policy),predictions_path=pred,
        diagnostics_path=write('diagnostics.json',diagnostics),label_release_path=labels,independent_release_path=release,oracle_path=oracle)
    na=result['test_results']['na'];ready=result['test_results']['ready']
    assert na['raw_P']['loss'] is None and na['raw_P_loss_interval']['upper']==1
    assert na['query_outcomes']=={'q1':None,'q2':None} and na['fixed_attempt_P']['loss']==0
    assert na['fixed_attempt_P']['attributed_common_start_cost']==7
    assert ready['fixed_attempt_P']['attributed_common_start_cost']==13 and ready['raw_P']['loss']==1
    assert ready['cold_start_P_attempt_cost']==10 and na['cold_start_P_attempt_cost']==7
    assert result['group_equal_macro']['raw_P_loss_interval']=={'lower':.5,'upper':1.}
    assert result['group_equal_macro']['fixed_attempt_P_objective_interval']['lower']==pytest.approx(.51)
    assert roles['labels.json']==roles['release.json']=='independent_validation'
    audit=write('audit.json',{'policy_sha256':'f'*64,'prediction_sha256':file_identity(pred)['sha256']})
    feedback=attach_retained_feedback(journal,evaluation_path=write('evaluation.json',result),coordinator_audit_path=audit)
    assert feedback['research_state']=='diagnosis_required' and feedback['phi_learning_not_established']
    assert feedback['promotion']=='none' and feedback['complete_RSI'] is False
    # Explicit fixture: a distinct old-model support audit stays now-seen feedback.
    driver=tmp_path/'replay_support_round4b.py';driver.write_text('# fixture driver\n')
    old_model={'fixture':'frozen model'};old_policy='a'*64;old_rows='b'*64
    journal.records['learnedpolicy-'+old_policy]={'model':old_model}
    journal.records['taskrows-'+old_rows]={'role':'test'}
    replay={'policy_sha256':old_policy,'model_sha256':digest(old_model),'rows_semantic_sha256':old_rows,
        'original_row_role':'test','refitted':False,'future_outcomes_passed_to_historical_controller':False,
        'costs':{'new_scientific_executions':0,'new_ledger_charges':0},
        'source_pins':{str(driver):file_identity(driver)}}
    replay_path=write('support-replay.json',replay)
    enriched=attach_retained_feedback(journal,evaluation_path=tmp_path/'evaluation.json',coordinator_audit_path=audit,
        support_replay_path=replay_path,support_replay_driver_path=driver)
    assert enriched['seen_model_support_replay']==replay and enriched['seen_outcome_gain_audit'] is None
    assert roles['support-replay.json']=='independent_validation' and enriched['promotion']=='none'
    replay['future_outcomes_passed_to_historical_controller']=True;replay_path.write_text(canonical(replay))
    with pytest.raises(StateError,match='visibility'):
        attach_retained_feedback(journal,evaluation_path=tmp_path/'evaluation.json',coordinator_audit_path=audit,
            support_replay_path=replay_path,support_replay_driver_path=driver)


def test_release_gate_precedes_test_label_read(tmp_path):
    policy=tmp_path/'policy.json';policy.write_text(canonical({'policy_sha256':'f'*64,'split_sha256':'e'*64}))
    release=tmp_path/'release.json';release.write_text('{}')
    with pytest.raises(StateError,match='before label read'):
        evaluate_retained(None,policy_path=policy,independent_release_path=release,
            predictions_path=tmp_path/'MUST_NOT_OPEN',diagnostics_path=None,label_release_path=None,oracle_path=None)
