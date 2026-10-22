"""Bounded same-series pose preparation; no docking/affinity or model claims.

The generator only consumes a graph and a known template. Evaluation coordinates
are accepted by a separate command after generation manifests have been sealed.
PRISM supplies chemical identity and receptor geometry, RDKit supplies native
ETKDG/MMFF. ETALON's research driver owns method selection and memory.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time

import numpy as np
from rdkit import Chem, rdBase
from rdkit.Chem import AllChem, rdFMCS, rdMolAlign
from prism.design.chem import canonical_smiles
from prism.design.geometry import pdb_atoms


def pin(path):
    data = Path(path).read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def write(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def runtime():
    names = ['rdkit.Chem.AllChem', 'prism.design.chem', 'prism.design.geometry']
    return {'python': sys.executable, 'rdkit': rdBase.rdkitVersion,
            'modules': {n: {'path': importlib.import_module(n).__file__, **pin(importlib.import_module(n).__file__)} for n in names},
            'recipe': {'path': __file__, **pin(__file__)}}


def ligand(pdb, ccd, smiles, chain='A'):
    """Extract one experimental residue; assign only CCD graph bond orders."""
    atoms = [a for a in pdb_atoms(pdb) if a['key'][1] == chain and a['key'][4] == ccd]
    if len({a['key'][2:5] for a in atoms}) != 1:
        raise ValueError('need exactly one explicitly selected ligand residue')
    selected = {(a['key'], a['altloc']) for a in atoms}
    lines = []
    for line in Path(pdb).read_text('utf-8').splitlines():
        if line.startswith('ENDMDL'): break
        if not line.startswith(('ATOM  ', 'HETATM')): continue
        key = ('1', line[21:22].strip(), line[22:26].strip(), line[26:27].strip(), line[17:20].strip(), line[12:16].strip())
        if (key, line[16:17].strip()) in selected:
            lines.append(line[:16] + ' ' + line[17:])
    raw = Chem.MolFromPDBBlock('\n'.join(lines)+'\nEND\n', sanitize=False, removeHs=False)
    if raw is None: raise ValueError('experimental ligand parse failed')
    xyz = raw.GetConformer().GetPositions().copy()
    mol = AllChem.AssignBondOrdersFromTemplate(Chem.MolFromSmiles(smiles), raw)
    if canonical_smiles(mol) != canonical_smiles(Chem.MolFromSmiles(smiles)):
        raise ValueError('CCD and experimental graph disagree')
    if not np.array_equal(xyz, mol.GetConformer().GetPositions()):
        raise ValueError('bond assignment changed experimental coordinates')
    return mol, {'source': str(pdb), **pin(pdb), 'ccd': ccd, 'chain': chain,
                 'residue': list(atoms[0]['key'][2:5]), 'atoms': len(atoms),
                 'altloc_policy': 'PRISM highest occupancy, then blank, then A',
                 'atom_names': [a.GetPDBResidueInfo().GetName().strip() for a in mol.GetAtoms()]}


def mapping(template, target):
    result = rdFMCS.FindMCS([template, target], timeout=10,
        atomCompare=rdFMCS.AtomCompare.CompareElements,
        bondCompare=rdFMCS.BondCompare.CompareOrderExact,
        ringMatchesRingOnly=True, completeRingsOnly=True)
    if result.canceled: raise ValueError('common substructure search timed out')
    query = Chem.MolFromSmarts(result.smartsString)
    tm = sorted(template.GetSubstructMatches(query, uniquify=False, maxMatches=256))
    cm = sorted(target.GetSubstructMatches(query, uniquify=False, maxMatches=256))
    if not tm or not cm or len(tm) == 256 or len(cm) == 256:
        raise ValueError('missing or truncated graph mapping')
    pairs = list(zip(cm[0], tm[0]))
    xyz = np.array([template.GetConformer().GetPositions()[j] for _, j in pairs])
    noncollinear = bool(len(xyz) >= 3 and np.linalg.matrix_rank(xyz-xyz.mean(0), tol=1e-6) >= 2)
    covers = [len(pairs)/target.GetNumHeavyAtoms(), len(pairs)/template.GetNumHeavyAtoms()]
    applicable = (len(Chem.GetMolFrags(target)) == len(Chem.GetMolFrags(template)) == 1
        and Chem.GetFormalCharge(target) == Chem.GetFormalCharge(template) == 0
        and len(pairs) >= 6 and min(covers) >= .6 and noncollinear)
    return {'pairs': pairs, 'core_atoms': len(pairs), 'coverage_target_template': covers,
            'smarts': result.smartsString, 'mapping_counts': [len(cm),len(tm)],
            'applicable': applicable, 'noncollinear': noncollinear,
            'selection': 'lexicographic graph matches; no target coordinates'}


def input_bundle(root, pdb, ccd, template_smiles, target_smiles, *, chain='A'):
    root = Path(root); root.mkdir(parents=True, exist_ok=False)
    template, extraction = ligand(pdb, ccd, template_smiles, chain)
    target = Chem.MolFromSmiles(target_smiles)
    if target is None: raise ValueError('target graph invalid')
    for atom in template.GetAtoms(): atom.SetAtomMapNum(atom.GetIdx()+1)
    with Chem.SDWriter(str(root/'template.sdf')) as out: out.write(template)
    # Both arms consume the same serialized template coordinates.
    template = Chem.SDMolSupplier(str(root/'template.sdf'), removeHs=False)[0]
    graph_map = mapping(template, target)
    atoms = [a for a in pdb_atoms(pdb) if a['protein'] and a['key'][1] == chain]
    if not atoms: raise ValueError('empty receptor')
    write(root/'receptor.json', {'source':str(pdb), **pin(pdb), 'chain':chain,'atoms':atoms,
                                'waters_and_heteroatoms':'not used in clash screen; no receptor energy evaluation'})
    body = {'schema':'snd1-pose-input/1','target_smiles':canonical_smiles(target),
            'template_smiles':canonical_smiles(template),'extraction':extraction,'mapping':graph_map,
            'files':{n:pin(root/n) for n in ['template.sdf','receptor.json']},
            'role':'known_template_and_target_graph; no target pose'}
    write(root/'input.json',body)
    return body


def generate(input_dir, output, method, seeds):
    wall, cpu = time.perf_counter(), time.process_time()
    input_dir, output = Path(input_dir), Path(output)
    body=json.loads((input_dir/'input.json').read_text('utf-8'))
    for name, identity in body['files'].items():
        if pin(input_dir/name) != identity: raise ValueError('changed input bytes')
    if method not in {'free_align','fixed_core'}: raise ValueError('unknown method')
    request={'input':pin(input_dir/'input.json'),'method':method,'seeds':list(seeds),'runtime':runtime()}
    if output.exists():
        old=json.loads((output/'request.json').read_text('utf-8'))
        if old != request: raise ValueError('same execution path, changed request')
        if not (output/'result.json').exists(): raise RuntimeError('execution pending; no automatic retry')
        prior=json.loads((output/'result.json').read_text('utf-8'))
        for name, identity in prior['artifacts'].items():
            if pin(output/name) != identity: raise ValueError('changed completed artifact')
        return {'reused':True,'result':str(output/'result.json')}
    output.mkdir(parents=True, exist_ok=False); write(output/'request.json',request)
    target=Chem.MolFromSmiles(body['target_smiles'])
    template=Chem.SDMolSupplier(str(input_dir/'template.sdf'),removeHs=False)[0]
    # Recompute the graph map after canonical SMILES parsing, whose atom order may differ.
    match=mapping(template,target)
    if not match['applicable']: raise ValueError('same-series template method outside applicability')
    pairs=[tuple(p) for p in match['pairs']]
    receptor=json.loads((input_dir/'receptor.json').read_text('utf-8'))
    protein=np.array([a['xyz'] for a in receptor['atoms']])
    rows=[]
    for slot,seed in enumerate(seeds,1):
        t,c=time.perf_counter(),time.process_time()
        row={'slot':slot,'seed':seed,'status':'failed','pose':None,'rmsd_A':None}
        try:
            mol=Chem.AddHs(target)
            params=AllChem.ETKDGv3(); params.randomSeed=int(seed); params.numThreads=1; params.maxIterations=1000
            if method=='fixed_core': params.SetCoordMap({i:template.GetConformer().GetAtomPosition(j) for i,j in pairs})
            if AllChem.EmbedMolecule(mol,params)<0: raise ValueError('embedding failed')
            if method=='fixed_core':
                rdMolAlign.AlignMol(mol,template,atomMap=pairs)
                for i,j in pairs: mol.GetConformer().SetAtomPosition(i,template.GetConformer().GetAtomPosition(j))
            props=AllChem.MMFFGetMoleculeProperties(mol,mmffVariant='MMFF94s')
            if props is None: raise ValueError('MMFF parameters unavailable')
            ff=AllChem.MMFFGetMoleculeForceField(mol,props)
            if method=='fixed_core':
                for i,_ in pairs: ff.AddFixedPoint(i)
            ff.Initialize(); convergence=int(ff.Minimize(maxIts=1000)); energy=float(ff.CalcEnergy())
            if method=='free_align': rdMolAlign.AlignMol(mol,template,atomMap=pairs)
            if canonical_smiles(mol)!=canonical_smiles(target): raise ValueError('generated graph changed')
            path=output/f'pose-{slot:03d}.sdf'
            with Chem.SDWriter(str(path)) as writer: writer.write(mol)
            saved=Chem.SDMolSupplier(str(path),removeHs=False)[0]
            xyz=Chem.RemoveHs(saved).GetConformer().GetPositions()
            minimum=float(np.linalg.norm(xyz[:,None,:]-protein[None,:,:],axis=2).min())
            row.update(status='completed',pose=path.name,pose_identity=pin(path),graph_exact=True,
                       convergence=convergence,energy_kcal_mol=energy,energy_scope='internal MMFF94s only',
                       minimum_protein_heavy_distance_A=minimum,severe_clash=minimum<1.5)
        except Exception as exc: row['error']={'type':type(exc).__name__,'message':str(exc)}
        row['cost']={'wall_seconds':time.perf_counter()-t,'cpu_seconds':time.process_time()-c}
        rows.append(row); write(output/'progress.json',{'rows':rows})
    result={'schema':'snd1-pose-execution/1','request':request,'mapping':match,'rows':rows,
            'cost':{'wall_seconds':time.perf_counter()-wall,'cpu_seconds':time.process_time()-cpu},
            'scientific_scope':'conditional pose preparation; no docking or affinity',
            'artifacts':{p.name:pin(p) for p in output.glob('pose-*.sdf')}}
    write(output/'result.json',result)
    return {'reused':False,'result':str(output/'result.json')}


def evaluate(input_dir, output, reference_pdb, reference_ccd, reference_smiles):
    """Answer coordinates enter only this post-generation function."""
    wall,cpu=time.perf_counter(),time.process_time()
    input_dir,output=Path(input_dir),Path(output)
    result=json.loads((output/'result.json').read_text('utf-8'))
    receptor=json.loads((input_dir/'receptor.json').read_text('utf-8'))
    mobile={tuple(a['key'][2:]):a['xyz'] for a in pdb_atoms(reference_pdb)
            if a['protein'] and a['key'][1]=='A' and a['key'][-1]=='CA'}
    fixed={tuple(a['key'][2:]):a['xyz'] for a in receptor['atoms'] if a['key'][-1]=='CA'}
    keys=sorted(set(mobile)&set(fixed))
    if len(keys)<50: raise ValueError('insufficient matched protein CA atoms')
    x,y=np.array([mobile[k] for k in keys]),np.array([fixed[k] for k in keys])
    xc,yc=x.mean(0),y.mean(0); u,s,vt=np.linalg.svd((x-xc).T@(y-yc))
    d=np.eye(3); d[-1,-1]=np.linalg.det(u@vt); rotation=u@d@vt
    fit=float(np.sqrt(np.mean(np.sum(((x-xc)@rotation+yc-y)**2,axis=1))))
    if fit>2: raise ValueError('protein frame alignment exceeds 2 A')
    answer,extraction=ligand(reference_pdb,reference_ccd,reference_smiles)
    truth=answer.GetConformer().GetPositions()
    truth=(truth-xc)@rotation+yc
    rows=[]
    for row in result['rows']:
        out={'slot':row['slot'],'success':False,'rmsd_A':None,'status':row['status']}
        if row['pose']:
            path=output/row['pose']
            if pin(path)!=row['pose_identity']: raise ValueError('generated pose changed')
            mol=Chem.RemoveHs(Chem.SDMolSupplier(str(path),removeHs=False)[0])
            if canonical_smiles(mol)!=canonical_smiles(answer): raise ValueError('evaluation graph mismatch')
            matches=answer.GetSubstructMatches(mol,uniquify=False,maxMatches=2048,useChirality=True)
            if not matches or len(matches)==2048: raise ValueError('incomplete evaluation atom mapping')
            xyz=mol.GetConformer().GetPositions()
            rmsd=min(float(np.sqrt(np.mean(np.sum((xyz-truth[list(m)])**2,axis=1)))) for m in matches)
            out.update(rmsd_A=rmsd,success=(row['graph_exact'] and row['convergence']==0 and not row['severe_clash'] and rmsd<=2))
        rows.append(out)
    success=sum(r['success'] for r in rows); vals=[r['rmsd_A'] for r in rows if r['rmsd_A'] is not None]
    return {'schema':'snd1-pose-evaluation/1','execution':{'path':str(output/'result.json'),**pin(output/'result.json')},
            'reference':extraction,'protein_alignment':{'matched_ca':len(keys),'rmsd_A':fit,'ligand_answer_aligned_to_prediction':False},
            'rows':rows,'requested':len(rows),'successes':success,'success_fraction':success/len(rows),
            'execution_failures':sum(r['status']=='failed' for r in result['rows']),
            'nonconverged':sum(r.get('convergence',0)!=0 for r in result['rows']),
            'severe_clashes':sum(r.get('severe_clash',False) for r in result['rows']),
            'median_rmsd_A':float(np.median(vals)) if vals else None,
            'generation_cost':result['cost'],'wall_seconds_per_success':result['cost']['wall_seconds']/success if success else None,
            'evaluation_cost':{'wall_seconds':time.perf_counter()-wall,'cpu_seconds':time.process_time()-cpu}}


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--input',required=True); p.add_argument('--output',required=True)
    p.add_argument('--method',choices=['free_align','fixed_core'],required=True); p.add_argument('--seed',type=int,required=True)
    p.add_argument('--count',type=int,default=12); a=p.parse_args()
    if not 1<=a.count<=12: p.error('bounded to at most 12 slots')
    print(json.dumps(generate(a.input,a.output,a.method,range(a.seed,a.seed+a.count))))
