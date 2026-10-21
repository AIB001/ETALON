"""Ordered visible-query test execution; counterfactual completion is a later command."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path

from etalon.active.schema import canonical,digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from .pool import execute,semantic_request,validate
from .rows import visible_receipt
from .transfer import unpack,record_policy,next_action
from .workflows import bindings_for,terminal_scores,validate_receptor_binding


def execute_visible_task(policy,*,task_id,candidate_ids,acquire,receipt_builder):
    """No full task row or scientific label enters this execution boundary."""
    _,catalog,_=unpack(policy)
    binding=bindings_for(policy,catalog)
    admissions={};actions=[];history=[]
    def obtain(rid,reason):
        if rid not in admissions:
            admissions[rid]=acquire(rid,reason)
            actions.append({'kind':'acquire','resource_id':rid,'reason':reason,
                            'execution_id':admissions[rid]['execution_id']})
    for rid in catalog.initial_resources:obtain(rid,'common_initial_prefix')
    initial=receipt_builder({r:admissions[r] for r in catalog.initial_resources},None)
    probes={p.id:p for p in catalog.probes}
    while True:
        action=next_action(policy,history,initial_receipt=initial)
        actions.append({'kind':'decision','action':action,'visible_queries':[r['probe_id'] for r in history]})
        if action['kind']=='query':
            pid=action['probe_id']
            for rid in action['resources_acquired']:obtain(rid,'query:'+pid)
            needed=set(catalog.initial_resources)|set(probes[pid].resources)
            history.append(receipt_builder({r:admissions[r] for r in needed},pid))
        else:
            # A stop node selects a workflow; it does not waive that workflow's cost.
            for rid in action.get('resources_acquired',[]):obtain(rid,'terminal:'+action['decision'])
            decision=action['decision'];break
    if not set(catalog.decision_resources[decision])<=set(admissions):
        raise StateError('terminal decision lacks paid required resources')
    scores=terminal_scores(binding,decision,{rid:{s['candidate_id']:s['score'] for s in a['slots']}
        for rid,a in admissions.items()},candidate_ids,catalog.abstention)
    return {'task_id':task_id,'policy_sha256':policy['policy_sha256'],'decision':decision,
        'candidate_scores':scores,'controller_status':action['status'],'query_history':history,
        'terminal_score_state':('complete' if all(v is not None for v in scores.values()) else
            'missing' if all(v is None for v in scores.values()) else 'partial'),
        'initial_receipt':initial,'actions':actions,
        'resources':{rid:{'execution_id':a['execution_id'],'result_sha256':a['result_sha256'],
                         'cost':a['cost'],'result_artifact_id':a['result_artifact_id']} for rid,a in admissions.items()},
        'actual_path_cost':sum(a['cost']['wall_second'] for a in admissions.values()),
        'cost_unit':catalog.cost_unit,'labels_accessed':False,'terminal_resources_paid':True}


def _write_once(path,value):
    text=canonical(value)+'\n'
    if path.exists():
        if path.read_text(encoding='utf-8')!=text:raise StateError('immutable online output changed')
    else:
        # Same-directory hard-link publication is exclusive and atomic. A crash
        # cannot leave a half-written file under the frozen output name.
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                         prefix='.online-',suffix='.tmp',delete=False) as stream:
            temp=Path(stream.name);stream.write(text);stream.flush();os.fsync(stream.fileno())
        try:
            try:os.link(temp,path)
            except FileExistsError:
                if path.read_text(encoding='utf-8')!=text:raise StateError('immutable online output changed')
        finally:temp.unlink(missing_ok=True)


def freeze_plan(journal,policy,manifest_path,input_freeze_path):
    split,catalog,_=unpack(policy)
    binding=bindings_for(policy,catalog)
    manifest=json.loads(manifest_path.read_text());frozen=json.loads(input_freeze_path.read_text())
    if (manifest['role']!='test' or manifest.get('labels_accessed') is not False
            or manifest.get('pose_generation') is not False
            or manifest['input_freeze_sha256']!=file_identity(input_freeze_path)['sha256']):
        raise StateError('test request manifest lacks frozen label-free structural provenance')
    requests={};semantic_map={}
    for entry in manifest['requests']:
        task,rid=entry['task'],entry['resource']
        if task not in split.task_ids('test') or entry['role']!='test' or rid not in binding['resources']:
            raise StateError('test request outside frozen task/resource catalogue')
        if {k:frozen['tasks'][task][k] for k in ('role','family','group')}!=split.tasks[task]:
            raise StateError('input-freeze grouping differs from policy')
        path=Path(entry['path'])
        if file_identity(path)['sha256']!=entry['sha256']:raise StateError('test request file changed')
        request=json.loads(path.read_text());validate(request,'request')
        sha=digest(semantic_request(request))
        if sha!=entry['request_sha256'] or sha in semantic_map:raise StateError('test semantic request identity conflict')
        candidates=frozen['tasks'][task]['candidates']
        if len(candidates)!=binding['candidate_count']:raise StateError('frozen candidate count differs from workflow')
        resource=binding['resources'][rid]
        expected=[candidates[i] for i in resource['candidate_indices']]
        if [p['candidate_id'] for p in request['poses']]!=[p['id'] for p in expected]:
            raise StateError('test candidate chunk differs from frozen input')
        for pose,candidate in zip(request['poses'],expected):
            ref=request['inputs'][pose['input_key']]
            if pose['expected_smiles']!=candidate['smiles'] or any(ref[k]!=candidate['input'][k] for k in ('sha256','bytes')):
                raise StateError('test candidate chemical state or input bytes changed')
        if request['evaluation']['scorer']!=resource['scorer']:
            raise StateError('wrong test resource scorer')
        if policy.get('workflow_bindings'):validate_receptor_binding(request,resource,binding['task_states'][task])
        if rid in requests.setdefault(task,{}):raise StateError('duplicate task resource')
        requests[task][rid]=request;semantic_map[sha]=task
    if set(requests)!=split.task_ids('test') or any(set(rs)!=set(binding['resources']) for rs in requests.values()):
        raise StateError('complete test request catalogue required, without reading its outcomes')
    for task,rs in (() if policy.get('workflow_bindings') else requests.items()):
        baseline=rs['r0']
        for request in rs.values():
            if request['input_identity']!=baseline['input_identity'] or any(
                    request['evaluation'][k]!=baseline['evaluation'][k] for k in ('center_A','size_A','spacing_A')):
                raise StateError('test scoring intervention changed receptor/frame/box')
    record_policy(journal,policy)
    body={'schema':'etalon-online-test-plan/1','policy_sha256':policy['policy_sha256'],'split_sha256':split.sha256,
          'requests':semantic_map,'manifest':{'path':str(manifest_path.absolute()),**file_identity(manifest_path)},
          'input_freeze':{'path':str(input_freeze_path.absolute()),**file_identity(input_freeze_path)}}
    identifier='poolplan-'+digest(body)
    with journal.store.connection(write=True) as db:journal._put(db,identifier,body)
    return identifier,requests,frozen


def online_run(journal,client,policy,*,manifest_path,input_freeze_path,root,output_dir):
    output_dir=Path(output_dir).resolve();output_dir.mkdir(parents=True,exist_ok=True);root=Path(root).resolve()
    plan_id,requests,frozen=freeze_plan(journal,policy,manifest_path,input_freeze_path)
    run_binding={'plan_id':plan_id,'root':str(root),'output_dir':str(output_dir),'source_sha256':file_identity(__file__)['sha256']}
    run_id='onlinerun-'+digest(run_binding);final_id='onlinepredictions-'+digest(run_id)
    try:
        final=journal.get(final_id)
        _write_once(output_dir/'predictions-frozen.json',final)
        return final
    except StateError as exc:
        if 'unknown research record' not in str(exc):raise
    # Refuse an already-acquired matrix masquerading as an online test. A resume
    # may see only executions for which this exact run wrote intent beforehand.
    with journal.store.connection() as db:
        for task,rs in requests.items():
            for rid,q in rs.items():
                eid='execution-'+digest({'idempotency_key':q['idempotency_key']})
                existing=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+eid,)).fetchone()
                intent='onlineintent-'+digest([run_id,task,rid])
                owned=db.execute('SELECT body FROM metadata WHERE key=?',('research:'+intent,)).fetchone()
                if (existing or (root/eid/'result.json').exists()) and not owned:
                    raise StateError('test resource already acquired outside this online run; use explicit offline evaluation')
    with journal.store.connection(write=True) as db:journal._put(db,run_id,run_binding)
    predictions={}
    for task,rs in sorted(requests.items()):
        def acquire(rid,reason):
            q=rs[rid];intent='onlineintent-'+digest([run_id,task,rid])
            with journal.store.connection(write=True) as db:
                journal._put(db,intent,{'run_id':run_id,'task_id':task,'resource_id':rid,'reason':reason,
                    'request_sha256':digest(semantic_request(q))})
            result=execute(journal,client,q,root=root,task_id=task,stage='test',resource_id=rid,frozen_plan_id=plan_id)
            aid='pooladmission-'+digest([result['request_sha256'],result['result_sha256']])
            body=journal.get(aid)
            _write_once(output_dir/(task+'-'+rid+'.admission.json'),body)
            return {**body,'admission_id':aid}
        def receipt_builder(admissions,probe_id):
            return visible_receipt(journal,policy,task_id=task,
                admission_ids={rid:a['admission_id'] for rid,a in admissions.items()},probe_id=probe_id)
        prediction=execute_visible_task(policy,task_id=task,
            candidate_ids=[c['id'] for c in frozen['tasks'][task]['candidates']],acquire=acquire,receipt_builder=receipt_builder)
        predictions[task]=prediction
        with journal.store.connection(write=True) as db:
            journal._put(db,'onlineprediction-'+digest([run_id,task]),prediction)
    final={'schema':'etalon-online-test-predictions/1','run_id':run_id,'plan_id':plan_id,
        'policy_sha256':policy['policy_sha256'],'split_sha256':policy['split_sha256'],'tasks':predictions,
        'labels_accessed':False,'counterfactual_completion_started':False,'budget_at_freeze':journal.store.balance(),
        'scope':'real ordered acquisition before frozen predictions; scientific loss assessed independently later'}
    with journal.store.connection(write=True) as db:journal._put(db,final_id,final)
    _write_once(output_dir/'predictions-frozen.json',final)
    return final


def complete_counterfactual(journal,client,policy,*,manifest_path,input_freeze_path,root,output_dir):
    """Separate operation gated by the exact previously frozen online predictions."""
    output_dir=Path(output_dir).resolve();prediction_path=output_dir/'predictions-frozen.json'
    if not prediction_path.is_file():raise StateError('freeze online predictions before counterfactual acquisition')
    final=json.loads(prediction_path.read_text())
    if journal.get('onlinepredictions-'+digest(final['run_id']))!=final or final['policy_sha256']!=policy['policy_sha256']:
        raise StateError('frozen prediction/journal/policy mismatch')
    plan_id,requests,_=freeze_plan(journal,policy,manifest_path,input_freeze_path)
    if plan_id!=final['plan_id']:raise StateError('counterfactual request plan changed')
    admission_map={}
    for task,rs in sorted(requests.items()):
        admission_map[task]={}
        for rid,q in rs.items():
            result=execute(journal,client,q,root=Path(root),task_id=task,stage='test',resource_id=rid,frozen_plan_id=plan_id)
            aid='pooladmission-'+digest([result['request_sha256'],result['result_sha256']]);body=journal.get(aid)
            path=output_dir/(task+'-'+rid+'.admission.json');_write_once(path,body)
            admission_map[task][rid]=str(path)
    _write_once(output_dir/'complete-admission-map.json',admission_map)
    body={'schema':'etalon-test-counterfactual-completion/1','policy_sha256':policy['policy_sha256'],
        'prediction_file':{'path':str(prediction_path),**file_identity(prediction_path)},'admissions':admission_map,
        'scope':'additional measured counterfactual resources AFTER frozen online predictions; correlated offline controls'}
    with journal.store.connection(write=True) as db:journal._put(db,'counterfactual-'+digest(body),body)
    return body
