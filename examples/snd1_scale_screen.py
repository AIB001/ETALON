"""Frozen SND1 P2 batch connection over existing pose-task/engine interfaces.

This example owns only its run directory. It does not alter a shared deployment,
learn from confirmation outcomes, or automatically retry an ambiguous attempt.
"""
from __future__ import annotations
import argparse, collections, concurrent.futures, contextlib, hashlib, importlib, importlib.metadata
import json, os, platform, subprocess, sys, threading, time
from pathlib import Path
from etalon.active.schema import digest
from etalon.host import _consumer_lock
from etalon.research.pose_task import compile_task
from etalon.research import pose_engine
from etalon.research.scientific_input import identity
from etalon.research.result_integrity import (freeze_check_contract, validate_check_contract,
    result_binding, validate_verifier, top1_observation, validate_native_receipt,
    confirmation_eligibility)

QUESTION='SND1 known-pocket source-analogue screening; same-pool CNN ranking and physical checks'
OUTPUT={'poses':'SDF','scores':'typed-with-direction','receipt':'request-and-file-identities','failure':'retained'}

def read(p):return json.loads(Path(p).read_text('utf-8'))
def save(p,value,*,replace=False):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    b=(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode()
    if p.exists() and not replace:
        if p.read_bytes()!=b:raise ValueError('immutable artifact differs '+str(p))
        return
    tmp=p.with_name(p.name+'.tmp-'+str(threading.get_ident()));tmp.write_bytes(b);os.replace(tmp,p)

def make_task(root,row,receptor,seed):
    sources=[]
    paths={'receptor':receptor['receptor'],'ligand':row['ligand'],'settings':str(root/'freeze.json')}
    for key,path in paths.items():
        sources.append({'id':key,'path':path,'locator':'frozen execution-only input; no reference pose/activity',
            'role':'conditioning','scope':QUESTION,**identity(path)})
    params={'receptor':paths['receptor'],'ligand':paths['ligand'],'box':receptor['box'],'seed':seed,'num_modes':10,'exhaustiveness':8}
    return {'schema':'etalon-pose-task/1','task_id':'snd1-scale-P2','case_id':row['id']+'-'+receptor['id']+'-'+str(seed),
      'question':QUESTION,'split':'development' if row['split']=='development' else 'validation',
      'method':{'engine':'GNINA1.3.3','empirical':'vina','candidate':'crossdock_default2018 same pool score_only'},'output_contract':OUTPUT,
      'input':{'schema':'etalon-scientific-input/1','task_id':'snd1-scale-P2','scope':QUESTION,
        'subject':{'molecule':row['state_smiles'],'parent_key':row['parent_key'],'series_id':row['series_id'],'cohort':row['split']},
        'sources':sources,'observations':[],'interpretations':[],'bindings':[
          {'id':key,'target':key,'value':v,'use':'constraint' if key in ('receptor','ligand','box') else 'selection',
           'basis_ids':[key if key in ('receptor','ligand') else 'settings'],'rationale':'predeclared frozen screening parameter'} for key,v in params.items()]}}

def execution_key(compiled,stage,freeze_pin):return digest({'compiled':compiled,'stage':stage,'freeze':freeze_pin})

def pool_identity(path):
    from rdkit import Chem
    import numpy as np
    result=[]
    for index,m in enumerate(Chem.SDMolSupplier(str(path),removeHs=False)):
        if m is None:raise ValueError('unreadable native pose '+str(index))
        heavy=Chem.RemoveHs(m)
        result.append((Chem.MolToSmiles(heavy,isomericSmiles=True),heavy.GetConformer().GetPositions()))
    if not result:raise ValueError('native pool has no poses')
    return result

def same_pool(before,after):
    import numpy as np
    a,b=pool_identity(before),pool_identity(after)
    if len(a)!=len(b):raise ValueError('CNN changed pool length')
    maximum=0.
    for (ga,xa),(gb,xb) in zip(a,b):
        if ga!=gb or xa.shape!=xb.shape:raise ValueError('CNN changed pose identity/order')
        maximum=max(maximum,float(np.abs(xa-xb).max()))
    if maximum>1.e-6:raise ValueError('CNN changed heavy-atom coordinates: '+str(maximum))
    return {'pose_count':len(a),'max_heavy_coordinate_delta_A':maximum,'same_graph_order_and_coordinates':True}

def pose_check_contract():
    from posebusters import PoseBusters
    import posebusters
    package=Path(posebusters.__file__).parent
    implementation={str(p):identity(p) for p in sorted(package.rglob('*'))
                    if p.is_file() and p.suffix in ('.py','.yml','.yaml','.json')}
    return freeze_check_contract(PoseBusters(config='dock').config,
        version=importlib.metadata.version('posebusters'),implementation=implementation)


def physical_check(pool,receptor,reference=None,*,check_contract=None,binding=None):
    from rdkit import Chem
    from posebusters import PoseBusters
    from etalon.research.pose_verifier import symmetry_rmsd
    import numpy as np
    check_contract=validate_check_contract(check_contract)
    if pose_check_contract()!=check_contract:raise ValueError('checker implementation/configuration changed')
    bust=PoseBusters(config=check_contract['configuration']);rows=[];mols=list(Chem.SDMolSupplier(str(pool),removeHs=False))
    reference_mol=Chem.SDMolSupplier(str(reference),removeHs=False)[0] if reference else None
    for index,mol in enumerate(mols):
        r={'pose_index':index,'physical_valid':None,'rmsd_A':None,'valid_and_RMSD_le_2A':None,'checks':{},'error':None}
        try:
            if mol is None:raise ValueError('unreadable pose')
            frame=bust.bust(mol_pred=mol,mol_cond=Path(receptor),full_report=False)
            if len(frame)!=1:raise ValueError('PoseBusters must return exactly one row per requested pose')
            values=frame.iloc[0].to_dict()
            if set(values)!=set(check_contract['required_checks']):raise ValueError('PoseBusters required check set mismatch')
            if any(not isinstance(v,(bool,np.bool_)) for v in values.values()):raise ValueError('missing or non-boolean PoseBusters checks')
            r['checks']={k:bool(v) for k,v in values.items()}
            r['physical_valid']=all(r['checks'].values())
            if reference_mol is not None:
                r['rmsd_A']=symmetry_rmsd(mol,reference_mol)
                r['valid_and_RMSD_le_2A']=r['physical_valid'] and r['rmsd_A']<=2.
        except Exception as exc:r['error']=type(exc).__name__+': '+str(exc)
        rows.append(r)
    return {'schema':'snd1-p2-pose-verification/2','posebusters_version':importlib.metadata.version('posebusters'),
       'binding':binding,'check_contract_digest':digest(check_contract),
       'expected_pose_slots':len(mols),'coverage_complete':bool(rows) and all(r['error'] is None and type(r['physical_valid']) is bool for r in rows),
       'pool':identity(pool),'receptor':identity(receptor),'reference':identity(reference) if reference else None,'rows':rows,
       'rmsd_scope':'own-receptor exact G crystal only; none for U or cross-receptor G',
       'physical_scope':'PoseBusters dock validity only; no affinity/activity truth or correctness inference for U'}

def top1_verification(verifier,rank,expected_pose_count,*,check_contract=None,binding=None):
    """Missing frozen requirements/identities cannot establish measured validity."""
    indices=rank.get('ordered_pose_indices',[]);index=indices[0] if indices else None
    result={'pose_index':index,'physical_valid':None,'rmsd_A':None,'valid_and_RMSD_le_2A':None}
    if check_contract is None or binding is None:return result
    return top1_observation(verifier,rank,expected_pose_count,check_contract=check_contract,expected_binding=binding)

def ranking(path,prop,reverse=False):
    from rdkit import Chem
    scores=[]
    for i,m in enumerate(Chem.SDMolSupplier(str(path),removeHs=False)):
        if m is not None and m.HasProp(prop):
            value=float(m.GetProp(prop))
            if __import__('math').isfinite(value):scores.append((value,i))
    scores.sort(reverse=reverse)
    return {'property':prop,'direction':'maximize' if reverse else 'minimize','ordered_pose_indices':[x[1] for x in scores],'scores':[x[0] for x in scores]}

def freeze(root,binary):
    panel=read(root/'panel.json')
    modules={}
    for name in ('etalon.research.pose_task','etalon.research.scientific_input','etalon.research.pose_engine',
                 'etalon.research.pose_verifier','etalon.research.result_integrity','molcascade.plugins.builtin.docking.common','posebusters'):
        module=importlib.import_module(name);modules[module.__file__]=identity(module.__file__)
    modules[__file__]=identity(__file__)
    runtime={'python':sys.version,'executable':sys.executable,'rdkit':importlib.metadata.version('rdkit'),
             'posebusters':importlib.metadata.version('posebusters'),'modules':modules}
    provenance=[root/'prepare_inputs.py',root/'organizer/recruitment-audit.json',root/'organizer/exclusions.json']
    provenance += [root/'organizer/source'/name for name in ('seeds.json','recruitment-plan.json','recruitment-amendment-01.json',
                    'recruitment-amendment-02.json','recruitment-cids-final.json')]
    value={'schema':'snd1-p2-frozen-protocol/2','panel':identity(root/'panel.json'),'check_contract':pose_check_contract(),
       'input_provenance':{str(p):identity(p) for p in provenance},'binary':{'path':str(binary),**identity(binary),
             'version':subprocess.check_output([str(binary),'--version'],text=True).strip()},'runtime':runtime,
       'gpu_physical_index':'1','gnina_visible_device':'0','cpu_threads_per_call':4,'parallel_native_cpu_calls':4,
       'limits':{'cpu_core_hour':384,'cnn_gpu_device_hour':24,'native_timeout_second':590},
       'parameters':{'seeds':[41,137,271],'num_modes':10,'exhaustiveness':8,'box_size_A':[24.,24.,24.],
                     'cnn':'crossdock_default2018','cnn_mode':'score_only','search_cnn_scoring':'none'},
       'policy':{'selection':'empirical','reason':'No adopted SND1 CNN benefit; retain empirical before scoring',
                 'all_alternatives':'compute CNN only as charged same-pool audit','visible':'graph,source identity,cohort,receptor pocket,predeclared descriptors',
                 'confirmation_gate':'parent P3 decisions must be frozen; --include-confirmation requires confirmation-release.json'},
       'evaluation':{'success':'G only physical valid AND receptor-frame symmetry RMSD<=2A','U':'physical checks and between-method differences only',
                     'comparison':'paired empirical/CNN on unchanged heavy-coordinate pool','failures':'retain all requested attempts and failed/N/A/censored slots',
                     'statistics_unit':'parent/series; seed repetitions correlated','selection_regret':'rank alternatives audited after selection; U correctness regret unknown'},
       'chemistry':'one explicit source state per normalized parent; no tautomer/protonation enumeration; SDF properties stripped',
       'scope':'seen G regression and source-similar U analogues; no validated large SND1 active/inactive set, affinity or independent-chemistry performance claim',
       'r4b':'paused','frozen_at_ns':time.time_ns()}
    save(root/'freeze.json',value)
    plans=[];confirmation=[]
    for row in panel['parents']:
        if row['preparation_status']!='ready':continue
        for rec in panel['receptors']:
            for seed in panel['seeds']:
                compiled=compile_task(make_task(root,row,rec,seed))
                common={'case_id':compiled['case_id'],'parent_id':row['id'],'parent_key':row['parent_key'],'series_id':row['series_id'],
                  'cohort':row['split'],'evidence_class':row['evidence_class'],'receptor_id':rec['id'],'seed':seed,
                  'empirical_request_id':execution_key(compiled,'empirical',identity(root/'freeze.json')),
                  'cnn_request_id':execution_key(compiled,'cnn',identity(root/'freeze.json'))}
                plans.append(common)
                if row['split']=='confirmation':confirmation.append({**common,'visible':{'state_smiles':row['state_smiles'],'formal_charge':row['formal_charge'],
                    'max_seed_morgan_similarity':row['max_seed_morgan_similarity'],'scaffold':row['scaffold'],'source_cid':row['cid'],
                    'pocket_context':rec['id'],'num_modes':10,'exhaustiveness':8},'evaluation_results_visible':False})
    save(root/'requests-manifest.json',{'schema':'snd1-p2-frozen-requests/1','rows':plans,'native_calls':2*len(plans),'freeze':identity(root/'freeze.json')})
    save(root/'confirmation-decision-input.json',{'schema':'snd1-p2-confirmation-decision-input/1','rows':confirmation,
        'restriction':'freeze P3 choices before any listed scientific execution; no truth/activity/reference coordinates in visible fields'})
    return {'parents':len(panel['parents']),'task_pairs':len(plans),'native_calls':2*len(plans),'splits':panel['split_counts'],
            'confirmation_tasks_held':len(confirmation),'freeze':identity(root/'freeze.json')}

class Batch:
    def __init__(self,root):
        self.root=root;self.panel=read(root/'panel.json');self.freeze=read(root/'freeze.json');self.freeze_pin=identity(root/'freeze.json')
        self.cnn_lock=threading.Lock();self.budget_lock=threading.Lock();self.stop=threading.Event();self.cost_cpu=0.;self.cost_gpu=0.;self.res_cpu=0.;self.res_gpu=0.
        for p in (root/'executions').glob('*/receipt.json'):
            r=read(p);self.cost_cpu+=r.get('cpu_budget_charge_second',0.);self.cost_gpu+=r.get('gpu_device_second',0.)
    def validate(self):
        if identity(self.root/'panel.json')!=self.freeze['panel']:raise ValueError('frozen panel changed')
        if pose_check_contract()!=self.freeze.get('check_contract'):raise ValueError('checker contract changed or missing; legacy runs require versioned reassessment')
        for path,pin in self.freeze['runtime']['modules'].items():
            if identity(path)!=pin:raise ValueError('implementation changed '+path)
        b=self.freeze['binary']
        if identity(b['path'])!={k:b[k] for k in ('sha256','bytes')}:raise ValueError('native binary changed')
        for row in self.panel['parents']:
            if row['preparation_status']=='ready' and identity(row['ligand'])!=row['ligand_identity']:raise ValueError('ligand changed')
        for r in self.panel['receptors']:
            if identity(r['receptor'])!=r['receptor_identity']:raise ValueError('receptor changed')
    def native(self,compiled,stage,pool,outfile):
        key=execution_key(compiled,stage,self.freeze_pin);folder=self.root/'executions'/key;folder.mkdir(parents=True,exist_ok=True)
        request={'request_id':key,'attempt_id':'trial-1','compiled':compiled,'stage':stage,'freeze':self.freeze_pin,
                 'pool':identity(pool) if stage=='cnn' else None,'execution_mode':'new_execution'}
        save(folder/'request.json',request)
        receipt=folder/'receipt.json'
        if receipt.exists():
            r=read(receipt)
            return validate_native_receipt(r,read(folder/'request.json'),compiled=compiled,stage=stage,
                protocol=self.freeze_pin,pool=request['pool'],output=outfile)
        if (folder/'started.json').exists():raise RuntimeError('ambiguous started request; no automatic retry '+key)
        cpu_res=2400.;gpu_res=600. if stage=='cnn' else 0.
        with self.budget_lock:
            if self.cost_cpu+self.res_cpu+cpu_res>384*3600 or self.cost_gpu+self.res_gpu+gpu_res>24*3600:
                self.stop.set();return {'status':'censored','request_id':key,'reason':'predeclared resource cap','execution_mode':'not_executed'}
            self.res_cpu+=cpu_res;self.res_gpu+=gpu_res
        args=pose_engine.command(self.freeze['binary']['path'],compiled['parameters'],outfile,stage=stage,pool=pool)
        command=['/usr/bin/timeout','--signal=TERM','--kill-after=8s','590s','/usr/bin/time','-o',str(folder/'native-resource.txt'),
                 '-f','{"user_second":%U,"system_second":%S,"elapsed_second":%e,"peak_rss_kb":%M,"exit_status":%x}']+args
        save(folder/'command.json',{'argv':command,'CUDA_VISIBLE_DEVICES':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1',
            'consumed_parameters':list(compiled['parameters']) if stage=='empirical' else ['receptor','pool','seed'],
            'deferred_parameters':[] if stage=='empirical' else ['original ligand coordinates','box','exhaustiveness','num_modes'],
            'cpu_threads':4,'device_binding':'CUDA_VISIBLE_DEVICES=1 then --device0; empirical --cnn_scoring none'})
        save(folder/'started.json',{'request_id':key,'attempt_id':'trial-1','pid':os.getpid(),'started_ns':time.time_ns()})
        start=time.perf_counter();err=None
        try:
            log=pose_engine.execute(command,directory=folder,gpu='1');(folder/'stdout.log').write_text(log,encoding='utf-8')
            if not outfile.exists():raise ValueError('missing native SDF')
            pool_identity(outfile)
            if stage=='cnn':save(folder/'same-pool.json',same_pool(pool,outfile))
        except Exception as exc:
            err={'type':type(exc).__name__,'message':str(exc),'context':getattr(exc,'context',None)}
            (folder/'stdout.log').write_text(json.dumps(err,default=str),encoding='utf-8')
        wall=time.perf_counter()-start;resource=None
        if (folder/'native-resource.txt').exists():
            try:resource=json.loads((folder/'native-resource.txt').read_text().splitlines()[-1])
            except (ValueError,IndexError):pass
        cpu=resource['user_second']+resource['system_second'] if resource else None
        r={'schema':'snd1-p2-execution-receipt/1','request_id':key,'attempt_id':'trial-1','stage':stage,'case_id':compiled['case_id'],
           'status':'failed' if err else 'completed','execution_mode':'new_execution','wall_seconds':wall,'native_resource':resource,
           'cpu_second':cpu,'cpu_budget_charge_second':max(4*wall,cpu or 0.),'cpu_budget_note':'conservative four-thread wall reservation; actual CPU separately measured',
           'gpu_device_second':wall if stage=='cnn' else 0.,'gpu_note':'exclusive native scoring wall on GPU1, distinct from device utilization',
           'error':err,'artifacts':{str(p):identity(p) for p in [outfile,folder/'stdout.log',folder/'native-resource.txt',folder/'same-pool.json'] if p.exists()},
           'actual_consumption':{'wall_second':wall,'cpu_second':cpu,'gpu_device_second':wall if stage=='cnn' else 0.}}
        save(receipt,r)
        with self.budget_lock:
            self.res_cpu-=cpu_res;self.res_gpu-=gpu_res;self.cost_cpu+=r['cpu_budget_charge_second'];self.cost_gpu+=r['gpu_device_second']
        return r
    def case(self,row,rec,seed):
        compiled=compile_task(make_task(self.root,row,rec,seed));folder=self.root/'outputs'/compiled['case_id'];folder.mkdir(parents=True,exist_ok=True)
        ref=rec['reference'] if row['full_inchikey']==rec['reference_inchikey'] else None
        if (folder/'result.json').exists():
            return self.cached_case(compiled,row,rec,ref,folder)
        choice={'case_id':compiled['case_id'],'selected':'empirical','frozen_before_cnn':True,'basis':self.freeze['policy'],'freeze':self.freeze_pin}
        save(folder/'choice.json',choice)
        empirical=self.native(compiled,'empirical',None,folder/'empirical.sdf')
        if empirical['status']!='completed':return {'case_id':compiled['case_id'],'empirical':empirical['status'],'cnn':'not_applicable'}
        with self.cnn_lock:cnn=self.native(compiled,'cnn',folder/'empirical.sdf',folder/'cnn.sdf')
        check=folder/'verifier.json'
        binding=result_binding(compiled=compiled,protocol=self.freeze_pin,pool=identity(folder/'empirical.sdf'),
            receptor=identity(rec['receptor']),reference=identity(ref) if ref else None,check_contract=self.freeze['check_contract'])
        if not check.exists():
            start=time.perf_counter()
            try:verifier=physical_check(folder/'empirical.sdf',rec['receptor'],ref,check_contract=self.freeze['check_contract'],binding=binding)
            except Exception as exc:verifier={'rows':[],'error':type(exc).__name__+': '+str(exc)}
            verifier['wall_seconds']=time.perf_counter()-start;save(check,verifier)
        verifier=read(check)
        ranks={'empirical':ranking(folder/'empirical.sdf','minimizedAffinity')}
        if cnn['status']=='completed':ranks['cnn']=ranking(folder/'cnn.sdf','CNNscore',True)
        result={'schema':'snd1-p2-result/2','binding':binding,'case_id':compiled['case_id'],'parent_id':row['id'],'parent_key':row['parent_key'],'series_id':row['series_id'],'split':row['split'],
           'evidence_class':row['evidence_class'],'receptor':rec['id'],'seed':seed,'stages':{'empirical':empirical['status'],'cnn':cnn['status']},
           'rankings':ranks,'verifier':{'path':str(check),**identity(check)},'top1':{},'correctness_unknown':ref is None}
        expected_pose_count=len(pool_identity(folder/'empirical.sdf'))
        for stage,rank in ranks.items():
            result['top1'][stage]=top1_verification(verifier,rank,expected_pose_count,check_contract=self.freeze['check_contract'],binding=binding)
        result['integrity']=validate_verifier(verifier,expected_pose_count=expected_pose_count,check_contract=self.freeze['check_contract'],expected_binding=binding)
        save(folder/'result.json',result);return result

    def cached_case(self,compiled,row,rec,ref,folder):
        """A cached result never causes a missing native attempt to be executed."""
        result=read(folder/'result.json');contract=self.freeze['check_contract']
        if (result.get('schema')!='snd1-p2-result/2' or not isinstance(result.get('stages'),dict)
                or set(result['stages'])!={'empirical','cnn'} or result['stages']['empirical']!='completed'
                or result.get('correctness_unknown') is not (ref is None)):
            raise ValueError('result cache contract/stage coverage mismatch')
        binding=result_binding(compiled=compiled,protocol=self.freeze_pin,pool=identity(folder/'empirical.sdf'),
            receptor=identity(rec['receptor']),reference=identity(ref) if ref else None,check_contract=contract)
        if result.get('binding')!=binding:raise ValueError('result cache identity mismatch; use versioned reassessment')
        for field,value in {'case_id':compiled['case_id'],'parent_id':row['id'],'parent_key':row['parent_key'],
            'series_id':row['series_id'],'split':row['split'],'evidence_class':row['evidence_class'],
            'receptor':rec['id'],'seed':compiled['parameters']['seed']}.items():
            if result.get(field)!=value:raise ValueError('result cache metadata mismatch: '+field)
        for stage,state in result['stages'].items():
            if state=='not_applicable':continue
            key=execution_key(compiled,stage,self.freeze_pin);execution=self.root/'executions'/key
            receipt=validate_native_receipt(read(execution/'receipt.json'),read(execution/'request.json'),compiled=compiled,
                stage=stage,protocol=self.freeze_pin,pool=binding['pool'] if stage=='cnn' else None,output=folder/(stage+'.sdf'))
            if receipt['status']!=state:raise ValueError('result cache stage state differs from original receipt')
        verifier_path=folder/'verifier.json'
        if result.get('verifier')!={'path':str(verifier_path),**identity(verifier_path)}:raise ValueError('result cache verifier identity mismatch')
        verifier=read(verifier_path);count=len(pool_identity(folder/'empirical.sdf'))
        ranks={'empirical':ranking(folder/'empirical.sdf','minimizedAffinity')}
        if result['stages'].get('cnn')=='completed':ranks['cnn']=ranking(folder/'cnn.sdf','CNNscore',True)
        tops={stage:top1_verification(verifier,rank,count,check_contract=contract,binding=binding) for stage,rank in ranks.items()}
        if result.get('rankings')!=ranks or result.get('top1')!=tops:raise ValueError('result cache derived observations mismatch')
        audit=validate_verifier(verifier,expected_pose_count=count,check_contract=contract,expected_binding=binding)
        if result.get('integrity')!=audit:raise ValueError('result cache integrity report mismatch')
        return result
    def run(self,limit=None,include_confirmation=False):
        self.validate()
        if include_confirmation:
            release=read(self.root/'confirmation-release.json')
            candidate=read(release['candidate_freeze']['path']);overlay=read(release['seen_series_overlay']['path'])
            if candidate.get('candidate_digest')!=digest(self.freeze):raise ValueError('confirmation candidate differs from the actual frozen implementation/protocol')
            for field in ('candidate_freeze','seen_series_overlay'):
                pin=release[field]
                if identity(pin['path'])!={k:pin[k] for k in ('sha256','bytes')}:raise ValueError('confirmation evidence identity changed: '+field)
        jobs=[]
        for row in self.panel['parents']:
            if row['preparation_status']!='ready':continue
            if row['split']=='confirmation' and not include_confirmation:continue
            if row['split']=='confirmation':
                eligibility=confirmation_eligibility(row,candidate_freeze=candidate,overlay=overlay,panel_identity=self.freeze['panel'])
                if not eligibility['eligible']:raise ValueError('confirmation eligibility rejected '+row['id']+': '+','.join(eligibility['reasons']))
            for rec in self.panel['receptors']:
                for seed in self.panel['seeds']:
                    case_id=row['id']+'-'+rec['id']+'-'+str(seed)
                    if (self.root/'outputs'/case_id/'result.json').exists():
                        self.case(row,rec,seed)
                    else:jobs.append((row,rec,seed))
        if limit:jobs=jobs[:limit]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pending={};iterator=iter(jobs)
            while True:
                while len(pending)<4 and not self.stop.is_set():
                    job=next(iterator,None)
                    if job is None:break
                    pending[pool.submit(self.case,*job)]=job
                if not pending:break
                done,_=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    job=pending.pop(future)
                    try:result=future.result();print(json.dumps({'event':'case_finished','case_id':result['case_id'],'stages':result.get('stages',result)}),flush=True)
                    except Exception as exc:
                        self.stop.set();save(self.root/'worker-errors'/('error-'+str(time.time_ns())+'.json'),{'job':[job[0]['id'],job[1]['id'],job[2]],'type':type(exc).__name__,'message':str(exc)})
                save(self.root/'progress.json',status(self.root),replace=True)
        return status(self.root)

def status(root):
    panel=read(root/'panel.json');receipts=[read(p) for p in (root/'executions').glob('*/receipt.json')]
    started=list((root/'executions').glob('*/started.json'));ambiguous=[str(p.parent) for p in started if not (p.parent/'receipt.json').exists()]
    results=[read(p) for p in (root/'outputs').glob('*/result.json')]
    stages=collections.Counter((r['stage']+':'+r['status']) for r in receipts)
    bysplit=collections.Counter(r['split'] for r in results)
    return {'schema':'snd1-p2-progress/1','parents':len(panel['parents']),'planned_pairs':len(panel['parents'])*6,
      'planned_native_calls':len(panel['parents'])*12,'completed_pair_results':len(results),'result_splits':dict(bysplit),
      'receipts':len(receipts),'stage_status':dict(stages),'started':len(started),'active_or_ambiguous_requests':ambiguous,
      'actual_native_wall_second':sum(r['wall_seconds'] for r in receipts),
      'actual_native_cpu_second':sum(r['cpu_second'] or 0 for r in receipts),'cpu_measurement_missing':sum(r['cpu_second'] is None for r in receipts),
      'cpu_conservative_budget_core_hour':sum(r['cpu_budget_charge_second'] for r in receipts)/3600,
      'cnn_gpu_device_hour':sum(r['gpu_device_second'] for r in receipts)/3600,
      'verifier_wall_second':sum(read(p).get('wall_seconds',0.) for p in (root/'outputs').glob('*/verifier.json')),
      'confirmation_held':not (root/'confirmation-release.json').exists(),'scope':panel['scope'],
      'resume':'CUDA_VISIBLE_DEVICES=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 '+sys.executable+' '+str(Path(__file__).absolute())+' run --root '+str(root)}

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['freeze','run','status']);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--binary',type=Path);p.add_argument('--limit-pairs',type=int);p.add_argument('--include-confirmation',action='store_true');a=p.parse_args();root=a.root.absolute()
    os.environ['CUDA_VISIBLE_DEVICES']='1';os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
    if a.action=='freeze':result=freeze(root,a.binary)
    elif a.action=='status':result=status(root)
    else:
        with _consumer_lock(root/'.p2-worker.lock'):result=Batch(root).run(a.limit_pairs,a.include_confirmation)
    print(json.dumps(result,sort_keys=True,indent=2),flush=True)

if __name__=='__main__':main()
