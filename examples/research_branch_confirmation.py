"""Optional finite-panel CNN branch confirmation over existing pose interfaces.

Owns only its explicit run directory. Scientific implementations and the native
receipt/cache behavior are reused from a pinned validate_pose_ranking snapshot.
This is an experiment, never a default router or deployment manager.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import zipfile

from etalon.active.schema import digest
from etalon.research.scientific_input import identity
from etalon.research.pose_task import compile_task
from etalon.research.result_integrity import result_binding, validate_verifier, top1_observation

TASK = 'branch-confirmation-20261025'
QUESTION = 'Can empirical near-tie gating preserve CNN pose quality with lower attributed CNN cost?'
METHOD = {'id':'empirical-gap-cnn-gate-v1','score_property':'minimizedAffinity',
          'gap_kcal_mol_max':0.5,'gate':'CNN iff at least two finite empirical scores and second-best minus best <=0.5',
          'single_pose':'empirical; no CNN policy request',
          'cnn_failure':'empirical fallback, CNN comparator unknown, adoption blocked',
          'information':'empirical score/order only before CNN; never reference/checker labels',
          'cnn':'crossdock_default2018 score_only rescore; original coordinates unchanged'}
REMOTE = Path('/home/zx/etalon/runs')
OLD = REMOTE/'etalon-research-closeout-20261025/ranking'
PRIOR = [REMOTE/'posebusters24-20261023/cases.json',REMOTE/'pose-selection24-20261023/cases.json',OLD/'panel.json']
ARCHIVE = REMOTE/'posebusters24-20261023/downloads/posebusters_paper_data.zip'
RAW = ARCHIVE.parent.parent/'organizer/raw'
SCOPE = ('Project-unseen structural/operational series from frozen PB308 plus Astex85 archive; '
         'known pocket, holo receptor, graph-regenerated ligand; no model-training independence, IID-family, affinity or activity claim')


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode('utf-8')
    if path.exists():
        if path.read_bytes()!=data:raise ValueError('immutable artifact differs: '+str(path))
        return
    tmp=path.with_name(path.name+'.tmp-'+str(threading.get_ident()))
    tmp.write_bytes(data);os.replace(tmp,path)


def load(name,path):
    import importlib.util
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


def modules(root):
    prior=load('branch_pinned_prior_driver',root/'source/validate_pose_ranking.py')
    helper=load('branch_pinned_native_helper',root/'source/snd1_scale_screen.py')
    return prior,helper


def gate_from_ranking(ranking):
    scores,indices=ranking.get('scores',[]),ranking.get('ordered_pose_indices',[])
    if not scores or len(scores)!=len(indices) or len(set(indices))!=len(indices):
        raise ValueError('complete nonempty empirical ranking required')
    if any(type(x) not in (int,float) or not math.isfinite(x) for x in scores):
        raise ValueError('finite empirical scores required')
    if scores!=sorted(scores):raise ValueError('ascending empirical scores required')
    gap=scores[1]-scores[0] if len(scores)>1 else None
    return {'method':METHOD,'gap_kcal_mol':gap,'request_cnn':gap is not None and gap<=METHOD['gap_kcal_mol_max'],
            'empirical_pose_index':indices[0],'missing_is_zero':False}


def design():
    return {'schema':'etalon-branch-confirmation-design/1','frozen_at_ns':time.time_ns(),'candidate':METHOD,
        'requested_count':24,'minimum_adoption_count':12,'selection_salt':'etalon-branch-independent-20261025-v1:',
        'source_order':'single SHA256(salt+caseID) order over PB308 and Astex85; no source outcome labels read',
        'selection':'exclude all historical accepted/rejected/inspected IDs plus additional-navigation 5SB2_1K2; all historical inspected graphs/chains excluded by exact nonempty Murcko scaffold, Morgan radius2/2048 Tanimoto>=0.4, or chain SequenceMatcher ratio>=0.4; unique among newly accepted; single organic 10-45 heavy atoms; graph ETKDGv3 seed20261025 and MMFF200 convergence required',
        'input_guard':'reject any target CCD ATOM/HETATM including H/D, preserve raw source, no silent cleaning; renamed remnants not certified absent',
        'shortage':'stop at24 or exhaust fixed source universe; all accepted are confirmation; exact exclusions retained; never replace after prediction or relax filters',
        'native':{'seed':41,'modes':10,'exhaustiveness':8,'box_A':[24.,24.,24.],'cpu_parallel':2,'cpu_threads':4,'GPU':1,'timeout_second':590},
        'roles':{'conditioning':'protein+graph-regenerated ligand+known pocket centroid only',
                 'gate':'empirical pool scores only; frozen before any CNN/checker/reference output',
                 'cnn':'same-pool independent evaluator comparator, all cases physically executed for paired audit',
                 'reference':'exact native ligand exclusively verifier after gate and candidate ranking frozen'},
        'primary':'strict22check physical-valid AND unrounded fixed-receptor-frame heavy-atom symmetry RMSD<=2A top1',
        'statistics':'all frozen accepted cases in denominator incl failed/unknown; paired wins/losses only when both bool, report unknown pairs; one-sided exact McNemar/binomial improvement p; Wilson95 intervals; one-sided exact95 loss probability upper bound; no IID or population5percent noninferiority claim',
        'adopt_requirements':{'complete_all':True,'minimum_count':12,'lost_CNN_success_max':0,
            'quality_gain_over_CNN_min':0,'quality_gain_over_empirical_min':0,'physical_valid_loss_vs_both_max':0,
            'measured_attributed_CNN_seconds_reduction_min':0.25,'policy_skipped_CNN_cases_min':3,
            'gate_cpu_fraction_of_generation_max':0.01},
        'adoption_scope':'All rules permit only finite-panel experimental adoption; never general default or claimed population noninferiority. Otherwise retain empirical/no_change. Statistical quality superiority is separately unestablished unless paired p<=0.05 and net gain>=3.',
        'cost':'full actual experiment cost includes all empirical,CNN,verification and failures; policy attribution selects measured already-paid CNN branches, not physical savings this experiment; skipped/unknown branch cost is never invented zero; actual branch cost and counterfactual policy attribution separated',
        'budget':{'maximum_native_calls':48,'search_core_hour_bound':16,'CNN_device_hour_bound':4,'automatic_retry':False},
        'seen_basis':'old24 empirical15 CNN16; fixed0.5gate predicted16, opportunities to skip high-gap cases; this is seen model-selection evidence only, target-H contamination in old8EX2 retained in caveat',
        'scope':SCOPE}


def prepare(root):
    from rdkit import Chem,DataStructs,RDLogger
    from rdkit.Chem import AllChem,rdFingerprintGenerator
    from rdkit.Chem.Scaffolds import MurckoScaffold
    RDLogger.DisableLog('rdApp.warning')
    prior,_=modules(root)
    frozen=read(root/'design-freeze.json') if (root/'design-freeze.json').exists() else design()
    save(root/'design-freeze.json',frozen)
    if (root/'panel.json').exists():return read(root/'panel.json')
    started=time.perf_counter()
    if identity(ARCHIVE)['sha256']!='495a8f432ee5612c0dfa3cc582829f112bfca3c29dddc2db2c3a8dc7609e721c':raise ValueError('source archive differs')
    fpgen=rdFingerprintGenerator.GetMorganGenerator(radius=2,fpSize=2048)
    fps,seqs,scaffolds,seen=[],[],set(),{'5SB2_1K2'}
    provenance={str(p):identity(p) for p in PRIOR}
    for p in PRIOR:
        panel=read(p)
        seen.update(r['id'] for r in panel['cases']+panel.get('rejected',[]))
    for cid in sorted(seen):
        folders=list(RAW.rglob(cid))
        if len(folders)!=1:continue
        for p in folders[0].glob('*protein*.pdb'):seqs.extend(prior.sequences(p))
        p=folders[0]/(cid+'_ligand.sdf')
        mol=Chem.SDMolSupplier(str(p),removeHs=True)[0] if p.exists() else None
        if mol is not None:
            fps.append(fpgen.GetFingerprint(mol));scaffolds.add(MurckoScaffold.MurckoScaffoldSmiles(mol=mol))
    snd=REMOTE/'snd1-scale-20261024/p2/panel.json';provenance[str(snd)]=identity(snd)
    sndpanel=read(snd)
    for r in sndpanel['receptors']:seqs.extend(prior.sequences(r['receptor']));seen.add(r['id'])
    for r in sndpanel.get('parents',[]):
        if not r.get('state_smiles'):continue
        m=Chem.MolFromSmiles(r['state_smiles'])
        if m is not None:fps.append(fpgen.GetFingerprint(m));scaffolds.add(MurckoScaffold.MurckoScaffoldSmiles(mol=m))
    with zipfile.ZipFile(ARCHIVE) as z:
        astex=z.read('astex_diverse_set_ids.txt').decode('ascii').split()
    pb=(ARCHIVE.parent/'final308.txt').read_text('ascii').split()
    datasets={x:'PB308' for x in pb};datasets.update({x:'Astex85' for x in astex})
    rows,rejected=[],[]
    for cid in sorted(datasets,key=lambda x:hashlib.sha256((frozen['selection_salt']+x).encode()).hexdigest()):
        reason=None
        if cid in seen:
            rejected.append({'id':cid,'dataset':datasets[cid],'reason':'historical accepted/inspected/rejected ID'});continue
        folders=list(RAW.rglob(cid))
        if len(folders)!=1:
            rejected.append({'id':cid,'dataset':datasets[cid],'reason':'missing/ambiguous raw folder'});continue
        folder=folders[0];ligand=folder/(cid+'_ligand.sdf');proteins=sorted(folder.glob('*protein*.pdb'))
        if not ligand.exists() or len(proteins)!=1:
            rejected.append({'id':cid,'dataset':datasets[cid],'reason':'missing/ambiguous raw files'});continue
        protein=proteins[0];ccd=cid.split('_',1)[1]
        try:guard=prior.guard_target_component_absence(protein,target_component_id=ccd)
        except ValueError as exc:
            rejected.append({'id':cid,'dataset':datasets[cid],'reason':'target CCD atoms including H/D: '+str(exc),'source':identity(protein)});continue
        mol=Chem.SDMolSupplier(str(ligand),removeHs=True)[0]
        if mol is None:
            rejected.append({'id':cid,'dataset':datasets[cid],'reason':'unreadable source graph'});continue
        smiles=Chem.MolToSmiles(mol,isomericSmiles=True);scaffold=MurckoScaffold.MurckoScaffoldSmiles(mol=mol)
        if '.' in smiles or not 10<=mol.GetNumHeavyAtoms()<=45 or any(a.GetAtomicNum() not in {1,6,7,8,9,15,16,17,35,53} for a in mol.GetAtoms()):reason='outside frozen single organic10-45HA scope'
        elif not scaffold or scaffold in scaffolds:reason='empty or historical/accepted duplicate scaffold'
        fp=fpgen.GetFingerprint(mol);fpsim=max((DataStructs.TanimotoSimilarity(fp,f) for f in fps),default=0.)
        if reason is None and fpsim>=0.4:reason='Morgan similarity>=0.4 historical/accepted series'
        seq=prior.sequences(protein)
        seqsim=max((difflib.SequenceMatcher(None,a,b,autojunk=False).ratio() for a in seq for b in seqs),default=1.) if reason is None else None
        if reason is None and seqsim>=0.4:reason='chain string similarity>=0.4 or missing chain'
        if reason:
            rejected.append({'id':cid,'dataset':datasets[cid],'reason':reason,'max_fp_similarity':fpsim,'max_sequence_ratio':seqsim});continue
        fresh=Chem.AddHs(Chem.MolFromSmiles(smiles));cfg=AllChem.ETKDGv3();cfg.randomSeed=20261025
        if AllChem.EmbedMolecule(fresh,cfg)!=0:
            rejected.append({'id':cid,'dataset':datasets[cid],'reason':'graph embedding failed before prediction'});continue
        status=AllChem.MMFFOptimizeMolecule(fresh,maxIters=200)
        if status!=0:
            rejected.append({'id':cid,'dataset':datasets[cid],'reason':'graph MMFF preparation nonconverged','status':status});continue
        center=[round(float(x),4) for x in mol.GetConformer().GetPositions().mean(axis=0)]
        conf=fresh.GetConformer();shift=center-conf.GetPositions().mean(axis=0)
        for i,pos in enumerate(conf.GetPositions()):conf.SetAtomPosition(i,tuple(float(x) for x in pos+shift))
        public=root/'inputs'/cid;gold=root/'evaluator'/cid;public.mkdir(parents=True,exist_ok=True);gold.mkdir(parents=True,exist_ok=True)
        shutil.copy2(protein,public/'receptor.pdb');shutil.copy2(ligand,gold/'reference.sdf')
        with Chem.SDWriter(str(public/'ligand.sdf')) as writer:writer.write(fresh)
        save(public/'settings.json',{'box':{'center':center,'size':[24.,24.,24.]},'seed':41,'num_modes':10,'exhaustiveness':8,'reference_role':'exact atomic coordinates evaluator-only; known centroid conditioning'})
        rows.append({'id':cid,'series_id':cid,'dataset':datasets[cid],'target_component_id':ccd,'smiles':smiles,'scaffold':scaffold,
            'heavy_atoms':mol.GetNumHeavyAtoms(),'max_fp_similarity':fpsim,'max_sequence_ratio':seqsim,'input_guard':guard,
            'chain_sequence_sha256':[hashlib.sha256(s.encode()).hexdigest() for s in seq],
            'sources':{str(p.relative_to(RAW)):identity(p) for p in (ligand,protein)},
            'inputs':{p.name:identity(p) for p in sorted(public.iterdir())},'reference_identity':identity(gold/'reference.sdf')})
        fps.append(fp);scaffolds.add(scaffold);seqs.extend(seq)
        if len(rows)==24:break
    panel={'schema':'etalon-branch-independent-panel/1','cases':rows,'rejected':rejected,'excluded_historical_ids':sorted(seen),
        'history':provenance,'source_archive':{'path':str(ARCHIVE),**identity(ARCHIVE)},'universe':{'PB308':len(pb),'Astex85':len(astex)},
        'design':identity(root/'design-freeze.json'),'requested_count':24,'frozen_count':len(rows),'all_source_ids_exhausted':len(rows)<24,
        'post_prediction_replacement':False,'preparation_wall_seconds':time.perf_counter()-started,'scope':SCOPE}
    save(root/'panel.json',panel)
    return {'count':len(rows),'rejected':len(rejected),'ids':[r['id'] for r in rows],'panel':identity(root/'panel.json')}


def freeze(root,binary):
    prior,helper=modules(root);panel=read(root/'panel.json')
    if not panel['cases']:raise ValueError('no eligible cases')
    for row in panel['cases']:prior.guard_target_component_absence(root/'inputs'/row['id']/'receptor.pdb',target_component_id=row['target_component_id'])
    contract=helper.pose_check_contract()
    if len(contract['required_checks'])!=22:raise ValueError('expected22checks')
    names=['etalon','etalon.research.pose_task','etalon.research.pose_engine','etalon.research.scientific_input','etalon.research.pose_verifier','etalon.research.result_integrity','molcascade.plugins.builtin.docking.common','rdkit','posebusters','numpy']
    imports={n:{'path':importlib.import_module(n).__file__,**identity(importlib.import_module(n).__file__)} for n in names}
    value={'schema':'etalon-branch-execution-freeze/1','frozen_at_ns':time.time_ns(),'design':identity(root/'design-freeze.json'),'panel':identity(root/'panel.json'),
        'candidate':METHOD,'check_contract':contract,'source':{str(p):identity(p) for folder in ('source','final-source') for p in sorted((root/folder).glob('*')) if p.is_file()},
        'pre_execution_amendment':identity(root/'pre-execution-amendment.json'),'final_protocol':identity(root/'PROTOCOL-v2.md'),
        'runtime':{'executable':sys.executable,'python':sys.version,'imports':imports,'versions':{n:importlib.metadata.version(n) for n in ('rdkit','posebusters','numpy')}},
        'binary':{'path':str(binary),**identity(binary),'version':subprocess.check_output([str(binary),'--version'],text=True).strip()},
        'preflight':identity(root/'preflight.json'),'no_predictions_exist':not bool(list((root/'outputs').glob('*/*')))}
    if not value['no_predictions_exist']:raise ValueError('freeze must precede predictions')
    save(root/'execution-freeze.json',value);return {'freeze':identity(root/'execution-freeze.json'),'count':len(panel['cases'])}


def task(root,row,prior):
    folder=root/'inputs'/row['id'];prior.guard_target_component_absence(folder/'receptor.pdb',target_component_id=row['target_component_id'])
    for name,pin in row['inputs'].items():
        if identity(folder/name)!=pin:raise ValueError('input changed')
    settings=read(folder/'settings.json')
    params={'receptor':str(folder/'receptor.pdb'),'ligand':str(folder/'ligand.sdf'),'box':settings['box'],'seed':41,'num_modes':10,'exhaustiveness':8}
    sources=[{'id':key,'path':str(folder/name),'locator':'frozen source/graph/known-centroid; no gold coordinates','role':'conditioning','scope':QUESTION,**identity(folder/name)} for key,name in [('receptor','receptor.pdb'),('ligand','ligand.sdf'),('settings','settings.json')]]
    return {'schema':'etalon-pose-task/1','task_id':TASK,'case_id':row['id'],'question':QUESTION,'split':'validation',
        'method':{'engine':'GNINA1.3.3','pool':'empirical vina','candidate':METHOD['id']},'output_contract':{'poses':'SDF','scores':'typed-with-direction','receipt':'request-and-file-identities','failure':'retained'},
        'input':{'schema':'etalon-scientific-input/1','task_id':TASK,'scope':QUESTION,'subject':{'molecule':row['smiles'],'series_id':row['series_id']},
            'sources':sources,'observations':[],'interpretations':[],
            'bindings':[{'id':k,'target':k,'value':v,'use':'constraint' if k in {'receptor','ligand','box'} else 'selection','basis_ids':[k if k in {'receptor','ligand'} else 'settings'],'rationale':'predeclared independent confirmation input'} for k,v in params.items()]}}


class Runner:
    def __init__(self,root):
        self.root=root;self.panel=read(root/'panel.json');self.freeze=read(root/'execution-freeze.json');self.pin=identity(root/'execution-freeze.json')
        self.prior,self.helper=modules(root);self.cnn_lock=threading.Lock()
        if self.freeze['candidate']!=METHOD or self.helper.pose_check_contract()!=self.freeze['check_contract']:raise ValueError('method/checker changed')
        for field in ('design','panel'):
            if identity(root/('design-freeze.json' if field=='design' else 'panel.json'))!=self.freeze[field]:raise ValueError('frozen '+field+' changed')
        for path,pin in self.freeze['source'].items():
            if identity(path)!=pin:raise ValueError('pinned source changed')
        for name,row in self.freeze['runtime']['imports'].items():
            if importlib.import_module(name).__file__!=row['path']:raise ValueError('actual import path changed')
            if identity(row['path'])!={k:row[k] for k in ('sha256','bytes')}:raise ValueError('pinned import changed')
        binary=self.freeze['binary']
        if identity(binary['path'])!={k:binary[k] for k in ('sha256','bytes')}:raise ValueError('native binary changed')

    def native(self,*args):
        return self.prior.Runner.native(self,*args)

    def case(self,row):
        compiled=compile_task(task(self.root,row,self.prior));folder=self.root/'outputs'/row['id'];folder.mkdir(parents=True,exist_ok=True)
        save(folder/'compiled.json',compiled)
        empirical=self.native(compiled,'empirical',None,folder/'empirical.sdf')
        if empirical['status']!='completed':
            result={'case_id':row['id'],'status':'native_generation_failed','stages':{'empirical':empirical['status'],'cnn':'not_executed'},'top1':{},'unknown':True}
            save(folder/'result.json',result);return result
        empirical_rank=self.helper.ranking(folder/'empirical.sdf','minimizedAffinity')
        if not (folder/'gate.json').exists():
            wall,cpu=time.perf_counter(),time.thread_time();gate=gate_from_ranking(empirical_rank)
            gate.update(wall_seconds=time.perf_counter()-wall,cpu_seconds=time.thread_time()-cpu,frozen_at_ns=time.time_ns(),pool=identity(folder/'empirical.sdf'),protocol=self.pin)
            save(folder/'gate.json',gate)
        gate=read(folder/'gate.json')
        if {k:gate[k] for k in gate_from_ranking(empirical_rank)}!=gate_from_ranking(empirical_rank):raise ValueError('cached gate differs')
        if gate['pool']!=identity(folder/'empirical.sdf') or gate['protocol']!=self.pin:raise ValueError('cached gate binding changed')
        with self.cnn_lock:cnn=self.native(compiled,'cnn',folder/'empirical.sdf',folder/'cnn.sdf')
        ranks={'empirical':empirical_rank}
        if cnn['status']=='completed':ranks['cnn']=self.helper.ranking(folder/'cnn.sdf','CNNscore',True)
        candidate=ranks['cnn'] if gate['request_cnn'] and 'cnn' in ranks else empirical_rank
        ranks['candidate']=candidate
        if not (folder/'candidate.json').exists():
            save(folder/'candidate.json',{'ranking':candidate,'used_cnn':gate['request_cnn'] and 'cnn' in ranks,'fallback':gate['request_cnn'] and 'cnn' not in ranks,
                'gate':identity(folder/'gate.json'),'frozen_at_ns':time.time_ns(),'verifier_not_yet_run':not (folder/'verifier.json').exists()})
        if read(folder/'candidate.json')['ranking']!=candidate:raise ValueError('cached candidate differs')
        reference=self.root/'evaluator'/row['id']/'reference.sdf'
        if identity(reference)!=row['reference_identity']:raise ValueError('reference changed')
        binding=result_binding(compiled=compiled,protocol=self.pin,pool=identity(folder/'empirical.sdf'),receptor=compiled['files']['receptor'],reference=identity(reference),check_contract=self.freeze['check_contract'])
        count=len(self.helper.pool_identity(folder/'empirical.sdf'))
        if not (folder/'verifier.json').exists():
            wall,cpu=time.perf_counter(),time.thread_time()
            verifier=self.helper.physical_check(folder/'empirical.sdf',compiled['parameters']['receptor'],reference,check_contract=self.freeze['check_contract'],binding=binding)
            verifier.update(wall_seconds=time.perf_counter()-wall,cpu_seconds=time.thread_time()-cpu,started_after_candidate_ns=read(folder/'candidate.json')['frozen_at_ns'])
            save(folder/'verifier.json',verifier)
        verifier=read(folder/'verifier.json');integrity=validate_verifier(verifier,expected_pose_count=count,check_contract=self.freeze['check_contract'],expected_binding=binding)
        result={'schema':'etalon-branch-case-result/1','case_id':row['id'],'dataset':row['dataset'],'status':'completed','stages':{'empirical':empirical['status'],'cnn':cnn['status']},
            'binding':binding,'integrity':integrity,'gate':gate,'rankings':ranks,
            'top1':{m:top1_observation(verifier,r,count,check_contract=self.freeze['check_contract'],expected_binding=binding) for m,r in ranks.items()},
            'verifier':{'path':str(folder/'verifier.json'),**identity(folder/'verifier.json')},
            'pool_any_success':any(r['valid_and_RMSD_le_2A'] for r in verifier['rows']) if integrity['status']=='complete' else None}
        save(folder/'result.json',result)
        print(json.dumps({'case_id':row['id'],'top1':result['top1'],'request_cnn':gate['request_cnn']}),flush=True)
        return result

    def run(self):
        if not (self.root/'execution-start.json').exists():save(self.root/'execution-start.json',{'started_at_ns':time.time_ns(),'freeze':self.pin,'pid':os.getpid(),'count':len(self.panel['cases']),'maximum_native_calls':2*len(self.panel['cases'])})
        elif read(self.root/'execution-start.json')['freeze']!=self.pin:raise ValueError('execution binds another freeze')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for future in concurrent.futures.as_completed([pool.submit(self.case,row) for row in self.panel['cases']]):future.result()
        return evaluate(self.root)


def paired(results,other):
    win=loss=unknown=0
    for r in results:
        a=r.get('top1',{}).get('candidate',{}).get('valid_and_RMSD_le_2A');b=r.get('top1',{}).get(other,{}).get('valid_and_RMSD_le_2A')
        if type(a) is not bool or type(b) is not bool:unknown+=1;continue
        win+=a and not b;loss+=b and not a
    n=win+loss
    return {'wins':win,'losses':loss,'unknown_pairs':unknown,'net_gain':win-loss,
            'observed_pairs':len(results)-unknown,'inference_scope':'observed boolean pairs only; unknown cases are not losses or evidence of noninferiority',
            'one_sided_exact_improvement_p':sum(math.comb(n,k) for k in range(win,n+1))/2**n if n else 1.}


def success_metrics(tops,denominator):
    """Summarize observed outcomes without turning a missing endpoint into failure."""
    if denominator<=0 or len(tops)!=denominator:raise ValueError('one endpoint row per requested case required')
    n=denominator;s=sum(t.get('valid_and_RMSD_le_2A') is True for t in tops)
    unknown=sum(type(t.get('valid_and_RMSD_le_2A')) is not bool for t in tops);interval=None
    if not unknown:
        z=1.959963984540054;p=s/n;den=1+z*z/n;mid=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
        interval=[mid-half,mid+half]
    return {'top1_success':s,'denominator':n,'physical_valid_top1':sum(t.get('physical_valid') is True for t in tops),
        'known_success':s,'unknown_top1':unknown,'success_fraction_identification_bounds':[s/n,(s+unknown)/n],
        'success_count_identification_bounds':[s,s+unknown],'wilson95_descriptive':interval,
        'uncertainty_scope':'Unknown outcomes yield identification bounds, not binomial confidence intervals; complete-panel Wilson remains descriptive without certified IID.'}


def evaluate(root,*,output_path=None):
    panel=read(root/'panel.json');n=len(panel['cases'])
    rows=[read(root/'outputs'/r['id']/'result.json') if (root/'outputs'/r['id']/'result.json').exists() else {'case_id':r['id'],'status':'not_executed','top1':{}} for r in panel['cases']]
    metrics={m:success_metrics([r.get('top1',{}).get(m,{}) for r in rows],n) for m in ('empirical','cnn','candidate')}
    comparisons={m:paired(rows,m) for m in ('empirical','cnn')}
    receipts=[read(p) for p in (root/'receipts').glob('*/receipt.json')]
    cnn={r['case_id']:r for r in receipts if r['stage']=='cnn'}
    gates={r['case_id']:r.get('gate') for r in rows}
    cnn_full=sum(r['wall_seconds'] for r in cnn.values());cnn_selected=sum(r['wall_seconds'] for cid,r in cnn.items() if (gates.get(cid) or {}).get('request_cnn'))
    gen_cpu=sum(r['cpu_second'] for r in receipts if r['stage']=='empirical' and r['cpu_second'] is not None)
    gate_cpu=sum(g['cpu_seconds'] for g in gates.values() if g);skipped=[cid for cid,g in gates.items() if g and not g['request_cnn']]
    missed=[r['case_id'] for r in rows if not r.get('gate',{}).get('request_cnn',True) and r.get('top1',{}).get('cnn',{}).get('valid_and_RMSD_le_2A') is True and r.get('top1',{}).get('empirical',{}).get('valid_and_RMSD_le_2A') is False]
    costs={'actual_native_receipt_count':len(receipts),'actual_native_wall_sum_seconds':sum(r['wall_seconds'] for r in receipts),
        'actual_generation_cpu_seconds':gen_cpu,'actual_CNN_device_seconds':cnn_full,'actual_gate_cpu_seconds':gate_cpu,
        'actual_verification_wall_sum_seconds':sum(read(p)['wall_seconds'] for p in (root/'outputs').glob('*/verifier.json')),
        'actual_verification_thread_cpu_seconds':sum(read(p)['cpu_seconds'] for p in (root/'outputs').glob('*/verifier.json')),
        'actual_preparation_wall_seconds':panel['preparation_wall_seconds'],'missing_native_cpu_receipts':sum(r['cpu_second'] is None for r in receipts),
        'attribution_only_candidate_CNN_seconds':cnn_selected,'attribution_only_CNN_seconds_difference':cnn_full-cnn_selected,
        'attribution_only_CNN_fraction_reduction':1-cnn_selected/cnn_full if cnn_full else None,
        'gate_cpu_fraction_generation':gate_cpu/gen_cpu if gen_cpu else None,
        'policy_skipped_CNN_cases':skipped,'physical_CNN_calls_avoided_by_this_audit':0,
        'limitations':'All paired-control CNN branches physically paid. Selected-path accounting is a counterfactual attribution using measured branch times, not this experiment saving. Thread CPU verifier omits possible worker subprocess CPU; development/archive/system overhead unknown; wall sums are not elapsed makespan.'}
    complete=all(r.get('integrity',{}).get('status')=='complete' for r in rows)
    physical_regressions=[r['case_id'] for r in rows if r.get('top1',{}).get('candidate',{}).get('physical_valid') is False and any(r.get('top1',{}).get(m,{}).get('physical_valid') is True for m in ('empirical','cnn'))]
    checks={'complete_all':complete and all(metrics[m]['unknown_top1']==0 for m in metrics) and len(receipts)==2*n and all(r['status']=='completed' for r in receipts) and costs['missing_native_cpu_receipts']==0,
        'minimum_count':n>=12,'zero_lost_CNN_success':comparisons['cnn']['losses']==0,'quality_at_least_CNN':comparisons['cnn']['net_gain']>=0,
        'quality_at_least_empirical':comparisons['empirical']['net_gain']>=0,'physical_valid_retention':not physical_regressions,
        'attributed_CNN_reduction':costs['attribution_only_CNN_fraction_reduction'] is not None and costs['attribution_only_CNN_fraction_reduction']>=0.25,
        'three_skipped_cases':len(skipped)>=3,'gate_CPU':costs['gate_cpu_fraction_generation'] is not None and costs['gate_cpu_fraction_generation']<=0.01}
    strata={}
    for dataset in sorted({r['dataset'] for r in panel['cases']}):
        ids={r['id'] for r in panel['cases'] if r['dataset']==dataset};subset=[r for r in rows if r['case_id'] in ids]
        strata[dataset]={'denominator':len(subset),'top1_success':{m:sum(r.get('top1',{}).get(m,{}).get('valid_and_RMSD_le_2A') is True for r in subset) for m in metrics},
                        'paired':{m:paired(subset,m) for m in ('empirical','cnn')},'policy_CNN_requests':sum(bool(r.get('gate',{}).get('request_cnn')) for r in subset)}
    value={'schema':'etalon-branch-evaluation/1','denominator':n,'requested_target':24,'freeze':identity(root/'execution-freeze.json'),'panel':identity(root/'panel.json'),
        'metrics':metrics,'paired':comparisons,'costs':costs,'missed_CNN_rescues_outside_gate':missed,
        'physical_valid_regressions_vs_either':physical_regressions,
        'source_strata':strata,
        'zero_loss_one_sided_95_upper_if_zero':1-0.05**(1/n) if comparisons['cnn']['losses']==0 and comparisons['cnn']['unknown_pairs']==0 else None,
        'adoption_checks':checks,'decision':'adopt_finite_panel_experimental_only' if all(checks.values()) else 'retain','default_change':False,
        'quality_superiority':comparisons['empirical']['net_gain']>=3 and comparisons['empirical']['one_sided_exact_improvement_p']<=0.05 and comparisons['empirical']['unknown_pairs']==0,
        'pool_any_success':sum(r.get('pool_any_success') is True for r in rows),'cases':[{'case_id':r['case_id'],'dataset':r.get('dataset'),'status':r['status'],'top1':r.get('top1',{}),'request_cnn':r.get('gate',{}).get('request_cnn')} for r in rows],
        'scope':SCOPE,'reporting_version':'missing-aware-confirmation-report/2',
        'statistical_limit':'Small filtered finite panel; IID not certified. With unknown cases success bounds replace Wilson intervals, paired p uses observed pairs only, and no full-panel superiority/noninferiority claim follows.'}
    destination=Path(output_path) if output_path is not None else root/'evaluation.json'
    if destination!=root/'evaluation.json':
        value['reporting_reassessment']={'original_evaluation':identity(root/'evaluation.json'),'reporting_implementation':{'path':__file__,**identity(__file__)},
            'candidate_parameters_changed':False,'native_calls':0,'original_evaluation_overwritten':False,'reason':'Missing-outcome reporting correction only; original outcomes, costs, fixed adoption checks and retain decision preserved'}
    save(destination,value);return value


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','freeze','run','evaluate']);p.add_argument('--root',type=Path,required=True);p.add_argument('--binary',type=Path)
    args=p.parse_args();root=args.root.absolute()
    os.environ.update(CUDA_VISIBLE_DEVICES='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    result={'prepare':lambda:prepare(root),'freeze':lambda:freeze(root,args.binary),'run':lambda:Runner(root).run(),'evaluate':lambda:evaluate(root)}[args.action]()
    print(json.dumps(result,sort_keys=True,indent=2),flush=True)


if __name__=='__main__':main()
