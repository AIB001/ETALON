"""Admit PRISM's independent preparation receipts into the existing ledger."""
import json
from pathlib import Path
from etalon.active.schema import digest
from etalon.active.runner import ExecutionPending
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from .schema import Artifact

SCHEMA_HASHES={'request':'cbdc53a126d433004e86b9d5e8f9717e69626f15486ace5a3fc855ac16598c7c',
               'result':'4aa44d4b60668be8f8dc5b3e9fb021120db75eae12b52ea4e7dafad0a58e2791'}


def validate(value,kind):
    from jsonschema import Draft202012Validator
    path=Path(__file__).parent/'contracts'/f'receptor-preparation-{kind}-v1.schema.json'
    if file_identity(path)['sha256']!=SCHEMA_HASHES[kind]:raise StateError('pinned preparation schema changed')
    Draft202012Validator(json.loads(path.read_text())).validate(value);digest(value)


def checked_file(ref):
    if not Path(ref['path']).is_absolute() or file_identity(ref['path'])!={k:ref[k] for k in ('sha256','bytes')}:
        raise StateError('preparation input/artifact identity changed')


def admit_preparation(journal,*,request_path,result_path,reservation=None,invoke=None):
    """invoke is owner-supplied actual preparer, called only after original reserve.

    Existing unsettled receipts recover their original charge; missing receipts
    with a reservation remain pending. Never retroactively manufacture a reserve.
    """
    request_path=Path(request_path);result_path=Path(result_path)
    request=json.loads(request_path.read_text());validate(request,'request')
    request_sha=digest(request);eid=request['execution_id']
    for ref in request['inputs'].values():checked_file(ref)
    with journal.store.connection() as db:
        row=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+eid,)).fetchone()
    prior=json.loads(row[0]) if row else None
    if prior:
        if prior['request_sha256']!=request_sha or prior['unit']!='wall_second':
            raise StateError('preparation execution already bound to another request')
        if not result_path.is_file():raise ExecutionPending('existing preparation reservation unresolved; no redispatch')
    else:
        if result_path.exists():raise StateError('preparation receipt lacks prior reservation; no retrospective charge creation')
        if invoke is None or reservation is None:raise ExecutionPending('preparation has no original execution receipt')
        journal.store.reserve_resource_execution(eid,request_sha,reservation,unit='wall_second',provenance={
            'kind':'prism_receptor_preparation','task_id':request['task_id'],'resource_id':request['resource_id'],
            'stage':'precompile','state_key':request['state_key'],'result_path':str(result_path.absolute())})
        try:invoke(request_path,result_path)
        except Exception as exc:raise ExecutionPending('preparation execution outcome unresolved; reservation retained') from exc
    if not result_path.is_file():raise ExecutionPending('no terminal preparation receipt; reservation retained')
    result=json.loads(result_path.read_text());validate(result,'result')
    for field in ('execution_id','task_id','state_key','resource_id','stage','protocol_sha256','inputs','parent_state'):
        if result[field]!=request[field]:raise StateError('preparation result binding mismatch: '+field)
    if result['request_sha256']!=request_sha:raise StateError('preparation request fingerprint differs')
    for ref in [*result['artifacts'].values(),*result['source_pins'].values()]:checked_file(ref)
    request_artifact=result['artifacts']['request']
    if json.loads(Path(request_artifact['path']).read_text())!=request:
        raise StateError('retained preparation request differs')
    cost=result['cost']['wall_second']
    if prior and prior['status']=='settled':
        if prior['cost']!=cost:raise StateError('preparation cost differs from original settled charge')
    else:
        journal.store.settle_resource_execution(eid,measured_cost=cost,
            receipt={'preparation_status':result['status'],'result':{'path':str(result_path.absolute()),**file_identity(result_path)}})
    parent=request['parent_state']
    if parent:
        with journal.store.connection() as db:
            candidates=[json.loads(row[0]) for row in db.execute("SELECT body FROM metadata WHERE key LIKE 'research:preparationadmission-%'")]
        matches=[p for p in candidates if p['execution_id']==parent['execution_id'] and p['state_key']==parent['state_key']
                 and p['result_sha256']==parent['receipt_sha256'] and p['task_id']==request['task_id']]
        if len(matches)!=1:raise StateError('preparation parent must reference an admitted original receipt')
    def artifact(ref,kind):
        return journal.record(Artifact(str(Path(ref['path']).absolute()),ref['sha256'],ref['bytes'],
            'existing_real_artifact','diagnostic',{'kind':kind,'task_id':request['task_id'],'execution_id':eid}))
    refs=[artifact(v,'preparation_input') for v in result['inputs'].values()]
    refs.extend(artifact(v,'preparation_'+k) for k,v in result['artifacts'].items())
    refs.extend(artifact(v,'preparation_source_pin') for v in result['source_pins'].values())
    receipt_id=artifact({'path':str(result_path.absolute()),**file_identity(result_path)},'preparation_receipt')
    body={'schema':'etalon-preparation-admission/1','execution_id':eid,'task_id':request['task_id'],
        'state_key':request['state_key'],'resource_id':request['resource_id'],'stage':'precompile',
        'request_sha256':request_sha,'result_sha256':file_identity(result_path)['sha256'],
        'result_artifact_id':receipt_id,'evidence':sorted(set(refs+[receipt_id])),
        'status':result['status'],'reason':result['reason'],'cost':result['cost'],
        'artifacts':result['artifacts'],'input_identity':result['input_identity'],'grid':result['grid'],
        'parent_state':parent,'scope':result['scope'],'scientific_loss':None}
    identifier='preparationadmission-'+digest([request_sha,body['result_sha256']])
    if request['recipe'].get('operation')=='certification_only':
        body['cost_attribution']=_preparation_cost_attribution(journal,body)
    with journal.store.connection(write=True) as db:journal._put(db,identifier,body)
    return {'id':identifier,**body}


