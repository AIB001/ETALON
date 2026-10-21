"""Synthetic wire receipts test accounting, persistence and NA identity, not native preparation."""
import json
from pathlib import Path
import pytest
from etalon.active import CampaignSpec,CampaignStore,Endpoint
from etalon.active.schema import canonical,digest
from etalon.active.store import StateError
from etalon.active.runner import ExecutionPending
from etalon.boundary.prism_v1 import file_identity
from etalon.research import ResearchJournal
from etalon.research.preparation import admit_preparation,paid_preparation


def setup_case(tmp_path,status):
    store=CampaignStore(tmp_path/'fixture.sqlite');store.configure(CampaignSpec('unmeasured',1200,'wall_second','fixture'),
        [Endpoint('unmeasured','fixture','not_measured','count','fixture',0,queryable=False)])
    journal=ResearchJournal(store)
    def file(name,value):
        p=tmp_path/name;p.write_text(value);return {'path':str(p),**file_identity(p)}
    source=file('source.fixture','fixture source');pin=file('source.py','fixture source pin')
    request={'schema':'prism-receptor-preparation-request/1','execution_id':'execution-fixture-prep','task_id':'t',
        'state_key':'P','resource_id':'p1','stage':'precompile','protocol_sha256':'0'*64,
        'inputs':{'source':source},'recipe':{'fixture':True},'parent_state':None}
    req=file('request.json',canonical(request));result_path=tmp_path/'result.json'
    artifacts={'request':req,'certificate':file('certificate.json',canonical({'fixture':True,'status':status})),
        'execution_log':file('execution.log','fixture')}
    if status=='PREPARED':
        for key in ('receptor_source','scorer_receptor','scorer_manifest','native_parameters'):artifacts[key]=file(key+'.fixture',key)
    result={k:v for k,v in request.items() if k!='recipe'}
    result.update(schema='prism-receptor-preparation-result/1',request_sha256=digest(request),status=status,
        reason=None if status=='PREPARED' else {'code':status,'message':'synthetic fixture'},
        cost={'wall_second':.2,'cpu_second':.1,'scope':'whole preparation execution including child processes; excludes ledger operations and prior research inspection'},
        artifacts=artifacts,input_identity={'receptor_state_id':'P','coordinate_frame_id':'f'} if status=='PREPARED' else None,
        grid={'center_A':[0,0,0],'size_A':[22,22,22],'spacing_A':.375} if status=='PREPARED' else None,
        command=['fixture-only'],toolchain={'python':'fixture','modules':{'fixture':{'version':'0','path':pin['path'],'sha256':pin['sha256']}}},
        source_pins={'fixture':pin},scope='precompiled receptor resource; online scoring only')
    return journal,Path(req['path']),result_path,result


@pytest.mark.parametrize('status',['PREPARED','NOT_APPLICABLE','FAILED'])
def test_real_reservation_precedes_fixture_invoke_and_replay_never_recharges(tmp_path,status):
    journal,req,path,result=setup_case(tmp_path,status);called=[]
    def invoke(request,output):
        assert journal.store.balance()['reserved']==120
        called.append(True);output.write_text(canonical(result))
    first=admit_preparation(journal,request_path=req,result_path=path,reservation=120,invoke=invoke)
    balance=journal.store.balance();events=journal.store.events()
    second=admit_preparation(journal,request_path=req,result_path=path,reservation=120,invoke=invoke)
    assert first==second and len(called)==1 and journal.store.events()==events
    assert balance==journal.store.balance() and balance['spent']==.2
    assert paid_preparation(journal,first['id'])['status']==status and first['scientific_loss'] is None
    Path(result['artifacts']['certificate']['path']).write_text('tampered')
    with pytest.raises(StateError,match='identity changed'):admit_preparation(journal,request_path=req,result_path=path)


def test_pending_is_not_redispatched_and_late_receipt_settles_original_reservation(tmp_path):
    journal,req,path,result=setup_case(tmp_path,'FAILED');called=[]
    def crash(*args):called.append(True);raise RuntimeError('fixture interrupted')
    with pytest.raises(ExecutionPending):admit_preparation(journal,request_path=req,result_path=path,reservation=120,invoke=crash)
    with pytest.raises(ExecutionPending):admit_preparation(journal,request_path=req,result_path=path,reservation=120,invoke=crash)
    assert len(called)==1 and journal.store.balance()['reserved']==120
    path.write_text(canonical(result));value=admit_preparation(journal,request_path=req,result_path=path)
    assert value['status']=='FAILED' and journal.store.balance()['reserved']==0 and journal.store.balance()['spent']==.2


def test_receipt_without_preexisting_reservation_is_not_retrospectively_fabricated(tmp_path):
    journal,req,path,result=setup_case(tmp_path,'NOT_APPLICABLE');path.write_text(canonical(result))
    with pytest.raises(StateError,match='prior reservation'):admit_preparation(journal,request_path=req,result_path=path)
    assert journal.store.balance()['spent']==journal.store.balance()['reserved']==0


