"""Frozen SND1 conditional robustness panel using existing native solvers.

This example is a task driver, not a scheduler or a second research database.
The crystallographic starts make this retrospective; no new chemistry or blind
pose-prediction claim is supported. Completed and unknown attempts fail closed.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import resource
import sys
import time
import traceback

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem
import snd1_torsion_workflow as torsion

old = torsion.legacy.old
CONFIGS = ['fixed_core', 'torsion_distance_fd', 'torsion_distance_cartesian_chain',
           'torsion_directional_fd', 'torsion_directional_cartesian_chain']
LIGANDS = {
    'QOY': {'pdb': '7KNW', 'name': 'C26-A2', 'smiles': 'COc1ccc(Cl)cc1S(=O)(=O)Nc1cccn2ncnc12'},
    'QOV': {'pdb': '7KNX', 'name': 'C26-A6', 'smiles': 'COc1ccc(Cl)cc1S(=O)(=O)Nc1cccn2nc(C)nc12'},
}


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def write(p, body):
    p = Path(p)
    temp = p.with_name(p.name + '.tmp-' + str(os.getpid()))
    temp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, p)


def pin(p):
    data = Path(p).read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def protocol():
    return {
        'schema': 'snd1-scale-p1-protocol/1', 'r4b': 'paused',
        'scope': 'Retrospective known-crystal conditional robustness, two related ligands only',
        'conditioning': 'Each ligand own crystal heavy coordinates initialize all three torsion families and anchor soft positions; known-answer initialization is declared, not blind prediction',
        'coordinate_frame': '7KNW chain A; 7KNX protein CA Kabsch fit, never ligand fit to answer',
        'ligands': LIGANDS, 'receptor_contexts': ['7KNW', '7KNX'], 'chain': 'A',
        'initialization_families': {'small': [5, 5, 5, 10], 'medium': [15, 15, 15, 30], 'large': [30, 30, 30, 60],
                                    'existing_docking': None},
        'sampling': 'numpy.default_rng(seed).uniform(-half_width,+half_width), in degrees; seeds 1..24 reused across systems as paired nuisance conditions',
        'seeds': list(range(1, 25)), 'configurations': CONFIGS,
        'docking_family_status': 'not_applicable: no authenticated SND1 docking-pool originals found in scoped historical inputs/runs; historical ETKDG/MMFF are not docking',
        'deduplication': 'exact float64 coordinate SHA256 within ligand/receptor context across families; duplicate provenance kept and removed from execution denominator',
        'fixed_core': 'Existing MMFF94s native Minimize(maxIts=1000), common heavy core to other series ligand fixed at current perturbed input; no pocket objective',
        'torsion': 'Existing snd1_torsion_workflow.refine unchanged; MMFF94s+soft position+reported distance+protein exclusion; directional adds graph-typed virtual-H proxies',
        'torsion_correction_half_width_degrees': [10, 10, 10, 20],
        'optimizer': {'maxiter': 500, 'maxls': 40, 'ftol': 1e-12, 'gtol': 1e-5},
        'hard': {'position_tolerance_A': 1e-8, 'bond_tolerance_A': 1e-8, 'angle_tolerance_deg': 1e-5},
        'soft': {'position_flat_bottom_A': .25, 'position_k': 5., 'distance_k': 10.,
                 'distance_window': 'own native crystal observed distance +/- .30A with lower floor2.4A; unchanged in cross receptor',
                 'pocket_radius_A': 8., 'exclusion_scale': .75, 'exclusion_k': 20., 'direction_DHA_k': 2., 'direction_axis_k': 2.},
        'donors': 'Arg255 NH2 and Asn281 ND2 constructed planar H, not experimental H or fully prepared receptor; nonacceptor contact distance retained with direction excluded',
        'order': 'For odd seed F,T0FD,T0CG,T1FD,T1CG; for even seed T1CG,T1FD,T0CG,T0FD,F; pairs sequential in same process',
        'solver_equivalence': {'maximum_atom_delta_A': .001, 'maximum_absolute_objective_delta_kcal_mol': .0001, 'new_hard_violations_allowed': 0},
        'quality': 'Independent fixed-coordinate symmetry RMSD plus original PoseBusters dock checks via pose_verifier; valid & RMSD<=2A. Nonfixed heavy RMSD, clash, internal MMFF and constraint violation supplementary. Optimization score is not truth.',
        'quality_missing': 'No physical-valid success claimed before independent verifier result',
        'statistics': 'Per-system and family counts; seed repeats correlated and two related ligands do not support chemical-generalization confidence intervals',
        'cost': 'process_time and perf_counter solver and per-attempt end-to-end, input/summary/evaluator overhead separate; all failures charged',
        'resume': 'terminal receipts hash verified and reused; request without receipt unresolved, never automatic rerun',
        'workers': 8, 'threads_per_worker': 1, 'cpu_affinity': list(range(8, 16)), 'cpu_cap_seconds': 128*3600,
        'not_implemented': ['No new molecular screening', 'No affinity claim', 'No new scheduler', 'No GPU use'],
    }


def transform(xyz, fit):
    return (np.asarray(xyz) - fit['mobile_center']) @ np.asarray(fit['rotation']) + fit['fixed_center']


def protein_alignment(mobile, fixed):
    a = {tuple(r['key'][2:]): r['xyz'] for r in mobile if r['key'][-1] == 'CA'}
    b = {tuple(r['key'][2:]): r['xyz'] for r in fixed if r['key'][-1] == 'CA'}
    keys = sorted(set(a) & set(b))
    if len(keys) < 50:
        raise ValueError('insufficient exact protein residue/CA mapping')
    x, y = np.array([a[k] for k in keys]), np.array([b[k] for k in keys])
    xc, yc = x.mean(0), y.mean(0)
    u, _, vt = np.linalg.svd((x-xc).T @ (y-yc))
    d = np.eye(3); d[-1,-1] = np.linalg.det(u @ vt)
    rot = u @ d @ vt
    rms = float(np.sqrt(np.mean(np.sum(((x-xc) @ rot+yc-y)**2, axis=1))))
    if rms > 2:
        raise ValueError('protein fit exceeds frozen 2A threshold')
    return {'mobile_center': xc.tolist(), 'fixed_center': yc.tolist(), 'rotation': rot.tolist(),
            'matched_ca': len(keys), 'rmsd_A': rms, 'keys': [list(k) for k in keys], 'ligand_fit': False}


def with_xyz(mol, xyz):
    result = Chem.Mol(mol)
    for i, pos in enumerate(xyz): result.GetConformer().SetAtomPosition(i, pos)
    return result


def save_mol(path, mol):
    with Chem.SDWriter(str(path)) as writer: writer.write(Chem.Mol(mol))


def validate_serialized_identity(original, serialized):
    """Validate indexed chemistry before binding the consumed SDF representation.

    PDB extraction can retain a tetrahedral tag on sulfonyl S even though its
    two identical O substituents make that center nonstereogenic. RDKit SDF
    parsing removes that tag and may reverse a bond record. We preserve strict
    index/element/charge/isotope/aromatic/bond/stereo checks and allow only this
    demonstrated representational difference.
    """
    atoms=lambda m:[(a.GetAtomicNum(),a.GetFormalCharge(),a.GetIsotope(),a.GetIsAromatic()) for a in m.GetAtoms()]
    bonds=lambda m:sorted((min(b.GetBeginAtomIdx(),b.GetEndAtomIdx()),max(b.GetBeginAtomIdx(),b.GetEndAtomIdx()),
                           str(b.GetBondType()),str(b.GetStereo()),tuple(b.GetStereoAtoms())) for b in m.GetBonds())
    if atoms(original)!=atoms(serialized) or bonds(original)!=bonds(serialized):
        raise ValueError('serialized indexed chemical graph changed: atoms='+str([(i,a,b) for i,(a,b) in enumerate(zip(atoms(original),atoms(serialized))) if a!=b])+' bonds='+str([(a,b) for a,b in zip(bonds(original),bonds(serialized)) if a!=b]))
    if old.canonical_smiles(original)!=old.canonical_smiles(serialized):
        raise ValueError('serialized chemical state/stereochemistry changed')
    potential={int(i) for i,_ in Chem.FindMolChiralCenters(Chem.Mol(original),includeUnassigned=True,includeCIP=False)}
    allowed=[]
    for a,b in zip(original.GetAtoms(),serialized.GetAtoms()):
        if a.GetChiralTag()==b.GetChiralTag():continue
        double_oxygen=[n.GetIdx() for n in a.GetNeighbors() if n.GetAtomicNum()==8 and
                       original.GetBondBetweenAtoms(a.GetIdx(),n.GetIdx()).GetBondType()==Chem.BondType.DOUBLE]
        if a.GetAtomicNum()!=16 or len(double_oxygen)!=2 or a.GetIdx() in potential or b.GetChiralTag()!=Chem.ChiralType.CHI_UNSPECIFIED:
            raise ValueError('unapproved serialized chiral-tag change')
        allowed.append(a.GetIdx())
    return {'indexed_elements_charges_isotopes_aromaticity_exact':True,'labelled_undirected_bonds_and_stereo_exact':True,
            'canonical_stereochemical_identity_exact':True,'removed_nonstereogenic_sulfonyl_tags':allowed,
            'consumed_atom_order_digest':torsion.atom_order(serialized)}


def make_parameters(mol, own_atoms, context_atoms, core_indices, frame):
    xyz = mol.GetConformer().GetPositions()
    names = {a.GetPDBResidueInfo().GetName().strip(): a.GetIdx() for a in mol.GetAtoms() if a.GetPDBResidueInfo() and a.GetAtomicNum()>1}
    sulfur = [a for a in mol.GetAtoms() if a.GetAtomicNum() == 16]
    if len(sulfur) != 1: raise ValueError('requires one sulfonamide')
    s = sulfur[0].GetIdx(); ns = list(sulfur[0].GetNeighbors())
    n = next(a.GetIdx() for a in ns if a.GetAtomicNum() == 7)
    aryl = next(a.GetIdx() for a in ns if a.GetAtomicNum() == 6)
    hetero = next(a.GetIdx() for a in mol.GetAtomWithIdx(n).GetNeighbors() if a.GetAtomicNum()==6)
    ethers = [a for a in mol.GetAtoms() if a.GetAtomicNum()==8 and len([b for b in a.GetNeighbors() if b.GetAtomicNum()==6])==2]
    if len(ethers) != 1: raise ValueError('requires one methoxy')
    o=ethers[0].GetIdx(); oc=next(a.GetIdx() for a in ethers[0].GetNeighbors() if a.GetIsAromatic())
    dofs=[]
    for name,a,b,limit in [('phenyl_sulfonyl',aryl,s,10),('sulfonyl_N',s,n,10),('N_heteroaryl',n,hetero,10),('methoxy',oc,o,20)]:
        dofs.append({'id':name,'axis':[a,b],'moving_indices':torsion.component(mol,a,b),'bounds_degrees':[-limit,limit],
                     'zero':'input_pose','evidence_ids':['geometry-assumptions','graph'],'rationale':'Existing graph-validated narrow SND1 rotation'})
    moving={i for d in dofs for i in d['moving_indices']}
    pocket=[a for a in context_atoms if a['element']!='H' and np.min(np.linalg.norm(xyz-np.array(a['xyz']),axis=1))<=8.]
    def atom(atoms,res,name):
        hits=[a for a in atoms if a['key'][2]==res and a['key'][-1]==name]
        if len(hits)!=1: raise ValueError('ambiguous exact protein donor mapping')
        return hits[0]
    acceptors, typing=torsion.typed_acceptors(mol)
    distances=[];directions=[];exclusions=[]
    for ligand_name,res,donor_name in [('N','255','NE'),('N2','255','NH2'),('O','281','ND2')]:
        i=names[ligand_name];native=atom(own_atoms,res,donor_name);donor=atom(context_atoms,res,donor_name)
        observed=float(np.linalg.norm(xyz[i]-native['xyz']))
        cid=ligand_name+'--'+donor_name
        distances.append({'id':cid,'ligand_index':i,'protein_key':donor['key'],'protein_xyz':donor['xyz'],
                          'lower_A':max(2.4,observed-.30),'upper_A':observed+.30,'k_kcal_mol_A2':10.,
                          'interpretation':'reported_contact_distance','evidence_ids':['contact-observation','geometry-assumptions']})
        if i not in acceptors:
            exclusions.append({'id':cid,'reason':'not graph-typed acceptor; distance only'});continue
        parent,plane=('CZ','NE') if donor_name=='NH2' else ('CG','OD1')
        d=np.array(donor['xyz']);h=torsion.virtual_hydrogen(d,np.array(atom(context_atoms,res,parent)['xyz']),np.array(atom(context_atoms,res,plane)['xyz']),xyz[i])
        ia=mol.GetAtomWithIdx(i);neighbors=sorted(a.GetIdx() for a in ia.GetNeighbors() if a.GetAtomicNum()>1) if ia.GetAtomicNum()==7 and ia.GetIsAromatic() else []
        directions.append({'id':cid,'kind':'explicit_DHA_proxy','acceptor_index':i,'acceptor_type':'graph_typed_acceptor',
                           'donor_key':donor['key'],'donor_xyz':d.tolist(),'hydrogen_xyz':h.tolist(),
                           'hydrogen_origin':'constructed_planar','donor_state_assumption':'Arg guanidinium +1' if donor_name=='NH2' else 'Asn neutral amide',
                           'target_DHA_degrees':180.,'k_DHA_kcal_mol_rad2':2.,'acceptor_neighbors':neighbors,
                           'k_axis_kcal_mol_rad2':2. if neighbors else 0.,'evidence_ids':['contact-observation','geometry-assumptions','feature-typing']})
    g={'schema':torsion.SCHEMA,'representation':'bounded_bond_rotations','atom_count':mol.GetNumAtoms(),'atom_order_digest':torsion.atom_order(mol),
       'coordinate_frame':frame,'chemical_state':{'ligand_smiles':old.canonical_smiles(mol),'formal_charge':Chem.GetFormalCharge(mol),
       'assumptions':['Input crystal chemical state, no enumeration','Planar virtual protein H; not prepared receptor']},
       'hard':{'fixed_indices':sorted(set(range(mol.GetNumAtoms()))-moving),'reference':'per_execution_input','preserve':'bond_lengths_angles_and_rings',
               'position_tolerance_A':1e-8,'bond_tolerance_A':1e-8,'angle_tolerance_deg':1e-5},
       'dofs':dofs,'soft_positions':[{'index':i,'xyz':xyz[i].tolist(),'flat_bottom_A':.25,'k_kcal_mol_A2':5.,'evidence_ids':['template','geometry-assumptions']} for i in core_indices],
       'soft_distances':distances,'directions':directions,'direction_exclusions':exclusions,'scope':'SND1 two-known-ligand retrospective perturbation robustness'}
    # Exclusion parameters match the existing evidence workflow exactly.
    p={'geometry':g,'pocket':{'atoms':pocket,'steric_scale':.75,'force_constant':20.},
       'binding_support':{'eligible':True,'basis':'Two known crystal members; original native sources pinned'},'optimizer':protocol()['optimizer']}
    torsion.compile_geometry(g,mol)
    return p,typing


def initialize(root, originals):
    root=Path(root);originals=Path(originals);root.mkdir(parents=True,exist_ok=True)
    if (root/'freeze.json').exists():
        freeze=read(root/'freeze.json');verify_freeze(root,freeze);return freeze
    if (root/'protocol.json').exists() and read(root/'protocol.json')!=protocol(): raise ValueError('changed protocol')
    write(root/'protocol.json',protocol())
    t,c=time.perf_counter(),time.process_time()
    inp=root/'inputs';inp.mkdir(exist_ok=True)
    raw={p:[a for a in old.pdb_atoms(originals/(p+'.pdb')) if a['protein'] and a['key'][1]=='A'] for p in ['7KNW','7KNX']}
    fits={p:protein_alignment(raw[p],raw['7KNW']) for p in raw}
    receptors={}
    for p in raw:
        receptors[p]=copy.deepcopy(raw[p])
        for a in receptors[p]:a['xyz']=transform(a['xyz'],fits[p]).tolist()
        lines=[]
        # Verifier sees the same selected chain A protein only, transformed once.
        for line in (originals/(p+'.pdb')).read_text('utf-8').splitlines():
            if line.startswith('ENDMDL'):break
            if line.startswith('ATOM  ') and line[21:22]=='A':
                xyz=transform([float(line[30:38]),float(line[38:46]),float(line[46:54])],fits[p])
                lines.append(line[:30]+f'{xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}'+line[54:])
        (inp/(p+'-protein.pdb')).write_text('\n'.join(lines)+'\nEND\n',encoding='utf-8')
    molecules={};extractions={}
    for lid,meta in LIGANDS.items():
        mol,ex=old.ligand(originals/(meta['pdb']+'.pdb'),lid,meta['smiles'])
        mol=with_xyz(mol,transform(mol.GetConformer().GetPositions(),fits[meta['pdb']]))
        mol=Chem.AddHs(mol,addCoords=True)
        # Native MMFF typing can alter aromatic flags on the object it receives.
        # Applicability inspection must not mutate the graph used for freezing.
        if AllChem.MMFFGetMoleculeProperties(Chem.Mol(mol),mmffVariant='MMFF94s') is None: raise ValueError('missing MMFF')
        molecules[lid]=mol;extractions[lid]=ex
    systems=[];conditions=[];na=[]
    for lid,mol in molecules.items():
        other=next(m for n,m in molecules.items() if n!=lid)
        mapping=old.mapping(Chem.RemoveHs(other),Chem.RemoveHs(mol))
        if not mapping['applicable']:raise ValueError('common-core mapping not applicable')
        core=sorted({i for i,j in mapping['pairs']})
        for receptor in ['7KNW','7KNX']:
            sid=lid+'-'+receptor;sd=inp/sid;sd.mkdir(exist_ok=True)
            frame={'sha256':digest(fits),'bytes':len(json.dumps(fits,sort_keys=True,separators=(',',':')).encode())}
            parameters,typing=make_parameters(mol,receptors[LIGANDS[lid]['pdb']],receptors[receptor],core,frame)
            save_mol(sd/'base.sdf',mol);np.save(sd/'base.npy',mol.GetConformer().GetPositions())
            serialized=Chem.SDMolSupplier(str(sd/'base.sdf'),removeHs=False)[0]
            serialized_identity=validate_serialized_identity(mol,serialized)
            parameters['geometry']['atom_order_digest']=serialized_identity['consumed_atom_order_digest']
            torsion.compile_geometry(parameters['geometry'],serialized)
            write(sd/'parameters.json',parameters)
            save_mol(sd/'reference.sdf',Chem.RemoveHs(mol))
            system={'system_id':sid,'ligand':lid,'receptor':receptor,'native_or_cross':'native' if LIGANDS[lid]['pdb']==receptor else 'cross',
                    'base':'inputs/'+sid+'/base.sdf','base_xyz':'inputs/'+sid+'/base.npy','parameters':'inputs/'+sid+'/parameters.json',
                    'reference':'inputs/'+sid+'/reference.sdf','receptor_pdb':'inputs/'+receptor+'-protein.pdb',
                    'common_core_indices':core,'mapping_to_other_ligand':mapping,'extraction':extractions[lid],'typing':typing,
                    'torsion_fixed_indices':parameters['geometry']['hard']['fixed_indices'],'frame':frame,
                    'serialization_identity':serialized_identity}
            systems.append(system);seen={}
            for family,ranges in protocol()['initialization_families'].items():
                if ranges is None:
                    for seed in range(1,25):
                        na.append({'condition_id':f'{sid}-{family}-{seed:02d}','system_id':sid,'family':family,'seed':seed,
                                   'status':'not_applicable','reason':protocol()['docking_family_status'],'configurations':CONFIGS})
                    continue
                for seed in range(1,25):
                    angles=np.deg2rad(np.random.default_rng(seed).uniform(-np.array(ranges),np.array(ranges)))
                    # Initialization ranges can be wider than correction ranges.
                    init_dofs=copy.deepcopy(parameters['geometry']['dofs'])
                    for row in init_dofs:row['bounds_degrees']=[-180.,180.]
                    xyz=torsion.rotate(mol.GetConformer().GetPositions(),init_dofs,angles)
                    sha=hashlib.sha256(xyz.astype('<f8').tobytes()).hexdigest()
                    cid=f'{sid}-{family}-{seed:02d}'
                    if sha in seen:
                        na.append({'condition_id':cid,'system_id':sid,'family':family,'seed':seed,'status':'not_applicable',
                                   'reason':'duplicate coordinates','duplicate_of':seen[sha],'configurations':CONFIGS});continue
                    seen[sha]=cid
                    np.save(sd/(family+f'-{seed:02d}.npy'),xyz)
                    conditions.append({'condition_id':cid,'system_id':sid,'family':family,'seed':seed,
                                       'input_xyz':f'inputs/{sid}/{family}-{seed:02d}.npy','coordinate_sha256':sha,'angles_degrees':np.rad2deg(angles).tolist()})
    write(root/'systems.json',systems);write(root/'conditions.json',conditions);write(root/'not-applicable.json',na);write(root/'alignment.json',fits)
    files={str(p.relative_to(root)):pin(p) for p in sorted(inp.rglob('*')) if p.is_file()}
    for name in ['protocol.json','systems.json','conditions.json','not-applicable.json','alignment.json']:files[name]=pin(root/name)
    source={}
    for p in sorted((root/'source').rglob('*')):
        if p.is_file() and p.suffix in {'.py','.so'}:source[str(p)]=pin(p)
    native={}
    for name in ['rdkit.Chem.rdForceFieldHelpers','rdkit.Chem.rdMolTransforms','scipy.optimize._lbfgsb','numpy._core._multiarray_umath','etalon.research.geometry_input','etalon.research.scientific_input']:
        module=importlib.import_module(name);native[name]={'path':module.__file__,**pin(module.__file__)}
    freeze={'schema':'snd1-p1-freeze/1','frozen_before_optimization':True,'inputs':files,'source':source,'native':native,
            'python':sys.executable,'python_executable':pin(sys.executable),'protocol':pin(root/'protocol.json'),
            'input_originals':{str(originals/(p+'.pdb')):pin(originals/(p+'.pdb')) for p in raw},
            'conditions':len(conditions),'not_applicable_conditions':len(na),'expected_execution_calls':len(conditions)*5,
            'not_applicable_configuration_slots':len(na)*5,
            'initialization_cost':{'wall_seconds':time.perf_counter()-t,'cpu_seconds':time.process_time()-c}}
    write(root/'freeze.json',freeze)
    return freeze


def verify_freeze(root,freeze):
    root=Path(root)
    for relative,p in freeze['inputs'].items():
        if pin(root/relative)!=p:raise ValueError('frozen input changed: '+relative)
    for file,p in freeze['source'].items():
        if pin(file)!=p:raise ValueError('frozen source changed: '+file)
    for row in freeze['native'].values():
        if pin(row['path'])!={k:row[k] for k in ['sha256','bytes']}:raise ValueError('native import changed')


def load_base(root,system,xyz_path):
    mol=Chem.SDMolSupplier(str(Path(root)/system['base']),removeHs=False)[0]
    if mol is None:raise ValueError('unreadable frozen input')
    return with_xyz(mol,np.load(Path(root)/xyz_path))


def fixed_core(mol,indices):
    mol=Chem.Mol(mol);initial=mol.GetConformer().GetPositions().copy()
    props=AllChem.MMFFGetMoleculeProperties(mol,mmffVariant='MMFF94s')
    if props is None:raise ValueError('missing MMFF')
    ff=AllChem.MMFFGetMoleculeForceField(mol,props)
    for i in indices:ff.AddFixedPoint(i)
    ff.Initialize();before=float(ff.CalcEnergy());status=int(ff.Minimize(maxIts=1000))
    xyz=mol.GetConformer().GetPositions().copy()
    fixed_delta=float(np.max(np.abs(xyz[indices]-initial[indices])))
    if fixed_delta>1e-8:raise ValueError('fixed core moved')
    return mol,{'convergence':status,'initial_objective':before,'final_objective':float(ff.CalcEnergy()),
                'hard_invariants':{'valid':True,'fixed_max_A':fixed_delta,'bond_max_error_A':None,'angle_max_error_deg':None,
                                   'ring_distance_max_error_A':None,'hard_bounds_pass':None},
                'objective_scope':'MMFF94s internal only; native fixed-core control'},xyz


def controlled_torsion_parameters(parameters, *, half_width_degrees, pocket_terms):
    """Declare a range x pocket-term ablation without changing the torsion map.

    MMFF, positional anchors, hard geometry and optimizer settings are preserved.
    ``pocket_terms=False`` removes both reported contact distances and protein
    exclusion. Directional proxies are retained as diagnostic metadata; the
    caller must use ``torsion_distance`` so they never enter either objective.
    A wider range is a new conditional method, not a repair using a reference.
    """
    if type(pocket_terms) is not bool:
        raise ValueError('pocket_terms must be an explicit boolean')
    widths=list(half_width_degrees)
    if len(widths)!=len(parameters['geometry']['dofs']) or any(
            isinstance(v,bool) or not math.isfinite(v) or not 0<float(v)<=180 for v in widths):
        raise ValueError('one finite positive half width <=180 degrees per torsion required')
    result=copy.deepcopy(parameters)
    for row,width in zip(result['geometry']['dofs'],widths):
        row['bounds_degrees']=[-float(width),float(width)]
    if not pocket_terms:
        result['geometry']['soft_distances']=[]
        result['pocket']['force_constant']=0.
    return result


def supplementary(mol,initial,system,parameters,reference):
    xyz=mol.GetConformer().GetPositions();heavy=[a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum()>1]
    ref=reference.GetConformer().GetPositions();matches=reference.GetSubstructMatches(Chem.RemoveHs(mol),uniquify=False,maxMatches=2048,useChirality=True)
    if not matches or len(matches)==2048:raise ValueError('incomplete reference symmetry mapping')
    fixed=set(parameters['geometry']['hard']['fixed_indices']);free=[i for i in heavy if i not in fixed]
    best=min(matches,key=lambda m:float(np.sum((xyz[heavy]-ref[list(m)])**2)))
    full=float(np.sqrt(np.mean(np.sum((xyz[heavy]-ref[list(best)])**2,axis=1))))
    # Same symmetry choice as whole-ligand RMSD; no separate ligand fitting.
    local=float(np.sqrt(np.mean(np.sum((xyz[free]-ref[[best[heavy.index(i)] for i in free]])**2,axis=1)))) if free else None
    lp=torsion.legacy_parameters(parameters);metric=torsion.legacy.metrics(mol,lp)
    props=AllChem.MMFFGetMoleculeProperties(mol,mmffVariant='MMFF94s');ff=AllChem.MMFFGetMoleculeForceField(mol,props)
    internal=float(ff.CalcEnergy())
    deviations=[]
    for row,d in zip(parameters['geometry']['soft_distances'],metric['contact_distances_A']):
        deviations.append(max(row['lower_A']-d,0.,d-row['upper_A']))
    return {**metric,'symmetry_rmsd_A':full,'nonfixed_heavy_rmsd_A':local,'nonfixed_heavy_count':len(free),
            'internal_mmff_kcal_mol':internal,'contact_max_outside_window_A':max(deviations),
            'metric_scope':'supplementary geometry; physical validity awaits independent PoseBusters verifier'}


def completed_receipt(run,expected):
    if read(run/'request.json')!=expected:raise ValueError('changed request under existing attempt')
    if not (run/'receipt.json').exists():
        lease=read(run/'lease.json') if (run/'lease.json').exists() else None
        raise RuntimeError('UNKNOWN_OUTCOME no automatic retry: '+str(run)+' lease='+str(lease))
    row=read(run/'receipt.json')
    for name,identity in row.get('artifacts',{}).items():
        if pin(run/name)!=identity:raise ValueError('completed output changed')
    return row


def run_condition(root,condition,configurations=None,attempt_id='trial-1'):
    root=Path(root);systems={s['system_id']:s for s in read(root/'systems.json')};system=systems[condition['system_id']]
    parameters=read(root/system['parameters']);reference=Chem.SDMolSupplier(str(root/system['reference']),removeHs=False)[0]
    allowed=CONFIGS if configurations is None else configurations
    configs=allowed if condition['seed']%2 else list(reversed(allowed));result=[]
    for config in configs:
        rid=condition['condition_id']+'--'+config;run=root/'executions'/rid
        req={'schema':'snd1-p1-request/1','request_id':rid,'attempt_id':attempt_id,'system_id':system['system_id'],
             'ligand':system['ligand'],'receptor':system['receptor'],'family':condition['family'],'seed':condition['seed'],
             'configuration':config,'input_xyz':pin(root/condition['input_xyz']),'parameters':pin(root/system['parameters']),
             'freeze':pin(root/'freeze.json'),'protocol':pin(root/'protocol.json'),'execution_mode':'new_execution'}
        if run.exists():
            result.append(completed_receipt(run,req));continue
        run.mkdir(parents=True,exist_ok=False);write(run/'request.json',req)
        write(run/'lease.json',{'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().split()[21],
                               'cmdline':Path('/proc/self/cmdline').read_bytes().replace(b'\0',b' ').decode(),'started_unix':time.time()})
        st,sc=time.perf_counter(),time.process_time();row={**req,'status':'failed','artifacts':{},'error':None}
        try:
            mol=load_base(root,system,condition['input_xyz']);initial=mol.GetConformer().GetPositions().copy()
            t,c=time.perf_counter(),time.process_time()
            if config=='fixed_core':out,details,xyz=fixed_core(mol,system['common_core_indices'])
            else:
                method='torsion_directional' if config.startswith('torsion_directional') else 'torsion_distance'
                grad='cartesian_chain' if config.endswith('cartesian_chain') else 'finite_difference'
                out,details,trace,xyz=torsion.refine(mol,parameters,method,gradient_mode=grad)
                details['final_objective']=sum(details['final_terms'].values())
            solver={'wall_seconds':time.perf_counter()-t,'cpu_seconds':time.process_time()-c}
            if old.canonical_smiles(out)!=old.canonical_smiles(mol):raise ValueError('chemical identity changed')
            save_mol(run/'pose.sdf',out);np.save(run/'pose.npy',xyz)
            if config!='fixed_core':np.save(run/'torsion-trace.npy',trace)
            metrics=supplementary(out,initial,system,parameters,reference)
            row.update(status='completed',graph_exact=True,details=details,metrics=metrics,solver_cost=solver)
            row['artifacts']={p.name:pin(p) for p in run.iterdir() if p.suffix in {'.sdf','.npy'}}
        except Exception as exc:
            row['error']={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
        row['actual_consumption']={'wall_second':time.perf_counter()-st,'cpu_second':time.process_time()-sc}
        write(run/'receipt.json',row);result.append(row)
    return [r['request_id'] for r in result]


def summarize(root):
    root=Path(root);conditions=read(root/'conditions.json');systems=read(root/'systems.json');na=read(root/'not-applicable.json')
    receipts=[];pending=[];unknown=[]
    for cond in conditions:
        for config in CONFIGS:
            rid=cond['condition_id']+'--'+config;p=root/'executions'/rid
            if (p/'receipt.json').exists():
                row=read(p/'receipt.json')
                for n,v in row['artifacts'].items():
                    if pin(p/n)!=v:raise ValueError('artifact verification failed')
                receipts.append(row)
            elif (p/'request.json').exists():unknown.append(rid)
            else:pending.append(rid)
    lookup={r['request_id']:r for r in receipts};pairs=[]
    for cond in conditions:
        for objective in ['torsion_distance','torsion_directional']:
            aid=cond['condition_id']+'--'+objective+'_fd';bid=cond['condition_id']+'--'+objective+'_cartesian_chain'
            a,b=lookup.get(aid),lookup.get(bid)
            pair={'condition_id':cond['condition_id'],'system_id':cond['system_id'],'family':cond['family'],'objective':objective,'status':'unavailable'}
            if a and b and a['status']==b['status']=='completed':
                xa=np.load(root/'executions'/aid/'pose.npy');xb=np.load(root/'executions'/bid/'pose.npy')
                delta=float(np.max(np.linalg.norm(xa-xb,axis=1)))
                energy=abs(a['details']['final_objective']-b['details']['final_objective'])
                pair.update(status='compared',max_atom_delta_A=delta,absolute_objective_delta_kcal_mol=energy,
                            equivalent=delta<=.001 and energy<=.0001 and b['details']['hard_invariants']['valid'] and a['details']['hard_invariants']['valid'],
                            fd_solver_cost=a['solver_cost'],cg_solver_cost=b['solver_cost'])
            pairs.append(pair)
    groups=[]
    for system in systems:
        for family in ['small','medium','large']:
            for config in CONFIGS:
                rs=[r for r in receipts if r['system_id']==system['system_id'] and r['family']==family and r['configuration']==config]
                ok=[r for r in rs if r['status']=='completed'];rms=[r['metrics']['symmetry_rmsd_A'] for r in ok]
                groups.append({'system_id':system['system_id'],'family':family,'configuration':config,'expected':24,'received':len(rs),
                               'completed':len(ok),'failed':len(rs)-len(ok),'converged':sum(r['details']['convergence']==0 for r in ok),
                               'hard_invalid':sum(not r['details']['hard_invariants']['valid'] for r in ok),
                               'severe_clashes':sum(r['metrics']['severe_clash'] for r in ok),'median_symmetry_rmsd_A':float(np.median(rms)) if rms else None,
                               'physical_valid_success_count':None,'cost':{'wall_seconds':sum(r['actual_consumption']['wall_second'] for r in rs),
                                                                         'cpu_seconds':sum(r['actual_consumption']['cpu_second'] for r in rs)}})
    summary={'schema':'snd1-p1-summary/1','state':'completed' if not pending and not unknown else 'running' if pending else 'unresolved',
             'expected_execution_calls':len(conditions)*5,'received':len(receipts),'completed':sum(r['status']=='completed' for r in receipts),
             'failed':sum(r['status']=='failed' for r in receipts),'not_applicable_configuration_slots':len(na)*5,
             'pending':pending,'unknown_outcome':unknown,'solver_pairs':len(pairs),'compared_pairs':sum(p['status']=='compared' for p in pairs),
             'equivalence_pass':sum(p.get('equivalent',False) for p in pairs),'equivalence_fail':sum(p.get('equivalent') is False for p in pairs),
             'actual_consumption':{'wall_second_sum':sum(r['actual_consumption']['wall_second'] for r in receipts),
                                   'cpu_second':sum(r['actual_consumption']['cpu_second'] for r in receipts)},
             'initialization_cost':read(root/'freeze.json')['initialization_cost'],'scope':protocol()['scope'],
             'physical_validity':'pending separate immutable PoseBusters evaluator; not assumed from restraint objective'}
    write(root/'summary.json',summary);write(root/'group-statistics.json',groups);write(root/'solver-equivalence.json',pairs)
    return summary


def run(root):
    root=Path(root);freeze=read(root/'freeze.json');verify_freeze(root,freeze)
    # Exclusive controller; a stale controller requires explicit identity audit.
    lock=root/'controller.lock'
    fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    os.write(fd,json.dumps({'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().split()[21],
                            'started_unix':time.time(),'root':str(root)}).encode());os.close(fd)
    t,c=time.perf_counter(),time.process_time();start_child=resource.getrusage(resource.RUSAGE_CHILDREN)
    write(root/'controller-identity.json',{'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().split()[21],
          'affinity':sorted(os.sched_getaffinity(0)),'threads':{k:os.environ.get(k) for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']},
          'started_unix':time.time(),'freeze':pin(root/'freeze.json')})
    failures=[]
    try:
        conditions=read(root/'conditions.json')
        # Bound outstanding work so a resource stop does not silently dispatch the whole panel.
        with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
            cursor=iter(conditions);active={}
            for _ in range(8):
                cond=next(cursor,None)
                if cond is not None:active[pool.submit(run_condition,str(root),cond)]=cond['condition_id']
            while active:
                done,_=concurrent.futures.wait(active,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    cid=active.pop(future)
                    try:future.result()
                    except Exception as exc:failures.append({'condition_id':cid,'error':str(exc)})
                    summary=summarize(root)
                    write(root/'progress.json',{k:v for k,v in summary.items() if k not in ['pending','unknown_outcome']})
                    if failures:continue
                    if summary['actual_consumption']['cpu_second']>=protocol()['cpu_cap_seconds']:continue
                    cond=next(cursor,None)
                    if cond is not None:active[pool.submit(run_condition,str(root),cond)]=cond['condition_id']
        summary=summarize(root)
        child=resource.getrusage(resource.RUSAGE_CHILDREN)
        write(root/'controller-receipt.json',{'status':summary['state'],'errors':failures,
              'cost':{'wall_seconds':time.perf_counter()-t,'controller_cpu_seconds':time.process_time()-c,
                      'children_user_system_cpu_seconds':child.ru_utime+child.ru_stime-start_child.ru_utime-start_child.ru_stime},
              'summary':pin(root/'summary.json'),'finished_unix':time.time()})
        return summary
    finally:
        lock.unlink()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['initialize','run','status']);parser.add_argument('--root',required=True)
    parser.add_argument('--originals');args=parser.parse_args()
    if args.command=='initialize':result=initialize(args.root,args.originals)
    elif args.command=='run':result=run(args.root)
    else:result=summarize(args.root)
    print(json.dumps({k:v for k,v in result.items() if k not in ['inputs','source','native','pending','unknown_outcome']},sort_keys=True))
