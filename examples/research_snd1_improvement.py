"""SND1 normal research driver using the delivered ETALON research interfaces."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=REPO=PRISM=INSTALLED=AUTH=CHAIN=None

def configure(*, root, etalon_source, prism_source, authority, installed=None):
    global ROOT, REPO, PRISM, INSTALLED, AUTH, CHAIN
    import etalon
    ROOT=Path(root).absolute(); REPO=Path(etalon_source).absolute(); PRISM=Path(prism_source).absolute()
    INSTALLED=Path(installed).absolute() if installed else Path(etalon.__file__).parents[1]
    AUTH=Path(authority).absolute(); CHAIN=ROOT/'chain-final'
    sys.path.insert(0,str(ROOT/'prism-source'))
    importlib.invalidate_caches()
from etalon.active.store import CampaignStore
from etalon.active.schema import CampaignSpec, Endpoint
from etalon.research.journal import ResearchJournal
from etalon.research.schema import Artifact,MethodVersion,MethodUse,TaskSplit
from etalon.research.components import record_json
from etalon.research.method_revision import journal_ref,build_method_revision,record_method_revision,resolve_method_revision
from etalon.research.continuation import record_content,STEP,CHECKPOINT,resume_research
from etalon.research.composition import WORKFLOW,node_step,record_evaluation
from etalon.research import improvement as imp

SID,TASK='snd1','snd1-c26-pose-preparation'
QUESTION='Can template-constrained pose preparation improve SND1 C26 geometry and guide the next branch?'
COND={'target':'SND1','site':'C26-PPI-groove','chemistry':'neutral-connected-same-series-core>=60pct','endpoint':'receptor-frame-pose-fidelity'}


def pin(p):
    data=Path(p).read_bytes();return {'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
def write(p,obj):
    Path(p).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n','utf-8')
def read(p):return json.loads(Path(p).read_text('utf-8'))
def recipe():
    importlib.invalidate_caches()
    spec=importlib.util.spec_from_file_location('snd1_recipe',ROOT/'recipe/snd1_pose_workflow.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
def smiles(cc):
    return read(ROOT/'data/data'/('ccd-'+cc.lower())/'payload/records.json')[0]['rcsb_chem_comp_descriptor']['SMILES_stereo']
def journal():return ResearchJournal(CampaignStore(CHAIN/'research.sqlite'))
def ref(j,rid):return journal_ref(j,rid,source_id=SID)
def art(j,obj,kind='research-evidence'):
    return ref(j,record_json(j,obj,CHAIN/'memory',source_kind='new_execution',bindings={'kind':kind}))
def file(j,path,kind):
    return ref(j,j.record(Artifact(str(Path(path).absolute()),**pin(path),source_kind='existing_real_artifact',role='diagnostic',bindings={'kind':kind})))
def save(j,obj):return ref(j,record_content(j,obj,CHAIN/'memory',source_id=SID,source_kind='new_execution'))
def publish(j):
    path=CHAIN/'etalon-workspace.json'
    write(path,{'schema':'etalon-evidence-workspace/1','workspace_id':TASK,'sources':[{'source_id':SID,'kind':'journal',
         'location':str(j.store.path),'identity':{'kind':'snapshot_sha256','value':pin(j.store.path)['sha256']}}],
         'status_refs':[str(AUTH)],'location_map':[]})
    return path
def use(j,method,reason,subject,conditions=COND):
    return ref(j,j.record(MethodUse(({'kind':'task','id':TASK,'scope':str(j.store.path)},
        {'kind':'request','id':subject,'scope':str(j.store.path)}),
        ({'method_version_ref':method['record_id'],'scope':'SND1 same-series pose preparation'},),host_conditions=conditions,reason=reason)))
def cp(j,state,steps,basis,previous=()):
    value=save(j,{'schema':CHECKPOINT,'task_id':TASK,'question':QUESTION,'step_refs':steps,'claim_refs':[],
        'counter_refs':[],'method_refs':[state['base'],state['candidate'],state['generator']],
        'branches':[{'branch_id':name,'state':status,'basis_refs':basis,'next_read_refs':basis,
                     'reconsider_when':'New molecular evidence or changed protocol; do not replay completed executions'}
                    for name,status in [('c26-method-comparison','resolved' if state.get('resolution') else 'waiting'),
                       ('reverse-series-transfer','resolved' if state.get('followup') else 'waiting'),('R4b','stopped')]],
        'pending_handles':[],'source_watermarks':[],'supersedes':list(previous),
        'status_guards':[{'location':str(AUTH),**pin(AUTH),'state':'paused'}]})
    write(CHAIN/'entry.json',{'workspace':str(publish(j)),'checkpoint_ref':value})
    return value

def choose_from_experience(retrieved, applicable):
    """Example host rule: respect explicit successors and refuse ambiguous advice."""
    matches=retrieved['matches']
    key=lambda ref:(ref['source_id'],ref['record_id'],ref['record_digest'])
    superseded={key(ref) for match in matches for ref in match['experience']['supersedes']}
    active=[match for match in matches if key(match['ref']) not in superseded]
    if len(active)>1:raise ValueError('conflicting experience requires host review')
    return 'fixed_core' if applicable and len(active)==1 and active[0]['experience']['outcome']=='adopt' else 'free_align'

def init():
    if (CHAIN/'state.json').exists():return read(CHAIN/'state.json')
    CHAIN.mkdir(parents=True,exist_ok=True)
    (ROOT/'recipe').mkdir(exist_ok=True)
    frozen_recipe=ROOT/'recipe/snd1_pose_workflow.py'
    if frozen_recipe.exists():
        assert pin(frozen_recipe)==pin(REPO/'examples/snd1_pose_workflow.py')
    else:shutil.copyfile(REPO/'examples/snd1_pose_workflow.py',frozen_recipe)
    copies={}
    for path in (PRISM/'prism').rglob('*'):
        if path.is_file() and path.suffix in {'.py','.json'} and '__pycache__' not in path.parts:
            target=ROOT/'prism-source'/path.relative_to(PRISM);target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():assert pin(path)==pin(target)
            else:shutil.copyfile(path,target)
            copies[str(target.relative_to(ROOT/'prism-source'))]=pin(target)
    write(ROOT/'prism-source-manifest.json',copies)
    r=recipe()
    if (ROOT/'inputs-first/input.json').exists():
        inputs=read(ROOT/'inputs-first/input.json')
        for name,identity in inputs['files'].items():assert pin(ROOT/'inputs-first'/name)==identity
        assert pin(ROOT/'data/originals/7KNW.pdb')['sha256']==inputs['extraction']['sha256']
    else:inputs=r.input_bundle(ROOT/'inputs-first',ROOT/'data/originals/7KNW.pdb','QOY',smiles('QOY'),smiles('QOV'))
    store=CampaignStore(CHAIN/'research.sqlite');store.configure(CampaignSpec('structure',540,'wall_second',TASK),[
        Endpoint('structure','diagnostic','unmeasured','count','snd1-pose/1',0,queryable=False)])
    j=ResearchJournal(store)
    protocol=file(j,ROOT/'PROTOCOL.md','frozen-scientific-protocol')
    inputref=file(j,ROOT/'inputs-first/input.json','real-molecular-input')
    sources=[file(j,ROOT/'data/data'/name/'snapshot.json','molquarry-source-snapshot') for name in ['entry-7knw','polymer-7knw','ccd-qoy','ccd-qov']]
    history=file(j,REPO/'src/etalon/campaign/calibrate.py','historical-summary-not-raw-baseline')
    problem=art(j,{'schema':'snd1-structural-question/1','question':QUESTION,'basis':[inputref,history,*sources],
        'problem':'Prior SND1 docking gates and rankings did not supply pose-fidelity evidence. Prepare the related C26-A6 graph in the known C26-A2 receptor frame.',
        'not_claimed':'The new baseline is not the missing historical campaign execution.'})
    content={name:art(j,{'method':name,'implementation':file(j,ROOT/'recipe/snd1_pose_workflow.py','frozen-method-source'),
         'procedure':('ETKDG -> free MMFF -> common-core alignment' if name=='free_align' else 'coordinate-map ETKDG -> common-core alignment -> fixed-core MMFF'),
         'parameters':{'embed_iterations':1000,'minimize_iterations':1000,'threads':1},'runtime':r.runtime()}) for name in ['free_align','fixed_core']}
    base=ref(j,j.record(MethodVersion('snd1-free-conformer-align',('domain_skill','usage_method'),{'artifact_ref':content['free_align']['record_id']})))
    candidate=ref(j,j.record(MethodVersion('snd1-reference-fixed-core',('domain_skill','usage_method'),{'artifact_ref':content['fixed_core']['record_id']},
        parents=(base['record_id'],),entrypoint='snd1_pose_workflow.generate',change_summary='Preserve known same-series core during conformer generation and minimization')))
    initial=art(j,{'conditions':COND,'input_ref':inputref,'protocol_ref':protocol,'requested_slots_per_method':12,'seeds':list(range(1001,1013))})
    context=save(j,imp.build_revision_context(publish(j),task_id=TASK,scope='SND1 C26 same-series pose preparation',conditions=COND,
        target_ref=base,problem_refs=[problem],evidence_refs=[inputref,initial]))
    opportunity=imp.register_opportunity(j,context,CHAIN/'memory',source_id=SID,source_kind='new_execution')
    assert opportunity['state']=='new',opportunity
    gc=art(j,{'recipe':'Use a known related experimental ligand as a coordinate constraint; compare same inputs/seeds and retain failures.',
              'author':'host-authored; no learned generator claim'})
    generator=ref(j,j.record(MethodVersion('same-series-constraint-hypothesis',('improvement_method',),{'artifact_ref':gc['record_id']})))
    guse=use(j,generator,'Host-authored candidate based on available same-series structure',opportunity['request_id'])
    revision=ref(j,record_method_revision(j,build_method_revision(problem_refs=[problem,context],target_kind='U',base_method_ref=base,
        producer_method_ref=generator,producer_use_ref=guse,candidate_version_refs=[candidate],change_summary='Replace free optimization with explicit fixed-core optimization',
        mechanism_hypothesis='Known bound core geometry can prevent free conformer torsions from losing the receptor-frame pose',
        scope='SND1 C26 same-series pose preparation'),CHAIN/'memory',source_id=SID,source_kind='new_execution'))
    node={'node_id':'prepare-poses','capability_id':None,'method_refs':[base],'views':['X','R'],
          'purpose':'Prepare receptor-frame molecular poses','skip_when':'Exact input/method/seed execution already completed'}
    oldwf=save(j,{'schema':WORKFLOW,'name':'snd1-pose-preparation','scope':'SND1 C26 same-series pose preparation','nodes':[node],
        'edges':[],'method_refs':[base],'supersedes':[]})
    edits=[{'collection':'method_refs','before':base,'after':candidate},
           {'collection':'nodes','before':node,'after':{**node,'method_refs':[candidate]}}]
    built=imp.build_method_patch(j,context_ref=context,opportunity_ref=opportunity['opportunity_ref'],base_method_ref=base,candidate_method_ref=candidate,
        expected_change='Higher pose-fidelity success at bounded cost; no affinity claim',source_id=SID,workflow_before_ref=oldwf,workflow_edits=edits)
    newwf=save(j,built['workflow']);patch=save(j,{**built['patch'],'workflow_after_ref':newwf})
    split=ref(j,j.record(TaskSplit(('QOV-in-QOY-frame',),('unused-independent-validation',),('QOY-in-QOV-frame',),
        {'QOV-in-QOY-frame':'public target; answer withheld from generator','unused-independent-validation':'not-used',
         'QOY-in-QOV-frame':'correlated seen same-series reuse'},'No independent-target or affinity validation')))
    plan=save(j,{'schema':STEP,'task_id':TASK,'question':QUESTION,'branch_id':'c26-method-comparison','operation':'evaluation_plan',
       'reason':'Evaluate normal receptor-frame pose preparation against a withheld experimental pose','input_refs':[patch,revision],
       'output_refs':[],'method_refs':[base,candidate],'accounting_refs':[],'source_watermarks':[],'supersedes':[],
       'detail':{'target':'workflow_composition','subjects':[candidate],'comparators':[base],'protocol_ref':protocol,
          'task_split_ref':split,'initial_conditions_ref':initial,'use_refs':[],'trigger_refs':[problem],
          'metrics':[{'name':'pose-success','family':'retention','unit':'fraction','denominator':'all 12 requested slots per method','missing_policy':'fail'},
                     {'name':'execution-failure','family':'regression','unit':'count','denominator':'12 slots','missing_policy':'unknown'},
                     {'name':'generation-wall','family':'cost','unit':'second','denominator':'entire actual batch','missing_policy':'unknown'}]}})
    waiting=save(j,imp.assess_revision(j,patch_ref=patch,plan_ref=plan,source_id=SID))
    state={'base':base,'candidate':candidate,'generator':generator,'context':context,'opportunity':opportunity,
           'revision':revision,'oldwf':oldwf,'newwf':newwf,'patch':patch,'plan':plan,'waiting':waiting,'inputs':inputref,
           'steps':[plan],'nodes':[],'costs':[],'conditions':COND,'protocol_pin':pin(ROOT/'PROTOCOL.md')}
    state['checkpoint']=cp(j,state,state['steps'],[patch,waiting])
    write(CHAIN/'state.json',state)
    print(json.dumps({'initialized':True,'core':inputs['mapping']['core_atoms'],'coverage':inputs['mapping']['coverage_target_template'],'applicable':inputs['mapping']['applicable']}))
    return state

def execute_batch(state,name,method,input_dir,seed):
    j=journal();out=ROOT/'executions'/name;out.parent.mkdir(exist_ok=True)
    command=[sys.executable,'-B',str(ROOT/'recipe/snd1_pose_workflow.py'),'--input',str(input_dir),'--output',str(out),
             '--method',method,'--seed',str(seed),'--count','12']
    request={'input':pin(input_dir/'input.json'),'recipe':pin(ROOT/'recipe/snd1_pose_workflow.py'),'method':method,'seed':seed,'count':12}
    request_sha=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest();eid='snd1-'+request_sha
    reservation=j.store.reserve_resource_execution(eid,request_sha,180,unit='wall_second',provenance={'task_id':TASK,'kind':'local-snd1-pose','request':request})
    receipt=ROOT/'executions'/(name+'.receipt.json')
    if reservation['new_reservation']:
        env=os.environ.copy();env.update(PYTHONPATH=os.pathsep.join([str(ROOT/'prism-source'),str(INSTALLED)]),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        t=time.perf_counter()
        try:
            with receipt.with_suffix('.stdout.log').open('w',encoding='utf-8') as stdout,receipt.with_suffix('.stderr.log').open('w',encoding='utf-8') as stderr:
                result=subprocess.run(command,env=env,stdout=stdout,stderr=stderr,timeout=180)
            body={'command':command,'exit_code':result.returncode,'wall_seconds':time.perf_counter()-t,'execution_id':eid,'request':request,'pythonpath':env['PYTHONPATH']}
        except subprocess.TimeoutExpired:
            body={'command':command,'exit_code':None,'wall_seconds':time.perf_counter()-t,'execution_id':eid,'request':request,'status':'owned_process_timeout'}
        write(receipt,body)
        j.store.settle_resource_execution(eid,measured_cost=body['wall_seconds'],receipt=body)
    else:
        if not receipt.exists():raise RuntimeError('pending execution; no automatic retry')
        body=read(receipt)
    if body['exit_code']!=0:raise RuntimeError('execution failed; retained receipt '+str(receipt))
    actual=read(out/'result.json')
    # Existing terminal path is read through the scientific recipe's idempotent reader.
    assert recipe().generate(input_dir,out,method,range(seed,seed+12))['reused']
    return actual,body

def record_batch(state,name,method,actual,receipt,branch='c26-method-comparison',wf=None,input_ref=None):
    j=journal();m=state['base'] if method=='free_align' else state['candidate']
    output=file(j,ROOT/'executions'/name/'result.json','actual-molecular-execution')
    cost=art(j,{'receipt':receipt,'native_generation_cost':actual['cost'],'nested_costs_not_additive':True},kind='accounting')
    applied=use(j,m,'Executed native RDKit molecular preparation with PRISM identity and geometry',name)
    node=save(j,node_step(task_id=TASK,question=QUESTION,branch_id=branch,node_id='prepare-poses',
        workflow_ref=wf or (state['oldwf'] if method=='free_align' else state['newwf']),state='completed',
        reason='Normal molecular pose preparation, not a fixed probe stage',input_refs=[input_ref or state['inputs'],state['patch']],
        output_refs=[output],method_refs=[m],use_refs=[applied],receipt_refs=[output],accounting_refs=[cost]))
    state['steps'].append(node);state['nodes'].append(node);state['costs'].append(cost)
    write(CHAIN/'state.json',state)
    return node

def compare():
    state=init();j=journal()
    if state.get('resolution'):print('comparison already resolved; no execution');return
    for name,method in [('first-baseline','free_align'),('first-candidate','fixed_core')]:
        actual,receipt=execute_batch(state,name,method,ROOT/'inputs-first',1001)
        # Do not duplicate MethodUse/step on driver recovery.
        if not state.get(name):state[name]=record_batch(state,name,method,actual,receipt);write(CHAIN/'state.json',state)
    # Seal both actual output sets before opening answer coordinates.
    frozen={name:{str(p.relative_to(ROOT/'executions'/name)):pin(p) for p in (ROOT/'executions'/name).rglob('*') if p.is_file()}
            for name in ['first-baseline','first-candidate']}
    if not (ROOT/'first-output-freeze.json').exists():write(ROOT/'first-output-freeze.json',{'files':frozen,'answer_read_after_this':True,'protocol':pin(ROOT/'PROTOCOL.md')})
    else:assert read(ROOT/'first-output-freeze.json')['files']==frozen
    r=recipe();scores={}
    for name in ['first-baseline','first-candidate']:
        scores[name]=r.evaluate(ROOT/'inputs-first',ROOT/'executions'/name,ROOT/'data/originals/7KNX.pdb','QOV',smiles('QOV'))
        write(ROOT/(name+'-evaluation.json'),scores[name])
    base,cand=scores['first-baseline'],scores['first-candidate']
    gate={'at_least_three_more_successes':cand['successes']-base['successes']>=3,
          'no_more_than_one_extra_failure':cand['execution_failures']<=base['execution_failures']+1,
          'generation_wall_at_most_three_times':cand['generation_cost']['wall_seconds']<=3*base['generation_cost']['wall_seconds']}
    outcome='adopt' if all(gate.values()) else 'retain'
    comparison=art(j,{'schema':'snd1-pose-comparison/1','baseline':base,'candidate':cand,'adoption_tests':gate,'outcome':outcome,
        'scope':'two related public structures; conditional pose fidelity only, not affinity or generalization'})
    assessment=save(j,imp.assess_revision(j,patch_ref=state['patch'],plan_ref=state['plan'],observed_step_refs=state['nodes'],source_id=SID))
    ev=record_evaluation(j,state['plan'],directory=CHAIN/'memory',source_id=SID,source_kind='new_execution',result_refs=[comparison['record_id']],
        accounting_refs=[r['record_id'] for r in state['costs']],conclusion=f'{outcome}: valid pose successes {base["successes"]}/12 -> {cand["successes"]}/12; pose geometry only',
        scope='SND1 C26 same-series pose preparation')
    resolution=resolve_method_revision(j,state['revision'],ev['evaluation_ref'],outcome=outcome,
        candidate_ref=state['candidate'] if outcome=='adopt' else None,task_id=TASK,question=QUESTION,scope='SND1 C26 same-series pose preparation',
        reason='Frozen pose success/failure/cost criteria applied to actual normal execution',directory=CHAIN/'memory',source_id=SID,source_kind='new_execution')
    experience=imp.record_experience(j,patch_ref=state['patch'],outcome=outcome,
        lesson=f'On this SND1 C26 pair fixed-core preparation produced {cand["successes"]}/12 successful poses versus {base["successes"]}/12 free-aligned; disposition {outcome}. Apply only after graph/template applicability checks.',
        invalid_when='Different site, charge, insufficient core coverage, clashes, changed protocol or counterevidence',capability_id='prism.design',
        support_refs=[comparison,assessment,*state['nodes']],counter_refs=[comparison],evaluation_ref=ev['evaluation_ref'],resolution_ref=resolution['step_ref'],
        directory=CHAIN/'memory',source_id=SID,source_kind='new_execution')
    state.update(comparison=comparison,assessment=assessment,evaluation=ev,resolution=resolution,experience=experience,scores=scores)
    state['steps'].extend([ev['step_ref'],resolution['step_ref']]);state['checkpoint']=cp(j,state,state['steps'],[comparison,experience,resolution['step_ref']],previous=[state['checkpoint']])
    write(CHAIN/'state.json',state)
    print(json.dumps({'outcome':outcome,'baseline':base['successes'],'candidate':cand['successes'],'gate':gate,'costs':[base['generation_cost'],cand['generation_cost']]}))

def followup():
    state=read(CHAIN/'state.json');j=journal();r=recipe()
    if state.get('followup'):print('followup already completed; no execution');return
    if not state.get('resolution'):raise ValueError('first comparison must be resolved')
    # This input is made only after the first comparison. It is now seen evidence.
    if not (ROOT/'inputs-followup/input.json').exists():
        r.input_bundle(ROOT/'inputs-followup',ROOT/'data/originals/7KNX.pdb','QOV',smiles('QOV'),smiles('QOY'))
    inputs=read(ROOT/'inputs-followup/input.json')
    retrieved=imp.retrieve_experience(publish(j),source_ids=[SID],capability_id='prism.design',conditions=COND)
    decision_path=ROOT/'followup-selection.json'
    if not decision_path.exists():
        selected=choose_from_experience(retrieved,inputs['mapping']['applicable'])
        selection={'schema':'snd1-experience-branch-selection/1','retrieval':retrieved,
            'input':{'path':str(ROOT/'inputs-followup/input.json'),**pin(ROOT/'inputs-followup/input.json')},
            'applicability':inputs['mapping'],'selected':selected,'baseline_without_experience':'free_align',
            'reason':'Use adopted same-series recipe only after the new graph/template passes applicability; otherwise retain baseline',
            'policy':'one explicit host-authored rule; no autonomous workflow optimizer claim',
            'selected_before_execution':not (ROOT/'executions/followup-selected').exists()}
        assert selection['selected_before_execution']
        write(decision_path,selection)
    selection=read(decision_path);selected=selection['selected']
    if not state.get('selection_step'):
        selection_ref=file(j,decision_path,'branch-selection-before-execution')
        inputref=file(j,ROOT/'inputs-followup/input.json','later-molecular-branch-input')
        method=state['candidate'] if selected=='fixed_core' else state['base']
        step=save(j,{'schema':STEP,'task_id':TASK,'question':QUESTION,'branch_id':'reverse-series-transfer','operation':'decision',
            'reason':selection['reason'],'input_refs':[selection_ref,state['experience'],inputref],'output_refs':[],'method_refs':[method],
            'accounting_refs':[],'source_watermarks':[],'supersedes':[],
            'detail':{'choice':'proceed','alternatives':[
                {'branch_id':x,'disposition':'selected' if x==selected else 'skipped','reason':'Explicit applicability and retrieved disposition'}
                for x in ['free_align','fixed_core']], 'basis_refs':[selection_ref,state['experience']],
                'reconsider_when':'Changed chemistry/site or a failed normal execution'}})
        state.update(selection_step=step,followup_input=inputref);state['steps'].append(step);write(CHAIN/'state.json',state)
    actual,receipt=execute_batch(state,'followup-selected',selected,ROOT/'inputs-followup',2001)
    if not state.get('followup_node'):
        state['followup_node']=record_batch(state,'followup-selected',selected,actual,receipt,branch='reverse-series-transfer',input_ref=state['followup_input'])
        write(CHAIN/'state.json',state)
    score=r.evaluate(ROOT/'inputs-followup',ROOT/'executions/followup-selected',ROOT/'data/originals/7KNW.pdb','QOY',smiles('QOY'))
    write(ROOT/'followup-evaluation.json',score)
    # A different real molecular graph must not inherit the applicability label.
    from rdkit import Chem
    template=Chem.SDMolSupplier(str(ROOT/'inputs-followup/template.sdf'),removeHs=False)[0]
    aspirin=Chem.MolFromSmiles('CC(=O)Oc1ccccc1C(=O)O')
    mismatch=r.mapping(template,aspirin)
    incompatible={**COND,'chemistry':'outside-same-series-core-domain' if not mismatch['applicable'] else COND['chemistry']}
    rejected=imp.retrieve_experience(publish(j),source_ids=[SID],capability_id='prism.design',conditions=incompatible)
    negative={'molecule':'aspirin','graph':r.canonical_smiles(aspirin),'mapping':mismatch,'conditions':incompatible,
              'matched_experience':len(rejected['matches']),'decision':'do_not_apply_this_template_recipe' if not mismatch['applicable'] else 'review',
              'execution':'not_run; no suitable same-series template evidence','not_an_affinity_label':True}
    write(ROOT/'out-of-domain-branch.json',negative)
    result=art(j,{'schema':'snd1-followup-outcome/1','selection':selection,'normal_evaluation':score,
        'out_of_domain':negative,'interpretation':'Experience changed an actual later branch; no unexecuted counterfactual gain or independent transfer claim'})
    state.update(followup={'selected':selected,'score':score,'result_ref':result,'out_of_domain':negative})
    state['checkpoint']=cp(j,state,state['steps'],[state['comparison'],state['experience'],state['selection_step'],result],previous=[state['checkpoint']])
    write(CHAIN/'state.json',state)
    ctx=resume_research(CHAIN/'etalon-workspace.json',state['checkpoint'])
    assert not ctx['issues'],ctx['issues']
    before=len(j.store.events())
    duplicate=imp.register_opportunity(j,state['context'],CHAIN/'memory',source_id=SID,source_kind='new_execution')
    assert duplicate['state']=='already_proposed' and duplicate['patch_refs']==[state['patch']]
    assert len(j.store.events())==before
    write(ROOT/'acceptance.json',{'checked_refs':len(ctx['checked_refs']),'issues':ctx['issues'],'duplicate':duplicate,
        'events_unchanged':before,'science_execution_batches':3,'requested_poses':36,
        'first_successes':{k:v['successes'] for k,v in state['scores'].items()},
        'followup_selected':selected,'followup_successes':score['successes'],'out_of_domain':negative,
        'fixed_probe_stage':False,'R4b':'paused','branch_selection_before_execution':selection['selected_before_execution']})
    print(json.dumps({'selected':selected,'successes':score['successes'],'out_of_domain_applicable':mismatch['applicable'],
                      'out_of_domain_matches':len(rejected['matches']),'checked_refs':len(ctx['checked_refs'])}))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['init','compare','followup'])
    for name in ['root','etalon-source','prism-source','authority']:
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--installed',type=Path)
    args=parser.parse_args()
    configure(root=args.root,etalon_source=args.etalon_source,prism_source=args.prism_source,authority=args.authority,installed=args.installed)
    {'init':init,'compare':compare,'followup':followup}[args.stage]()