def certification_case(journal,req,path,result,tmp_path,*,name='cert',change=None):
    from copy import deepcopy
    request=json.loads(req.read_text());request['execution_id']='execution-'+name
    request['recipe']={'operation':'certification_only','reused_execution_id':result['execution_id']}
    reused={'path':str(path),**file_identity(path)}
    request['inputs']['reused_preparation_receipt']=reused
    new=deepcopy(result);new['cost']['wall_second']=.01
    new['input_identity']['receptor_state_id']='fixed-identity'
    if change:change(request,new)
    req2=tmp_path/(name+'-request.json');req2.write_text(canonical(request))
    for key in ('execution_id','task_id','state_key','resource_id','protocol_sha256','inputs','parent_state'):new[key]=request[key]
    new['request_sha256']=digest(request)
    new['artifacts']['request']={'path':str(req2),**file_identity(req2)}
    new['artifacts']['reused_preparation_receipt']=reused
    result2=tmp_path/(name+'-result.json')
    admitted=admit_preparation(journal,request_path=req2,result_path=result2,reservation=120,
        invoke=lambda q,p:p.write_text(canonical(new)))
    return admitted,req2,result2,new


def test_certification_adds_native_cost_once_preserves_old_admission_and_replays(tmp_path):
    from etalon.research.preparation import preparation_cost_attribution
    j,q,p,r=setup_case(tmp_path,'PREPARED')
    old=admit_preparation(j,request_path=q,result_path=p,reservation=120,invoke=lambda q,p:p.write_text(canonical(r)))
    before=canonical(j.get(old['id']))
    new,q2,p2,r2=certification_case(j,q,p,r,tmp_path)
    cost=preparation_cost_attribution(j,new['id'])
    assert new['cost']['wall_second']==.01 and cost['wall_second']==pytest.approx(.21)
    assert len(cost['execution_costs'])==2 and not cost['parent_state_charged']
    assert canonical(j.get(old['id']))==before and 'cost_attribution' not in old
    assert preparation_cost_attribution(j,old['id'])['wall_second']==.2
    events=j.store.events();balance=j.store.balance()
    assert admit_preparation(j,request_path=q2,result_path=p2)==new
    assert j.store.events()==events and j.store.balance()==balance
    assert balance['spent']==pytest.approx(.21)
    newest,_,_,_=certification_case(j,q2,p2,r2,tmp_path,name='cert2')
    cost2=preparation_cost_attribution(j,newest['id'])
    assert cost2['wall_second']==pytest.approx(.22) and len(cost2['execution_costs'])==3
    assert len({c['execution_id'] for c in cost2['execution_costs']})==3


@pytest.mark.parametrize('mismatch',['task','frame','native','cycle','unadmitted'])
def test_certification_rejects_incompatible_or_cyclic_reuse(tmp_path,mismatch):
    j,q,p,r=setup_case(tmp_path,'PREPARED')
    admit_preparation(j,request_path=q,result_path=p,reservation=120,invoke=lambda q,p:p.write_text(canonical(r)))
    def change(request,new):
        if mismatch=='task':request['task_id']='another-task'
        if mismatch=='frame':new['input_identity']['coordinate_frame_id']='another-frame'
        if mismatch=='native':new['artifacts']['native_parameters']=new['artifacts']['scorer_manifest']
        if mismatch=='cycle':request['recipe']['reused_execution_id']=request['execution_id']
        if mismatch=='unadmitted':request['recipe']['reused_execution_id']='execution-unadmitted'
    with pytest.raises(StateError,match='mismatch|coordinate frame|native artifact|cyclic|admitted'):
        certification_case(j,q,p,r,tmp_path,change=change)
    # A failed admission never refunds an actual already executed certification.
    assert j.store.balance()['spent']==pytest.approx(.21)


def test_scientific_parent_is_not_same_resource_cost_lineage(tmp_path):
    from copy import deepcopy
    from etalon.research.preparation import preparation_cost_attribution
    j,q,p,r=setup_case(tmp_path,'PREPARED')
    old=admit_preparation(j,request_path=q,result_path=p,reservation=120,invoke=lambda q,p:p.write_text(canonical(r)))
    rq=json.loads(q.read_text());rq['execution_id']='execution-child-native';rq['state_key']='P-child'
    rq['parent_state']={'execution_id':old['execution_id'],'state_key':old['state_key'],'receipt_sha256':old['result_sha256']}
    qchild=tmp_path/'child-request.json';qchild.write_text(canonical(rq));pchild=tmp_path/'child-result.json'
    child=deepcopy(r)
    for key in ('execution_id','state_key','parent_state'):child[key]=rq[key]
    child['request_sha256']=digest(rq);child['artifacts']['request']={'path':str(qchild),**file_identity(qchild)}
    admit_preparation(j,request_path=qchild,result_path=pchild,reservation=120,invoke=lambda q,p:p.write_text(canonical(child)))
    cert,_,_,_=certification_case(j,qchild,pchild,child,tmp_path)
    attributed=preparation_cost_attribution(j,cert['id'])
    assert attributed['wall_second']==pytest.approx(.21) and j.store.balance()['spent']==pytest.approx(.41)
    assert old['execution_id'] not in {c['execution_id'] for c in attributed['execution_costs']}
