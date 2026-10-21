"""A frozen retained baseline with paid preparation and post-freeze P diagnostics.

No empirical worlds or fitted intervention selector are created by this module.
"""
import json
from pathlib import Path
from etalon.active.schema import digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from .transfer import GroupSplit
from .preparation import paid_preparation,preparation_cost_attribution
from .pool import semantic_request,validate,execute
from .online import _write_once
from .rows import _file_artifact


def paid_score(journal,identifier):
    value=journal.get(identifier)
    with journal.store.connection() as db:
        artifacts=journal._artifacts(db,[value['result_artifact_id']],diagnostic_inputs=True)
        journal._artifacts(db,artifacts[0].parents,diagnostic_inputs=True)
        record=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+value['execution_id'],)).fetchone()
    charge=json.loads(record[0]) if record else None
    if not charge or charge['status']!='settled' or charge['cost']!=value['cost']['wall_second']:
        raise StateError('score does not match its original settled charge')
    return value


def freeze_retained_baseline(journal,*,config_path,manifest_path,input_freeze_path):
    config=json.loads(Path(config_path).read_text());frozen=json.loads(Path(input_freeze_path).read_text())
    split=GroupSplit(**config['split']);manifest=json.loads(Path(manifest_path).read_text())
    if config['protocol_sha256']!=split.protocol_sha256:
        raise StateError('configuration and split execution protocol differ')
    if set(split.tasks)!=set(frozen['tasks']) or len(split.tasks)!=12:
        raise StateError('retained baseline must preserve all twelve frozen tasks')
    required_base=split.task_ids('test')|{'hif2a'}
    if set(config['preparation_admissions'])!=set(split.tasks) or any(
            set(config['preparation_admissions'][t])!=({'p0','p1'} if t in required_base else {'p1'}) for t in split.tasks):
        raise StateError('preparation map requires all twelve p1 and exactly the four required p0 resources')
    freeze_sha=file_identity(input_freeze_path)['sha256']
    if config['input_freeze_sha256']!=freeze_sha or manifest['input_freeze_sha256']!=freeze_sha:
        raise StateError('retained baseline requires original frozen structural inputs')
    if config['evaluation_cost_weight']!=.001 or config['strategy']!='retained_baseline_due_insufficient_intervention_support':
        raise StateError('this driver only implements the reviewed retained B strategy at fixed .001')
    if manifest.get('labels_accessed') is not False or manifest.get('pose_generation') is not False:
        raise StateError('label-free prepared input request manifest required')
    if any({k:frozen['tasks'][t][k] for k in ('role','group','family')}!=entry for t,entry in split.tasks.items()):
        raise StateError('new split differs from structural freeze')
    preps={};evidence=[]
    for task,resources in config['preparation_admissions'].items():
        if task not in split.tasks:raise StateError('preparation task outside frozen split')
        preps[task]={}
        for rid,identifier in resources.items():
            value=paid_preparation(journal,identifier)
            if value['task_id']!=task or value['resource_id']!=rid:raise StateError('preparation task/resource mismatch')
            result_artifact=journal.get(value['result_artifact_id'])
            original=json.loads(Path(result_artifact['path']).read_text())
            if original['protocol_sha256']!=config['protocol_sha256']:
                raise StateError('preparation belongs to a different execution protocol')
            preps[task][rid]={'id':identifier,'execution_id':value['execution_id'],
                'result_sha256':value['result_sha256'],'status':value['status'],
                'cost_attribution':preparation_cost_attribution(journal,identifier)}
            evidence.extend(value['evidence'])
    if any('p0' not in preps.get(t,{}) for t in split.task_ids('test')):
        raise StateError('every new test requires a retained base preparation receipt, including failure')
    reused=config.get('baseline_admissions',{})
    for task,identifier in reused.items():
        a=paid_score(journal,identifier)
        if task not in split.tasks or task in split.task_ids('test') or a['task_id']!=task or a['resource_id']!='r0':
            raise StateError('baseline reuse only permits exact seen task r0')
        if 'p0' in preps.get(task,{}):
            prep=paid_preparation(journal,preps[task]['p0']['id'])
            result_art=journal.get(a['result_artifact_id'])
            inputs={journal.get(p)['bindings']['input_key']:journal.get(p) for p in result_art['parents']}
            for key in ('receptor_source','scorer_receptor','scorer_manifest'):
                if any(inputs[key][f]!=prep['artifacts'][key][f] for f in ('sha256','bytes')):
                    raise StateError('seen B reuse differs from new preparation; use authorized new B execution')
        evidence.append(a['result_artifact_id'])
    requests={};request_files={}
    for entry in manifest['requests']:
        task,rid=entry['task'],entry['resource']
        if task not in split.tasks or entry['role']!=split.tasks[task]['role'] or rid not in {'r0','r1','r2'}:
            raise StateError('score request outside frozen task/resource catalogue')
        path=Path(entry['path'])
        if file_identity(path)['sha256']!=entry['sha256']:raise StateError('score request file changed')
        request=json.loads(path.read_text());validate(request,'request');sha=digest(semantic_request(request))
        if sha!=entry['request_sha256']:raise StateError('score semantic request changed')
        pid='p0' if rid=='r0' else 'p1'
        prep=paid_preparation(journal,preps[task][pid]['id'])
        if prep['status']!='PREPARED':raise StateError('unavailable preparation cannot have a scoring request')
        if request['input_identity']!=prep['input_identity'] or request['evaluation']['scorer']!='vina':
            raise StateError('B/P request must use its own prepared receptor and frozen Vina scorer')
        for key in ('receptor_source','scorer_receptor','scorer_manifest'):
            if any(request['inputs'][key][f]!=prep['artifacts'][key][f] for f in ('sha256','bytes')):
                raise StateError('score input differs from paid preparation output')
        grid=prep['grid']
        for score_key,prep_key in [('center_A','center_A'),('size_A','size_A'),('spacing_A','spacing_A')]:
            if request['evaluation'][score_key]!=grid[prep_key]:raise StateError('prepared score grid changed')
        candidates=frozen['tasks'][task]['candidates'];expected=candidates if rid=='r0' else candidates[:3] if rid=='r1' else candidates[3:]
        if len(candidates)!=6 or [p['candidate_id'] for p in request['poses']]!=[c['id'] for c in expected]:
            raise StateError('score candidate partition differs from frozen input')
        for pose,candidate in zip(request['poses'],expected):
            if pose['expected_smiles']!=candidate['smiles'] or any(request['inputs'][pose['input_key']][k]!=candidate['input'][k] for k in ('sha256','bytes')):
                raise StateError('score candidate graph/input bytes changed')
        if rid in requests.setdefault(task,{}):raise StateError('duplicate score resource')
        requests[task][rid]=request;request_files[sha]=task
    for task in split.task_ids('test'):
        if preps[task]['p0']['status']=='PREPARED' and 'r0' not in requests.get(task,{}):
            raise StateError('prepared test B requires its frozen scoring request')
    evidence.extend(_file_artifact(journal,Path(p),bindings={'kind':'r4b_retained_baseline_freeze'})
                    for p in (config_path,manifest_path,input_freeze_path))
    body={'schema':'etalon-retained-baseline-policy/1','split':config['split'],'split_sha256':split.sha256,
        'protocol_sha256':config['protocol_sha256'],'input_freeze_sha256':freeze_sha,
        'strategy':config['strategy'],'phi_learning_not_established':True,'model':None,
        'evaluation_cost_weight':.001,'preparation_admissions':preps,'evidence':sorted(set(evidence)),
        'baseline_admissions':reused,
        'manifest':{'path':str(Path(manifest_path).absolute()),**file_identity(manifest_path)},
        'program':{'kind':'stop','decision':'B','required_resources':['p0','r0']},
        'promotion':'none','cost_scope':'precompiled preparation attributed once per path; online scoring only'}
    policy={**body,'policy_sha256':digest(body)};identifier='baselinepolicy-'+policy['policy_sha256']
    plan={'schema':'etalon-online-test-plan/1','policy_record_id':identifier,'policy_sha256':policy['policy_sha256'],
        'split_sha256':split.sha256,'requests':request_files}
    plan_id='poolplan-'+digest(plan)
    with journal.store.connection(write=True) as db:
        journal._put(db,identifier,policy);journal._put(db,plan_id,plan)
    return policy,plan_id,requests,frozen


