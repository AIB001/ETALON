"""Opt-in, bounded real-pose comparison using the existing thin harness.

The organizer supplies a frozen panel. GNINA generates one empirical candidate
pool and scores those exact poses on the specified GPU. Development lessons are
recorded before validation choices; complete alternative outcomes are an audit
panel, not free measurements attributed to the selective policy.
"""
from __future__ import annotations
import argparse, json, os, sqlite3, subprocess, sys, time
from pathlib import Path
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.host import _consumer_lock
from etalon.research.journal import ResearchJournal
from etalon.research.schema import Artifact, MethodVersion, MethodUse, TaskSplit
from etalon.research import improvement as imp
from etalon.research.continuation import STEP, CHECKPOINT, record_content
from etalon.research.composition import node_step, record_evaluation
from etalon.research.method_revision import journal_ref
from etalon.research.evidence_refs import file_identity
from etalon.research.publication import _save, prepare_publication, resume_publication, discover_publications
from etalon.research.pose_task import compile_task, select_pose_branch
from etalon.research.pose_verifier import verify_poses

SID='posebusters24'; TASK='posebusters24-known-pocket'; QUESTION='Does GPU pose rescoring improve valid pose recovery, and can recorded experience guide subsequent selection?'
CAP='molcascade.gnina.pose-ranking'
CONDITIONS={'endpoint':'valid-pose-recovery','task':'known-pocket-redocking','pose_pool':'empirical-GNINA-1.3.3',
            'cnn':'crossdock_default2018','scope':'organic-10-45-heavy-atoms; fixed-receptor-frame'}

def read(p):return json.loads(Path(p).read_text('utf-8'))


