"""Pinned fixed-pool consumer, using the existing shared scientific execution ledger."""
from __future__ import annotations
import json
from pathlib import Path

from etalon.active.runner import ExecutionPending
from etalon.active.schema import canonical, digest, finite
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import CLITransport, file_identity, _WRITE_SCRIPT
from .journal import ResearchJournal
from .producer import _artifact
from .schema import Artifact, Coupling, Experiment, Observation

SCHEMA_HASHES = {
    'request':'c6a6df5135d68a1c00fa86b6e740da70089bbd412b61431c21ad35b054836d07',
    'result':'fa89690d97cc51b01cab271125a7fdd25ec74a57f3f19780b9e180a2ec4bc307'}
COMPONENTS = ('total','lig_inter','flex_inter','other_inter','flex_intra','lig_intra','torsions','lig_intra_best_pose')
_BIND = """import json,sys
from prism.pool_scoring.contract import bind,semantic_request
from prism.pool_scoring.service import producer
from prism.design.bundle import identifier
r=bind(json.load(sys.stdin));print(json.dumps(dict(request=r,semantic=semantic_request(r),
execution_id=identifier('execution',dict(idempotency_key=r['idempotency_key'])),producer=producer())))
"""
_CLI = """import json,subprocess,sys
p=subprocess.run([sys.executable,'-m','prism.pool_scoring',*sys.argv[1:]],capture_output=True,text=True)
print(json.dumps(dict(exit_code=p.returncode,response=json.loads(p.stdout),stderr=p.stderr)))
"""


def semantic_request(request):
    return {**request,'inputs':{k:{f:v for f,v in ref.items() if f!='path'} for k,ref in request['inputs'].items()}}


def validate(value, kind):
    from jsonschema import Draft202012Validator
    path=Path(__file__).parent/'contracts'/f'fixed-pool-{kind}-v1.schema.json'
    if file_identity(path)['sha256']!=SCHEMA_HASHES[kind]: raise StateError('pinned pool schema changed')
    Draft202012Validator(json.loads(path.read_text())).validate(value); canonical(value)


class PoolClient:
    def __init__(self, transport: CLITransport):
        if transport.host is not None: raise ValueError('pool consumer must run on the actual execution host')
        self.transport=transport

    def capabilities(self):
        result=self.transport._run([self.transport.python,'-m','prism.pool_scoring','capabilities'])
        if result.get('capability')!='fixed-pose-pool-score/1' or any(
                result['schemas'][k]['sha256']!=v for k,v in SCHEMA_HASHES.items()):
            raise StateError('producer/consumer pool schema mismatch')
        return result

    def bind(self, request):
        return self.transport._run([self.transport.python,'-c',_BIND],canonical(request).encode())

    def run(self, request, root):
        path=str(Path(self.transport.request_root)/(digest(request)+'.json'))
        self.transport._run([self.transport.python,'-c',_WRITE_SCRIPT,path],canonical(request).encode())
        return self.transport._run([self.transport.python,'-c',_CLI,'run','--request',path,'--root',str(root)])


