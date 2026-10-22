"""Normal-use RSI for a specified-mobility candidate using existing interfaces."""
import argparse,hashlib,json,os,subprocess,sys,time
from pathlib import Path
import research_snd1_improvement as h
import snd1_torsion_workflow as science
import snd1_evidence_workflow as legacy
from snd1_evidence_evaluation import evaluate
from etalon.active.schema import CampaignSpec,Endpoint
from etalon.research.schema import MethodVersion,MethodUse,TaskSplit
from etalon.research import improvement as imp
from etalon.research.continuation import STEP,CHECKPOINT,resume_research
from etalon.research.composition import WORKFLOW,node_step,record_evaluation
from etalon.research.method_revision import build_method_revision,record_method_revision,resolve_method_revision
from etalon.research.scientific_input import compile_input,consumption_receipt

TASK='snd1-specified-torsion-normal-refinement'
QUESTION='Can specified torsions and chemically scoped directional proxies improve SND1 pose fidelity over fixed-core preparation?'
COND={'target':'SND1','chemistry':'neutral-C26-near-neighbor','endpoint':'non-phenyl-anchor-pose-fidelity',
    'protocol':'snd1-torsion/1','receptor_model':'single-chain-constructed-planar-donor-H','mobility':'four-bounded-rotations'}
ROOT=B5=B6=DIAG=AUTH=None


def configure(root):
    global ROOT,B5,B6,DIAG,AUTH
    ROOT=Path(root).absolute();B5=ROOT.parent/'batch5';B6=ROOT.parent/'batch6';DIAG=ROOT.parent/'batch6-diagnosis'
    AUTH=ROOT.parents[2]/'integration/round4b/PAUSED.md'
    h.configure(root=ROOT,etalon_source=ROOT/'package/installed',prism_source=B5/'prism-source',authority=AUTH)
    h.SID='snd1-torsion';h.TASK=TASK;h.QUESTION=QUESTION


def publish(j):
    prior=h.read(DIAG/'chain-final/etalon-workspace.json')
    prior.update(workspace_id=TASK,sources=[{'source_id':h.SID,'kind':'journal','location':str(j.store.path),
        'identity':{'kind':'snapshot_sha256','value':h.pin(j.store.path)['sha256']}},*prior['sources']])
    path=h.CHAIN/'etalon-workspace.json';h.write(path,prior);return path


def checkpoint(s,basis):
    j=h.journal();previous=[s['checkpoint']] if s.get('checkpoint') else []
    s['checkpoint']=h.save(j,{'schema':CHECKPOINT,'task_id':TASK,'question':QUESTION,'step_refs':s['steps'],
        'claim_refs':s['claims'],'counter_refs':[],'method_refs':[s[k] for k in ['base','candidate','control','generator']],
        'branches':[{'branch_id':name,'state':state,'basis_refs':basis,'next_read_refs':basis,
            'reconsider_when':'New applicable evidence or explicitly changed method; do not replay completed tasks'} for name,state in
            [('torsion-comparison','resolved' if s.get('resolution') else 'waiting'),
             ('reverse-normal-branch','resolved' if s.get('followup') else 'waiting'),('R4b','stopped')]],
        'pending_handles':[],'source_watermarks':[],'status_guards':[{'location':str(AUTH),**h.pin(AUTH),'state':'paused'}],'supersedes':previous})
    h.write(h.CHAIN/'state.json',s);h.write(h.CHAIN/'entry.json',{'workspace':str(publish(j)),'checkpoint_ref':s['checkpoint']})


def claim(j,statement,status,support,methods=()):
    return h.save(j,{'schema':'etalon-research-claim/1','question':QUESTION,'statement':statement,'epistemic_status':status,
        'subjects':['SND1','QOV','QOY'],'conditions':COND,'limitations':['Previously seen structures; correlated slots','Constructed donor H is an assumption','No affinity or independent-transfer evidence'],
        'support_refs':support,'counter_refs':[],'method_refs':list(methods),'supersedes':[]})


