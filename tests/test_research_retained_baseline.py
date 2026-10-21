"""Synthetic run-order fixture; preparation wire/ledger covered separately."""
from contextlib import contextmanager
import json
import pytest
from etalon.active.schema import digest
from etalon.active.store import StateError
from etalon.research.retained_baseline import run_retained_baseline


def test_retained_B_freezes_before_P_and_NA_never_becomes_success_or_zero_receipt(tmp_path,monkeypatch):
    tasks={t:{'role':role,'group':t,'family':t} for t,role in [('a','train'),('b','train'),('c','calibration'),('na','test'),('ready','test')]}
    policy={'schema':'etalon-retained-baseline-policy/1','policy_sha256':'f'*64,'split_sha256':'e'*64,
        'split':{'tasks':tasks,'grouping_evidence':'fixture','protocol_sha256':'0'*64},'baseline_admissions':{},
        'preparation_admissions':{t:{p:{'id':t+p} for p in ('p0','p1')} for t in ('na','ready')}}
    preparations={t+p:{'execution_id':'prep-'+t+p,'status':'NOT_APPLICABLE' if t=='na' and p=='p1' else 'PREPARED',
        'cost':{'wall_second':.01},'reason':None} for t in ('na','ready') for p in ('p0','p1')}
    requests={t:{r:{'idempotency_key':t+r,'inputs':{}} for r in (('r0',) if t=='na' else ('r0','r1','r2'))} for t in ('na','ready')}
    frozen={'tasks':{t:{'candidates':[{'id':f'c{i}'} for i in range(6)]} for t in ('na','ready')}}
    monkeypatch.setattr('etalon.research.retained_baseline.freeze_retained_baseline',lambda *a,**k:(policy,'plan',requests,frozen))
    monkeypatch.setattr('etalon.research.retained_baseline.paid_preparation',lambda j,i:preparations[i])
    monkeypatch.setattr('etalon.research.retained_baseline.preparation_cost_attribution',lambda j,i:{'wall_second':2.,'execution_costs':[]})
    class Cursor:
        def __init__(self,value):self.value=value
        def fetchone(self):return self.value
    class DB:
        def __init__(self,j):self.j=j
        def execute(self,sql,args):
            key=args[0]
            return Cursor((True,) if key.removeprefix('research:') in self.j.records else None)
    class Journal:
        def __init__(self):self.records={};self.store=self
        def get(self,key):
            if key not in self.records:raise StateError('unknown research record: '+key)
            return self.records[key]
        def _put(self,db,key,body):
            if key in self.records:assert self.records[key]==body
            self.records[key]=body
        @contextmanager
        def connection(self,**k):yield DB(self)
        def balance(self):return {'spent':8.,'reserved':0}
    journal=Journal();log=[]
    def execute(j,client,q,**kw):
        task,rid=kw['task_id'],kw['resource_id'];log.append((task,rid))
        if rid!='r0':assert (tmp_path/'out/predictions-frozen.json').exists()
        indices=range(6) if rid=='r0' else range(3) if rid=='r1' else range(3,6)
        result={'request_sha256':digest(q),'result_sha256':digest([task,rid]),'cost':{'wall_second':3.},
                'slots':[{'candidate_id':f'c{i}','score':-float(i)} for i in indices]}
        j.records['pooladmission-'+digest([result['request_sha256'],result['result_sha256']])]=result
        return result
    monkeypatch.setattr('etalon.research.retained_baseline.execute',execute)
    args=dict(config_path=None,manifest_path=None,input_freeze_path=None,root=tmp_path/'jobs',output_dir=tmp_path/'out')
    with pytest.raises(StateError,match='freeze B'):run_retained_baseline(journal,None,**args,complete_counterfactual=True)
    prediction=run_retained_baseline(journal,None,**args)
    assert log==[('na','r0'),('ready','r0')]
    assert prediction['tasks']['na']['attributed_path_cost']==5.
    assert prediction['phi_learning_not_established'] and prediction['tasks']['na']['query_history']==[]
    original=(tmp_path/'out/predictions-frozen.json').read_bytes()
    run_retained_baseline(journal,None,**args);assert len(log)==2
    report=run_retained_baseline(journal,None,**args,complete_counterfactual=True)
    assert log==[('na','r0'),('ready','r0'),('ready','r1'),('ready','r2')]
    na=report['tasks']['na'];assert na['raw_P_scores'] is na['score_cost'] is na['raw_P_scientific_loss'] is None
    assert na['preparation_cost']==2 and na['score_execution_state']=='branch_skipped' and na['effective_fallback']=='B'
    assert report['tasks']['ready']['score_cost']==6 and len(report['tasks']['ready']['raw_P_scores'])==6
    assert (tmp_path/'out/predictions-frozen.json').read_bytes()==original


def test_freeze_rejects_cross_protocol_preparation_and_missing_NA_group(tmp_path,monkeypatch):
    from etalon.research.retained_baseline import freeze_retained_baseline
    from etalon.active.schema import canonical
    from etalon.boundary.prism_v1 import file_identity
    names=['hif2a','a','b','c','d','e','cal1','cal2','cal3','ca2','ar','mmp12']
    tasks={t:{'role':'train' if i<6 else 'calibration' if i<9 else 'test','group':t,'family':t} for i,t in enumerate(names)}
    def write(name,value):
        p=tmp_path/name;p.write_text(canonical(value));return p
    frozen=write('frozen.json',{'tasks':tasks});manifest=write('manifest.json',{'input_freeze_sha256':file_identity(frozen)['sha256'],
        'labels_accessed':False,'pose_generation':False,'requests':[]})
    config={'split':{'tasks':tasks,'grouping_evidence':'fixture','protocol_sha256':'0'*64},'protocol_sha256':'0'*64,
        'input_freeze_sha256':file_identity(frozen)['sha256'],'strategy':'retained_baseline_due_insufficient_intervention_support',
        'evaluation_cost_weight':.001,'preparation_admissions':{t:{r:t+r for r in (('p0','p1') if t in ('hif2a','ca2','ar','mmp12') else ('p1',))} for t in names}}
    path=write('config.json',config);bad_receipt=write('old-protocol.json',{'protocol_sha256':'1'*64})
    def paid(j,identifier):
        rid=identifier[-2:];task=identifier[:-2]
        return {'task_id':task,'resource_id':rid,'result_artifact_id':'original-preparation-artifact'}
    monkeypatch.setattr('etalon.research.retained_baseline.paid_preparation',paid)
    class Journal:
        def get(self,key):return {'path':str(bad_receipt)}
    args=dict(config_path=path,manifest_path=manifest,input_freeze_path=frozen)
    with pytest.raises(StateError,match='different execution protocol'):freeze_retained_baseline(Journal(),**args)
    config['preparation_admissions'].pop('cal2');path.write_text(canonical(config))
    with pytest.raises(StateError,match='all twelve p1'):freeze_retained_baseline(Journal(),**args)
    config['split']['tasks'].pop('cal2');path.write_text(canonical(config))
    with pytest.raises(StateError,match='all twelve frozen'):freeze_retained_baseline(Journal(),**args)