def _check_result(request, result, result_path, req_sha, execution_id):
    validate(result,'result')
    for key in ('capability','experiment_id','program_id','action_id','idempotency_key','method_id',
                'protocol_instance_id','coupling','input_identity'):
        if result[key]!=request[key]: raise StateError('pool result binding mismatch: '+key)
    if (result['request_sha256']!=req_sha or result['execution_id']!=execution_id
            or result['cost']['charge_identity']!=execution_id):
        raise StateError('pool request/execution/cost identity mismatch')
    if {k:{f:v for f,v in ref.items() if f!='path'} for k,ref in result['input_bindings'].items()} != semantic_request(request)['inputs']:
        raise StateError('pool input hashes differ')
    files={ref['path']:_artifact(result_path.parent,ref) for ref in result['artifacts']}
    if len(files)!=len(result['artifacts']): raise StateError('duplicate result artifacts')
    counts,rows=result['counts'],result['outcomes']
    if (counts['requested']!=len(request['poses']) or counts['attempted']!=len(rows)
            or counts['succeeded']!=sum(r['status']=='SUCCEEDED' for r in rows)
            or counts['failed']!=len(rows)-counts['succeeded']
            or counts['not_attempted']!=counts['requested']-len(rows)
            or [r['slot'] for r in rows]!=list(range(1,len(rows)+1))):
        raise StateError('pool slot denominator mismatch')
    prepared=[]
    for i,pose in enumerate(request['poses']):
        row=rows[i] if i<len(rows) else {'status':'NOT_ATTEMPTED'}
        source=request['inputs'][pose['input_key']]
        if i<len(rows) and (any(row[k]!=pose[k] for k in ('slot','candidate_id','pose_label'))
                or {k:v for k,v in row['source_input'].items() if k!='path'}!={k:v for k,v in source.items() if k!='path'}):
            raise StateError('pool slot/candidate/source association changed')
        measured=None; path=None
        if row['status']=='SUCCEEDED':
            ref=row.get('measurement',{})
            if ref.get('path') not in files: raise StateError('unmanifested pool measurement')
            path=_artifact(result_path.parent,ref); measured=json.loads(path.read_text())
            if (measured.get('status')!='measured' or measured.get('unit')!='kcal/mol'
                    or measured.get('pose_optimized') is not False or measured.get('identity')!=row.get('identity')
                    or measured.get('receptor_preparation_sha256')!=request['inputs']['scorer_manifest']['sha256']
                    or measured.get('method')!=request['evaluation']['scorer']+'-1.2.7-score-only/meeko-0.7.1-gasteiger/1'):
                raise StateError('pool measurement method/pose identity mismatch')
            identity=row['identity']
            if any(identity.get(k)!=v for k,v in request['input_identity'].items()):
                raise StateError('pool produced receptor/frame identity mismatch')
            expected_pose='pose-'+digest({'source_sha256':source['sha256'],
                'chemical_state_id':identity['chemical_state_id'],'coordinate_frame_id':identity['coordinate_frame_id']})
            if identity.get('pose_id')!=expected_pose: raise StateError('pool source-to-pose identity mismatch')
            values=measured['components_kcal_mol']; named=measured['components_named_kcal_mol']
            if len(values)!=8 or set(named)!=set(COMPONENTS) or any(named[k]!=v for k,v in zip(COMPONENTS,values)):
                raise StateError('pool named energy component mismatch')
            for value in values: finite(value,'measured energy')
            if measured['total_kcal_mol']!=values[0]: raise StateError('pool total energy mismatch')
        prepared.append((pose,row,measured,path))
    return prepared