def initialize():
    if (h.CHAIN/'state.json').exists():return h.read(h.CHAIN/'state.json')
    entry=h.read(DIAG/'chain-final/entry.json');prior=resume_research(entry['workspace'],entry['checkpoint_ref'])
    if prior['issues'] or prior['pending']:raise ValueError('prior diagnosis not safely readable')
    h.CHAIN.mkdir(exist_ok=True)
    store=h.CampaignStore(h.CHAIN/'research.sqlite');store.configure(CampaignSpec('structure',600,'wall_second',TASK),[
        Endpoint('structure','diagnostic','unmeasured','count','snd1-torsion/1',0,queryable=False)])
    j=h.journal();protocol=h.file(j,ROOT/'PROTOCOL.md','frozen-scientific-protocol')
    diagnosis=h.file(j,DIAG/'conclusion.json','prior-diagnostic-conclusion')
    prior_ref=h.read(DIAG/'chain-final/state.json')['claims'][0]
    bundles={};summaries={}
    for name,base in [('first',B5/'executions/first-candidate'),('followup',B5/'executions/followup-selected')]:
        path=ROOT/(name+'-input.json')
        bundle=science.build_bundle(B6/(name+'-input.json'),base,ROOT/'PROTOCOL.md',task_id=TASK)
        if path.exists():
            if h.read(path)!=bundle:raise ValueError('frozen input changed')
        else:h.write(path,bundle)
        bundles[name]=h.file(j,path,'layered-scientific-input')
        p=compile_input(bundle,allowed_targets=science.TARGETS)['parameters'];g=p['geometry']
        summaries[name]=h.art(j,{'schema':'snd1-torsion-input-summary/1','input_ref':bundles[name],'chemical_state':g['chemical_state'],
            'hard':g['hard'],'dofs':g['dofs'],'directions':g['directions'],'excluded_directions':g['direction_exclusions'],
            'binding_support':p['binding_support'],'role':'explicit conditioning assumptions, not experimental hydrogen coordinates'})
    methods={}
    for key,name in [('base','fixed_core'),('candidate','torsion_directional'),('control','torsion_distance')]:
        content=h.art(j,{'name':name,'source_ref':h.file(j,Path(science.__file__),'frozen-method-source'),
            'contract_ref':h.file(j,ROOT/'package/installed/etalon/research/geometry_input.py','domain-contract'),
            'protocol_ref':protocol,'procedure':'Reuse exact completed fixed-core output' if key=='base' else 'Bounded named graph rotations; rigid internal geometry; explicit soft/directional terms'})
        methods[key]=h.ref(j,j.record(MethodVersion('snd1-'+name,('domain_skill','usage_method'),{'artifact_ref':content['record_id']},
            parents=(methods['base']['record_id'],) if key!='base' else (),change_summary='Specify allowed mobility and directional chemical assumptions' if key!='base' else 'Retained baseline')))
    hypothesis=claim(j,'Preserving bond/ring geometry while allowing only named rotations and scoped interaction directions may improve receptor-frame pose fidelity.','hypothesis',
        [diagnosis,summaries['first'],prior_ref],[methods['candidate']])
    problem=h.art(j,{'schema':'snd1-torsion-problem/1','diagnosis_ref':diagnosis,'prior_claim_ref':prior_ref,
        'problem':'Earlier Cartesian relaxation lowered ligand strain but displaced the bound scaffold; longer iterations did not fix it.'})
    context=h.save(j,imp.build_revision_context(publish(j),task_id=TASK,scope=TASK,conditions=COND,target_ref=methods['base'],
        problem_refs=[problem],evidence_refs=[diagnosis,summaries['first'],hypothesis]))
    opp=imp.register_opportunity(j,context,h.CHAIN/'memory',source_id=h.SID,source_kind='new_execution')
    if opp['state']!='new':raise ValueError('expected one complete new improvement context: '+str(opp))
    gc=h.art(j,{'rule':'Use completed normal-use diagnosis to restrict degrees of freedom and make chemical assumptions explicit; evaluate during the next normal refinement.',
        'not_claimed':'No learned optimizer or mandatory probe stage'})
    generator=h.ref(j,j.record(MethodVersion('diagnosis-to-specified-mobility',('improvement_method',),{'artifact_ref':gc['record_id']})))
    guse=h.use(j,generator,'Host-authored scoped candidate from existing evidence',opp['request_id'],conditions=COND)
    revision=h.ref(j,record_method_revision(j,build_method_revision(problem_refs=[problem,context],target_kind='U',base_method_ref=methods['base'],
        producer_method_ref=generator,producer_use_ref=guse,candidate_version_refs=[methods['candidate']],
        change_summary='Optional specified-torsion refinement instead of retaining all mapped core positions',
        mechanism_hypothesis='Remove Cartesian distortion and make directional constraints discriminate allowed motion',scope=TASK),
        h.CHAIN/'memory',source_id=h.SID,source_kind='new_execution'))
    node={'node_id':'prepare-or-refine','capability_id':None,'method_refs':[methods['base']],'views':['X','R'],
        'purpose':'Normal pose preparation and optional refinement','skip_when':'Completed exact request, unsupported chemical state or scoped retained-baseline experience'}
    oldwf=h.save(j,{'schema':WORKFLOW,'name':TASK,'scope':TASK,'nodes':[node],'edges':[],'method_refs':[methods['base']],'supersedes':[]})
    patch=imp.build_method_patch(j,context_ref=context,opportunity_ref=opp['opportunity_ref'],base_method_ref=methods['base'],
        candidate_method_ref=methods['candidate'],expected_change='Improved non-anchor pose fidelity with preserved internal geometry',source_id=h.SID,
        workflow_before_ref=oldwf,workflow_edits=[{'collection':'method_refs','before':methods['base'],'after':methods['candidate']},
            {'collection':'nodes','before':node,'after':{**node,'method_refs':[methods['candidate']]}}])
    newwf=h.save(j,patch['workflow']);patchref=h.save(j,{**patch['patch'],'workflow_after_ref':newwf})
    split=h.ref(j,j.record(TaskSplit(('QOV-in-QOY-seen',),('independent-not-used',),('QOY-in-QOV-seen',),
        {'QOV-in-QOY-seen':'previously seen','independent-not-used':'no independent validation performed','QOY-in-QOV-seen':'previously seen correlated followup'},'No blind or independent-transfer claim')))
    initial=h.art(j,{'conditions':COND,'input_ref':bundles['first'],'baseline':h.pin(B5/'executions/first-candidate/result.json')})
    plan=h.save(j,{'schema':STEP,'task_id':TASK,'question':QUESTION,'branch_id':'torsion-comparison','operation':'evaluation_plan',
        'reason':'Evaluate the predeclared candidate during normal optional refinement','input_refs':[patchref,revision],'output_refs':[],
        'method_refs':[methods['base'],methods['candidate']],'accounting_refs':[],'source_watermarks':[],'supersedes':[],
        'detail':{'target':'workflow_composition','subjects':[methods['candidate']],'comparators':[methods['base']],
            'protocol_ref':protocol,'task_split_ref':split,'initial_conditions_ref':initial,'use_refs':[],'trigger_refs':[problem],
            'metrics':[{'name':'non-anchor-RMSD','family':'retention','unit':'angstrom','denominator':'all twelve requested correlated slots','missing_policy':'fail'},
                {'name':'geometry-regression','family':'regression','unit':'count','denominator':'all paired slots','missing_policy':'fail'},
                {'name':'added-cost','family':'cost','unit':'second','denominator':'candidate process','missing_policy':'unknown'}]}})
    waiting=h.save(j,imp.assess_revision(j,patch_ref=patchref,plan_ref=plan,source_id=h.SID))
    s={**methods,'generator':generator,'context':context,'opportunity':opp,'revision':revision,'oldwf':oldwf,'newwf':newwf,
        'patch':patchref,'plan':plan,'waiting':waiting,'inputs':bundles,'input_summaries':summaries,'claims':[hypothesis],
        'steps':[plan],'nodes':[],'costs':[],'conditions':COND,'prior_checkpoint':entry['checkpoint_ref']}
    checkpoint(s,[hypothesis,waiting,diagnosis]);return s


