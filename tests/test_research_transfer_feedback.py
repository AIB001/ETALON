"""Synthetic feedback binding tests; actual evidence verification is tested in rows/journal suites."""
import json
from contextlib import contextmanager
from copy import deepcopy
import pytest
from etalon.active.schema import canonical,digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from etalon.research.feedback import attach_transfer_feedback
from etalon.research.online import execute_visible_task
from test_research_online import policy_fixture,callbacks


def test_feedback_binds_online_choice_and_release_without_promoting(tmp_path,monkeypatch):
    policy=policy_fixture();_,acquire,builder=callbacks(policy,{'q1':'same','q2':'changed'})
    prediction=execute_visible_task(policy,task_id='t5',candidate_ids=[f'c{i}' for i in range(6)],
                                    acquire=acquire,receipt_builder=builder)
    frozen={'policy_sha256':policy['policy_sha256'],'labels_accessed':False,'run_id':'fixture-run','tasks':{'t5':prediction}}
    def write(name,value):
        p=tmp_path/name;p.write_text(canonical(value));return p
    predictions=write('predictions.json',frozen)
    release=write('release.json',{'policy_sha256':policy['policy_sha256'],'split_sha256':policy['split_sha256'],
        'evidence':[{'kind':'online_predictions_frozen_before_label_read',
                     'sha256':file_identity(predictions)['sha256']}]})
    row=dict(task_id='t5',sample_unit='task',independence_group='g5',source_kind='synthetic_fixture',
        loss_definition='synthetic loss',cost_unit='wall_second',outcomes={'q1':'same','q2':'changed'},
        losses={'fixed_pool_vina':1.,'fixed_pool_vinardo':0.,'abstain':1.},resource_costs={'r0':.1,'r1':.1,'r2':.1},
        evidence=['fixture'],outcome_evidence={'q1':['fixture'],'q2':['fixture']},
        loss_evidence={k:['fixture'] for k in ('fixed_pool_vina','fixed_pool_vinardo','abstain')},
        cost_evidence={k:['fixture'] for k in ('r0','r1','r2')})
    rows=write('rows.json',[row]);cal=write('cal.json',{'final_policy_sha256':policy['policy_sha256']});seen=write('seen.json',{'groups':{}})
    dataset={'rows_sha256':digest([row]),'scientific_assessment':{'t5':{'fixed_pool_vinardo':{'candidate_scores':prediction['candidate_scores']}}}}
    monkeypatch.setattr('etalon.research.feedback.verify_dataset',lambda *a,**k:dataset)
    artifact_roles={}
    def artifact(j,p,**kwargs):
        artifact_roles[str(p)]=kwargs.get('role','diagnostic')
        return 'fixture-artifact-'+digest(str(p))
    monkeypatch.setattr('etalon.research.feedback._file_artifact',artifact)
    class Store:
        def balance(self):return {'budget':1200,'spent':.3,'reserved':0}
        @contextmanager
        def connection(self,**kwargs):yield None
    class Journal:
        store=Store()
        records={'onlinepredictions-'+digest('fixture-run'):frozen}
        def get(self,key):return self.records[key]
        def _put(self,db,key,body):
            if key in self.records:assert self.records[key]==body
            self.records[key]=body
    journal=Journal();args=dict(rows_path=rows,predictions_path=predictions,independent_release_path=release,
                                calibration_report_path=cal,seen_audit_path=seen)
    first=attach_transfer_feedback(journal,policy,**args);count=len(journal.records)
    assert attach_transfer_feedback(journal,policy,**args)==first and len(journal.records)==count
    assert first['promotion']=='none' and first['complete_RSI'] is False and first['phi_learned'] is False
    assert first['group_equal_macro']['phi']['scientific_loss']==0
    assert artifact_roles[str(rows)]==artifact_roles[str(release)]=='independent_validation'
    assert first['future_use'].startswith('seen_evaluation_feedback_only')
    assert first['training_policy_sha256'] is None
    assert first['frozen_policy_and_predictions_unchanged'] is True
    original=predictions.read_bytes()
    dataset['scientific_assessment']['t5']['fixed_pool_vinardo']['candidate_scores']={}
    with pytest.raises(StateError,match='offline phi'):attach_transfer_feedback(journal,policy,**args)
    assert predictions.read_bytes()==original


def test_release_gate_precedes_reading_test_rows(tmp_path):
    policy=policy_fixture();release=tmp_path/'release.json';release.write_text('{}')
    with pytest.raises(StateError,match='test release'):
        attach_transfer_feedback(None,policy,rows_path=tmp_path/'MUST_NOT_OPEN',predictions_path=tmp_path/'MUST_NOT_OPEN',
            independent_release_path=release,calibration_report_path=None,seen_audit_path=None)
