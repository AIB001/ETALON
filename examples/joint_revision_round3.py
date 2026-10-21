"""Public staged R3b panel, workflow selection, revision, prediction and feedback driver.

Only development labels are accepted by initialize/freeze. Independent feedback
must be explicitly released after all predictions have been frozen.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical,digest
from etalon.boundary.prism_v1 import CLITransport,file_identity
from etalon.research import Artifact,ResearchJournal
from etalon.research.pose import PoseClient,measurement_methods
from etalon.research.panel import (PanelSpec,freeze_panel,record_cell,summarize_panel,
    choose_development_policy,preparation_contrast,propose_joint_revision,freeze_selection,
    execute_frozen_policies)
from etalon.research.controller import register_controller,next_segment,independent_feedback
from etalon.research.validation import attach_decision_validation

DEVELOPMENT='t4-phenol-catechol-development'
VALIDATION='t4-ortho-substituent-transfer-v1'
ALIASES={'methoxyphenol':'2-methoxyphenol','methylphenol':'2-methylphenol'}
DESCRIBE="""import json,sys
from prism.pose_diagnostic.contract import bind,semantic_request
from prism.pose_diagnostic.service import producer
from prism.diagnostic.structure import bound_design,target_graph,identity
out=[]
for q in json.load(sys.stdin):
 assert bind(q)==q
 g=bound_design(q);mol,_=target_graph(g,q['intervention']);target=identity(g,mol);target.pop('pose_id')
 out.append(dict(semantic=semantic_request(q),target=target))