def run_method(name,baseline,bundle,method):
    out=ROOT/'executions'/name;out.parent.mkdir(exist_ok=True);receipt=out.parent/(name+'.receipt.json')
    request={'bundle':h.pin(bundle),'baseline':h.pin(Path(baseline)/'result.json'),'source':h.pin(science.__file__),'method':method}
    sha=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest();eid='snd1-torsion-'+sha;j=h.journal()
    reserved=j.store.reserve_resource_execution(eid,sha,180,unit='wall_second',provenance={'task_id':TASK,'request':request})
    if reserved['new_reservation']:
        cmd=[sys.executable,'-B',str(Path(science.__file__)),'--bundle',str(bundle),'--baseline',str(baseline),'--output',str(out),'--method',method]
        t=time.perf_counter()
        with receipt.with_suffix('.stdout.log').open('w',encoding='utf-8') as stdout,receipt.with_suffix('.stderr.log').open('w',encoding='utf-8') as stderr:
            try:r=subprocess.run(cmd,stdout=stdout,stderr=stderr,env=os.environ.copy(),timeout=180);code=r.returncode
            except subprocess.TimeoutExpired:code=None
        body={'execution_id':eid,'command':cmd,'exit_code':code,'wall_seconds':time.perf_counter()-t,'request':request}
        h.write(receipt,body);j.store.settle_resource_execution(eid,measured_cost=body['wall_seconds'],receipt=body)
    elif not receipt.exists():raise RuntimeError('pending execution; inspect retained handle, never auto-retry')
    body=h.read(receipt)
    if body['exit_code']!=0:raise RuntimeError('retained failed execution: '+str(receipt))
    science.generate(bundle,baseline,out,method) # Identity-checked reuse only.
    return out,body


