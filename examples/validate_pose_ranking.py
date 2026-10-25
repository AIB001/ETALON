"""Bounded organizer and same-pool ranking validation; never changes deployment.

Run in an isolated task environment with pinned ETALON and scientific packages.
The task-local helper is a byte-identical snapshot of snd1_scale_screen.py.
No result is imported into research memory automatically; the controller owns it.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from etalon.active.schema import digest
from etalon.research.scientific_input import identity
from etalon.research.pose_task import compile_task
from etalon.research import pose_engine
from etalon.research.result_integrity import result_binding, validate_verifier, validate_native_receipt, top1_observation


QUESTION = "Independent known-pocket same-pool contact/relaxation pose ranking"
REMOTE = Path('/home/zx/etalon/runs')
PRIOR = [REMOTE/'posebusters24-20261023', REMOTE/'pose-selection24-20261023']
RELEASE = Path('/home/zx/etalon/releases/20261024-integrity-eca6c9be84ac')
INPUT_GUARD_VERSION = 'target-component-residue-absence/1'


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)+'\n').encode('utf-8')
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('immutable artifact differs: '+str(path))
        return
    tmp = path.with_name(path.name+'.tmp-'+str(threading.get_ident()))
    tmp.write_bytes(data)
    os.replace(tmp, path)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def modules(root):
    ranking = load_module('etalon.research.pose_ranking', root/'source/pose_ranking.py')
    helper = load_module('ranking_native_helper', root/'source/snd1_scale_screen.py')
    return ranking, helper


def guard_target_component_absence(receptor_pdb, *, target_component_id):
    """Reject any explicitly target-named residue atom, including hydrogen.

    This is a thin input preflight. It never edits a source or infers that an
    arbitrary renamed atom is absent. The target CCD identity must be supplied
    explicitly from the selected structural case, not guessed from coordinates.
    """
    target = str(target_component_id).strip().upper()
    if not target or len(target) > 3 or not target.isascii() or not target.isalnum():
        raise ValueError('explicit PDB target component ID required')
    path = Path(receptor_pdb)
    residual = [{'record':line[:6].strip(), 'serial':line[6:11].strip(),
                 'atom':line[12:16].strip(), 'element':line[76:78].strip()}
                for line in path.read_text('ascii').splitlines()
                if line[:6] in {'ATOM  ','HETATM'} and line[17:20].strip().upper() == target]
    if residual:
        raise ValueError('target component '+target+' atoms remain in receptor (including H/D): '+json.dumps(residual,sort_keys=True))
    return {'schema':INPUT_GUARD_VERSION,'target_component_id':target,'target_named_atom_count':0,
            'receptor':identity(path),'scope':'exact target residue identity; renamed/misnamed remnants are not certified absent'}


def sequences(path):
    codes = dict(zip('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL'.split(), 'ARNDCQEGHILKMFPSTWYV'))
    codes.update(HID='H', HIE='H', HIP='H')
    chains, seen = {}, set()
    for line in Path(path).read_text('ascii').splitlines():
        if line[:6] != 'ATOM  ' or line[12:16].strip() != 'CA' or line[16:17] not in {' ', 'A'}:
            continue
        key = (line[21:22], line[22:27])
        if key in seen:
            continue
        seen.add(key)
        chains.setdefault(key[0], []).append(codes.get(line[17:20], 'X'))
    return [''.join(v) for v in chains.values() if len(v) >= 30]


def prepare(root):
    """Freeze recruitment first; inspect graph/conditioning only, never results."""
    from rdkit import Chem, DataStructs
    from rdkit.Chem import AllChem, rdFingerprintGenerator
    from rdkit.Chem.Scaffolds import MurckoScaffold
    ranking, _ = modules(root)
    design = {'schema': 'etalon-ranking-design-freeze/1', 'frozen_at_ns': time.time_ns(),
        'candidate': ranking.METHOD, 'independent_case_count': 24, 'seed': 41,
        'selection_salt': 'etalon-ranking-confirmation-20261025-v1:',
        'selection': 'fixed SHA256 order of PB308 IDs; exclude both earlier 24-case panels and their inspected/rejected IDs, SND1 IDs; single organic ligand 10-45 heavy atoms; unique nonempty Murcko scaffold; Morgan radius2/2048 Tanimoto <0.4; max chain SequenceMatcher ratio <0.4 against all prior and accepted new cases; graph-only ETKDGv3/MMFF preparation must succeed',
        'independence_limit': 'project-unseen structure/operational series; sequence-string heuristic does not certify remote homology or model-training absence',
        'preparation': {'ETKDGv3_seed': 20261025, 'MMFF_max_iterations': 200, 'box_A': [24., 24., 24.]},
        'input_preflight': {'version':INPUT_GUARD_VERSION,'rule':'reject any ATOM/HETATM with target CCD residue identity, including H/D; preserve raw source; no silent cleanup'},
        'native': {'seed': 41, 'num_modes': 10, 'exhaustiveness': 8, 'cpu_threads': 4, 'cpu_parallel_cases': 2,
                   'cnn': 'crossdock_default2018', 'cnn_mode': 'score_only rescore', 'gpu': 1, 'timeout_second': 590},
        'cost_cap': {'native_calls': 48, 'generation_core_hour_bound': 16, 'cnn_gpu_device_hour_bound': 4, 'no_automatic_retries': True},
        'roles': {'conditioning': 'protein + graph-regenerated ligand + released pocket centroid; no crystal ligand atomic coordinates',
                  'ranking': 'same native empirical pool and receptor only, before CNN and verifier',
                  'evaluator': 'exact corresponding crystal ligand, fixed receptor frame, heavy-atom symmetry RMSD',
                  'all_24': 'independent_confirmation; failures retained in denominator; no development subset'},
        'primary': 'Top1 physical-valid AND unrounded symmetry RMSD <=2A, every requested case in denominator',
        'secondary': ['pool any-success', 'top2/top3 success', 'physical-valid top1', 'paired wins/losses', 'failures/unknowns', 'runtime/resource'],
        'adopt_requirements': {'complete_cases': 24, 'net_top1_gain_over_empirical_min': 3, 'lost_baseline_successes_max': 1,
            'paired_one_sided_exact_binomial_p_max': 0.05, 'top1_count_at_least_CNN': True,
            'physical_valid_top1_count_at_least_empirical': True, 'candidate_cpu_fraction_of_search_max': 0.15,
            'candidate_mean_wall_second_max': 5.0},
        'decision': 'adopt only if every requirement passes; otherwise retain empirical / no_change; no tuning or replacements after prediction',
        'scope': 'Retrospective known-pocket redocking ranking; no affinity, activity, unbiased model-generalization or prospective blind docking claim'}
    save(root/'design-freeze.json', design)
    start = time.perf_counter()
    archive = PRIOR[0]/'downloads/posebusters_paper_data.zip'
    idsfile = PRIOR[0]/'downloads/final308.txt'
    if identity(archive)['sha256'] != '495a8f432ee5612c0dfa3cc582829f112bfca3c29dddc2db2c3a8dc7609e721c':
        raise ValueError('source archive differs')
    raw = PRIOR[0]/'organizer/raw'
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    fps, seqs, scaffolds, seenids = [], [], set(), set()
    provenance = {}
    for previous in PRIOR:
        panel = read(previous/'cases.json')
        provenance[str(previous/'cases.json')] = identity(previous/'cases.json')
        seenids.update(r['id'] for r in panel.get('rejected', []))
        for row in panel['cases']:
            seenids.add(row['id'])
            mol = Chem.MolFromSmiles(row['smiles'])
            fps.append(generator.GetFingerprint(mol))
            scaffolds.add(MurckoScaffold.MurckoScaffoldSmiles(mol=mol))
            seqs.extend(sequences(previous/'inputs'/row['id']/'receptor.pdb'))
        for row in panel.get('rejected', []):
            # Inspected historical graphs/series stay seen even when not docked.
            matches = list(raw.rglob(row['id']))
            if len(matches) != 1:
                continue
            for protein in matches[0].glob('*protein*.pdb'):
                seqs.extend(sequences(protein))
            ligand = matches[0]/(row['id']+'_ligand.sdf')
            if ligand.exists():
                m = Chem.SDMolSupplier(str(ligand), removeHs=True)[0]
                if m is not None:
                    fps.append(generator.GetFingerprint(m))
                    scaffolds.add(MurckoScaffold.MurckoScaffoldSmiles(mol=m))
    snd = REMOTE/'snd1-scale-20261024/p2/panel.json'
    sndpanel = read(snd)
    provenance[str(snd)] = identity(snd)
    for receptor in sndpanel['receptors']:
        seqs.extend(sequences(receptor['receptor']))
        seenids.add(receptor['id'])
    # SND1 chemical lineage is excluded even though PB308 does not contain these exact parents.
    snd_parents = sndpanel.get('parents', [])
    for row in snd_parents:
        smi = row.get('state_smiles')
        if smi:
            m = Chem.MolFromSmiles(smi)
            if m is not None:
                fps.append(generator.GetFingerprint(m))
                scaffolds.add(MurckoScaffold.MurckoScaffoldSmiles(mol=m))
    rows, rejected = [], []
    ids = idsfile.read_text('ascii').split()
    for cid in sorted(ids, key=lambda x: hashlib.sha256((design['selection_salt']+x).encode()).hexdigest()):
        reason = None
        if cid in seenids or cid.startswith(('7KNW_', '7KNX_')):
            rejected.append({'id': cid, 'reason': 'historical seen/inspected ID'})
            continue
        folders = list(raw.rglob(cid))
        if len(folders) != 1:
            rejected.append({'id': cid, 'reason': 'missing/ambiguous raw directory'})
            continue
        folder = folders[0]
        ligand = folder/(cid+'_ligand.sdf')
        proteins = sorted(folder.glob('*protein*.pdb'))
        if not ligand.exists() or len(proteins) != 1:
            rejected.append({'id': cid, 'reason': 'missing/ambiguous raw input'})
            continue
        protein = proteins[0]
        target_component_id = cid.split('_',1)[1]
        try:
            guard_target_component_absence(protein,target_component_id=target_component_id)
        except ValueError as exc:
            rejected.append({'id':cid,'reason':'target component input preflight rejected: '+str(exc),
                             'retained_source':{'path':str(protein),**identity(protein)}})
            continue
        mol = Chem.SDMolSupplier(str(ligand), removeHs=True)[0]
        if mol is None:
            rejected.append({'id': cid, 'reason': 'unreadable graph'})
            continue
        smiles = Chem.MolToSmiles(mol, isomericSmiles=True)
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol)
        if '.' in smiles or not 10 <= mol.GetNumHeavyAtoms() <= 45 or any(a.GetAtomicNum() not in {1,6,7,8,9,15,16,17,35,53} for a in mol.GetAtoms()):
            reason = 'outside frozen noncovalent single organic 10-45 heavy-atom scope'
        elif not scaffold or scaffold in scaffolds:
            reason = 'empty or duplicate scaffold'
        fp = generator.GetFingerprint(mol)
        fpsim = max((DataStructs.TanimotoSimilarity(fp, f) for f in fps), default=0.)
        if reason is None and fpsim >= .4:
            reason = 'Morgan similarity >=0.4 to historical or accepted series'
        currentseqs = sequences(protein)
        seqsim = max((difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() for a in currentseqs for b in seqs), default=1.) if reason is None else None
        if reason is None and seqsim >= .4:
            reason = 'chain sequence-string ratio >=0.4 or missing sequence'
        if reason:
            rejected.append({'id': cid, 'reason': reason, 'max_fingerprint_similarity': fpsim, 'max_sequence_ratio': seqsim})
            continue
        center = [round(float(x), 4) for x in mol.GetConformer().GetPositions().mean(axis=0)]
        fresh = Chem.AddHs(Chem.MolFromSmiles(smiles))
        cfg = AllChem.ETKDGv3()
        cfg.randomSeed = 20261025
        if AllChem.EmbedMolecule(fresh, cfg) != 0:
            rejected.append({'id': cid, 'reason': 'pre-prediction graph embedding failed'})
            continue
        prep_status = AllChem.MMFFOptimizeMolecule(fresh, maxIters=200)
        if prep_status != 0:
            rejected.append({'id': cid, 'reason': 'pre-prediction MMFF preparation failed/nonconverged', 'status': prep_status})
            continue
        conf = fresh.GetConformer()
        shift = center-conf.GetPositions().mean(axis=0)
        for i, pos in enumerate(conf.GetPositions()):
            conf.SetAtomPosition(i, tuple(float(x) for x in pos+shift))
        public, gold = root/'inputs'/cid, root/'evaluator'/cid
        public.mkdir(parents=True, exist_ok=True)
        gold.mkdir(parents=True, exist_ok=True)
        shutil.copy2(protein, public/'receptor.pdb')
        shutil.copy2(ligand, gold/'reference.sdf')
        with Chem.SDWriter(str(public/'ligand.sdf')) as writer:
            writer.write(fresh)
        save(public/'settings.json', {'box': {'center': center, 'size': [24.,24.,24.]}, 'seed':41, 'num_modes':10, 'exhaustiveness':8,
            'pocket_policy': 'Organizer releases known-pocket centroid only; graph regenerated; gold atomic coordinates evaluator-only'})
        rows.append({'id': cid, 'series_id': cid, 'target_component_id':target_component_id,'split':'validation', 'smiles':smiles, 'scaffold':scaffold,
            'heavy_atoms':mol.GetNumHeavyAtoms(), 'max_prior_fingerprint_similarity':fpsim, 'max_prior_sequence_string_ratio':seqsim,
            'chain_sequence_sha256':[hashlib.sha256(s.encode()).hexdigest() for s in currentseqs],
            'source_members': {str(p.relative_to(raw)): identity(p) for p in (ligand, protein)},
            'inputs':{p.name:identity(p) for p in sorted(public.iterdir())}, 'reference_identity':identity(gold/'reference.sdf')})
        fps.append(fp)
        scaffolds.add(scaffold)
        seqs.extend(currentseqs)
        if len(rows) == 24:
            break
    panel = {'schema':'etalon-independent-ranking-panel/1', 'design':identity(root/'design-freeze.json'), 'cases':rows, 'rejected':rejected,
        'historical_panel_identities':provenance, 'excluded_historical_ids':sorted(seenids),
        'sources':{'archive':{'path':str(archive), **identity(archive)}, 'pb308':{'path':str(idsfile), **identity(idsfile)},
            'data_url':'https://zenodo.org/records/8278563', 'repository_url':'https://github.com/maabuu/posebusters'},
        'requested_denominator':24, 'available_eligible':len(rows), 'post_prediction_replacements_permitted':False,
        'preparation_wall_seconds':time.perf_counter()-start}
    save(root/'panel.json', panel)
    return {'available_eligible':len(rows), 'ids':[r['id'] for r in rows], 'rejected':len(rejected), 'panel':identity(root/'panel.json')}


def freeze(root, binary):
    ranking, helper = modules(root)
    panel = read(root/'panel.json')
    if len(panel['cases']) != 24:
        raise ValueError('fewer than 24 eligible independent cases; do not execute or silently loosen filter')
    for row in panel['cases']:
        guard_target_component_absence(root/'inputs'/row['id']/'receptor.pdb',target_component_id=row.get('target_component_id') or row['id'].split('_',1)[1])
    contract = helper.pose_check_contract()
    if len(contract['required_checks']) != 22:
        raise ValueError('expected 22 required PoseBusters checks')
    names = ['etalon','etalon.research.pose_task','etalon.research.pose_engine','etalon.research.scientific_input',
        'etalon.research.pose_verifier','etalon.research.result_integrity','molcascade.plugins.builtin.docking.common','rdkit','posebusters','numpy']
    imports = {name: {'path': importlib.import_module(name).__file__, **identity(importlib.import_module(name).__file__)} for name in names}
    value = {'schema':'etalon-ranking-execution-freeze/1', 'frozen_at_ns':time.time_ns(), 'design':identity(root/'design-freeze.json'),
        'panel':identity(root/'panel.json'), 'candidate':ranking.METHOD, 'check_contract':contract,
        'source':{str(p):identity(p) for p in sorted((root/'source').rglob('*')) if p.is_file() and p.suffix in {'.py','.md'}},
        'runtime':{'executable':sys.executable, 'python':sys.version, 'imports':imports,
            'versions':{n:importlib.metadata.version(n) for n in ('rdkit','posebusters','numpy')}},
        'binary':{'path':str(binary), **identity(binary), 'version':subprocess.check_output([str(binary),'--version'],text=True).strip()},
        'preflight':identity(root/'preflight.json'), 'predictions_or_verifier_results_exist':bool(list((root/'outputs').glob('*/*')))}
    if value['predictions_or_verifier_results_exist']:
        raise ValueError('execution freeze must precede any predictions')
    save(root/'freeze.json', value)
    return {'freeze':identity(root/'freeze.json'), 'candidate':ranking.METHOD['id'], 'cases':len(panel['cases']), 'checks':contract['required_checks']}


def task(root, row):
    folder = root/'inputs'/row['id']
    guard_target_component_absence(folder/'receptor.pdb',target_component_id=row.get('target_component_id') or row['id'].split('_',1)[1])
    settings = read(folder/'settings.json')
    for name, pin in row['inputs'].items():
        if identity(folder/name) != pin:
            raise ValueError('conditioning input changed')
    sources = [{'id':key,'path':str(folder/name),'locator':'PB308 known-pocket conditioning input; no gold atoms',
        'role':'conditioning','scope':QUESTION,**identity(folder/name)} for key,name in [('receptor','receptor.pdb'),('ligand','ligand.sdf'),('settings','settings.json')]]
    params = {'receptor':str(folder/'receptor.pdb'),'ligand':str(folder/'ligand.sdf'), 'box':settings['box'], 'seed':41,'num_modes':10,'exhaustiveness':8}
    return {'schema':'etalon-pose-task/1','task_id':'ranking-independent-20261025','case_id':row['id'],'question':QUESTION,'split':'validation',
        'method':{'engine':'GNINA1.3.3', 'pool':'empirical vina', 'candidate':'contact-relaxation-v1'},
        'output_contract':{'poses':'SDF','scores':'typed-with-direction','receipt':'request-and-file-identities','failure':'retained'},
        'input':{'schema':'etalon-scientific-input/1','task_id':'ranking-independent-20261025','scope':QUESTION,
          'subject':{'molecule':row['smiles'],'series_id':row['series_id']},'sources':sources,'observations':[],'interpretations':[],
          'bindings':[{'id':k,'target':k,'value':v,'use':'constraint' if k in {'receptor','ligand','box'} else 'selection',
              'basis_ids':[k if k in {'receptor','ligand'} else 'settings'],'rationale':'Predeclared parameters, graph-generated ligand, no reference pose constraint'} for k,v in params.items()]}}


class Runner:
    def __init__(self, root):
        self.root, self.panel, self.freeze = root, read(root/'panel.json'), read(root/'freeze.json')
        self.pin = identity(root/'freeze.json')
        self.ranking, self.helper = modules(root)
        self.cnn_lock = threading.Lock()
        for field, path in [('design', root/'design-freeze.json'), ('panel',root/'panel.json')]:
            if identity(path) != self.freeze[field]:
                raise ValueError('frozen '+field+' changed')
        for path, pin in self.freeze['source'].items():
            if identity(path) != pin:
                raise ValueError('source changed')
        for row in self.freeze['runtime']['imports'].values():
            if identity(row['path']) != {k:row[k] for k in ('sha256','bytes')}:
                raise ValueError('imported implementation changed')
        if self.freeze['candidate'] != self.ranking.METHOD or self.helper.pose_check_contract() != self.freeze['check_contract']:
            raise ValueError('method or checker changed')
        binary = self.freeze['binary']
        if identity(binary['path']) != {k:binary[k] for k in ('sha256','bytes')}:
            raise ValueError('native binary changed')

    def native(self, compiled, stage, pool, output):
        key = digest({'compiled':compiled, 'stage':stage, 'freeze':self.pin})
        folder = self.root/'receipts'/key
        folder.mkdir(parents=True,exist_ok=True)
        request = {'request_id':key,'attempt_id':'trial-1','compiled':compiled,'stage':stage,'freeze':self.pin,
            'pool':identity(pool) if pool else None, 'execution_mode':'new_execution'}
        save(folder/'request.json',request)
        if (folder/'receipt.json').exists():
            return validate_native_receipt(read(folder/'receipt.json'),request,compiled=compiled,stage=stage,protocol=self.pin,pool=request['pool'],output=output)
        if (folder/'started.json').exists():
            raise RuntimeError('Ambiguous started request; no automatic retry')
        args = pose_engine.command(self.freeze['binary']['path'],compiled['parameters'],output,stage=stage,pool=pool)
        command = ['/usr/bin/timeout','--signal=TERM','--kill-after=8s','590s','/usr/bin/time','-o',str(folder/'native-resource.txt'),
            '-f','{"user_second":%U,"system_second":%S,"elapsed_second":%e,"peak_rss_kb":%M,"exit_status":%x}']+args
        save(folder/'command.json',{'argv':command,'CUDA_VISIBLE_DEVICES':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1',
            'reference_excluded':True, 'fixed_pool_input':request['pool']})
        save(folder/'started.json',{'request_id':key,'pid':os.getpid(),'started_at_ns':time.time_ns()})
        start = time.perf_counter()
        error = None
        try:
            log = pose_engine.execute(command,directory=folder,gpu='1')
            (folder/'stdout.log').write_text(log,encoding='utf-8')
            if not output.exists():
                raise ValueError('missing native output')
            self.helper.pool_identity(output)
            if stage == 'cnn':
                save(folder/'same-pool.json',self.helper.same_pool(pool,output))
        except Exception as exc:
            error = {'type':type(exc).__name__,'message':str(exc),'context':getattr(exc,'context',None)}
            (folder/'stdout.log').write_text(json.dumps(error,default=str),encoding='utf-8')
        wall = time.perf_counter()-start
        resource = None
        if (folder/'native-resource.txt').exists():
            try:
                resource = json.loads((folder/'native-resource.txt').read_text('utf-8').splitlines()[-1])
            except (ValueError,IndexError):
                pass
        cpu = resource['user_second']+resource['system_second'] if resource else None
        receipt = {'schema':'etalon-ranking-native-receipt/1','request_id':key,'attempt_id':'trial-1','case_id':compiled['case_id'],
            'stage':stage,'status':'failed' if error else 'completed','execution_mode':'new_execution','wall_seconds':wall,'cpu_second':cpu,
            'gpu_device_second':wall if stage=='cnn' else 0.,'native_resource':resource,'error':error,
            'artifacts':{str(p):identity(p) for p in (output,folder/'stdout.log',folder/'native-resource.txt',folder/'same-pool.json') if p.exists()}}
        save(folder/'receipt.json',receipt)
        return validate_native_receipt(receipt,request,compiled=compiled,stage=stage,protocol=self.pin,pool=request['pool'],output=output)

    def case(self, row):
        compiled = compile_task(task(self.root,row))
        folder = self.root/'outputs'/row['id']
        folder.mkdir(parents=True,exist_ok=True)
        if (folder/'result.json').exists():
            # Cached completion is read-only and still checks every pinned native artifact.
            result = read(folder/'result.json')
            if read(folder/'compiled.json') != compiled:
                raise ValueError('cached compiled request changed')
            for stage in ('empirical','cnn'):
                key = digest({'compiled':compiled,'stage':stage,'freeze':self.pin})
                receipt = self.root/'receipts'/key/'receipt.json'
                if not receipt.exists():
                    if stage=='cnn' and result['stages']['empirical']!='completed':
                        continue
                    raise ValueError('cached result lacks native receipt')
                request = read(receipt.parent/'request.json')
                validate_native_receipt(read(receipt),request,compiled=compiled,stage=stage,protocol=self.pin,
                    pool=identity(folder/'empirical.sdf') if stage=='cnn' else None,output=folder/(stage+'.sdf'))
            if result.get('verifier') and result['verifier'] != {'path':str(folder/'verifier.json'),**identity(folder/'verifier.json')}:
                raise ValueError('cached verifier changed')
            return result
        save(folder/'compiled.json',compiled)
        empirical = self.native(compiled,'empirical',None,folder/'empirical.sdf')
        if empirical['status'] != 'completed':
            result = {'case_id':row['id'],'status':'native_generation_failed','stages':{'empirical':empirical['status'],'cnn':'not_executed'},'top1':{},'unknown':True}
            save(folder/'result.json',result)
            return result
        candidate_path = folder/'candidate.json'
        if not candidate_path.exists():
            wallstart, cpustart = time.perf_counter(), time.thread_time()
            candidate = self.ranking.rank_pool(folder/'empirical.sdf',compiled['parameters']['receptor'],
                expected_pool=identity(folder/'empirical.sdf'),expected_receptor=compiled['files']['receptor'])
            candidate.update(wall_seconds=time.perf_counter()-wallstart, cpu_seconds=time.thread_time()-cpustart,
                frozen_at_ns=time.time_ns(), protocol=self.pin, compiled_digest=digest(compiled))
            save(candidate_path,candidate)
        candidate = read(candidate_path)
        # Candidate output is immutable before either CNN or any gold evaluation.
        with self.cnn_lock:
            cnn = self.native(compiled,'cnn',folder/'empirical.sdf',folder/'cnn.sdf')
        reference = self.root/'evaluator'/row['id']/'reference.sdf'
        if identity(reference) != row['reference_identity']:
            raise ValueError('evaluator reference differs')
        binding = result_binding(compiled=compiled,protocol=self.pin,pool=identity(folder/'empirical.sdf'),receptor=compiled['files']['receptor'],
            reference=identity(reference),check_contract=self.freeze['check_contract'])
        count = len(self.helper.pool_identity(folder/'empirical.sdf'))
        if not (folder/'verifier.json').exists():
            start = time.perf_counter()
            verifier = self.helper.physical_check(folder/'empirical.sdf',compiled['parameters']['receptor'],reference,
                check_contract=self.freeze['check_contract'],binding=binding)
            verifier['wall_seconds'] = time.perf_counter()-start
            verifier['started_after_candidate_ns'] = candidate['frozen_at_ns']
            save(folder/'verifier.json',verifier)
        verifier = read(folder/'verifier.json')
        integrity = validate_verifier(verifier,expected_pose_count=count,check_contract=self.freeze['check_contract'],expected_binding=binding)
        ranks = {'empirical':self.helper.ranking(folder/'empirical.sdf','minimizedAffinity'),'candidate':candidate}
        if cnn['status'] == 'completed':
            ranks['cnn'] = self.helper.ranking(folder/'cnn.sdf','CNNscore',True)
        result = {'schema':'etalon-ranking-case-result/1','case_id':row['id'],'series_id':row['series_id'],'status':'completed','binding':binding,
            'stages':{'empirical':empirical['status'],'cnn':cnn['status']}, 'integrity':integrity,
            'candidate_fallback':candidate['fallback'],'rankings':ranks,
            'top1':{name:top1_observation(verifier,rank,count,check_contract=self.freeze['check_contract'],expected_binding=binding) for name,rank in ranks.items()},
            'verifier':{'path':str(folder/'verifier.json'),**identity(folder/'verifier.json')},'pool_any_success':None,
            'source_role':'independent_confirmation; fixed method; no test-driven revision'}
        if integrity['status']=='complete':
            result['pool_any_success'] = any(r['valid_and_RMSD_le_2A'] for r in verifier['rows'])
        save(folder/'result.json',result)
        print(json.dumps({'case_id':row['id'],'completed':True,'top1':result['top1'],'integrity':integrity['status']}),flush=True)
        return result

    def run(self):
        started = time.time_ns()
        if not (self.root/'execution-start.json').exists():
            save(self.root/'execution-start.json',{'started_at_ns':started,'freeze':self.pin,'pid':os.getpid(),'requested_cases':24,'maximum_native_calls':48})
        elif read(self.root/'execution-start.json')['freeze'] != self.pin:
            raise ValueError('saved execution binds another freeze')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(self.case,row):row for row in self.panel['cases']}
            for future in concurrent.futures.as_completed(futures):
                future.result()
        return evaluate(self.root)


def evaluate(root):
    panel, frozen, design = read(root/'panel.json'),read(root/'freeze.json'),read(root/'design-freeze.json')
    results = [read(root/'outputs'/r['id']/'result.json') if (root/'outputs'/r['id']/'result.json').exists() else {'case_id':r['id'],'status':'not_executed','top1':{}} for r in panel['cases']]
    methods = ['empirical','cnn','candidate']
    metrics = {m:{'top1_success':0,'physical_valid_top1':0,'unknown_top1':0,'top2_success':0,'top3_success':0} for m in methods}
    rows = []
    for result in results:
        cid = result['case_id']
        compact = {'case_id':cid,'status':result['status'],'integrity':result.get('integrity',{}).get('status','unknown'),'pool_any_success':result.get('pool_any_success'),'top1':result['top1']}
        rows.append(compact)
        verification = root/'outputs'/cid/'verifier.json'
        observed = read(verification) if verification.exists() else {'rows':[]}
        byslot = {r['pose_index']:r for r in observed['rows']}
        for method in methods:
            top = result['top1'].get(method,{})
            metrics[method]['top1_success'] += top.get('valid_and_RMSD_le_2A') is True
            metrics[method]['physical_valid_top1'] += top.get('physical_valid') is True
            metrics[method]['unknown_top1'] += type(top.get('valid_and_RMSD_le_2A')) is not bool
            for k in (2,3):
                indices = result.get('rankings',{}).get(method,{}).get('ordered_pose_indices',[])[:k]
                metrics[method]['top'+str(k)+'_success'] += compact['integrity']=='complete' and any(byslot[i]['valid_and_RMSD_le_2A'] for i in indices)
    comparisons = {}
    for other in ('empirical','cnn'):
        wins = sum(r['top1'].get('candidate',{}).get('valid_and_RMSD_le_2A') is True and r['top1'].get(other,{}).get('valid_and_RMSD_le_2A') is not True for r in results)
        losses = sum(r['top1'].get('candidate',{}).get('valid_and_RMSD_le_2A') is not True and r['top1'].get(other,{}).get('valid_and_RMSD_le_2A') is True for r in results)
        n = wins+losses
        p = sum(math.comb(n,k) for k in range(wins,n+1))/2**n if n else 1.
        comparisons[other] = {'wins':wins,'losses':losses,'net_gain':wins-losses,'paired_one_sided_exact_p':p}
    receipts = [read(p) for p in (root/'receipts').glob('*/receipt.json')]
    candidates = [read(p) for p in (root/'outputs').glob('*/candidate.json')]
    native_cpu = sum(r['cpu_second'] for r in receipts if r['stage']=='empirical' and r['cpu_second'] is not None)
    candidate_cpu = sum(r['cpu_seconds'] for r in candidates)
    candidate_wall = sum(r['wall_seconds'] for r in candidates)
    costs = {'native_wall_seconds':sum(r['wall_seconds'] for r in receipts),'generation_cpu_seconds':native_cpu,
        'cnn_gpu_device_seconds':sum(r['gpu_device_second'] for r in receipts),'native_receipts':len(receipts),
        'native_cpu_measurement_missing':sum(r['cpu_second'] is None for r in receipts),'candidate_cpu_seconds':candidate_cpu,
        'candidate_sum_wall_seconds':candidate_wall,'candidate_mean_wall_seconds':candidate_wall/24,
        'candidate_cpu_fraction_of_search':candidate_cpu/native_cpu if native_cpu else None,
        'verification_sum_wall_seconds':sum(read(p)['wall_seconds'] for p in (root/'outputs').glob('*/verifier.json')),
        'input_preparation_wall_seconds':panel['preparation_wall_seconds'],
        'formation_development_overhead':'partly unmeasured; unknown, never zero','policy_cost_scope':'full audit physically paid; hypothetical empirical+candidate excludes diagnostic CNN/verifier acquisition only as path accounting'}
    complete = sum(r.get('integrity',{}).get('status')=='complete' for r in results)
    rules = design['adopt_requirements']
    checks = {'complete_cases':complete==24,'native_calls_complete':len(receipts)==48 and all(r['status']=='completed' for r in receipts),
        'all_method_top1_observed':all(metrics[m]['unknown_top1']==0 for m in methods),
        'minimum_gain':comparisons['empirical']['net_gain']>=rules['net_top1_gain_over_empirical_min'],
        'lost_baseline_successes':comparisons['empirical']['losses']<=rules['lost_baseline_successes_max'],
        'paired_exact_p':comparisons['empirical']['paired_one_sided_exact_p']<=rules['paired_one_sided_exact_binomial_p_max'],
        'at_least_CNN':metrics['candidate']['top1_success']>=metrics['cnn']['top1_success'],
        'physical_valid_retention':metrics['candidate']['physical_valid_top1']>=metrics['empirical']['physical_valid_top1'],
        'candidate_cpu_fraction':costs['candidate_cpu_fraction_of_search'] is not None and costs['candidate_cpu_fraction_of_search']<=rules['candidate_cpu_fraction_of_search_max'],
        'candidate_wall':costs['candidate_mean_wall_seconds']<=rules['candidate_mean_wall_second_max']}
    value = {'schema':'etalon-independent-ranking-evaluation/1','denominator':24,'series_count':24,'seeds_per_case':1,
        'freeze':identity(root/'freeze.json'),'panel':identity(root/'panel.json'),'metrics':metrics,'paired_candidate_vs':comparisons,
        'complete_verifier_cases':complete,'pool_any_success':sum(r.get('pool_any_success') is True for r in results),
        'candidate_fallbacks':sum(r.get('candidate_fallback') is True for r in results),'costs':costs,'adoption_checks':checks,
        'decision':'adopt' if all(checks.values()) else 'retain','method_change':'adopt contact-relaxation-v1 within tested scope' if all(checks.values()) else 'no_change; retain empirical ranking',
        'cases':rows,'scope':design['scope'],'source_independence_limit':design['independence_limit']}
    save(root/'evaluation.json',value)
    return value


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action',choices=['prepare','freeze','run','evaluate'])
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--binary',type=Path)
    a = p.parse_args()
    os.environ['CUDA_VISIBLE_DEVICES']='1'
    os.environ['OMP_NUM_THREADS']='1'
    os.environ['OPENBLAS_NUM_THREADS']='1'
    root = a.root.absolute()
    if a.action=='prepare':
        value = prepare(root)
    elif a.action=='freeze':
        value = freeze(root,a.binary)
    elif a.action=='run':
        value = Runner(root).run()
    else:
        value = evaluate(root)
    print(json.dumps(value,sort_keys=True,indent=2),flush=True)


if __name__=='__main__':
    main()
