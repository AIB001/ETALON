"""Evidence-backed R4 task rows; calls the coordinator's pinned assay oracle."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path

from etalon.active.schema import digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from .schema import Artifact
from .fixed_pool import subset_rank_outcome
from .transfer import Catalog, GroupSplit, checked_rows
from .diagnostics import Probe, Resource
from .workflows import bindings_for,probe_outcome,terminal_scores,validate_receptor_binding


def config_objects(config):
    c=config['catalog']
    return GroupSplit(**config['split']),Catalog(tuple(Probe(**p) for p in c['probes']),
        tuple(Resource(**r) for r in c['resources']),{k:tuple(v) for k,v in c['decision_resources'].items()},
        c['loss_definition'],c['cost_unit'],c['abstention'],c['initial_decision'],tuple(c.get('initial_resources',())))


def _file_artifact(journal,path,*,role='diagnostic',bindings=None):
    path=Path(path).resolve()
    artifact=Artifact(str(path),**file_identity(path),source_kind='existing_real_artifact',role=role,bindings=bindings or {})
    journal.record(artifact);return artifact.id


def verify_dataset(journal,rows,*,role,split_sha256):
    body=journal.get('taskrows-'+digest(rows))
    if body['role']!=role or body['split_sha256']!=split_sha256 or body['rows_sha256']!=digest(rows):
        raise StateError('row dataset not admitted for this frozen split/role')
    with journal.store.connection() as db:
        journal._artifacts(db,body['evidence'],diagnostic_inputs=role!='test')
        for row in rows:
            journal._artifacts(db,row['evidence'],diagnostic_inputs=role!='test')
            for resource,execution_id in body['executions'][row['task_id']].items():
                value=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+execution_id,)).fetchone()
                if not value:raise StateError('task row cost lacks actual ledger execution')
                receipt=json.loads(value[0])
                if receipt['status']!='settled' or receipt['cost']!=row['resource_costs'][resource]:
                    raise StateError('task row cost differs from settled ledger')
    return body


def visible_receipt(journal,policy,*,task_id,admission_ids,probe_id=None):
    """Build a controller receipt from paid pool admissions, with no label access."""
    from .transfer import unpack
    _,catalog,_=unpack(policy);binding=bindings_for(policy,catalog)
    if probe_id is not None and probe_id not in binding['probes']:raise StateError('unknown probe receipt')
    required=set(catalog.initial_resources)
    if probe_id is not None:required.update(binding['probes'][probe_id]['comparison_resources'])
    if set(admission_ids)!=required:
        raise StateError('receipt accepts only initial and the requested paid chunk')
    costs={};refs=[];scores={};execution_ids={}
    for rid,identifier in admission_ids.items():
        admission=journal.get(identifier)
        if admission['task_id']!=task_id or admission['resource_id']!=rid:
            raise StateError('visible receipt task/resource mismatch')
        with journal.store.connection() as db:
            journal._artifacts(db,(admission['result_artifact_id'],),diagnostic_inputs=True)
            row=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+admission['execution_id'],)).fetchone()
        ledger=json.loads(row[0]) if row else None
        if not ledger or ledger['status']!='settled' or ledger['cost']!=admission['cost']['wall_second']:
            raise StateError('visible outcome is not backed by a paid actual resource')
        refs.append(admission['result_artifact_id']);scores[rid]={s['candidate_id']:s['score'] for s in admission['slots']}
        if probe_id is None or rid not in catalog.initial_resources:
            costs[rid]=ledger['cost'];execution_ids[rid]=admission['execution_id']
    receipt={'policy_sha256':policy['policy_sha256'],'task_id':task_id,'resource_costs':costs,
             'evidence':sorted(refs),'admission_ids':admission_ids,'resource_executions':execution_ids}
    if probe_id:
        ordered={}
        for rid in catalog.initial_resources:
            for index,candidate in zip(binding['resources'][rid]['candidate_indices'],scores[rid]):ordered[index]=candidate
        if set(ordered)!=set(range(binding['candidate_count'])):raise StateError('initial scores must identify complete candidate pool')
        receipt.update(probe_id=probe_id,outcome=probe_outcome(binding,probe_id,scores,
            [ordered[i] for i in range(binding['candidate_count'])]))
    return receipt


def verify_visible_receipts(journal,policy,history,initial):
    for receipt in ([initial] if initial else [])+history:
        if visible_receipt(journal,policy,task_id=receipt['task_id'],admission_ids=receipt['admission_ids'],
                           probe_id=receipt.get('probe_id'))!=receipt:
            raise StateError('supplied controller receipt differs from actual paid evidence')
    if len({r['task_id'] for r in ([initial] if initial else [])+history})>1:
        raise StateError('controller history mixes tasks')


def assemble_rows(journal,*,config_path:Path,input_freeze_path:Path,label_release_path:Path,
                  oracle_path:Path,admission_map:dict,role:str,test_release:dict|None=None,policy:dict|None=None):
    config=json.loads(config_path.read_text());split,catalog=config_objects(config)
    binding=bindings_for(config,catalog)
    if role not in {'train','calibration','test'}:raise ValueError('explicit released role required')
    if role=='test' and (not policy or not test_release or test_release.get('policy_sha256')!=policy['policy_sha256']
            or test_release.get('split_sha256')!=split.sha256 or not test_release.get('evidence')):
        raise StateError('independent test release required before reading label file')
    # Only these explicitly released files are opened. No verification_only lookup.
    labels=json.loads(label_release_path.read_text());freeze=json.loads(input_freeze_path.read_text())
    if labels['role']!=role or labels['input_freeze_sha256']!=file_identity(input_freeze_path)['sha256']:
        raise StateError('label release role/input freeze mismatch')
    tasks=split.task_ids(role)
    if set(labels['tasks'])!=tasks or set(admission_map)!=tasks:
        raise StateError('release and admissions must contain exactly this role tasks')
    if any({k:freeze['tasks'][t][k] for k in ('role','group','family')}!=split.tasks[t] for t in split.tasks):
        raise StateError('input grouping differs from consumer split')
    if file_identity(oracle_path)['sha256']!=labels['script_sha256']:
        raise StateError('coordinator oracle script differs from released version')
    spec=importlib.util.spec_from_file_location('coordinator_r4_oracle',oracle_path)
    oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    label_role='independent_validation' if role=='test' else 'diagnostic'
    from .seen import register_seen_labels
    seen_artifact,lineage=register_seen_labels(journal,label_release_path,labels,split)
    if role=='test' and seen_artifact:raise StateError('seen development release cannot validate an independent test')
    common=[_file_artifact(journal,config_path,bindings={'kind':'frozen_consumer_config'}),
            _file_artifact(journal,input_freeze_path,bindings={'kind':'frozen_structural_input'}),
            seen_artifact or _file_artifact(journal,label_release_path,role=label_role,bindings={'kind':'released_assay_orders','role':role}),
            _file_artifact(journal,oracle_path,bindings={'kind':'coordinator_independent_loss_oracle'})]
    rows=[];executions={};assessments={}
    for task in sorted(tasks):
        ids=[c['id'] for c in freeze['tasks'][task]['candidates']]
        if ids!=sorted(ids) or len(ids)!=binding['candidate_count']:raise StateError('workflow requires its frozen ordered candidate count')
        resources={};evidence={};costs={};executions[task]={};source_kinds=set()
        if set(admission_map[task])!=set(binding['resources']):raise StateError('all actual resource receipts required')
        for rid,path in admission_map[task].items():
            value=json.loads(Path(path).read_text())
            aid='pooladmission-'+digest([value['request_sha256'],value['result_sha256']])
            admitted=journal.get(aid)
            source_kinds.add(journal.get(admitted['result_artifact_id'])['source_kind'])
            if any(value.get(k)!=v for k,v in admitted.items()):raise StateError('admission file differs from immutable journal')
            prior=lineage.get(task,{})
            reused_seen=(admitted['stage']=='test' and role in {'train','calibration'}
                and prior.get('original_executions',{}).get(rid)==admitted['execution_id'])
            if (admitted['task_id']!=task or (admitted['stage']!=role and not reused_seen) or admitted['resource_id']!=rid
                    or admitted['evaluation']['scorer']!=binding['resources'][rid]['scorer']):
                raise StateError('resource admission task/role/scorer mismatch')
            expected=[ids[i] for i in binding['resources'][rid]['candidate_indices']]
            if [s['candidate_id'] for s in admitted['slots']]!=expected:
                raise StateError('admission candidate partition differs from frozen catalogue')
            for slot in admitted['slots']:
                expected_input=next(c['input'] for c in freeze['tasks'][task]['candidates'] if c['id']==slot['candidate_id'])
                if any(slot['source_input'][k]!=expected_input[k] for k in ('sha256','bytes')):
                    raise StateError('candidate source hash differs from frozen input')
            resources[rid]={s['candidate_id']:s['score'] for s in admitted['slots']}
            if config.get('workflow_bindings'):
                artifact=journal.get(admitted['result_artifact_id'])
                inputs={journal.get(parent)['bindings']['input_key']:journal.get(parent) for parent in artifact['parents']}
                validate_receptor_binding({'input_identity':{k:v for k,v in artifact['bindings'].items()
                    if k not in {'task_id','resource_id'}},'evaluation':admitted['evaluation'],'inputs':inputs},
                    binding['resources'][rid],binding['task_states'][task])
            costs[rid]=admitted['cost']['wall_second'];executions[task][rid]=admitted['execution_id']
            evidence[rid]=[admitted['result_artifact_id'],_file_artifact(journal,path,
                bindings={'task_id':task,'role':role,'resource_id':rid})]
        if len(set(executions[task].values()))!=len(binding['resources']):raise StateError('resources require distinct actual execution receipts')
        if 'fixture' in source_kinds and source_kinds!={'fixture'}:
            raise StateError('fixture and scientific receipts cannot be combined')
        outcomes={pid:probe_outcome(binding,pid,resources,ids) for pid in binding['probes']}
        scores={d:terminal_scores(binding,d,resources,ids,catalog.abstention) for d in catalog.decision_resources}
        result={d:oracle.evaluate(s,labels['tasks'][task]) for d,s in scores.items()}
        assessments[task]=result
        if any(v['loss'] is None for v in result.values()):
            raise StateError('unavailable scientific loss; keep group unresolved, do not fabricate/drop it')
        all_refs=sorted(set(common+sum(evidence.values(),[])))
        rows.append({'task_id':task,'sample_unit':'task','independence_group':split.tasks[task]['group'],
            'source_kind':'synthetic_fixture' if source_kinds=={'fixture'} else 'measured',
            'loss_definition':catalog.loss_definition,'cost_unit':catalog.cost_unit,
            'outcomes':outcomes,'losses':{d:r['loss'] for d,r in result.items()},'resource_costs':costs,
            'evidence':all_refs,'outcome_evidence':{pid:sorted({e for rid in
                b['reference_resources']+b['comparison_resources'] for e in evidence[rid]}) for pid,b in binding['probes'].items()},
            'loss_evidence':{d:all_refs for d in scores},'cost_evidence':evidence})
    rows=checked_rows(rows,split,role,catalog)
    body={'schema':'etalon-admitted-task-rows/1','role':role,'split_sha256':split.sha256,'catalog_sha256':catalog.sha256,
        'rows_sha256':digest(rows),'evidence':sorted({e for r in rows for e in r['evidence']}),
        'executions':executions,'scientific_assessment':assessments,'oracle_scope':'coordinator released oracle; no duplicate assay logic',
        'source_kind':rows[0]['source_kind'],'test_release':test_release}
    if lineage:body['seen_development_lineage']=lineage
    # Validate every hash and actual charge BEFORE accepting rows for training.
    with journal.store.connection() as db:
        journal._artifacts(db,body['evidence'],diagnostic_inputs=role!='test')
        for row in rows:
            for rid,eid in executions[row['task_id']].items():
                old=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+eid,)).fetchone()
                receipt=json.loads(old[0]) if old else None
                if not receipt or receipt['status']!='settled' or receipt['cost']!=row['resource_costs'][rid]:
                    raise StateError('unsettled or mismatched resource cost')
    with journal.store.connection(write=True) as db:journal._put(db,'taskrows-'+digest(rows),body)
    return rows,body