def record_use(s,key,method,path,cost,inputref,branch):
    j=h.journal();result=h.file(j,Path(path)/'result.json','actual-molecular-result');accounting=h.art(j,cost,'accounting')
    use=h.ref(j,j.record(MethodUse(({'kind':'task','id':TASK,'scope':str(j.store.path)},),
        ({'method_version_ref':s[method]['record_id'],'scope':TASK},),visible_evidence_refs=(inputref['record_id'],),
        host_conditions=COND,reason='Normal use with exact raw outputs and separate actual receipt')))
    wf=s['newwf'] if method=='candidate' else s['oldwf'] if method=='base' else None
    step=h.save(j,node_step(task_id=TASK,question=QUESTION,branch_id=branch,node_id='prepare-or-refine',workflow_ref=wf,
        state='reused' if method=='base' else 'completed',reason='Exact method/input attribution',input_refs=[inputref],output_refs=[result],
        method_refs=[s[method]],use_refs=[use],receipt_refs=[result],accounting_refs=[accounting]))
    s[key]=step;s['steps'].append(step);s['costs'].append(accounting)
    if method=='candidate' and branch=='torsion-comparison':s['nodes'].append(step)
    h.write(h.CHAIN/'state.json',s);return step


def compare():
    s=initialize()
    if s.get('resolution'):return s
    j=h.journal();baseline=B5/'executions/first-candidate';bundle=ROOT/'first-input.json'
    candidate,receipt=run_method('candidate',baseline,bundle,'torsion_directional')
    control,controlreceipt=run_method('direction-ablation',baseline,bundle,'torsion_distance')
    h.write(ROOT/'output-freeze.json',{'candidate':h.pin(candidate/'result.json'),'control':h.pin(control/'result.json'),
        'baseline':h.pin(baseline/'result.json'),'protocol':h.pin(ROOT/'PROTOCOL.md'),'reference_not_passed_to_generator':True})
    for name,method,path,cost in [('baseline','base',baseline,{'kind':'reuse','new_generation_calls':0,
        'historical_receipt':h.read(B5/'executions/first-candidate.receipt.json')}),('candidate','candidate',candidate,receipt),('ablation','control',control,controlreceipt)]:
        if not s.get(name+'_step'):record_use(s,name+'_step',method,path,cost,s['inputs']['first'],'torsion-comparison')
    inp=h.read(B5/'inputs-first/input.json');scores={}
    for name,path in [('baseline',baseline),('candidate',candidate),('direction_ablation',control)]:
        scores[name]=evaluate(B6/'first-input.json',B5/'inputs-first',path,B5/'data/originals/7KNX.pdb','QOV',inp['target_smiles'])
        h.write(ROOT/(name+'-evaluation.json'),scores[name])
    decision=legacy.disposition(scores['baseline'],scores['candidate'],receipt['wall_seconds'])
    actual=h.read(candidate/'result.json');decision['gates']['hard_geometry_preserved']=all(r.get('hard_invariants',{}).get('valid') for r in actual['rows'])
    decision['outcome']='adopt' if all(decision['gates'].values()) else 'retain'
    report={'schema':'snd1-torsion-comparison/1',**scores,**decision,'candidate_receipt':receipt,'control_receipt':controlreceipt,
        'candidate_predeclared':True,'control_role':'direction ablation; not eligible for posthoc selection'}
    h.write(ROOT/'comparison.json',report);comparison=h.art(j,report)
    assessment=h.save(j,imp.assess_revision(j,patch_ref=s['patch'],plan_ref=s['plan'],observed_step_refs=s['nodes'],source_id=h.SID))
    if imp.read_material(publish(j),assessment)['content']['status']!='ready_for_evaluation':raise ValueError('attribution incomplete')
    evaluation=record_evaluation(j,s['plan'],directory=h.CHAIN/'memory',source_id=h.SID,source_kind='new_execution',
        result_refs=[comparison['record_id']],accounting_refs=[r['record_id'] for r in s['costs']],conclusion=decision['outcome']+': unchanged pose gates and hard geometry checks',scope=TASK)
    resolution=resolve_method_revision(j,s['revision'],evaluation['evaluation_ref'],outcome=decision['outcome'],
        candidate_ref=s['candidate'] if decision['outcome']=='adopt' else None,task_id=TASK,question=QUESTION,scope=TASK,
        reason='Predeclared normal-use geometry benefit, regressions and cost',directory=h.CHAIN/'memory',source_id=h.SID,source_kind='new_execution')
    lesson=f"{decision['outcome']}: specified torsions plus directional proxies changed non-anchor median RMSD from {scores['baseline']['median_non_anchor_rmsd_A']:.6f} to {scores['candidate']['median_non_anchor_rmsd_A']:.6f} A; direction ablation {scores['direction_ablation']['median_non_anchor_rmsd_A']:.6f} A. Do not reuse the old Cartesian-failure lesson as proof this candidate works."
    experience=imp.record_experience(j,patch_ref=s['patch'],outcome=decision['outcome'],lesson=lesson,
        invalid_when='Different chemical state, geometry contract, directional model, receptor, site or contradictory normal results',capability_id='prism.design',
        support_refs=[comparison,assessment,s['candidate_step']],counter_refs=[comparison],evaluation_ref=evaluation['evaluation_ref'],resolution_ref=resolution['step_ref'],
        directory=h.CHAIN/'memory',source_id=h.SID,source_kind='new_execution')
    resultclaim=claim(j,lesson,'interpretation',[comparison,assessment],[s['base'],s['candidate'],s['control']])
    s.update(comparison=comparison,assessment=assessment,evaluation=evaluation,resolution=resolution,experience=experience,decision=decision)
    s['claims'].append(resultclaim);s['steps'] += [evaluation['step_ref'],resolution['step_ref']]
    checkpoint(s,[comparison,experience,resultclaim]);return s