def execute(journal: ResearchJournal, client: PoolClient, request: dict, *, root: Path,
            task_id: str, stage: str, resource_id: str, source_kind='new_execution',
            allow_dispatch=True, frozen_plan_id: str | None=None) -> dict:
    """Reserve -> dispatch/read -> validate -> settle once -> immutable admission.

    Existing settled producer-owned ledger entries are checked and preserved; a
    consumer import never relabels or recharges those historical receipts.
    """
    if stage not in {'seen','train','calibration','test'} or not task_id or not resource_id:
        raise ValueError('explicit task, role and resource identity required')
    if source_kind not in {'fixture','new_execution','existing_real_artifact'}: raise ValueError('source kind required')
    validate(request,'request')
    if any(r['role'] in {'sealed_holdout','independent_validation'} for r in request['inputs'].values()):
        raise StateError('withheld labels cannot enter pool scoring')
    req_sha=digest(semantic_request(request))
    if stage=='test':
        if not frozen_plan_id: raise StateError('test requests require frozen policy and request plan')
        plan=journal.get(frozen_plan_id)
        policy=journal.get(plan.get('policy_record_id','learnedpolicy-'+plan['policy_sha256']))
        if (plan['requests'].get(req_sha)!=task_id or policy['split']['tasks'][task_id]['role']!='test'
                or plan['split_sha256']!=policy['split_sha256']):
            raise StateError('test request is outside frozen plan')
    client.capabilities(); bound=client.bind(request)
    if bound['request']!=request or bound['semantic']!=semantic_request(request):
        raise StateError('producer pool fingerprint/semantic request mismatch')
    for ref in request['inputs'].values():
        if file_identity(ref['path'])!={k:ref[k] for k in ('sha256','bytes')}:
            raise StateError('bound pool input changed')
    root=Path(root).resolve(); execution_id=bound['execution_id']; result_path=root/execution_id/'result.json'
    with journal.store.connection() as db:
        old=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+execution_id,)).fetchone()
    prior=json.loads(old[0]) if old else None
    if prior:
        if prior['request_sha256']!=req_sha or prior['unit']!='wall_second':
            raise StateError('historical execution request/cost unit mismatch')
        if not result_path.is_file(): raise ExecutionPending('acknowledged pool execution pending; no redispatch')
    else:
        if not allow_dispatch and not result_path.is_file(): raise ExecutionPending('read-only admission has no terminal receipt')
        journal.store.reserve_resource_execution(execution_id,req_sha,request['budget']['wall_seconds'],unit='wall_second',
            provenance={'kind':'prism_fixed_pool','task_id':task_id,'stage':stage,'resource_id':resource_id,
                'source_kind':source_kind,'producer':bound['producer'],'output_root':str(root),
                'frozen_plan_id':frozen_plan_id})
    dispatched=False
    if not result_path.is_file():
        try:
            client.run(request,root); dispatched=True
        except Exception as exc:
            journal.store.settle_resource_execution(execution_id,measured_cost=None,
                receipt={'status':'transport_outcome_unknown','error':str(exc)})
            raise ExecutionPending('pool outcome unknown; reservation retained') from exc
    if not result_path.is_file():
        raise ExecutionPending('no terminal pool receipt; reservation retained')
    result=json.loads(result_path.read_text()); prepared=_check_result(request,result,result_path,req_sha,execution_id)
    result_sha=digest(result)
    if prior and prior['status']=='settled':
        receipt=prior['receipt'] or {}
        if (prior['cost']!=result['cost']['wall_second']
                or receipt.get('result_sha256',result_sha) not in {result_sha,file_identity(result_path)['sha256']}
                or receipt.get('status',result['status'])!=result['status']):
            raise StateError('existing settled charge differs from terminal pool receipt')
    else:
        journal.store.settle_resource_execution(execution_id,measured_cost=result['cost']['wall_second'],
            receipt={'result_sha256':result_sha,'path':str(result_path),'cost':result['cost'],'status':result['status']})
    admission_id='pooladmission-'+digest([req_sha,result_sha])
    with journal.store.connection() as db:
        old=db.execute('SELECT body FROM metadata WHERE key=?',('research:'+admission_id,)).fetchone()
        if old:
            body=json.loads(old[0])
            if any(body[k]!=v for k,v in {'task_id':task_id,'stage':stage,'resource_id':resource_id,
                    'execution_id':execution_id,'result_sha256':result_sha}.items()):
                raise StateError('pool admission cannot be rebound to another task/resource')
            arts=journal._artifacts(db,(body['result_artifact_id'],)); journal._artifacts(db,arts[0].parents)
            for slot in body['slots']:
                obs=journal._get(db,slot['observation_id']); journal._artifacts(db,obs['evidence'])
            return {**body,'replayed':True,'budget':journal.store.balance(),'additional_execution':False}
    inputs=tuple(journal.record(Artifact(ref['path'],ref['sha256'],ref['bytes'],source_kind,'diagnostic',
        {**request['input_identity'],'task_id':task_id,'input_key':name})) for name,ref in sorted(request['inputs'].items()))
    result_art=Artifact(str(result_path),**file_identity(result_path),source_kind='fixture' if source_kind=='fixture' else 'new_execution',
        role='diagnostic',bindings={**request['input_identity'],'task_id':task_id,'resource_id':resource_id},parents=inputs)
    journal.record(result_art); slots=[]
    for pose,row,measured,path in prepared:
        refs=[result_art.id]
        if measured:
            art=Artifact(str(path),**file_identity(path),source_kind=result_art.source_kind,role='diagnostic',
                bindings={**request['input_identity'],'candidate_id':pose['candidate_id'],'pose_label':pose['pose_label']},
                parents=(result_art.id,)); journal.record(art); refs.append(art.id)
        ex=Experiment(task_id,'reanalysis',{'stage':stage,'resource_id':resource_id,'execution_id':execution_id,
            'candidate_id':pose['candidate_id'],'pose_label':pose['pose_label'],'expected_smiles':pose['expected_smiles'],
            **request['input_identity']},(result_art.id,),request['method_id'],request['protocol_instance_id'],
            ('score',),Coupling((result_art.id,),(),(),('supplied fixed pool; coupled scores, not independent replicates',)))
        journal.record(ex)
        obs=Observation(ex.id,'measured' if measured else 'failed' if row['status']=='FAILED' else 'missing',
            {'score':measured['total_kcal_mol']} if measured else {},{'score':'kcal/mol'} if measured else {},
            tuple(refs),reason='' if measured else (row.get('error') or {}).get('message','not attempted'))
        journal.record(obs)
        slots.append({'slot':pose['slot'],'candidate_id':pose['candidate_id'],'pose_label':pose['pose_label'],
            'expected_smiles':pose['expected_smiles'],'status':row['status'],'score':measured['total_kcal_mol'] if measured else None,
            'identity':row.get('identity'),'source_input':request['inputs'][pose['input_key']],
            'observation_id':obs.id,'components_kcal_mol':measured['components_named_kcal_mol'] if measured else None})
    body={'schema':'etalon-fixed-pool-admission/1','task_id':task_id,'stage':stage,'resource_id':resource_id,
        'request_sha256':req_sha,'result_sha256':result_sha,'result_path':str(result_path),'execution_id':execution_id,
        'result_artifact_id':result_art.id,'producer_status':result['status'],'cost':result['cost'],
        'slots':slots,'counts':result['counts'],'method_id':request['method_id'],'evaluation':request['evaluation'],
        'frozen_plan_id':frozen_plan_id,'scientific_scope':'empirical fixed-pose score; no affinity observation'}
    with journal.store.connection(write=True) as db: journal._put(db,admission_id,body)
    return {**body,'replayed':not dispatched,'budget':journal.store.balance(),'additional_execution':dispatched}
