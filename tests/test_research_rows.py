"""Synthetic artifact/receipt ingress; no scientific labels or computations."""
from dataclasses import asdict
import json
from pathlib import Path
import pytest
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical,digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Artifact,ResearchJournal
from etalon.research.fixed_pool import two_chunk_catalog
from etalon.research.rows import assemble_rows,verify_dataset,visible_receipt
from etalon.research.transfer import fit_policy,record_policy


def test_actual_artifact_binding_idempotency_and_unreleased_rows(tmp_path):
    store=CampaignStore(tmp_path/'fixture.sqlite');store.configure(CampaignSpec('unmeasured',1200,'wall_second','fixture'),
        [Endpoint('unmeasured','fixture','not_measured','count','fixture',0,queryable=False)])
    journal=ResearchJournal(store)
    def write(name,value):
        path=tmp_path/name;path.write_text(canonical(value));return path
    tasks={t:dict(group=t,family=t,role=role) for t,role in [('a','train'),('b','train'),('c','calibration'),('d','test')]}
    catalog=two_chunk_catalog(initial_quote=300,first_quote=300,second_quote=300,loss_definition='synthetic_loss')
    config=write('config.json',dict(split=dict(tasks=tasks,grouping_evidence='synthetic',protocol_sha256='0'*64),catalog=asdict(catalog)))
    candidates=[]
    for i in range(6):
        p=tmp_path/f'candidate{i}.fixture';p.write_text('synthetic input')
        candidates.append(dict(id=f'c{i}',input=dict(path=str(p),**file_identity(p))))
    frozen=write('freeze.json',dict(tasks={t:{**v,'candidates':candidates} for t,v in tasks.items()}))
    oracle=tmp_path/'oracle.py';oracle.write_text("def evaluate(scores,task):\n return {'loss': 1. if all(v is None for v in scores.values()) else task['fixture_loss']}\n")
    release=write('labels.json',dict(role='train',input_freeze_sha256=file_identity(frozen)['sha256'],
        script_sha256=file_identity(oracle)['sha256'],tasks={'a':{'fixture_loss':.25},'b':{'fixture_loss':.75}}))
    admissions={};admission_ids={}
    for t in ('a','b'):
        admissions[t]={};admission_ids[t]={}
        for rid,indices in [('r0',range(6)),('r1',range(3)),('r2',range(3,6))]:
            eid='execution-'+digest([t,rid]);req=digest(['request',t,rid])
            store.reserve_resource_execution(eid,req,300,unit='wall_second',provenance={'synthetic':True})
            store.settle_resource_execution(eid,measured_cost=.2,receipt={'fixture':True})
            artifact_path=write(t+rid+'.result.json',{'fixture':True})
            artifact=Artifact(str(artifact_path),**file_identity(artifact_path),source_kind='fixture',role='diagnostic',bindings={})
            journal.record(artifact)
            slots=[dict(candidate_id=f'c{i}',source_input=candidates[i]['input'],score=-float(i)) for i in indices]
            body=dict(task_id=t,stage='train',resource_id=rid,request_sha256=req,result_sha256=digest([t,rid,'result']),
                result_artifact_id=artifact.id,execution_id=eid,evaluation={'scorer':'vina' if rid=='r0' else 'vinardo'},
                slots=slots,cost={'wall_second':.2})
            aid='pooladmission-'+digest([req,body['result_sha256']]);admission_ids[t][rid]=aid
            with store.connection(write=True) as db:journal._put(db,aid,body)
            admissions[t][rid]=str(write(t+rid+'.admission.json',body))
    args=dict(config_path=config,input_freeze_path=frozen,label_release_path=release,oracle_path=oracle,
              admission_map=admissions,role='train')
    rows,report=assemble_rows(journal,**args);events=store.events()
    assert all(r['source_kind']=='synthetic_fixture' and all(e.startswith('artifact-') for e in r['evidence']) for r in rows)
    repeated,_=assemble_rows(journal,**args)
    assert repeated==rows and store.events()==events
    verify_dataset(journal,rows,role='train',split_sha256=report['split_sha256'])
    from etalon.research.rows import config_objects
    split,_=config_objects(json.loads(config.read_text()))
    policy=fit_policy(rows,split=split,catalog=catalog,budget=900,max_depth=2,cost_weight=.001,static_order=('q1','q2'))
    record_policy(journal,policy)
    init=visible_receipt(journal,policy,task_id='a',admission_ids={'r0':admission_ids['a']['r0']})
    assert init['resource_costs']=={'r0':.2}
    q1=visible_receipt(journal,policy,task_id='a',probe_id='q1',admission_ids={r:admission_ids['a'][r] for r in ('r0','r1')})
    assert q1['outcome']=='same' and q1['resource_costs']=={'r1':.2}
    with pytest.raises(StateError,match='only initial'):
        visible_receipt(journal,policy,task_id='a',probe_id='q1',admission_ids=admission_ids['a'])
    with pytest.raises(StateError,match='before reading'):
        assemble_rows(journal,**{**args,'role':'test','label_release_path':tmp_path/'MUST_NOT_OPEN.json'})
    release.write_text('{}')
    with pytest.raises(StateError,match='artifact changed'):
        verify_dataset(journal,rows,role='train',split_sha256=report['split_sha256'])