def followup():
    s=h.read(h.CHAIN/'state.json')
    if s.get('followup'):return s
    if not s.get('resolution'):raise ValueError('comparison must be resolved')
    j=h.journal();bundle=ROOT/'followup-input.json';compiled=compile_input(h.read(bundle),allowed_targets=science.TARGETS)
    selectionpath=ROOT/'followup-selection.json'
    if not selectionpath.exists():
        retrieved=imp.retrieve_experience(publish(j),source_ids=[h.SID],capability_id='prism.design',conditions=COND)
        selected=legacy.choose_branch(retrieved,compiled['parameters']['binding_support']['eligible'])
        selected='torsion_directional' if selected=='local_contact' else 'fixed_core'
        h.write(selectionpath,{'schema':'snd1-torsion-branch-selection/1','retrieval':retrieved,'selected':selected,'input':h.pin(bundle),'before_execution':True})
    selection=h.read(selectionpath);selected=selection['selected'];baseline=B5/'executions/followup-selected'
    if selected=='torsion_directional':out,cost=run_method('followup',baseline,bundle,selected);method='candidate'
    else:
        out=baseline;method='base';cost={'kind':'reuse','new_generation_calls':0,'skipped_optional_slots':12,
            'historical_receipt':h.read(B5/'executions/followup-selected.receipt.json'),
            'consumption':consumption_receipt(compiled,consumed_targets=['binding_support'],deferred={k:'Retained baseline: no optional refinement applied' for k in science.TARGETS if k!='binding_support'})}
    if not s.get('followup_step'):record_use(s,'followup_step',method,out,cost,s['inputs']['followup'],'reverse-normal-branch')
    inp=h.read(B5/'inputs-followup/input.json');score=evaluate(B6/'followup-input.json',B5/'inputs-followup',out,B5/'data/originals/7KNW.pdb','QOY',inp['target_smiles'])
    result={'schema':'snd1-torsion-followup/1','selection':selection,'evaluation':score,'receipt':cost,'counterfactual':'Unselected branch not executed; no independent-transfer claim'}
    h.write(ROOT/'followup.json',result);s['followup']=h.art(j,result);checkpoint(s,[s['comparison'],s['experience'],s['followup']]);return s


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['init','compare','followup']);p.add_argument('--root',required=True)
    args=p.parse_args();configure(args.root);s={'init':initialize,'compare':compare,'followup':followup}[args.action]()
    print(json.dumps({'action':args.action,'decision':s.get('decision'),'followup':bool(s.get('followup'))}))