class Case:
    SID=SID; TASK=TASK; QUESTION=QUESTION; CAP=CAP; CONDITIONS=CONDITIONS
    driver_name="research_posebusters.py"
    protocol_file='protocol.json'

    def __init__(self,root):
        self.root=Path(root).absolute();self.panel=read(self.root/'cases.json');self.protocol=read(self.root/self.protocol_file)
        self.directory=self.root/'memory';self.database=self.root/'research.sqlite'
        store=CampaignStore(self.database)
        store.configure(CampaignSpec('pose',21600,'wall_second',self.TASK),[Endpoint('pose','diagnostic','unmeasured','count','pose/1',0,queryable=False)])
        self.j=ResearchJournal(store)
    def ref(self,rid):return journal_ref(self.j,rid,source_id=self.SID)
    def file(self,p,kind='result'):
        return self.ref(self.j.record(Artifact(str(Path(p).absolute()),**file_identity(p),source_kind='new_execution',role='diagnostic',bindings={'kind':kind})))
    def artifact(self,v,kind='evidence'):
        p=self.directory/(digest(v)+'.json');_save(p,v);return self.file(p,kind)
    def content(self,v):return self.ref(record_content(self.j,v,self.directory,source_id=self.SID,source_kind='new_execution'))
    def state(self):return read(self.root/'state.json')
    def persist(self,s):_save(self.root/'state.json',s,replace=True)
    def workspace(self):
        p=self.root/'live-workspace.json';_save(p,{'schema':'etalon-evidence-workspace/1','workspace_id':self.TASK,
          'sources':[{'source_id':self.SID,'kind':'journal','location':str(self.database),
                      'identity':{'kind':'snapshot_sha256','value':file_identity(self.database)['sha256']}}],
          'status_refs':[str(self.root/'STATUS.md')],'location_map':[]},replace=True);return p
    def checkpoint(self,s,interrupt=False):
        progress=self.artifact({'schema':'pose-panel-progress/1','node_refs':list(s['nodes'].values()),'decision_refs':list(s['decisions'].values()),
            'completed_nodes':len(s['nodes']),'planned_scientific_calls':96,'completed':bool(s.get('final')),
            'development_experience':s.get('experience'),'entry_command':s['command'],
            'current_result':s.get('final',s.get('development_result')),'pending_semantics':'Inspect exact receipts; never retry ambiguous started requests.'})
        step=self.content(node_step(task_id=self.TASK,question=self.QUESTION,branch_id='panel',node_id='progress',
            state='completed' if s.get('final') else 'partial',reason='Bounded aggregate; all detailed nodes remain linked',
            input_refs=[s['plan']],output_refs=[progress],receipt_refs=[progress]))
        cp=self.content({'schema':CHECKPOINT,'task_id':self.TASK,'question':self.QUESTION,'step_refs':[s['plan'],step],
            'claim_refs':[],'counter_refs':[],'method_refs':[s['base'],s['candidate']],
            'branches':[{'branch_id':'panel','state':'resolved' if s.get('final') else 'active','basis_refs':[s.get('final',s['problem'])],
                        'next_read_refs':[progress,s['command']]+([s['experience']] if s.get('experience') else []),
                        'reconsider_when':'New task or changed evidence; completed requests and current opportunity remain reusable.'}],
            'pending_handles':[],'source_watermarks':[], 'status_guards':[{'location':str(self.root/'STATUS.md'),**file_identity(self.root/'STATUS.md'),'state':'active'}],
            'supersedes':[s['checkpoint']] if s.get('checkpoint') else []})
        s['checkpoint']=cp;self.persist(s)
        snaps=self.root/'snapshots';snaps.mkdir(exist_ok=True);tmp=snaps/'pending.sqlite'
        with sqlite3.connect(self.database) as src,sqlite3.connect(tmp) as dst:src.backup(dst)
        pin=file_identity(tmp);snapshot=snaps/(pin['sha256']+'.sqlite')
        if snapshot.exists():tmp.unlink()
        else:os.replace(tmp,snapshot)
        ws={**read(self.workspace()),'sources':[{'source_id':self.SID,'kind':'journal','location':str(snapshot),'identity':{'kind':'snapshot_sha256','value':pin['sha256']}}]}
        wsp=snaps/(pin['sha256']+'.workspace.json');_save(wsp,ws)
        parent=snaps/(pin['sha256']+'.entry.json');_save(parent,{'workspace':str(wsp),'checkpoint_ref':cp})
        entry=self.root/'entry.json';staged=snaps/(pin['sha256']+'.published.json')
        publication_step=node_step(task_id=self.TASK,question=self.QUESTION,branch_id='panel',node_id='published-progress',
            state='completed',reason='Publish this exact journal snapshot and progress artifact',
            input_refs=[step],output_refs=[progress],receipt_refs=[progress])
        prepared=prepare_publication(staged,parent_entry=parent,operation_id='checkpoint-'+cp['record_digest'],
            expected_entry_identity=None,steps=[{'name':'progress','content':publication_step}])
        _save(self.root/'handoff.json',{'intent_path':prepared['intent_path'],'staged':str(staged),
            'expected_entry':file_identity(entry) if entry.exists() else None,'checkpoint_ref':cp,'complete':False},replace=True)
        if interrupt and not (self.root/'publication-interrupted.json').exists():
            from etalon.research import publication as pub
            original=pub._stage
            def crash(path,phase,payload):
                result=original(path,phase,payload)
                if phase=='checkpoint_recorded':
                    _save(self.root/'publication-interrupted.json',{'intent':prepared['intent_path'],'phase':phase});os._exit(87)
                return result
            pub._stage=crash
        self.recover_publication()

    def recover_publication(self):
        pending=self.root/'handoff.json'
        if not pending.exists():return
        handoff=read(pending)
        if handoff['complete']:return
        resume_publication(handoff['intent_path'])
        staged=Path(handoff['staged']);entry=self.root/'entry.json'
        with _consumer_lock(self.root/'.entry-handoff.lock'):
            current=file_identity(entry) if entry.exists() else None
            published=read(staged)
            if current==file_identity(staged):pass
            elif current!=handoff['expected_entry']:raise ValueError('entry handoff CAS conflict; inspect, never overwrite')
            else:_save(entry,published,replace=True)
            finished={**handoff,'complete':True,'published_entry':file_identity(entry)}
            _save(self.root/'acceptance/handoffs'/(handoff['checkpoint_ref']['record_digest']+'.json'),finished)
            _save(pending,finished,replace=True)

    def initialize(self):
        if (self.root/'state.json').exists():return self.state()
        protocol=self.file(self.root/self.protocol_file,'protocol')
        problem=self.artifact({'question':self.QUESTION,'problem':'Empirical ordering may miss physically valid native-like poses within the same sampled pool.',
            'hypothesis':'CNN pose rescoring may improve selection; paired evaluation can also reject it.',
            'prior_SND1':'Solver-cost experience is not applicable to new targets or pose correctness.'})
        methods={}
        for key,ranking in [('base','empirical'),('candidate','cnn')]:
            body=self.artifact({'ranking':ranking,'engine':self.protocol['engine'],'protocol_ref':protocol,'implementation':self.protocol['implementation']})
            methods[key]=self.ref(self.j.record(MethodVersion('pb24-'+ranking,('usage_method','domain_skill'),{'artifact_ref':body['record_id']},
                parents=(methods['base']['record_id'],) if key=='candidate' else (),change_summary='GPU CNN ranking of the same candidate pool' if key=='candidate' else 'Empirical score order')))
        ctx=self.content(imp.build_revision_context(self.workspace(),task_id=self.TASK,scope=self.QUESTION,conditions=self.CONDITIONS,
            target_ref=methods['base'],problem_refs=[problem],evidence_refs=[protocol]))
        opp=imp.register_opportunity(self.j,ctx,self.directory,source_id=self.SID,source_kind='new_execution')
        patch=self.content(imp.build_method_patch(self.j,context_ref=ctx,opportunity_ref=opp['opportunity_ref'],base_method_ref=methods['base'],
            candidate_method_ref=methods['candidate'],expected_change='Improve valid Top-1 recovery; preserve scientific standards and sampled coordinates',source_id=self.SID)['patch'])
        split=self.ref(self.j.record(TaskSplit(tuple(r['id'] for r in self.panel['cases'] if r['split']=='development'),
            tuple(r['id'] for r in self.panel['cases'] if r['split']=='validation'),('not-run-transfer',),
            {**{r['id']:r['split'] for r in self.panel['cases']},'not-run-transfer':'not run'},'Deterministic new panel; ligand scaffolds distinct; sequence heuristic, not homology certification')))
        initial=self.artifact({'conditions':self.CONDITIONS,'panel':file_identity(self.root/'cases.json')})
        command=self.artifact({'continue_command':str(self.root/'venv/bin/python')+' '+str(self.root/self.driver_name)+' continue --root '+str(self.root),
            'status_command':str(self.root/'venv/bin/python')+' '+str(self.root/self.driver_name)+' status --root '+str(self.root),
            'scope':'Only this authorized 24-case panel. R4b paused. Saved requests never recomputed. Publication reconciliation precedes execution.',
            'remaining_semantics':'96 native calls total = 24 cases x 2 seeds x (CPU search + GPU score). All alternatives form an audit panel.'})
        plan=self.content({'schema':STEP,'task_id':self.TASK,'question':self.QUESTION,'branch_id':'panel','operation':'evaluation_plan',
            'reason':'Normal research tasks; fixed evaluator before prediction','input_refs':[problem,patch,command],'output_refs':[],
            'method_refs':list(methods.values()),'accounting_refs':[],'source_watermarks':[],'supersedes':[],
            'detail':{'target':'method','subjects':[methods['candidate']],'comparators':[methods['base']], 'protocol_ref':protocol,'task_split_ref':split,
                'initial_conditions_ref':initial,'use_refs':[],'trigger_refs':[problem], 'metrics':[
                    {'name':'valid-top1','family':'retention','unit':'case-seed','denominator':'all requested cases and seeds including failures','missing_policy':'fail'},
                    {'name':'lost-valid-top1','family':'regression','unit':'case-seed','denominator':'baseline successes','missing_policy':'fail'},
                    {'name':'runtime','family':'cost','unit':'wall_second','denominator':'all actual attempts; audit cost separate from policy cost','missing_policy':'unknown'}]}})
        s={**methods,'context':ctx,'opportunity':opp,'patch':patch,'problem':problem,'plan':plan,'protocol':protocol,'command':command,
           'nodes':{},'uses':{},'decisions':{},'results':{},'cost_ids':[]}
        self.checkpoint(s);return s

    def task(self,row,seed):
        folder=self.root/'inputs'/row['id']; settings=read(folder/'settings.json')
        for name in ('receptor.pdb','ligand.sdf','settings.json'):
            if file_identity(folder/name)!=row['inputs'].get(name):
                raise ValueError('frozen conditioning input changed: '+row['id']+'/'+name)
        sources=[{'id':name,'path':str(folder/file),'locator':'PoseBusters released known-pocket input','role':'conditioning','scope':self.QUESTION,
                  **file_identity(folder/file)} for name,file in [('receptor','receptor.pdb'),('ligand','ligand.sdf'),('settings','settings.json')]]
        values={'receptor':str(folder/'receptor.pdb'),'ligand':str(folder/'ligand.sdf'),'box':settings['box'],'seed':seed,'num_modes':10,'exhaustiveness':8}
        return {'schema':'etalon-pose-task/1','task_id':self.TASK,'case_id':row['id'],'question':self.QUESTION,'split':row['split'],
          'method':{'engine':self.protocol['engine'],'search':'empirical','candidate_ranking':'CNNscore'},
          'output_contract':{'poses':'SDF','scores':'typed-with-direction','receipt':'request-and-file-identities','failure':'retained'},
          'input':{'schema':'etalon-scientific-input/1','task_id':self.TASK,'scope':self.QUESTION,'subject':{'molecule':row['smiles'],'case':row['id']},
             'sources':sources,'observations':[],'interpretations':[], 'bindings':[
                 {'id':k,'target':k,'value':v,'use':'constraint' if k in ('receptor','ligand','box') else 'selection',
                  'basis_ids':[k if k in ('receptor','ligand') else 'settings'],'rationale':'Declared redocking task parameter; no answer coordinates'} for k,v in values.items()]}}

    def execution_method(self,s,stage):
        return s['base'] if stage=='empirical' else s['candidate']

    def execute(self,s,row,seed,stage,interrupt=False):
        from etalon.research.pose_engine import command, execute
        compiled=compile_task(self.task(row,seed));p=compiled['parameters']
        request={'compiled':compiled,'stage':stage,'protocol':file_identity(self.root/self.protocol_file)}
        if stage=='cnn':request['pool']=file_identity(self.root/'outputs'/row['id']/str(seed)/'empirical.sdf')
        key=digest(request);eid='pb24-'+key;out=self.root/'executions'/key;out.mkdir(parents=True,exist_ok=True)
        _save(out/'request.json',request)
        reservation=self.j.store.reserve_resource_execution(eid,key,600,unit='wall_second',provenance={'request':request})
        dest=self.root/'outputs'/row['id']/str(seed);dest.mkdir(parents=True,exist_ok=True)
        posefile=dest/(stage+'.sdf');receipt=out/'receipt.json'
        if receipt.exists():
            observed=read(receipt)
            for name,pin in observed['artifacts'].items():
                if file_identity(name)!=pin:raise ValueError('saved artifact changed; never rerun')
        elif not reservation['new_reservation']:raise RuntimeError('Ambiguous reserved request; no automatic retry '+eid)
        else:
            _save(out/'started.json',{'execution_id':eid,'request_id':key,'pid':os.getpid(),'time_ns':time.time_ns()})
            args=command(self.root/'tools/gnina',p,posefile,stage=stage,pool=dest/'empirical.sdf')
            _save(out/'command.json',{'argv':args,'CUDA_VISIBLE_DEVICES':'1','stage':stage,
                'orchestrator':file_identity(__file__),'actual_inputs':['receptor','original generated ligand','box'] if stage=='empirical' else ['receptor','existing empirical pool'],
                'search_parameters_deferred':[] if stage=='empirical' else ['exhaustiveness','num_modes','box','original ligand coordinates']})
            start=time.perf_counter();error=None
            try:
                log=execute(args,directory=out)
                (out/'stdout.log').write_text(log,encoding='utf-8')
                if not posefile.exists():raise ValueError('missing native SDF')
            except Exception as exc:
                error={'type':type(exc).__name__,'message':str(exc),'context':getattr(exc,'context',None)}
                (out/'stdout.log').write_text(json.dumps(error,default=str),encoding='utf-8')
            observed={'execution_id':eid,'request_id':key,'stage':stage,'case_id':row['id'],'seed':seed,
                'wall_seconds':time.perf_counter()-start,'error':error,'artifacts':{str(path):file_identity(path) for path in [posefile,out/'stdout.log'] if path.exists()}}
            _save(receipt,observed)
            if interrupt and not (self.root/'receipt-interrupted.json').exists():
                _save(self.root/'receipt-interrupted.json',{'request_id':key,'receipt':str(receipt)});os._exit(86)
        self.j.store.settle_resource_execution(eid,measured_cost=observed['wall_seconds'],receipt=observed)
        if key not in s['nodes']:
            ref=self.file(receipt,'execution-receipt');method=self.execution_method(s,stage)
            use=self.ref(self.j.record(MethodUse(({'kind':'task','id':self.TASK,'scope':str(self.database)}, {'kind':'request','id':key,'scope':str(self.database)}),
                ({'method_version_ref':method['record_id'],'scope':self.QUESTION,'adoption':'full'},),host_conditions=self.CONDITIONS,reason='Actual native '+stage+' execution')))
            result=self.file(posefile,'poses') if posefile.exists() else ref
            s['nodes'][key]=self.content(node_step(task_id=self.TASK,question=self.QUESTION,branch_id=row['id'],node_id=key,capability_id=self.CAP,
                state='failed' if observed['error'] else 'completed',reason='Normal task; validation alternatives include charged audit work',
                input_refs=[s['plan']],output_refs=[result],receipt_refs=[ref],method_refs=[method],use_refs=[use],accounting_refs=[ref]))
            s['uses'][key]=use;s['results'][key]=ref;s['cost_ids'].append(eid);self.persist(s)
        return observed

    def score_case(self,row,seed):
        from rdkit import Chem
        folder=self.root/'outputs'/row['id']/str(seed); ref=self.root/'evaluator'/row['id']/'reference.sdf'
        report={}
        for method in ('empirical','cnn'):
            p=folder/(method+'.sdf');checked=folder/(method+'.verification.json')
            if not checked.exists():
                if not p.exists():_save(checked,{'rows':[],'error':'no native output'})
                else:_save(checked,verify_poses(p,ref,self.root/'inputs'/row['id']/'receptor.pdb'))
            verification=read(checked)
            molecules=list(Chem.SDMolSupplier(str(p),removeHs=False)) if p.exists() else []
            scores=[]
            for i,m in enumerate(molecules):
                prop='minimizedAffinity' if method=='empirical' else 'CNNscore'
                if m is not None and m.HasProp(prop):scores.append((float(m.GetProp(prop)),i))
            scores.sort(reverse=method=='cnn');indices=[i for _,i in scores]
            rows=verification['rows'];byindex={v['pose_index']:v for v in rows}
            best=byindex.get(indices[0],{}) if indices else {}
            report[method]={'top1':bool(best.get('success',False)),'rmsd_A':best.get('rmsd_A'),'physical_valid':best.get('physical_valid',False),
                'top5':any(byindex[i]['success'] for i in indices[:5]),'nposes':len(rows),'selected_index':indices[0] if indices else None,
                'verification':file_identity(checked)}
        return {'case_id':row['id'],'seed':seed,'split':row['split'],**report}

    def comparison(self,rows):
        values=[self.score_case(row,seed) for row in rows for seed in self.panel['seeds']]
        a=sum(v['empirical']['top1'] for v in values);b=sum(v['cnn']['top1'] for v in values)
        return {'rows':values,'requested':len(values),'baseline_successes':a,'candidate_successes':b,
            'rescued':sum(not v['empirical']['top1'] and v['cnn']['top1'] for v in values),
            'regressed':sum(v['empirical']['top1'] and not v['cnn']['top1'] for v in values),
            'validity_change':sum(v['cnn']['physical_valid']-v['empirical']['physical_valid'] for v in values),
            'interpretation':'Paired seeds are correlated within cases; retrospective small-panel evidence, not affinity or broad generalization.'}

    def record_comparison(self,s,result,keys,conclusion):
        # Multiple bounded attribution steps point to ONE immutable evaluation.
        # They do not replicate the comparison or double-charge its costs.
        evaluation=None
        for offset in range(0,len(keys),16):
            batch=keys[offset:offset+16]
            evaluation=record_evaluation(self.j,s['plan'],directory=self.directory,source_id=self.SID,source_kind='new_execution',
                result_refs=(result['record_id'],),accounting_refs=tuple('pb24-'+k for k in keys),conclusion=conclusion,scope=self.QUESTION,
                use_refs=[s['uses'][k] for k in batch],node_refs=[s['nodes'][k] for k in batch])
        return evaluation

    def evaluate_development(self,s):
        report=self.comparison([r for r in self.panel['cases'] if r['split']=='development'])
        _save(self.root/'development-comparison.json',report);result=self.file(self.root/'development-comparison.json')
        outcome='adopt' if report['candidate_successes']>report['baseline_successes'] and report['validity_change']>=0 else 'retain'
        candidate_nodes=[n for k,n in s['nodes'].items() if read(self.root/'executions'/k/'receipt.json')['stage']=='cnn']
        assessments=[]
        for offset in range(0,len(candidate_nodes),8):
            assessment_payload=imp.assess_revision(self.j,patch_ref=s['patch'],plan_ref=s['plan'],observed_step_refs=candidate_nodes[offset:offset+8],source_id=self.SID)
            if assessment_payload['status']!='ready_for_evaluation':raise ValueError('candidate actual use is not evaluable: '+str(assessment_payload['missing']))
            assessments.append(self.content(assessment_payload))
        ev=self.record_comparison(s,result,list(s['nodes']),outcome+' on 8 development cases only')
        selected=s['candidate'] if outcome=='adopt' else s['base']
        decision=self.content({'schema':STEP,'task_id':self.TASK,'question':self.QUESTION,'branch_id':'development','operation':'decision',
            'reason':'Frozen development rule; validation remains unreleased','input_refs':[ev['evaluation_ref']], 'output_refs':[],
            'method_refs':[selected],'accounting_refs':[],'source_watermarks':[],'supersedes':[],
            'detail':{'choice':'proceed','alternatives':[{'branch_id':'cnn','disposition':outcome,'reason':'Development valid pose counts and validity floor'}],
                'basis_refs':[result],'reconsider_when':'Validation contradicts benefit, endpoint/model/input conditions change'}})
        exp=imp.record_experience(self.j,patch_ref=s['patch'],outcome=outcome,lesson='Development-only ranking lesson: '+json.dumps({k:v for k,v in report.items() if k!='rows'}),
            invalid_when='Contradictory validation; different model, chemistry, pocket knowledge or objective. Not affinity evidence.',capability_id=self.CAP,
            support_refs=[result,*assessments],counter_refs=[s['problem']],evaluation_ref=ev['evaluation_ref'],resolution_ref=decision,
            directory=self.directory,source_id=self.SID,source_kind='new_execution')
        s.update(experience=exp,development_result=result,development_outcome=outcome);self.checkpoint(s)

    def validation_experience(self,s,report):
        validation=self.comparison([r for r in self.panel['cases'] if r['split']=='validation'])
        _save(self.root/'validation-comparison.json',validation);result=self.file(self.root/'validation-comparison.json')
        keys=[k for k in s['nodes'] if read(self.root/'executions'/k/'receipt.json')['case_id'] in
              {r['id'] for r in self.panel['cases'] if r['split']=='validation'}]
        beneficial=validation['candidate_successes']>validation['baseline_successes'] and validation['validity_change']>=0
        outcome='adopt' if beneficial else ('rollback' if s['development_outcome']=='adopt' else 'retain')
        ev=self.record_comparison(s,result,keys,outcome+' after frozen 16-case validation; small retrospective diagnostic')
        decision=self.content({'schema':STEP,'task_id':self.TASK,'question':self.QUESTION,'branch_id':'validation','operation':'decision',
            'reason':'Validation feedback retains the pre-execution standard and all failures','input_refs':[ev['evaluation_ref']], 'output_refs':[],
            'method_refs':[s['candidate'] if outcome=='adopt' else s['base']],'accounting_refs':[], 'source_watermarks':[],'supersedes':[],
            'detail':{'choice':'stop','alternatives':[{'branch_id':'cnn','disposition':outcome,'reason':'Frozen valid Top1 and physical-validity comparison'}],
                'basis_refs':[result],'reconsider_when':'New independent panel or changed scientific conditions; no repeat of this completed panel'}})
        exp=imp.record_experience(self.j,patch_ref=s['patch'],outcome=outcome,
            lesson=outcome+': validation valid Top1 '+str(validation['baseline_successes'])+' -> '+str(validation['candidate_successes'])+
                '; validity change '+str(validation['validity_change'])+'. This is a small retrospective pose-ranking result, not affinity.',
            invalid_when='Different chemistry, model, pocket knowledge or endpoint; contradictory new evidence; this validation set is now seen.',
            capability_id=self.CAP,support_refs=[result],counter_refs=[s['development_result']],evaluation_ref=ev['evaluation_ref'],resolution_ref=decision,
            supersedes=[s['experience']],directory=self.directory,source_id=self.SID,source_kind='new_execution')
        s['development_experience']=s['experience'];s['experience']=exp;s['validation_outcome']=outcome
        report['validation_outcome']=outcome;report['scientific_expansion_gate']=beneficial

    def choose(self,s,row):
        if row['id'] in s['decisions']:return
        lessons=imp.retrieve_experience(self.workspace(),source_ids=[self.SID],capability_id=self.CAP,conditions=self.CONDITIONS)
        choice=select_pose_branch(lessons)
        _save(self.root/'choices'/(row['id']+'.json'),choice)
        evidence=self.artifact({'case_id':row['id'],'retrieval':lessons,'selection':choice})
        s['decisions'][row['id']]=self.content({'schema':STEP,'task_id':self.TASK,'question':self.QUESTION,'branch_id':row['id'],'operation':'decision',
            'reason':'Read development experience before any validation execution or label','input_refs':[evidence,s['experience']], 'output_refs':[],
            'method_refs':[s['candidate'] if choice['selected']=='cnn' else s['base']], 'accounting_refs':[], 'source_watermarks':[],'supersedes':[],
            'detail':{'choice':'proceed','alternatives':[{'branch_id':'cnn','disposition':'select' if choice['selected']=='cnn' else 'skip',
                'reason':choice['reason']}],'basis_refs':[evidence],'reconsider_when':'New contradictory results; audited alternative is not a free policy observation'}})
        self.persist(s)

    def continue_case(self,interrupt=False,limit=None):
        with _consumer_lock(self.root/'.case.lock'):
            for path,pin in self.protocol['implementation'].items():
                if file_identity(path)!=pin:raise ValueError('implementation changed: '+path)
            self.recover_publication()
            s=self.initialize()
            imp.register_opportunity(self.j,s['context'],self.directory,source_id=self.SID,source_kind='new_execution')
            if s.get('final'):
                for receipt in (self.root/'executions').glob('*/receipt.json'):
                    for name,pin in read(receipt)['artifacts'].items():
                        if file_identity(name)!=pin:raise ValueError('saved output changed')
                return {'status':'already_complete',**self.status()}
            processed=0
            for row in self.panel['cases']:
                before=len(s['nodes'])
                if row['split']=='validation':
                    if not s.get('experience'):self.evaluate_development(s)
                    self.choose(s,row)
                for seed in self.panel['seeds']:
                    for stage in ('empirical','cnn'):
                        if stage=='cnn' and not (self.root/'outputs'/row['id']/str(seed)/'empirical.sdf').exists():
                            raise RuntimeError('Baseline search failed; preserve failure and inspect before dependent scoring')
                        self.execute(s,row,seed,stage,interrupt=interrupt)
                if len(s['nodes'])>before:
                    processed+=1;self.checkpoint(s,interrupt=interrupt)
                    if limit and processed>=limit:return {'status':'partial',**self.status()}
            report=self.comparison(self.panel['cases'])
            val=[v for v in report['rows'] if v['split']=='validation']
            report['validation']={'requested':len(val),'baseline_successes':sum(v['empirical']['top1'] for v in val),
                'candidate_successes':sum(v['cnn']['top1'] for v in val),
                'selected_successes':sum(v[read(self.root/'choices'/(v['case_id']+'.json'))['selected']]['top1'] for v in val)}
            report['policy_note']='Choices frozen before each validation task; all alternatives actually computed for this pilot audit, costs remain charged.'
            self.validation_experience(s,report)
            _save(self.root/'comparison.json',report);s['final']=self.file(self.root/'comparison.json');self.checkpoint(s)
            return {'status':'completed',**self.status()}

    def status(self):
        s=self.state();receipts=list((self.root/'executions').glob('*/receipt.json'))
        return {'entry':str(self.root/'entry.json'),'scientific_starts':len(list((self.root/'executions').glob('*/started.json'))),
            'durable_receipts':len(receipts),'recorded_nodes':len(s['nodes']),'unrecorded_receipts':len(receipts)-len(s['nodes']),
            'remaining_native_calls':96-len(receipts),'development_experience':s.get('experience'),'validation_choices':len(s['decisions']),
            'complete':bool(s.get('final')),'balance':self.j.store.balance(),'comparison':str(self.root/'comparison.json') if s.get('final') else None}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['initialize','continue','status']);parser.add_argument('--root',required=True)
    parser.add_argument('--interrupt',action='store_true');parser.add_argument('--limit',type=int);args=parser.parse_args();case=Case(args.root)
    if args.action=='initialize':case.initialize();value=case.status()
    elif args.action=='continue':value=case.continue_case(args.interrupt,args.limit)
    else:value=case.status()
    print(json.dumps(value,indent=2),flush=True)
