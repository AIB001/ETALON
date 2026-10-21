"""Explicit synthetic receipt/transport fixtures; these do not validate Vina or PRISM execution."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical, digest
from etalon.active.store import StateError, BudgetExhausted
from etalon.active.runner import ExecutionPending
from etalon.boundary.prism_v1 import file_identity
from etalon.research import ResearchJournal
from etalon.research.pool import COMPONENTS, execute, semantic_request


def fixture(tmp_path, status='SUCCEEDED'):
    inputs={}
    for name in ('receptor_source','scorer_receptor','scorer_manifest','pose'):
        path=tmp_path/(name+'.fixture');path.write_text('synthetic-'+name)
        inputs[name]={'path':str(path),**file_identity(path),'role':'diagnostic_input'}
    q=dict(schema_version='prism-fixed-pool-request/1',capability='fixed-pose-pool-score/1',operation='measure',
        experiment_id='synthetic',program_id='synthetic',action_id='synthetic',idempotency_key='synthetic',
        inputs=inputs,poses=[dict(slot=1,candidate_id='fixture-candidate',pose_label='fixture-pose',input_key='pose',expected_smiles='C')],
        input_identity=dict(receptor_state_id='fixture-receptor',coordinate_frame_id='fixture-frame'),
        evaluation=dict(scorer='vina',center_A=[0,0,0],size_A=[20,20,20],spacing_A=.375),
        coupling=dict(group_id='fixture-group',shared_artifact_sha256s=[],randomness='deterministic_reanalysis',independent_replicate=False),
        budget=dict(wall_seconds=1.,threads=1),method_id='fixture-method',protocol_instance_id='fixture-instance')
    store=CampaignStore(tmp_path/'store.sqlite');store.configure(
        CampaignSpec('unmeasured',1200,'wall_second','research-fixed-pool/1'),
        [Endpoint('unmeasured','fixture','not_measured','count','fixture/1',0,queryable=False)])
    class FakeClient:
        calls=0
        def capabilities(self):return {'scope':'synthetic receipt fixture'}
        def bind(self,request):return dict(request=request,semantic=semantic_request(request),
            execution_id='execution-'+digest({'idempotency_key':request['idempotency_key']}),producer={'fixture':True})
        def run(self,request,root):
            self.calls+=1
            eid=self.bind(request)['execution_id'];out=Path(root)/eid;out.mkdir(parents=True)
            identity={**q['input_identity'],'chemical_state_id':'fixture-state',
                'pose_id':'pose-'+digest({'source_sha256':q['inputs']['pose']['sha256'],
                    'chemical_state_id':'fixture-state','coordinate_frame_id':'fixture-frame'})}
            row=dict(slot=1,candidate_id='fixture-candidate',pose_label='fixture-pose',source_input=inputs['pose'],status=status,
                     error={'code':'FIXTURE','message':'synthetic failure'} if status=='FAILED' else None)
            artifacts=[]
            if status=='SUCCEEDED':
                measurement=dict(status='measured',unit='kcal/mol',pose_optimized=False,identity=identity,
                    receptor_preparation_sha256=inputs['scorer_manifest']['sha256'],
                    method='vina-1.2.7-score-only/meeko-0.7.1-gasteiger/1',total_kcal_mol=-1.,
                    components_kcal_mol=[-1.]+[0.]*7,components_named_kcal_mol=dict(zip(COMPONENTS,[-1.]+[0.]*7)))
                path=out/'measurement.json';path.write_text(canonical(measurement));ref={'path':path.name,**file_identity(path)}
                artifacts=[ref];row.update(identity=identity,measurement=ref)
            result=dict(schema_version='prism-fixed-pool-result/1',
                **{k:q[k] for k in ('capability','experiment_id','program_id','action_id','idempotency_key','method_id','protocol_instance_id','coupling','input_identity')},
                execution_id=eid,attempt=1,request_sha256=digest(semantic_request(q)),request_file_sha256=digest(q),
                status='COMPLETED' if status=='SUCCEEDED' else 'FAILED',supported=True,error=None,input_bindings=inputs,
                outcomes=[row],artifacts=artifacts,counts=dict(requested=1,attempted=1,succeeded=int(status=='SUCCEEDED'),failed=int(status=='FAILED'),not_attempted=0),
                cost=dict(wall_second=.2,cpu_second=.1,cpu_measurement='synthetic fixture',charge_identity=eid),
                producer={'fixture':True},limitations=['synthetic fixture, no scientific computation'])
            (out/'result.json').write_text(canonical(result));return {'response':{'result':str(out/'result.json')}}
    return q,FakeClient(),ResearchJournal(store),tmp_path/'jobs'


@pytest.mark.parametrize('status',['SUCCEEDED','FAILED'])
def test_receipt_admission_cost_once_and_key_order_replay(tmp_path,status):
    q,c,j,root=fixture(tmp_path,status)
    args=dict(root=root,task_id='fixture',stage='train',resource_id='r0',source_kind='fixture')
    first=execute(j,c,q,**args);events=j.store.events();balance=j.store.balance()
    q['inputs']=dict(reversed(list(q['inputs'].items())))
    second=execute(j,c,q,**{**args,'source_kind':'existing_real_artifact'})
    assert c.calls==1 and second['slots']==first['slots']
    assert j.store.events()==events and j.store.balance()==balance and balance['spent']==.2
    assert first['slots'][0]['score']==(-1. if status=='SUCCEEDED' else None)
    assert not j.store.observations()
    with pytest.raises(StateError,match='rebound'):
        execute(j,c,q,**{**args,'resource_id':'r1'})


def test_existing_producer_owned_charge_remains_byte_identical(tmp_path):
    q,c,j,root=fixture(tmp_path);c.run(q,root);bound=c.bind(q)
    path=root/bound['execution_id']/'result.json';result=json.loads(path.read_text())
    j.store.reserve_resource_execution(bound['execution_id'],digest(semantic_request(q)),1.,unit='wall_second',
                                       provenance={'owner':'producer historical bootstrap'})
    j.store.settle_resource_execution(bound['execution_id'],measured_cost=.2,
                                      receipt={'result_sha256':file_identity(path)['sha256'],'status':'COMPLETED'})
    with j.store.connection() as db:
        before=db.execute("SELECT body FROM metadata WHERE key LIKE 'budget:execution:%'").fetchone()[0]
    admission=execute(j,c,q,root=root,task_id='fixture',stage='seen',resource_id='smoke',source_kind='fixture',allow_dispatch=False)
    with j.store.connection() as db:
        after=db.execute("SELECT body FROM metadata WHERE key LIKE 'budget:execution:%'").fetchone()[0]
    assert before==after and c.calls==1 and not admission['additional_execution']


def test_pending_and_sealed_inputs_or_test_plan_never_dispatch(tmp_path):
    q,c,j,root=fixture(tmp_path);args=dict(root=root,task_id='fixture',stage='train',resource_id='r0',source_kind='fixture')
    with pytest.raises(ExecutionPending):execute(j,c,q,**args,allow_dispatch=False)
    bound=c.bind(q);j.store.reserve_resource_execution(bound['execution_id'],digest(semantic_request(q)),1.,unit='wall_second',provenance={})
    with pytest.raises(ExecutionPending):execute(j,c,q,**args)
    assert c.calls==0 and j.store.balance()['reserved']==1
    changed=deepcopy(q);changed['inputs']['pose']['role']='sealed_holdout'
    with pytest.raises(StateError,match='withheld'):execute(j,c,changed,**args)
    with pytest.raises(StateError,match='frozen policy'):execute(j,c,q,**{**args,'stage':'test'})
    assert c.calls==0


def test_tampered_measurement_is_not_admitted_or_double_charged(tmp_path):
    q,c,j,root=fixture(tmp_path);args=dict(root=root,task_id='fixture',stage='train',resource_id='r0',source_kind='fixture')
    first=execute(j,c,q,**args);events=j.store.events()
    (Path(first['result_path']).parent/'measurement.json').write_text('{}')
    with pytest.raises(StateError,match='hash'):execute(j,c,q,**args)
    assert c.calls==1 and j.store.events()==events and j.store.balance()['spent']==.2
