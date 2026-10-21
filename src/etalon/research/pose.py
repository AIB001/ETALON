"""Consumer of the separate-graph-pose PRISM capability, with shared budget admission."""
from __future__ import annotations
import json
from pathlib import Path
from etalon.active.runner import ExecutionPending
from etalon.active.schema import canonical, digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import CLITransport, file_identity, _WRITE_SCRIPT
from .journal import ResearchJournal
from .producer import _artifact
from .schema import Artifact, Change, Coupling, Experiment, Observation

SCHEMA_HASHES = {'request':'776292f3f5e4fe9fd58813ee37615eb570312a03910da39e8550d46f05e64c0f',
                 'result':'2bfd4dc64cb671509390c44adf31b72c45429fa2c3955c2796642a664fc3fe3a'}
_BIND = """import json,sys
from prism.pose_diagnostic.contract import bind,semantic_request
from prism.design.bundle import identifier
r=bind(json.load(sys.stdin));print(json.dumps({'request':r,'semantic':semantic_request(r),'execution_id':identifier('execution',{'idempotency_key':r['idempotency_key']})}))
"""
_CLI = """import json,subprocess,sys
p=subprocess.run([sys.executable,'-m','prism.pose_diagnostic',*sys.argv[1:]],capture_output=True,text=True)
print(json.dumps({'exit_code':p.returncode,'response':json.loads(p.stdout),'stderr':p.stderr}))
"""
_CHECK = """import json,sys
from prism.pose_diagnostic.structure import check_inputs
g=check_inputs(json.load(sys.stdin));print(json.dumps({'checked':True,'design':str(g.document.path)}))
"""
_TARGET = """import json,sys
from prism.diagnostic.structure import bound_design,target_graph,identity
q=json.load(sys.stdin);g=bound_design(q);mol,_=target_graph(g,q['intervention'])
target=identity(g,mol);target.pop('pose_id')
print(json.dumps({'requested_target_identity':target,'source_identity':identity(g)}))
"""


def validate(value, kind):
    from jsonschema import Draft202012Validator
    path=Path(__file__).parent/'contracts'/f'pose-diagnostic-{kind}-v1.schema.json'
    if file_identity(path)['sha256']!=SCHEMA_HASHES[kind]:raise StateError('pinned pose schema changed')
    Draft202012Validator(json.loads(path.read_text())).validate(value);canonical(value)


class PoseClient:
    def __init__(self, transport: CLITransport):
        if transport.host is not None:raise ValueError('pose consumer runs on execution host')
        self.transport=transport

    def capabilities(self):
        result=self.transport._run([self.transport.python,'-m','prism.pose_diagnostic','capabilities'])
        if any(result['schemas'][k]['sha256']!=v for k,v in SCHEMA_HASHES.items()):
            raise StateError('producer/consumer pose schema mismatch')
        return result

    def bind(self,request):
        return self.transport._run([self.transport.python,'-c',_BIND],canonical(request).encode())

    def run(self,request,root):
        path=str(Path(self.transport.request_root)/(digest(request)+'.json'))
        self.transport._run([self.transport.python,'-c',_WRITE_SCRIPT,path],canonical(request).encode())
        return self.transport._run([self.transport.python,'-c',_CLI,'run','--request',path,'--root',str(root)])


def measurement_methods(request: dict, modules: dict) -> dict:
    return {
        'E0':'measurement-'+digest({'readout':'all_ligand_NO_receptor_NO_pairs_le_3.5A',
                                   'module':modules['prism.diagnostic.structure']['sha256']}),
        'E1':'measurement-'+digest({'readout':'Vina_score_only_total',
                                   'module':modules['prism.pose_diagnostic.scoring']['sha256'],
                                   'evaluation':request['evaluation'],
                                   'scorer_manifest':request['inputs'].get('scorer_manifest',{}).get('sha256')})}


def slot_identity(requested_target: dict, produced_identity: dict | None) -> dict:
    """Failed/unattempted slots retain intended chemistry, without inventing a pose."""
    identity=dict(requested_target)
    if 'pose_id' in identity:raise StateError('requested target is a graph/state, not a produced pose')
    if produced_identity:
        if any(produced_identity.get(k)!=v for k,v in identity.items()):
            raise StateError('produced pose does not match requested target chemical identity')
        identity.update(produced_identity)
    return identity