print(json.dumps(dict(requests=out,producer=producer())))
"""


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:f.write(canonical(value)+'\n')


def evidence(journal,path,*,role='diagnostic',bindings=None):
    path=Path(path).resolve()
    artifact=Artifact(str(path),**file_identity(path),source_kind='existing_real_artifact',
                      role=role,bindings=bindings or {})
    journal.record(artifact);return artifact.id


def initialize(a):
    task=read(a.task_freeze);labels=read(a.development_labels)
    if task['task_id']!=VALIDATION or labels['task_id']!=DEVELOPMENT or labels['role']!='development' or labels['validation_labels_included'] is not False:
        raise ValueError('wrong task freeze or forbidden label scope')
    if file_identity(a.protocol)['sha256']!=task['protocol']['sha256']:
        raise ValueError('coordinator protocol changed')
    if file_identity(a.task_freeze)['sha256']!='458e96bb84f5f88d72055ae515c79654d226380cf7455a4148f1379f108438a2':
        raise ValueError('wrong coordinator task freeze')
    if not a.database.exists():
        store=CampaignStore(a.database);store.configure(CampaignSpec('unmeasured',1200,'wall_second','research-pose-evidence/1'),
            [Endpoint('unmeasured',DEVELOPMENT,'not_measured','count','research-pose-evidence/1',0,queryable=False,requires_handoff=False)])
    else:store=CampaignStore(a.database)
    if store.configuration()[0].budget!=1200:raise ValueError('use the single authorized 1200 scientific campaign')
    journal=ResearchJournal(store)
    refs=[evidence(journal,p) for p in (a.protocol,a.amendment,a.task_freeze,a.development_labels)]
    client=PoseClient(CLITransport(a.python,str(a.producer_source.resolve()),str(a.request_root.resolve()),timeout=120))
    client.capabilities()
    panels={};schedule=[];last=None
    for role,path in [('development',a.development_panel),('validation',a.validation_panel)]:
        source=read(path);records=source['records'];requests=[read(r['request']) for r in records]
        if len(records)!=8:raise ValueError('two candidates x two mappings x two preparations required')
        description=client.transport._run([client.transport.python,'-c',DESCRIBE],canonical(requests).encode())
        candidates={};preparations={};methods=None;receptor=None;frame=None;bound_records=[]
        for r,q,desc in zip(records,requests,description['requests']):
            if file_identity(r['request'])['sha256']!=r['request_sha256']:raise ValueError('request file changed')
            name=ALIASES.get(r['candidate'],r['candidate']);p=r['preparation']
            if q['parameters']['num_poses']!=4 or q['parameters']['base_conformers']!=(4 if p=='P0' else 1):
                raise ValueError('AMENDMENT_01 seed allocation mismatch')
            if len(q['pose_constraints']['transforms'])!=(1 if p=='P0' else 4):
                raise ValueError('AMENDMENT_01 transform count mismatch')
            target=desc['target'];current_methods=measurement_methods(q,description['producer']['modules'])
            if methods is not None and methods!=current_methods:raise ValueError('evaluators differ within panel')
            methods=current_methods
            if receptor is not None and (receptor,frame)!=(target['receptor_state_id'],target['coordinate_frame_id']):
                raise ValueError('panel receptor/frame mismatch')
            receptor,frame=target['receptor_state_id'],target['coordinate_frame_id']
            candidate={'smiles':r['expected_smiles'],'chemical_state_id':target['chemical_state_id']}
            if name in candidates and candidates[name]!=candidate:raise ValueError('mapping changed chemical candidate')
            candidates[name]=candidate
            prep={k:q[k] for k in ('parameters','pose_constraints','seed')}
            if p in preparations and preparations[p]!=prep:raise ValueError('preparation parameters differ across mappings')
            preparations[p]=prep
            request_ref=evidence(journal,r['request']);refs.append(request_ref)
            sha=digest(desc['semantic']);segment=q['action_id']
            schedule.append({'id':segment,'stage':role,'request_sha256':sha,'request':r['request'],
                             'quote_wall_second':q['budget']['wall_seconds'],'depends_on':[last] if last else []})
            last=segment
            bound_records.append({**r,'candidate':name,'request_semantic_sha256':sha,'artifact_id':request_ref,
                                  'segment_id':segment,'target_identity':target})
        if role=='validation' and set(candidates)!={r['id'] for r in task['candidates']}:
            raise ValueError('validation candidate set differs from frozen task')
        evaluations={e:{'kind':'polar_pair_count' if e=='E0' else 'docking_score',
                        'unit':'heavy_atom_pair_count' if e=='E0' else 'kcal/mol',
                        'direction':'max' if e=='E0' else 'min','method_id':methods[e],
                        'source_identity':digest(description['producer']['modules'])} for e in methods}
        spec=PanelSpec(DEVELOPMENT if role=='development' else VALIDATION,role,candidates,preparations,
                       evaluations,8,receptor,frame,{'primary':'experimental pair order loss',
                       'correct':0,'wrong':1,'tie_or_abstain':.5,'all_failed':1,'tie_tolerance':1e-6,
                       'protocol_sha256':task['protocol']['sha256'],
                       'amendment_sha256':file_identity(a.amendment)['sha256']})
        freeze_panel(journal,spec)
        panels[role]={'panel_id':spec.id,'records':bound_records,'producer':description['producer'],
                      'source_panel':str(path.resolve()),'source_panel_identity':file_identity(path)}
    controller=register_controller(journal,name='phi1-hand-authored-diagnostic-controller',schedule=schedule,
                                   source_evidence=tuple(refs),mode='audit_then_compare')
    static=register_controller(journal,name='phi0-static-schedule-control',schedule=schedule,
                               source_evidence=tuple(refs),mode='static_schedule')
    result={'schema':'etalon-r3b-initialization/1','panels':panels,'controller_id':controller,
            'static_controller_id':static,'static_controller_execution':'not_separately_run',
            'phi_learned':False,'evidence':refs,'development_labels':str(a.development_labels.resolve()),
            'protocol':str(a.protocol.resolve()),'amendment':str(a.amendment.resolve()),
            'task_freeze':str(a.task_freeze.resolve()),'budget':store.balance()}
    write(a.output,result);return result


def assemble(a):
    init=read(a.initialization);journal=ResearchJournal(CampaignStore(a.database));panel=init['panels'][a.role]
    admissions={}
    for path in a.admission:
        receipt=read(path)
        if receipt['request_sha256'] in admissions:raise ValueError('duplicate admission input')
        if digest(read(receipt['result_path']))!=receipt['result_sha256']:raise ValueError('producer result changed')
        admissions[receipt['request_sha256']]=(receipt,path)
    spec=PanelSpec(**journal.get(panel['panel_id']))
    for candidate in spec.candidates:
        for preparation in ('P0','P1'):
            records=sorted([r for r in panel['records'] if r['candidate']==candidate and r['preparation']==preparation],key=lambda r:r['mapping'])
            if any(r['request_semantic_sha256'] not in admissions for r in records):continue
            for evaluation in ('E0','E1'):
                slots=[];executions=[]
                for record in records:
                    receipt,_=admissions[record['request_semantic_sha256']]
                    if receipt['task_id']!=spec.task_id:raise ValueError('admission task mismatch')
                    executions.append(receipt['execution_id'])
                    for s in receipt['slots']:
                        slots.append({'slot':len(slots)+1,**s['observations'][evaluation],
                                      'pose_id':s['pose_id'],'mapping_id':s['mapping_id'],
                                      'source_slot':s['slot'],'mapping_condition':record['mapping'],
                                      'raw':s['raw']})
                record_cell(journal,spec.id,candidate=candidate,preparation=preparation,evaluation=evaluation,
                            slots=slots,execution_ids=tuple(executions))
    result=summarize_panel(journal,spec.id)
    result['admission_files']=[{'path':str(p.resolve()),**file_identity(p)} for p in a.admission]
    write(a.output,result);return result


def decision_status(row):
    if row['status']=='failed_all_candidates':return 'execution_failed'
    if row['status']=='abstain_missing_cells':return 'missing_cell'
    if row['top_tie']:return 'tie'
    return 'selected' if row['selected_candidate'] is not None else 'abstain'


def freeze(a):
    init=read(a.initialization);journal=ResearchJournal(CampaignStore(a.database))
    panel_id=init['panels']['development']['panel_id'];panel=summarize_panel(journal,panel_id)
    summary=read(a.development_summary)
    if summary['panel_id']!=panel_id or summary['policies']!=panel['policies']:
        raise ValueError('development summary differs from admitted panel')
    with journal.store.connection() as db:journal._artifacts(db,init['evidence'],diagnostic_inputs=True)
    labels=read(init['development_labels'])
    if labels['task_id']!=panel['task_id'] or labels['validation_labels_included'] is not False:raise ValueError('development label scope mismatch')
    losses={}
    for key,row in panel['policies'].items():
        status=decision_status(row)
        losses[key]=1. if status in ('execution_failed','missing_cell') else .5 if status in ('tie','abstain') else float(row['selected_candidate']!=labels['reported_stronger_candidate'])
    refs=(evidence(journal,init['development_labels']),evidence(journal,a.development_summary))
    selection=choose_development_policy(journal,panel_id,losses=losses,evidence=refs,
        loss_definition={'kind':'experimental pair order','correct':0,'wrong':1,'tie_or_abstain':.5,'all_failed':1,
                         'source':'coordinator released development table; not calibrated uncertainty'})
    spec=PanelSpec(**journal.get(panel_id))
    contrasts=tuple(preparation_contrast(journal,panel_id,candidate_order=('phenol','catechol'),evaluation=e) for e in ('E0','E1'))
    before={'design':{'graph_admission':'exact mapped graph/edit/charge retained','pose_admission':'source_core_exact'},
            'preparation':{'policy':spec.preparations['P0']},'evaluation':{'policy':spec.evaluations['E0']},
            'budget':{'attempted_slots_per_candidate':8}}
    revision=propose_joint_revision(journal,task_id=DEVELOPMENT,configuration=before,
        updated_fields={'design':{'pose_admission':'declared proper transform, bounds, anchors and native QC'},
                        'preparation':{'policy':spec.preparations['P1']},
                        'evaluation':{'policy':spec.evaluations['E1']}},
        evidence=refs+tuple(init['evidence']),contrast_ids=contrasts)
    frozen=freeze_selection(journal,development_panel_id=panel_id,selection=selection,
        revision_receipt=revision,validation_panel_id=init['panels']['validation']['panel_id'],
        validation_request_sha256s=tuple(r['request_semantic_sha256'] for r in init['panels']['validation']['records']))
    assessment={'schema':'etalon-development-diagnostic-assessment/1','panel_id':panel_id,
        'evidence':list(refs),'proxy_scorer_conflicts':{p:panel['policies'][p+'/E0']['selected_candidate']!=panel['policies'][p+'/E1']['selected_candidate'] for p in ('P0','P1')},
        'experimental_order_losses':losses,'hypotheses':['preparation_seed_or_pose_policy','evaluator_proxy_mismatch',
            'receptor_or_solvent_omission','chemical_state_error','outside_model'],
        'mechanism_posterior':None,'expected_gain':None,
        'current_M':'finite observed workflow losses only; one development pair; no causal likelihood',
        'next_action':'test proposed nonempty joint revision on frozen new task',
        'revision_id':revision['revision_id'],'decision_sha256':frozen['freeze_sha256']}
    with journal.store.connection(write=True) as db:journal._put(db,'diagnosticassessment-'+digest(assessment),assessment)
    result={'decision':frozen,'contrasts':{key:journal.factorial(key) for key in contrasts},
            'controller_id':init['controller_id'],'next':next_segment(journal,init['controller_id'],decision_sha256=frozen['freeze_sha256']),
            'workflow_selection_is_not_phi_learning':True,'diagnostic_assessment':assessment}
    write(a.output,result);return result


def predictions(a):
    init=read(a.initialization);journal=ResearchJournal(CampaignStore(a.database));frozen=read(a.decision)['decision']
    if journal.get('decision-'+frozen['freeze_sha256'])!=frozen:raise ValueError('decision file differs from frozen journal')
    report=execute_frozen_policies(journal,frozen['freeze_sha256'])
    panel=summarize_panel(journal,init['panels']['validation']['panel_id'])
    evidence_paths=[a.decision,a.validation_summary,a.initialization]
    for entry in read(a.validation_summary)['admission_files']:
        path=Path(entry['path']);evidence_paths.append(path)
        receipt=read(path);evidence_paths.append(Path(receipt['result_path']))
        for s in receipt['slots']:
            if s['raw']:evidence_paths.append(Path(receipt['result_path']).parent/s['raw']['path'])
    summary={'schema':'etalon-r3b-predictions/1','task_id':VALIDATION,
             'candidate_ids':list(journal.get(panel['panel_id'])['candidates']),
             'policies':{p:{**row,'decision_status':decision_status(row)} for p,row in panel['policies'].items()},
             'frozen_selected_policy':frozen['selection']['selected_workflow'],
             'decision_sha256':frozen['freeze_sha256'],'phi_learned':False,
             'evidence':[{'path':str(p.resolve()),**file_identity(p)} for p in dict.fromkeys(evidence_paths)],
             'policy_replay':report,'labels_accessed':False,'budget':journal.store.balance()}
    write(a.output,summary);return summary


def feedback(a):
    init=read(a.initialization);journal=ResearchJournal(CampaignStore(a.database));pred=read(a.predictions)
    result=read(a.validation_result)
    if result['predictions_sha256']!=file_identity(a.predictions)['sha256'] or result['task_id']!=VALIDATION:
        raise ValueError('independent result does not bind frozen predictions')
    sha=pred['decision_sha256'];binding={'decision_sha256':sha}
    ref=evidence(journal,a.validation_result,role='independent_validation',bindings=binding)
    release=evidence(journal,a.release,role='independent_validation',bindings=binding)
    validation=attach_decision_validation(journal,decision_sha256=sha,evidence=(ref,),release_evidence=release,
        protocol={'script_sha256':result['script_sha256'],'predictions_sha256':result['predictions_sha256'],
                  'endpoint':'experimental pair order'},comparison=result)
    transition=independent_feedback(journal,controller_id=init['controller_id'],decision_sha256=sha,validation_id=validation['id'])
    output={'validation':validation,'controller_transition':transition,'budget':journal.store.balance()}
    write(a.output,output);return output


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for name,extra in {
        'initialize':['producer-source','request-root','development-panel','validation-panel','protocol','amendment','task-freeze','development-labels'],
        'assemble':['initialization'],'freeze':['initialization','development-summary'],
        'predictions':['initialization','decision','validation-summary'],
        'feedback':['initialization','predictions','validation-result','release']}.items():
        q=sub.add_parser(name)
        for key in ['database','output',*extra]:q.add_argument('--'+key,type=Path,required=True)
        if name=='initialize':q.add_argument('--python',default=sys.executable)
        if name=='assemble':
            q.add_argument('--role',choices=('development','validation'),required=True)
            q.add_argument('--admission',type=Path,action='append',required=True)
    a=p.parse_args();result=globals()[a.command](a)
    print(json.dumps({'output':str(a.output),'command':a.command,'budget':result.get('budget')}))


if __name__=='__main__':main()
