"""Normal-use SND1 revision, layered inputs and continuation via existing APIs.

Run from a frozen example/package snapshot. --prior points to batch5 artifacts;
--root holds this run's PROTOCOL.md, activity.json, sources and new memory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import research_snd1_improvement as h
import snd1_evidence_workflow as science
from snd1_evidence_evaluation import evaluate
from etalon.active.schema import CampaignSpec, Endpoint
from etalon.research.schema import MethodVersion, MethodUse, TaskSplit
from etalon.research import improvement as imp
from etalon.research.composition import WORKFLOW, node_step, record_evaluation
from etalon.research.continuation import STEP, CHECKPOINT
from etalon.research.method_revision import build_method_revision, record_method_revision, resolve_method_revision
from etalon.research.scientific_input import compile_input, consumption_receipt

TASK='snd1-evidence-conditioned-refinement'
QUESTION='Can grounded pocket, contact and activity evidence improve local refinement over fixed-core preparation?'
COND={'target':'SND1','site':'C26-PPI-groove','chemistry':'neutral-C26-near-neighbor','endpoint':'non-anchor-pose-fidelity',
      'protocol':'snd1-evidence/1','activity_use':'MST-template-support-not-affinity-label'}
ROOT=PRIOR=AUTH=None


def configure(root,prior,authority):
    global ROOT,PRIOR,AUTH
    ROOT,PRIOR,AUTH=map(lambda x:Path(x).absolute(),(root,prior,authority))
    h.configure(root=ROOT,etalon_source=ROOT,prism_source=PRIOR/'prism-source',authority=AUTH)
    h.SID='snd1-evidence';h.TASK=TASK;h.QUESTION=QUESTION


def checkpoint(s,basis):
    j=h.journal();previous=[s['checkpoint']] if s.get('checkpoint') else []
    s['checkpoint']=h.save(j,{'schema':CHECKPOINT,'task_id':TASK,'question':QUESTION,'step_refs':s['steps'],
        'claim_refs':s['claims'],'counter_refs':[],'method_refs':[s[k] for k in ['base','candidate','generator']],
        'branches':[{'branch_id':name,'state':state,'basis_refs':basis,'next_read_refs':basis,
          'reconsider_when':'New evidence outside this recorded scope; never replay completed executions'}
          for name,state in [('local-refinement','resolved' if s.get('resolution') else 'waiting'),
                             ('reverse-normal-branch','resolved' if s.get('followup') else 'waiting'),('R4b','stopped')]],
        'pending_handles':[],'source_watermarks':[],'status_guards':[{'location':str(AUTH),**h.pin(AUTH),'state':'paused'}],
        'supersedes':previous})
    h.write(h.CHAIN/'entry.json',{'workspace':str(h.publish(j)),'checkpoint_ref':s['checkpoint']})
    h.write(h.CHAIN/'state.json',s)


def claim(j,status,statement,support,methods=()):
    return h.save(j,{'schema':'etalon-research-claim/1','question':QUESTION,'statement':statement,
        'epistemic_status':status,'subjects':['SND1','C26-A2','C26-A6'],'conditions':COND,
        'limitations':['Retrospective two related compounds; no affinity or independent-transfer validation'],
        'support_refs':support,'counter_refs':[],'method_refs':list(methods),'supersedes':[]})


def initialize():
    if (h.CHAIN/'state.json').exists():return h.read(h.CHAIN/'state.json')
    h.CHAIN.mkdir(parents=True,exist_ok=True)
    store=h.CampaignStore(h.CHAIN/'research.sqlite')
    store.configure(CampaignSpec('structure',600,'wall_second',TASK),[
        Endpoint('structure','diagnostic','unmeasured','count','snd1-evidence/1',0,queryable=False)])
    j=h.journal()
    protocol=h.file(j,ROOT/'PROTOCOL.md','frozen-scientific-protocol')
    layer_refs={};bundles={};summaries={}
    for name,inp in [('first','inputs-first'),('followup','inputs-followup')]:
        path=ROOT/(name+'-input.json')
        bundle=science.build_input(PRIOR/inp,ROOT/'activity.json',ROOT/'sources/paper.xml',
                    ROOT/'sources/supplementary-tables.xls',ROOT/'PROTOCOL.md',task_id=TASK)
        bundle=json.loads(json.dumps(bundle,allow_nan=False))
        compiled=compile_input(bundle,allowed_targets=science.TARGETS)
        if path.exists():
            if h.read(path)!=bundle:raise ValueError('frozen input differs')
        else:h.write(path,bundle)
        bundles[name]=h.file(j,path,'layered-scientific-input')
        summaries[name]=h.art(j,{'schema':'etalon-scientific-input-summary/1','full_input_ref':bundles[name],
            'subject':bundle['subject'],'bindings':compiled['bindings'],
            'parameter_value_hashes':{k:hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest() for k,v in compiled['parameters'].items()},
            'binding_support':compiled['parameters']['binding_support'],
            'pocket_atom_count':len(compiled['parameters']['pocket']['atoms']),
            'fixed_heavy_indices':compiled['parameters']['mobility']['fixed_indices'],
            'reading':'Summary is complete for this declared view; full coordinates remain in the verified full_input_ref.'})
        mapped={}
        for layer in ['sources','observations','interpretations']:
            for row in bundle[layer]:
                if layer=='sources':r=h.file(j,row['path'],'scientific-source')
                else:r=h.art(j,{'schema':'etalon-scientific-'+layer+'/1','record':row,
                      'basis_refs':[mapped[x] for x in row['basis_ids']]},'scientific-'+layer)
                mapped[row['id']]=r
        layer_refs[name]=mapped
    content={}
    for method in ['fixed_core','local_contact']:
        content[method]=h.art(j,{'method':method,'source':h.file(j,Path(science.__file__),'frozen-domain-adapter'),
            'baseline_source':h.file(j,Path(science.old.__file__),'reused-pose-recipe'),
            'protocol_ref':protocol,'input_contract':'etalon-scientific-input/1',
            'procedure':'reuse exact fixed-core output' if method=='fixed_core' else 'reuse prepared pose -> grounded local relaxation'})
    base=h.ref(j,j.record(MethodVersion('snd1-fixed-core-with-grounded-assessment',('domain_skill','usage_method'),{'artifact_ref':content['fixed_core']['record_id']})))
    cand=h.ref(j,j.record(MethodVersion('snd1-local-contact-refinement',('domain_skill','usage_method'),{'artifact_ref':content['local_contact']['record_id']},
        parents=(base['record_id'],),change_summary='Add evidence-grounded local relaxation; keep only phenyl ring fixed')))
    report=claim(j,'extracted_report','Published MST supports direct SND1 binding of C26-A2/A6; atom-level contacts are reported in the C26 groove.',
        [layer_refs['first']['activity-observation'],layer_refs['first']['contact-observation']])
    hypothesis=claim(j,'hypothesis','Grounded contacts and steric exclusion may permit useful local movement outside the phenyl anchor.',
        [layer_refs['first']['local-hypothesis'],report],[cand])
    problem=h.art(j,{'schema':'snd1-refinement-problem/1','basis_refs':[bundles['first'],report,hypothesis],
        'problem':'Previous fixed-core recipe freezes 22 of 23 heavy atoms and does not consume pocket forces; evaluate local refinement on the existing normal preparation.'})
    context=h.save(j,imp.build_revision_context(h.publish(j),task_id=TASK,scope=TASK,conditions=COND,
        target_ref=base,problem_refs=[problem],evidence_refs=[summaries['first'],report,hypothesis]))
    opp=imp.register_opportunity(j,context,h.CHAIN/'memory',source_id=h.SID,source_kind='new_execution')
    if opp['state']=='insufficient_evidence':raise ValueError('revision context incomplete; inspect missing sources before proposing')
    gc=h.art(j,{'method':'host-grounded-constraint-revision','rule':'Use named contacts, direct-binding assay and bounded local freedom; evaluate during ordinary refinement.',
                'learning':'host-authored hypothesis and disposition rule; no learned optimizer claim'})
    generator=h.ref(j,j.record(MethodVersion('evidence-to-local-refinement',('improvement_method',),{'artifact_ref':gc['record_id']})))
    guse=h.use(j,generator,'Ground existing evidence into explicit numerical bindings',opp['request_id'],conditions=COND)
    revision=h.ref(j,record_method_revision(j,build_method_revision(problem_refs=[problem,context],target_kind='U',base_method_ref=base,
        producer_method_ref=generator,producer_use_ref=guse,candidate_version_refs=[cand],change_summary='Append optional local refinement to fixed-core preparation',
        mechanism_hypothesis='Pocket exclusion and contact restraints may improve non-anchor placement without disrupting the known ring',scope=TASK),
        h.CHAIN/'memory',source_id=h.SID,source_kind='new_execution'))
    node={'node_id':'prepare-or-refine','capability_id':None,'method_refs':[base],'views':['X','R'],
          'purpose':'Normal molecular preparation and optional refinement','skip_when':'Exact request already completed, input unsupported or scoped negative experience'}
    oldwf=h.save(j,{'schema':WORKFLOW,'name':TASK,'scope':TASK,'nodes':[node],'edges':[],'method_refs':[base],'supersedes':[]})
    built=imp.build_method_patch(j,context_ref=context,opportunity_ref=opp['opportunity_ref'],base_method_ref=base,candidate_method_ref=cand,
        expected_change='Better non-anchor receptor-frame geometry at bounded added cost',source_id=h.SID,workflow_before_ref=oldwf,
        workflow_edits=[{'collection':'method_refs','before':base,'after':cand},{'collection':'nodes','before':node,'after':{**node,'method_refs':[cand]}}])
    newwf=h.save(j,built['workflow']);patch=h.save(j,{**built['patch'],'workflow_after_ref':newwf})
    split=h.ref(j,j.record(TaskSplit(('QOV-in-QOY-seen',),('unused-independent',),('QOY-in-QOV-seen',),
         {'QOV-in-QOY-seen':'previously seen retrospective case','unused-independent':'not used','QOY-in-QOV-seen':'correlated seen followup'},'No new independent validation')))
    initial=h.art(j,{'conditions':COND,'input_refs':[bundles['first']],'baseline':h.pin(PRIOR/'executions/first-candidate/result.json')})
    plan=h.save(j,{'schema':STEP,'task_id':TASK,'question':QUESTION,'branch_id':'local-refinement','operation':'evaluation_plan',
        'reason':'Evaluate optional refinement during normal preparation','input_refs':[patch,revision],'output_refs':[],'method_refs':[base,cand],
        'accounting_refs':[],'source_watermarks':[],'supersedes':[],
        'detail':{'target':'workflow_composition','subjects':[cand],'comparators':[base],'protocol_ref':protocol,'task_split_ref':split,
          'initial_conditions_ref':initial,'use_refs':[],'trigger_refs':[problem],
          'metrics':[{'name':'non-anchor-RMSD','family':'retention','unit':'angstrom','denominator':'all twelve requested slots; failures retained','missing_policy':'fail'},
             {'name':'structural-regression','family':'regression','unit':'count','denominator':'twelve paired slots','missing_policy':'fail'},
             {'name':'added-process-wall','family':'cost','unit':'second','denominator':'actual refinement batch','missing_policy':'unknown'}]}})
    waiting=h.save(j,imp.assess_revision(j,patch_ref=patch,plan_ref=plan,source_id=h.SID))
    s={'base':base,'candidate':cand,'generator':generator,'inputs':bundles,'input_summaries':summaries,'layers':layer_refs,'claims':[report,hypothesis],
       'context':context,'opportunity':opp,'revision':revision,'oldwf':oldwf,'newwf':newwf,'patch':patch,'plan':plan,
       'waiting':waiting,'steps':[plan],'nodes':[],'costs':[],'conditions':COND}
    checkpoint(s,[patch,waiting,report,hypothesis]);return s


def run_local(name,baseline,bundle):
    j=h.journal();out=ROOT/'executions'/name;out.parent.mkdir(exist_ok=True)
    cmd=[sys.executable,'-B',str(Path(science.__file__)),'--bundle',str(bundle),'--baseline',str(baseline),'--output',str(out)]
    request={'bundle':h.pin(bundle),'baseline':h.pin(baseline/'result.json'),'source':h.pin(science.__file__)}
    sha=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest();eid='snd1-evidence-'+sha
    receipt=out.parent/(name+'.receipt.json')
    reserved=j.store.reserve_resource_execution(eid,sha,180,unit='wall_second',provenance={'task_id':TASK,'request':request})
    if reserved['new_reservation']:
        t=time.perf_counter()
        try:
            with receipt.with_suffix('.stdout.log').open('w',encoding='utf-8') as stdout,receipt.with_suffix('.stderr.log').open('w',encoding='utf-8') as stderr:
                result=subprocess.run(cmd,stdout=stdout,stderr=stderr,env=os.environ.copy(),timeout=180)
            body={'execution_id':eid,'command':cmd,'exit_code':result.returncode,'wall_seconds':time.perf_counter()-t,'request':request}
        except subprocess.TimeoutExpired:
            body={'execution_id':eid,'command':cmd,'exit_code':None,'wall_seconds':time.perf_counter()-t,'request':request,'status':'owned-process-timeout'}
        h.write(receipt,body);j.store.settle_resource_execution(eid,measured_cost=body['wall_seconds'],receipt=body)
    elif not receipt.exists():raise RuntimeError('pending execution; inspect existing handle without restarting')
    body=h.read(receipt)
    if body['exit_code']!=0:raise RuntimeError('retained failed attempt: '+str(receipt))
    actual=science.generate(bundle,baseline,out)  # checks completed identity; no execution
    return out,body


def record_use(s,name,method,output,receipt,input_ref,branch):
    j=h.journal();candidate=method=='local_contact';m=s['candidate' if candidate else 'base']
    result=h.file(j,output/'result.json','actual-molecular-result')
    cost=h.art(j,receipt,'accounting')
    applied=h.ref(j,j.record(MethodUse(({'kind':'task','id':TASK,'scope':str(j.store.path)},),
        ({'method_version_ref':m['record_id'],'scope':TASK},),visible_evidence_refs=(input_ref['record_id'],),
        host_conditions=COND,reason='Normal local refinement with separately linked execution receipt' if candidate else 'Reuse exact completed fixed-core preparation')))
    step=h.save(j,node_step(task_id=TASK,question=QUESTION,branch_id=branch,node_id='prepare-or-refine',
        workflow_ref=s['newwf' if candidate else 'oldwf'],state='completed' if candidate else 'reused',reason='Normal use with exact evidence bindings',
        input_refs=[input_ref],output_refs=[result],method_refs=[m],use_refs=[applied],receipt_refs=[result],accounting_refs=[cost]))
    s['steps'].append(step);s['costs'].append(cost)
    if candidate:s['nodes'].append(step)
    h.write(h.CHAIN/'state.json',s);return step


def compare():
    s=initialize();j=h.journal()
    if s.get('resolution'):return s
    base=PRIOR/'executions/first-candidate';bundle=ROOT/'first-input.json'
    out,receipt=run_local('first-local',base,bundle)
    h.write(ROOT/'first-output-freeze.json',{'baseline':h.pin(base/'result.json'),'candidate':h.pin(out/'result.json'),
             'protocol':h.pin(ROOT/'PROTOCOL.md'),'evaluation_coordinates_not_passed_to_optimizer':True,'structures_previously_seen':True})
    for name,method,path,cost in [('baseline','fixed_core',base,{'kind':'reuse','historical_receipt':h.read(PRIOR/'executions/first-candidate.receipt.json'),
          'new_generation_calls':0,'cost_relation':'historical shared preparation; not charged again'}),('local','local_contact',out,receipt)]:
        if not s.get(name+'_step'):
            s[name+'_step']=record_use(s,name,method,path,cost,s['inputs']['first'],'local-refinement');h.write(h.CHAIN/'state.json',s)
    inp=h.read(PRIOR/'inputs-first/input.json');scores={}
    for name,path in [('baseline',base),('candidate',out)]:
        scores[name]=evaluate(bundle,PRIOR/'inputs-first',path,PRIOR/'data/originals/7KNX.pdb','QOV',inp['target_smiles'])
        h.write(ROOT/(name+'-evaluation.json'),scores[name])
    decision=science.disposition(scores['baseline'],scores['candidate'],receipt['wall_seconds'])
    comparison=h.art(j,{'schema':'snd1-evidence-comparison/1',**scores,**decision,'added_execution_receipt':receipt,
        'claim_scope':'two previously seen related structures; structural refinement only'})
    assessment=h.save(j,imp.assess_revision(j,patch_ref=s['patch'],plan_ref=s['plan'],observed_step_refs=s['nodes'],source_id=h.SID))
    if imp.read_material(h.publish(j),assessment)['content']['status']!='ready_for_evaluation':raise ValueError('normal-use attribution incomplete')
    evaluation=record_evaluation(j,s['plan'],directory=h.CHAIN/'memory',source_id=h.SID,source_kind='new_execution',
        result_refs=[comparison['record_id']],accounting_refs=[r['record_id'] for r in s['costs']],
        conclusion=decision['outcome']+': frozen criteria applied to normal local refinement',scope=TASK)
    resolution=resolve_method_revision(j,s['revision'],evaluation['evaluation_ref'],outcome=decision['outcome'],
        candidate_ref=s['candidate'] if decision['outcome']=='adopt' else None,task_id=TASK,question=QUESTION,scope=TASK,
        reason='Measured pose quality, regression and added cost determine scoped disposition',directory=h.CHAIN/'memory',source_id=h.SID,source_kind='new_execution')
    experience=imp.record_experience(j,patch_ref=s['patch'],outcome=decision['outcome'],
        lesson=f"{decision['outcome']}: non-anchor median RMSD {scores['baseline']['median_non_anchor_rmsd_A']} -> {scores['candidate']['median_non_anchor_rmsd_A']} A; preserve the exact scope and frozen gates.",
        invalid_when='Different pocket, chemistry, receptor preparation, protocol, activity endpoint, or contradictory normal results',
        capability_id='prism.design',support_refs=[comparison,assessment,s['local_step']],counter_refs=[comparison],
        evaluation_ref=evaluation['evaluation_ref'],resolution_ref=resolution['step_ref'],directory=h.CHAIN/'memory',source_id=h.SID,source_kind='new_execution')
    resultclaim=claim(j,'interpretation',decision['outcome']+' local refinement under this frozen structural protocol; no affinity improvement established.',[comparison,assessment],[s['base'],s['candidate']])
    s.update(comparison=comparison,assessment=assessment,evaluation=evaluation,resolution=resolution,experience=experience,decision=decision)
    s['claims'].append(resultclaim);s['steps'].extend([evaluation['step_ref'],resolution['step_ref']]);checkpoint(s,[comparison,experience,resultclaim])
    h.write(ROOT/'comparison.json',{'scores':scores,**decision,'receipt':receipt});return s


def followup():
    s=h.read(h.CHAIN/'state.json');j=h.journal()
    if s.get('followup'):return s
    if not s.get('resolution'):raise ValueError('comparison unresolved')
    bundle=ROOT/'followup-input.json';compiled=compile_input(h.read(bundle),allowed_targets=science.TARGETS)
    path=ROOT/'followup-selection.json'
    if not path.exists():
        retrieved=imp.retrieve_experience(h.publish(j),source_ids=[h.SID],capability_id='prism.design',conditions=COND)
        selected=science.choose_branch(retrieved,compiled['parameters']['binding_support']['eligible'])
        h.write(path,{'schema':'snd1-evidence-branch-selection/1','retrieval':retrieved,'selected':selected,
            'binding_support':compiled['parameters']['binding_support'],'input':h.pin(bundle),'before_execution':True,
            'without_outcome_rule':'eligible for optional local refinement; no presumption of benefit'})
    selection=h.read(path);selected=selection['selected'];selection_ref=h.file(j,path,'normal-branch-selection')
    if not s.get('selection_step'):
        s['selection_step']=h.save(j,{'schema':STEP,'task_id':TASK,'question':QUESTION,'branch_id':'reverse-normal-branch','operation':'decision',
          'reason':'Apply exact scoped normal-use disposition after recomputing input applicability','input_refs':[selection_ref,s['experience'],s['inputs']['followup']],
          'output_refs':[],'method_refs':[s['candidate' if selected=='local_contact' else 'base']],'accounting_refs':[],'source_watermarks':[],'supersedes':[],
          'detail':{'choice':'proceed' if selected=='local_contact' else 'reuse','alternatives':[
              {'branch_id':m,'disposition':'selected' if m==selected else 'skipped','reason':'Measured disposition and input applicability'} for m in ['fixed_core','local_contact']],
             'basis_refs':[selection_ref,s['experience']],'reconsider_when':'New evidence or changed protocol'}})
        s['steps'].append(s['selection_step']);h.write(h.CHAIN/'state.json',s)
    base=PRIOR/'executions/followup-selected'
    if selected=='local_contact':out,receipt=run_local('followup-local',base,bundle)
    else:
        out=base;receipt={'kind':'reuse','new_generation_calls':0,'skipped_local_slots':12,
            'historical_receipt':h.read(PRIOR/'executions/followup-selected.receipt.json'),
            'consumption':consumption_receipt(compiled,consumed_targets=['binding_support'],deferred={k:'not applied by retained fixed-core optimizer; geometry assessed separately' for k in set(science.TARGETS)-{'binding_support'}})}
    if not s.get('followup_step'):
        s['followup_step']=record_use(s,'followup',selected,out,receipt,s['inputs']['followup'],'reverse-normal-branch');h.write(h.CHAIN/'state.json',s)
    inp=h.read(PRIOR/'inputs-followup/input.json')
    score=evaluate(bundle,PRIOR/'inputs-followup',out,PRIOR/'data/originals/7KNW.pdb','QOY',inp['target_smiles'])
    outcome={'schema':'snd1-evidence-followup/1','selection':selection,'evaluation':score,'receipt':receipt,
             'counterfactual':'unselected local branch not executed; no measured counterfactual benefit'}
    h.write(ROOT/'followup.json',outcome);s['followup']=h.art(j,outcome)
    checkpoint(s,[s['comparison'],s['experience'],s['followup'],s['claims'][-1]]);return s


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['init','compare','followup']);p.add_argument('--root',required=True);p.add_argument('--prior',required=True);p.add_argument('--authority',required=True)
    a=p.parse_args();configure(a.root,a.prior,a.authority)
    result={'init':initialize,'compare':compare,'followup':followup}[a.action]()
    print(json.dumps({'action':a.action,'resolved':bool(result.get('resolution')),'decision':result.get('decision'),'followup':bool(result.get('followup'))}))