def paid_preparation(journal,identifier):
    value=journal.get(identifier)
    with journal.store.connection() as db:
        journal._artifacts(db,value['evidence'],diagnostic_inputs=True)
        row=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+value['execution_id'],)).fetchone()
    charge=json.loads(row[0]) if row else None
    if (not charge or charge['status']!='settled' or charge['cost']!=value['cost']['wall_second']
            or charge['request_sha256']!=value['request_sha256'] or charge['unit']!='wall_second'):
        raise StateError('preparation is not backed by its original settled charge')
    return value


def preparation_cost_attribution(journal,identifier):
    """Read verified execution lineage, without mutating or settling any charge.

    Reused native preparation and certification are summed once. The scientific
    parent_state is deliberately excluded; cold P separately requires p0+p1.
    """
    return _preparation_cost_attribution(journal,paid_preparation(journal,identifier))


def _preparation_cost_attribution(journal,value,trail=()):
    eid=value['execution_id']
    if eid in trail:raise StateError('cyclic preparation reuse lineage')
    ref=journal.get(value['result_artifact_id']);checked_file(ref)
    if ref['sha256']!=value['result_sha256']:raise StateError('preparation receipt hash mismatch')
    result=json.loads(Path(ref['path']).read_text());validate(result,'result')
    request_ref=result['artifacts']['request'];checked_file(request_ref)
    request=json.loads(Path(request_ref['path']).read_text());validate(request,'request')
    if digest(request)!=value['request_sha256'] or result['request_sha256']!=value['request_sha256']:
        raise StateError('preparation lineage request mismatch')
    for key in ('execution_id','task_id','state_key','resource_id','status','cost','artifacts','input_identity','grid','parent_state'):
        if value[key]!=result[key]:raise StateError('preparation lineage admission mismatch: '+key)
    for key in ('execution_id','task_id','state_key','resource_id','protocol_sha256','parent_state','inputs'):
        if request[key]!=result[key]:raise StateError('preparation lineage result mismatch: '+key)
    components=[]
    if request['recipe'].get('operation')=='certification_only':
        reused=request['inputs'].get('reused_preparation_receipt')
        if not reused or reused!=result['artifacts'].get('reused_preparation_receipt'):
            raise StateError('certification requires identical explicit reused receipt refs')
        checked_file(reused)
        reused_eid=request['recipe'].get('reused_execution_id')
        if reused_eid in (*trail,eid):raise StateError('cyclic preparation reuse lineage')
        with journal.store.connection() as db:
            candidates=[(row[0].removeprefix('research:'),json.loads(row[1])) for row in db.execute(
                "SELECT key,body FROM metadata WHERE key LIKE 'research:preparationadmission-%'")]
        matches=[(key,body) for key,body in candidates if body['execution_id']==reused_eid
                 and body['result_sha256']==reused['sha256']]
        if len(matches)!=1:raise StateError('certification requires one admitted reused preparation')
        original=paid_preparation(journal,matches[0][0])
        original_ref=journal.get(original['result_artifact_id'])
        if any(original_ref[k]!=reused[k] for k in ('path','sha256','bytes')):
            raise StateError('reused receipt differs from admitted artifact')
        old=json.loads(Path(reused['path']).read_text())
        if old['status']!='PREPARED' or result['status']!='PREPARED':
            raise StateError('certification reuse requires PREPARED states')
        for key in ('task_id','state_key','resource_id','protocol_sha256','grid','parent_state'):
            if old[key]!=result[key]:raise StateError('certification reused preparation mismatch: '+key)
        if old['input_identity']['coordinate_frame_id']!=result['input_identity']['coordinate_frame_id']:
            raise StateError('certification cannot change coordinate frame')
        for key in ('receptor_source','scorer_receptor','scorer_manifest','native_parameters'):
            checked_file(old['artifacts'][key]);checked_file(result['artifacts'][key])
            if old['artifacts'][key]!=result['artifacts'][key]:
                raise StateError('certification cannot replace native artifact: '+key)
        components.extend(_preparation_cost_attribution(journal,original,(*trail,eid))['execution_costs'])
    components.append({'execution_id':eid,'result_sha256':value['result_sha256'],
                       'wall_second':value['cost']['wall_second']})
    unique={}
    for component in components:
        key=component['execution_id']
        if key in unique and unique[key]!=component:raise StateError('conflicting preparation execution cost')
        unique[key]=component
    components=[unique[k] for k in sorted(unique)]
    attribution={'wall_second':sum(c['wall_second'] for c in components),'execution_costs':components,
        'parent_state_charged':False,'scope':'same-resource native preparation plus certification; parent_state excluded'}
    if 'cost_attribution' in value and value['cost_attribution']!=attribution:
        raise StateError('preparation cost attribution differs from verified lineage')
    return attribution