def run_retained_baseline(journal,client,*,config_path,manifest_path,input_freeze_path,root,output_dir,
                          complete_counterfactual=False):
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True);root=Path(root)
    policy,plan_id,requests,frozen=freeze_retained_baseline(journal,config_path=config_path,
        manifest_path=manifest_path,input_freeze_path=input_freeze_path)
    split=GroupSplit(**policy['split'])
    run_id='baselineonline-'+digest({'policy_sha256':policy['policy_sha256'],'plan_id':plan_id,
        'root':str(root.absolute()),'output_dir':str(output_dir.absolute())})
    prediction_path=output_dir/'predictions-frozen.json'
    _write_once(output_dir/'policy-frozen.json',policy)
    def acquire(task,rid,phase):
        request=requests[task][rid];eid='execution-'+digest({'idempotency_key':request['idempotency_key']})
        intent='baselineintent-'+digest([run_id,task,rid])
        with journal.store.connection(write=True) as db:
            old=db.execute('SELECT body FROM metadata WHERE key=?',('budget:execution:'+eid,)).fetchone()
            owned=db.execute('SELECT body FROM metadata WHERE key=?',('research:'+intent,)).fetchone()
            if (old or (root/eid/'result.json').exists()) and not owned:
                raise StateError('baseline score already acquired outside this frozen run')
            journal._put(db,intent,{'run_id':run_id,'task_id':task,'resource_id':rid,'phase':phase,'request_sha256':digest(semantic_request(request))})
        result=execute(journal,client,request,root=root,task_id=task,stage=split.tasks[task]['role'],resource_id=rid,
                       frozen_plan_id=plan_id if split.tasks[task]['role']=='test' else None)
        aid='pooladmission-'+digest([result['request_sha256'],result['result_sha256']]);admission=journal.get(aid)
        _write_once(output_dir/f'{task}-{rid}.admission.json',admission)
        return aid,admission
    if complete_counterfactual:
        if not prediction_path.exists():raise StateError('freeze B predictions before P diagnostics')
        predictions=json.loads(prediction_path.read_text())
        if journal.get('onlinepredictions-'+digest(run_id))!=predictions:raise StateError('prediction freeze/journal mismatch')
        diagnostics={}
        for task,preps in sorted(policy['preparation_admissions'].items()):
            if 'p1' not in preps:continue
            prep=paid_preparation(journal,preps['p1']['id'])
            prep_cost=preparation_cost_attribution(journal,preps['p1']['id']);scores=None;admissions={};score_cost=None
            baseline_admission=policy['baseline_admissions'].get(task)
            if task not in split.task_ids('test') and 'r0' in requests.get(task,{}):
                baseline_admission,_=acquire(task,'r0','seen_B_comparator')
            if prep['status']=='PREPARED':
                if not {'r1','r2'}<=set(requests.get(task,{})):raise StateError('prepared P requires both frozen score chunks')
                scores={};score_cost=0.
                for rid in ('r1','r2'):
                    aid,a=acquire(task,rid,'post_prediction_P_diagnostic');admissions[rid]=aid
                    scores.update({s['candidate_id']:s['score'] for s in a['slots']});score_cost+=a['cost']['wall_second']
            diagnostics[task]={'preparation_status':prep['status'],'preparation_admission_id':preps['p1']['id'],
                'preparation_cost':prep_cost['wall_second'],'preparation_cost_attribution':prep_cost,'score_admissions':admissions,'raw_P_scores':scores,
                'seen_B_admission_id':baseline_admission,
                'raw_P_scientific_loss':None,'raw_P_loss_bounds':[0,1],
                'score_cost':score_cost,'score_execution_state':'measured' if scores is not None else 'branch_skipped',
                'effective_fallback':'B' if prep['status']!='PREPARED' else None,
                'scope':'post-freeze diagnostic; no P score or zero measured cost invented for unavailable preparation'}
        body={'schema':'etalon-r4b-post-freeze-diagnostics/1','policy_sha256':policy['policy_sha256'],
            'prediction_sha256':file_identity(prediction_path)['sha256'],'tasks':diagnostics,'promotion':'none'}
        with journal.store.connection(write=True) as db:journal._put(db,'baselinediagnostics-'+digest(run_id),body)
        _write_once(output_dir/'diagnostics-complete.json',body);return body
    try:
        previous=journal.get('onlinepredictions-'+digest(run_id));_write_once(prediction_path,previous);return previous
    except StateError as exc:
        if 'unknown research record' not in str(exc):raise
    predictions={}
    for task in sorted(split.task_ids('test')):
        prep=paid_preparation(journal,policy['preparation_admissions'][task]['p0']['id'])
        prep_cost=preparation_cost_attribution(journal,policy['preparation_admissions'][task]['p0']['id'])
        ids=[c['id'] for c in frozen['tasks'][task]['candidates']];score_cost=None;admissions={}
        scores={c:None for c in ids}
        if prep['status']=='PREPARED':
            aid,a=acquire(task,'r0','online_B');admissions['r0']=aid;score_cost=a['cost']['wall_second']
            scores={s['candidate_id']:s['score'] for s in a['slots']}
        predictions[task]={'decision':'B' if prep['status']=='PREPARED' else 'abstain','candidate_scores':scores,
            'preparation_status':prep['status'],'preparation_admission_id':policy['preparation_admissions'][task]['p0']['id'],
            'preparation_cost':prep_cost['wall_second'],'preparation_cost_attribution':prep_cost,'score_admissions':admissions,'score_cost':score_cost,
            'attributed_path_cost':prep_cost['wall_second']+(score_cost if score_cost is not None else 0),
            'score_execution_state':'measured' if score_cost is not None else 'branch_skipped','query_history':[],
            'labels_accessed':False}
    result={'schema':'etalon-r4b-retained-predictions/1','run_id':run_id,'policy_sha256':policy['policy_sha256'],
        'split_sha256':split.sha256,'plan_id':plan_id,'tasks':predictions,'labels_accessed':False,
        'counterfactual_completion_started':False,'budget_at_freeze':journal.store.balance(),
        'phi_learning_not_established':True,'scope':'retained B; preparation precompiled and attributed, scoring truly online'}
    with journal.store.connection(write=True) as db:journal._put(db,'onlinepredictions-'+digest(run_id),result)
    _write_once(prediction_path,result);return result