def validation_request_binding(journal, frozen, bound, task_id):
    """New decisions pin request hashes; legacy decisions allow acknowledged replay only."""
    sha=digest(bound['semantic'])
    if 'validation_request_sha256s' in frozen:
        if sha not in frozen['validation_request_sha256s']:
            raise StateError('request was not frozen for this validation panel')
        return 'frozen_request'
    # The first actual controller already pinned requests but its decision did not
    # repeat that list. Do not invent a new authorization or rewrite its decision.
    with journal.store.connection() as db:
        row=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+bound['execution_id'],)).fetchone()
    prior=json.loads(row[0]) if row else None
    if (not prior or prior['status']!='settled' or prior['request_sha256']!=sha
            or prior['provenance'].get('task_id')!=task_id
            or prior['provenance'].get('stage')!='validation'
            or prior['provenance'].get('decision_sha256')!=frozen['freeze_sha256']):
        raise StateError('legacy decision permits acknowledged prior receipt replay only')
    return 'legacy_acknowledged_replay_only'


def execute(journal: ResearchJournal, client: PoseClient, request: dict, *, root: Path,
            task_id: str, stage: str, source_kind: str, decision_sha256: str | None = None) -> dict:
    if stage not in {'smoke','development','validation'}:raise ValueError('explicit scientific budget stage required')
    if stage=='validation':
        if not decision_sha256:raise StateError('validation execution requires frozen development decision')
        frozen=journal.get('decision-'+decision_sha256)
        if digest({k:v for k,v in frozen.items() if k!='freeze_sha256'})!=decision_sha256:
            raise StateError('decision changed')
        panel=journal.get(frozen['validation_panel_id'])
        if panel['task_id']!=task_id:raise StateError('wrong held-out task')
    capability=client.capabilities();bound=client.bind(request);validate(request,'request')
    if stage=='validation':validation_request_binding(journal,frozen,bound,task_id)
    if bound['request']!=request:raise StateError('request fingerprints differ from producer bind')
    if request['capability']!=capability['capability'] or request['operation'] not in capability['operations']:
        raise StateError('unsupported pose capability')
    if any(ref['role'] in {'independent_validation','sealed_holdout'} for ref in request['inputs'].values()):
        raise StateError('held-out labels/structures cannot enter preparation')
    for ref in request['inputs'].values():client.transport.verify(ref)
    target_binding=client.transport._run([client.transport.python,'-c',_TARGET],canonical(request).encode())
    if stage=='validation' and target_binding['requested_target_identity']['chemical_state_id'] not in {
            c['chemical_state_id'] for c in panel['candidates'].values()}:
        raise StateError('requested validation chemical state not in frozen candidate set')
    root=Path(root).resolve();result_path=root/bound['execution_id']/'result.json'
    req_sha=digest(bound['semantic'])
    provenance={'kind':'prism_pose_diagnostic','task_id':task_id,'stage':stage,
                'producer':client.transport.fingerprint(),'source_kind':source_kind,
                'output_root':str(root),'decision_sha256':decision_sha256,
                'action_id':request['action_id'],'program_id':request['program_id']}
    requested_source_kind=source_kind
    with journal.store.connection() as db:
        prior_row=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+bound['execution_id'],)).fetchone()
    if prior_row:
        # The caller is describing this read, not changing historical production.
        prior_ledger=json.loads(prior_row[0])
        source_kind=prior_ledger['provenance']['source_kind']
        provenance['source_kind']=source_kind
    reservation=journal.store.reserve_resource_execution(bound['execution_id'],req_sha,
        request['budget']['wall_seconds'],unit='wall_second',provenance=provenance)
    if reservation['new_reservation']:
        try:cli=client.run(request,root)
        except Exception as error:
            journal.store.settle_resource_execution(bound['execution_id'],measured_cost=None,
                receipt={'status':'transport_outcome_unknown','error':str(error)})
            raise ExecutionPending('pose outcome unknown; reservation retained, no redispatch') from error
    else:
        if not result_path.is_file():raise ExecutionPending('pose result pending; no redispatch')
        prior=json.loads(result_path.read_text());validate(prior,'result')
        if prior['status']=='COMPLETED' and prior['supported']:
            client.transport._run([client.transport.python,'-c',_CHECK],canonical(request).encode())
        cli={'response':{'replayed':True},'scope':'read acknowledged terminal receipt'}
    if not result_path.is_file():
        journal.store.settle_resource_execution(bound['execution_id'],measured_cost=None,receipt=cli)
        raise ExecutionPending('no terminal pose receipt')
    result=json.loads(result_path.read_text());validate(result,'result')
    for key in ('capability','experiment_id','program_id','action_id','idempotency_key','method_id','protocol_instance_id','coupling'):
        if result[key]!=request[key]:raise StateError('pose result identity mismatch: '+key)
    if result['request_sha256']!=req_sha or result['execution_id']!=bound['execution_id'] or result['cost']['charge_identity']!=bound['execution_id']:
        raise StateError('pose request/execution/cost identity mismatch')
    if {k:{f:v for f,v in ref.items() if f!='path'} for k,ref in result['input_bindings'].items()}!=bound['semantic']['inputs']:
        raise StateError('pose input binding mismatch')
    if result['input_identity'] is not None and request['input_identity'] is not None and result['input_identity']!=request['input_identity']:
        raise StateError('pose input scientific identity mismatch')
    journal.store.settle_resource_execution(bound['execution_id'],measured_cost=result['cost']['wall_second'],
        receipt={'result_sha256':digest(result),'path':str(result_path),'cost':result['cost'],'status':result['status']})
    files={ref['path']:_artifact(result_path.parent,ref) for ref in result['artifacts']}
    counts=result['counts'];rows=result['outcomes']
    if counts['attempted']!=len(rows) or counts['succeeded']!=sum(r['status']=='SUCCEEDED' for r in rows) or counts['failed']!=len(rows)-counts['succeeded'] or counts['not_attempted']!=counts['requested']-len(rows) or [r['slot'] for r in rows]!=list(range(1,len(rows)+1)):
        raise StateError('pose denominator mismatch')
    admission_id='poseadmission-'+digest([req_sha,digest(result)])
    with journal.store.connection() as db:
        stored_row=db.execute('SELECT body FROM metadata WHERE key=?',('research:'+admission_id,)).fetchone()
        if stored_row:
            stored=json.loads(stored_row[0])
            for key,value in {'request_sha256':req_sha,'result_sha256':digest(result),
                              'execution_id':bound['execution_id'],'task_id':task_id,'stage':stage,
                              'result_path':str(result_path)}.items():
                if stored.get(key)!=value:raise StateError('stored admission binding mismatch: '+key)
            artifacts=journal._artifacts(db,(stored['result_artifact_id'],))
            journal._artifacts(db,artifacts[0].parents)
            for slot in stored['slots']:
                for ref in slot['observations'].values():
                    observation=journal._get(db,ref['observation_id'])
                    journal._artifacts(db,observation['evidence'])
            # Never regenerate Artifact/Experiment/Observation parents on replay.
            # JSON key order and a new read's source flag must not change history.
            return {**stored,'replayed':True,'budget':journal.store.balance(),
                    'replay_context':{'requested_source_kind':requested_source_kind,
                                      'historical_source_kind':source_kind,
                                      'admission_id':admission_id,'projection':'original_immutable_admission'}}
    inputs=[]
    for name in sorted(request['inputs']):
        ref=request['inputs'][name]
        inputs.append(journal.record(Artifact(ref['path'],ref['sha256'],ref['bytes'],source_kind,'diagnostic',request['input_identity'] or {})))
    kind='fixture' if source_kind=='fixture' else 'new_execution'
    result_art=Artifact(str(result_path),**file_identity(result_path),source_kind=kind,role='diagnostic',
                        bindings=result['input_identity'] or {},parents=tuple(inputs));journal.record(result_art)
    mapping_id='mapping-'+digest(request['intervention'])
    measurements=[]
    modules=result['producer']['modules']
    methods=measurement_methods(request,modules)
    for slot in range(1,counts['requested']+1):
        row=rows[slot-1] if slot<=len(rows) else {'slot':slot,'status':'NOT_ATTEMPTED'}
        measured=None;evidence=[result_art.id]
        if row.get('measurement'):
            ref=row['measurement'];path=_artifact(result_path.parent,ref)
            if ref['path'] not in files:raise StateError('unmanifested pose measurement')
            measured=json.loads(path.read_text())
            if measured.get('schema_version')!='prism-pose-measurement/1' or measured.get('length_unit')!='angstrom' or measured.get('exact_graph') is not True:
                raise StateError('pose measurement graph/schema/units mismatch')
            art=Artifact(str(path),ref['sha256'],ref['bytes'],kind,'diagnostic',row.get('identity',{}),(result_art.id,))
            evidence.append(journal.record(art))
        produced_identity=row.get('identity')
        identity=slot_identity(target_binding['requested_target_identity'],produced_identity)
        changes=() if request['operation_nature']!='intervention' else (
            Change('preparation',('pose_constraints',),None,request['pose_constraints'],tuple(inputs),'explicit graph-preserving coordinate intervention'),)
        ex=Experiment(task_id,request['operation_nature'],{**identity,'mapping_id':mapping_id,
            'wire_action_id':request['action_id'],'slot':str(slot),'stage':stage},tuple(inputs),
            request['method_id'],digest([request['protocol_instance_id'],slot]),
            ('polar_pair_count','vina_total','geometry'),Coupling(tuple(inputs),(),(),
            ('shared receptor/core/seed; transformed slots are not independent tasks',)),changes)
        journal.record(ex)
        out={'slot':slot,'pose_id':produced_identity.get('pose_id') if produced_identity else None,
             'mapping_id':mapping_id,'raw':row.get('raw'),'pose_admitted':row.get('pose_admitted',False),
             'requested_target_identity':target_binding['requested_target_identity'],
             'produced_identity':produced_identity,'source_identity':target_binding['source_identity'],
             'source_slot':row,'observations':{}}
        qualified=bool(measured and row.get('pose_admitted') and measured['pose_policy']['pass_'])
        for evaluation,readout,unit in [('E0','polar_pair_count','heavy_atom_pair_count'),('E1','vina_total','kcal/mol')]:
            value=None
            if qualified and evaluation=='E0':value=len(measured['polar_pairs_within_3_5A'])
            if qualified and evaluation=='E1' and measured['vina_score']['status']=='measured':
                if measured['vina_score']['unit']!='kcal/mol' or measured['vina_score']['pose_optimized'] is not False:
                    raise StateError('scorer scope/unit mismatch')
                value=measured['vina_score']['total_kcal_mol']
            status='measured' if value is not None else 'missing' if row['status']=='NOT_ATTEMPTED' else 'rejected' if not qualified else 'failed'
            obs=Observation(ex.id,status,{readout:value} if value is not None else {},
                {readout:unit} if value is not None else {},tuple(evidence),
                '' if value is not None else canonical({'slot':slot,'row_status':row['status'],'scorer':measured.get('vina_score') if measured else None}),
                measurement_method_id=methods[evaluation]);journal.record(obs)
            out['observations'][evaluation]={'observation_id':obs.id,'readout':readout,'status':status}
        measurements.append(out)
    receipt={'schema':'etalon-pose-admission/1','request_sha256':req_sha,'execution_id':bound['execution_id'],
             'task_id':task_id,'stage':stage,'result_path':str(result_path),'result_sha256':digest(result),
             'result_artifact_id':result_art.id,'producer_status':result['status'],'slots':measurements,
             'measurement_methods':methods,'cost':result['cost'],'counts':counts,
             'requested_target_identity':target_binding['requested_target_identity'],
             'scientific_judgment':'unresolved','evaluation_cost':'see producer timing; unavailable if not separately measured',
             'scope':'all-atom polar proxy and Vina score-only; neither is experimental affinity'}
    with journal.store.connection(write=True) as db:journal._put(db,admission_id,receipt)
    return {**receipt,'replayed':not reservation['new_reservation'] or cli['response'].get('replayed',False),
            'budget':journal.store.balance()}
