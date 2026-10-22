"""Explicit SND1 bond rotations; no Cartesian relaxation or target-pose input."""
from __future__ import annotations
import argparse,copy,hashlib,json,math,time
from pathlib import Path
import numpy as np
import scipy
from scipy.optimize import minimize
from rdkit import Chem,RDConfig
from rdkit.Chem import AllChem,ChemicalFeatures,rdMolTransforms
import snd1_evidence_workflow as legacy
from etalon.research.geometry_input import SCHEMA,validate_geometry,geometry_evidence_ids
from etalon.research.scientific_input import compile_input,consumption_receipt,identity

TARGETS={'geometry':'constraint','pocket':'objective','binding_support':'selection','optimizer':'objective'}
read,write=legacy.read,legacy.write


def atom_order(mol):
    body={'atoms':[(a.GetAtomicNum(),a.GetFormalCharge(),a.GetIsAromatic(),a.GetIsotope(),str(a.GetChiralTag())) for a in mol.GetAtoms()],
        'bonds':[(b.GetBeginAtomIdx(),b.GetEndAtomIdx(),str(b.GetBondType()),str(b.GetStereo()),list(b.GetStereoAtoms())) for b in mol.GetBonds()]}
    return hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()


def component(mol,a,b):
    seen={b};stack=[b]
    while stack:
        i=stack.pop()
        for atom in mol.GetAtomWithIdx(i).GetNeighbors():
            j=atom.GetIdx()
            if {i,j}=={a,b}:continue
            if j not in seen:seen.add(j);stack.append(j)
    if a in seen:raise ValueError('rotating a ring bond is forbidden')
    return sorted(seen-{b})


def typed_acceptors(mol):
    path=Path(RDConfig.RDDataDir)/'BaseFeatures.fdef'
    factory=ChemicalFeatures.BuildFeatureFactory(str(path))
    features=factory.GetFeaturesForMol(mol)
    return {i for f in features if f.GetFamily()=='Acceptor' for i in f.GetAtomIds()}, {'path':str(path),**identity(path)}


def compile_geometry(g,mol,evidence_ids=None):
    g=validate_geometry(g,available_evidence_ids=evidence_ids)
    if g['atom_count']!=mol.GetNumAtoms() or atom_order(mol)!=g['atom_order_digest']:raise ValueError('molecular atom-order identity changed')
    for row in g['dofs']:
        a,b=row['axis'];bond=mol.GetBondBetweenAtoms(a,b)
        if bond is None or bond.IsInRing() or bond.GetBondType()!=Chem.BondType.SINGLE:raise ValueError('only explicitly selected acyclic single bonds may rotate')
        if component(mol,a,b)!=row['moving_indices']:raise ValueError('moving atoms differ from the complete downstream graph component')
    acceptors,feature_source=typed_acceptors(mol)
    for row in g['directions']:
        i=row['acceptor_index'];atom=mol.GetAtomWithIdx(i)
        if i not in acceptors:raise ValueError('directional acceptor conflicts with the declared chemical graph')
        if row['k_axis_kcal_mol_rad2']:
            neighbors=sorted(n.GetIdx() for n in atom.GetNeighbors() if n.GetAtomicNum()>1)
            if atom.GetAtomicNum()!=7 or not atom.GetIsAromatic() or neighbors!=sorted(row['acceptor_neighbors']):
                raise ValueError('acceptor axis is only defined for the declared aromatic nitrogen')
    return g,feature_source


def unit(v):
    norm=np.linalg.norm(v)
    if norm<1e-10:raise ValueError('degenerate geometric direction')
    return v/norm


def rotate(initial,dofs,angles):
    xyz=np.array(initial,copy=True)
    if len(angles)!=len(dofs):raise ValueError('wrong number of rotation variables')
    for row,angle in zip(dofs,angles):
        lower,upper=np.deg2rad(row['bounds_degrees'])
        if not lower-1e-12<=angle<=upper+1e-12:raise ValueError('hard torsion bound exceeded')
        a,b=row['axis'];axis=unit(xyz[b]-xyz[a]);indices=row['moving_indices']
        v=xyz[indices]-xyz[b];co,si=np.cos(angle),np.sin(angle)
        xyz[indices]=xyz[b]+v*co+np.cross(axis,v)*si+np.outer(v@axis,axis)*(1-co)
    return xyz


def direction_terms(xyz,geometry):
    values=[]
    for row in geometry['directions']:
        acceptor=xyz[row['acceptor_index']];donor=np.array(row['donor_xyz']);hydrogen=np.array(row['hydrogen_xyz'])
        dha=float(np.arccos(np.clip(unit(donor-hydrogen)@unit(acceptor-hydrogen),-1,1)))
        energy=.5*row['k_DHA_kcal_mol_rad2']*(np.pi-dha)**2
        axis_angle=None;axis_energy=0.
        if row['k_axis_kcal_mol_rad2']:
            axis=unit(-sum((unit(xyz[j]-acceptor) for j in row['acceptor_neighbors']),np.zeros(3)))
            axis_angle=float(np.arccos(np.clip(axis@unit(donor-acceptor),-1,1)))
            axis_energy=.5*row['k_axis_kcal_mol_rad2']*axis_angle**2
        values.append({'id':row['id'],'DHA_degrees':float(np.rad2deg(dha)),'DHA_energy':energy,
            'acceptor_axis_degrees':None if axis_angle is None else float(np.rad2deg(axis_angle)),
            'axis_energy':axis_energy,'energy':energy+axis_energy})
    return values


def legacy_parameters(parameters):
    g=parameters['geometry']
    # Compatibility adapter reuses the previously tested Cartesian soft terms.
    if len({(r['flat_bottom_A'],r['k_kcal_mol_A2']) for r in g['soft_positions']})!=1:
        raise ValueError('this consumer requires a common positional radius/coefficient')
    p=g['soft_positions'][0]
    return {'pocket':parameters['pocket'],
        'contacts':[{'ligand_index':r['ligand_index'],'protein_xyz':r['protein_xyz'],'lower_A':r['lower_A'],'upper_A':r['upper_A'],'force_constant':r['k_kcal_mol_A2']} for r in g['soft_distances']],
        'mobility':{'core':[{'index':r['index'],'xyz':r['xyz']} for r in g['soft_positions']],
                    'max_displacement_A':p['flat_bottom_A'],'force_constant':p['k_kcal_mol_A2']}}


def geometry_invariants(mol,initial,xyz,g,angles):
    bonds=[(b.GetBeginAtomIdx(),b.GetEndAtomIdx()) for b in mol.GetBonds()]
    bond_error=max(abs(np.linalg.norm(xyz[a]-xyz[b])-np.linalg.norm(initial[a]-initial[b])) for a,b in bonds)
    angle_error=0.
    for atom in mol.GetAtoms():
        center=atom.GetIdx();neighbors=[n.GetIdx() for n in atom.GetNeighbors()]
        for k,a in enumerate(neighbors):
            for b in neighbors[k+1:]:
                def angle(x):return np.arccos(np.clip(unit(x[a]-x[center])@unit(x[b]-x[center]),-1,1))
                angle_error=max(angle_error,abs(float(np.rad2deg(angle(xyz)-angle(initial)))))
    ring_error=0.
    for ring in mol.GetRingInfo().AtomRings():
        for a in ring:
            for b in ring:ring_error=max(ring_error,abs(float(np.linalg.norm(xyz[a]-xyz[b])-np.linalg.norm(initial[a]-initial[b]))))
    fixed_error=float(np.abs(xyz[g['hard']['fixed_indices']]-initial[g['hard']['fixed_indices']]).max())
    bounds=all(lo-1e-10<=v<=hi+1e-10 for v,(lo,hi) in zip(angles,[np.deg2rad(r['bounds_degrees']) for r in g['dofs']]))
    valid=fixed_error<=g['hard']['position_tolerance_A'] and bond_error<=g['hard']['bond_tolerance_A'] and angle_error<=g['hard']['angle_tolerance_deg'] and ring_error<=g['hard']['bond_tolerance_A'] and bounds
    return {'valid':bool(valid),'fixed_max_A':fixed_error,'bond_max_error_A':float(bond_error),'angle_max_error_deg':angle_error,'ring_distance_max_error_A':ring_error,'hard_bounds_pass':bool(bounds)}


def refine(mol,parameters,method):
    mol=Chem.Mol(mol);initial=mol.GetConformer().GetPositions().copy();g,feature=compile_geometry(parameters['geometry'],mol)
    params=legacy_parameters(parameters);numbers=[a.GetAtomicNum() for a in mol.GetAtoms()]
    props=AllChem.MMFFGetMoleculeProperties(mol,mmffVariant='MMFF94s')
    if props is None:raise ValueError('missing MMFF parameters')
    ff=AllChem.MMFFGetMoleculeForceField(mol,props);ff.Initialize()
    directional=method=='torsion_directional'
    def terms(q):
        xyz=rotate(initial,g['dofs'],q);mmff=ff.CalcEnergy(tuple(xyz.ravel()))
        external,_=legacy.external_terms(xyz,numbers,params);ds=direction_terms(xyz,g)
        return {'mmff':mmff,**external,'directional':sum(r['energy'] for r in ds) if directional else 0.}
    def objective(q):return sum(terms(q).values())
    trace=[np.zeros(len(g['dofs']))]
    result=minimize(objective,trace[0],method='L-BFGS-B',jac='3-point',bounds=[np.deg2rad(r['bounds_degrees']) for r in g['dofs']],
        callback=lambda q:trace.append(q.copy()),options=parameters['optimizer'])
    xyz=rotate(initial,g['dofs'],result.x)
    check=geometry_invariants(mol,initial,xyz,g,result.x)
    if not check['valid']:raise ValueError('hard molecular geometry invariant violated')
    for i,pos in enumerate(xyz):mol.GetConformer().SetAtomPosition(i,pos)
    active=[]
    for q,row in zip(np.rad2deg(result.x),g['dofs']):
        if min(abs(q-row['bounds_degrees'][0]),abs(q-row['bounds_degrees'][1]))<1e-4:active.append(row['id'])
    sensitivity=[]
    q0=np.zeros(len(g['dofs']));eps=1e-5
    for interaction in range(len(g['directions'])):
        derivative=[]
        for k in range(len(q0)):
            a=q0.copy();b=q0.copy();a[k]+=eps;b[k]-=eps
            derivative.append((direction_terms(rotate(initial,g['dofs'],a),g)[interaction]['energy']-direction_terms(rotate(initial,g['dofs'],b),g)[interaction]['energy'])/(2*eps))
        sensitivity.append({'id':g['directions'][interaction]['id'],'initial_energy_derivative_per_radian':derivative,
            'affects_allowed_motion':bool(np.linalg.norm(derivative)>1e-8)})
    return mol,{'convergence':0 if result.success else 1,'optimizer_status':int(result.status),'message':str(result.message),
        'iterations':int(result.nit),'nfev':int(result.nfev),'initial_terms':terms(q0),'final_terms':terms(result.x),
        'torsion_delta_degrees':np.rad2deg(result.x).tolist(),'active_hard_bounds':active,
        'directional_applied':directional,'initial_direction':direction_terms(initial,g),'final_direction':direction_terms(xyz,g),
        'directional_sensitivity':sensitivity,'hard_invariants':check,'typing_source':feature},np.array(trace),xyz


def virtual_hydrogen(donor,parent,plane,acceptor):
    u=unit(parent-donor);normal=unit(np.cross(parent-donor,plane-parent));v=unit(np.cross(normal,u))
    hydrogens=[donor+1.01*(-.5*u+sign*np.sqrt(.75)*v) for sign in [-1,1]]
    return min(hydrogens,key=lambda h:np.linalg.norm(h-acceptor))


def build_bundle(old_bundle_path,baseline,protocol,*,task_id):
    bundle=read(old_bundle_path);params=compile_input(bundle,allowed_targets=legacy.TARGETS)['parameters']
    mol=Chem.SDMolSupplier(str(Path(baseline)/'pose-001.sdf'),removeHs=False)[0]
    # Select by chemical connectivity, never experimental answer coordinates.
    sulfur=[a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum()==16]
    if len(sulfur)!=1:raise ValueError('requires one unambiguous sulfonamide')
    s=sulfur[0];neighbors=list(mol.GetAtomWithIdx(s).GetNeighbors())
    n=next(a.GetIdx() for a in neighbors if a.GetAtomicNum()==7)
    aryl=next(a.GetIdx() for a in neighbors if a.GetAtomicNum()==6)
    hetero=next(a.GetIdx() for a in mol.GetAtomWithIdx(n).GetNeighbors() if a.GetAtomicNum()==6)
    ether=[a for a in mol.GetAtoms() if a.GetAtomicNum()==8 and len([x for x in a.GetNeighbors() if x.GetAtomicNum()==6])==2]
    if len(ether)!=1:raise ValueError('requires one methoxy ether')
    o=ether[0].GetIdx();oc=next(a.GetIdx() for a in ether[0].GetNeighbors() if a.GetIsAromatic())
    dofs=[]
    for name,a,b,bound in [('phenyl_sulfonyl',aryl,s,10),('sulfonyl_N',s,n,10),('N_heteroaryl',n,hetero,10),('methoxy',oc,o,20)]:
        dofs.append({'id':name,'axis':[a,b],'moving_indices':component(mol,a,b),'bounds_degrees':[-bound,bound],
            'zero':'input_pose','evidence_ids':['geometry-assumptions','graph'],'rationale':'Host-selected narrow bond rotation; conjugation is retained in MMFF and bound, not treated as unrestricted rotation'})
    moving=set(i for d in dofs for i in d['moving_indices']);coords={r['index']:np.array(r['xyz']) for r in params['mobility']['core']}
    acceptors,feature=typed_acceptors(mol);directions=[];exclusions=[]
    atoms=params['pocket']['atoms']
    for contact in params['contacts']:
        i=contact['ligand_index'];key=contact['protein_key']
        if i not in acceptors:
            exclusions.append({'id':contact['template_atom_name']+'--'+key[-1],
                'reason':'Not a graph-typed acceptor in this chemical state; preserve reported distance only, no donor-donor H-bond direction'})
            continue
        if key[-1] not in {'NH2','ND2'}:raise ValueError('unsupported donor hydrogen construction')
        parent_name,plane_name=('CZ','NE') if key[-1]=='NH2' else ('CG','OD1')
        def position(name):
            found=[a for a in atoms if a['key'][:-1]==key[:-1] and a['key'][-1]==name]
            if len(found)!=1:raise ValueError('missing exact donor neighborhood')
            return np.array(found[0]['xyz'])
        donor=position(key[-1]);h=virtual_hydrogen(donor,position(parent_name),position(plane_name),coords[i])
        atom=mol.GetAtomWithIdx(i);axis_neighbors=sorted(a.GetIdx() for a in atom.GetNeighbors() if a.GetAtomicNum()>1) if atom.GetAtomicNum()==7 and atom.GetIsAromatic() else []
        directions.append({'id':contact['template_atom_name']+'--'+key[-1],'kind':'explicit_DHA_proxy','acceptor_index':i,
            'acceptor_type':'graph_typed_acceptor','donor_key':key,'donor_xyz':donor.tolist(),'hydrogen_xyz':h.tolist(),
            'hydrogen_origin':'constructed_planar','donor_state_assumption':'Arg guanidinium +1' if key[-1]=='NH2' else 'Asn neutral amide',
            'target_DHA_degrees':180,'k_DHA_kcal_mol_rad2':2.,'acceptor_neighbors':axis_neighbors,
            'k_axis_kcal_mol_rad2':2. if axis_neighbors else 0.,'evidence_ids':['contact-observation','geometry-assumptions','feature-typing']})
    geometry={'schema':SCHEMA,'representation':'bounded_bond_rotations','atom_count':mol.GetNumAtoms(),'atom_order_digest':atom_order(mol),
        'coordinate_frame':bundle['subject']['coordinate_frame'],
        'chemical_state':{'ligand_smiles':legacy.old.canonical_smiles(mol),'formal_charge':Chem.GetFormalCharge(mol),
            'assumptions':['Exact input tautomer/protonation; no enumeration','Constructed planar protein H; not experimentally observed or fully prepared receptor']},
        'hard':{'fixed_indices':sorted(set(range(mol.GetNumAtoms()))-moving),'reference':'per_execution_input','preserve':'bond_lengths_angles_and_rings',
            'position_tolerance_A':1e-8,'bond_tolerance_A':1e-8,'angle_tolerance_deg':1e-5},'dofs':dofs,
        'soft_positions':[{'index':r['index'],'xyz':r['xyz'],'flat_bottom_A':.25,'k_kcal_mol_A2':5.,'evidence_ids':['template','geometry-assumptions']} for r in params['mobility']['core']],
        'soft_distances':[{'id':r['template_atom_name']+'--'+r['protein_key'][-1],'ligand_index':r['ligand_index'],'protein_key':r['protein_key'],
            'protein_xyz':r['protein_xyz'],'lower_A':r['lower_A'],'upper_A':r['upper_A'],'k_kcal_mol_A2':10.,
            'interpretation':'reported_contact_distance','evidence_ids':['contact-observation','geometry-assumptions']} for r in params['contacts']],
        'directions':directions,'direction_exclusions':exclusions,'scope':'SND1 C26, limited torsional preparation, heuristic directional proxies'}
    scope=geometry['scope'];bundle=copy.deepcopy(bundle);bundle['task_id']=task_id;bundle['scope']=scope
    bundle['sources'] += [{'id':'geometry-protocol','path':str(Path(protocol).absolute()),**identity(protocol),'locator':'specified torsions, soft constants, virtual H assumptions','role':'conditioning','scope':scope},
        {'id':'feature-typing',**feature,'locator':'RDKit graph donor/acceptor rules','role':'conditioning','scope':scope}]
    bundle['interpretations'].append({'id':'geometry-assumptions','role':'conditioning','scope':scope,
        'basis_ids':['geometry-protocol','graph','template','receptor','contact-observation','feature-typing'],
        'assumption':'Specified bounded rotations preserve internal geometry; directional virtual-H proxies may improve normal pose fidelity; no affinity interpretation'})
    parameters={'geometry':geometry,'pocket':params['pocket'],'binding_support':params['binding_support'],
        'optimizer':{'maxiter':500,'maxls':40,'ftol':1e-12,'gtol':1e-5}}
    bases={'geometry':['geometry-assumptions'],'pocket':['pocket-observation','geometry-protocol'],
        'binding_support':['template-support'],'optimizer':['geometry-protocol']}
    bundle['bindings']=[{'id':'torsion-'+k,'target':k,'use':TARGETS[k],'value':v,'basis_ids':bases[k],
        'rationale':'Explicit domain semantics; see frozen geometry protocol'} for k,v in parameters.items()]
    compile_input(bundle,allowed_targets=TARGETS)
    compile_geometry(geometry,mol,geometry_evidence_ids(bundle))
    return bundle


def generate(bundle_path,baseline,output,method):
    if method not in {'torsion_directional','torsion_distance'}:raise ValueError('unknown method')
    bundle=read(bundle_path);compiled=compile_input(bundle,allowed_targets=TARGETS);parameters=compiled['parameters']
    validate_geometry(parameters['geometry'],available_evidence_ids=geometry_evidence_ids(bundle))
    if parameters['geometry']['coordinate_frame']!=bundle['subject']['coordinate_frame']:raise ValueError('geometry and subject coordinate frames differ')
    baseline,output=Path(baseline),Path(output);base=read(baseline/'result.json')
    for name,pin in base['artifacts'].items():
        if identity(baseline/name)!=pin:raise ValueError('baseline identity changed')
    request={'bundle':identity(bundle_path),'baseline':identity(baseline/'result.json'),'method':method,
        'runtime':legacy.old.runtime(),'scipy':{'version':scipy.__version__,'path':scipy.__file__,**identity(scipy.__file__)},
        'source':{'path':__file__,**identity(__file__)},'contract_source':identity(__import__('etalon.research.geometry_input',fromlist=['x']).__file__),
        'soft_term_source':identity(legacy.__file__)}
    if output.exists():
        if read(output/'request.json')!=request:raise ValueError('changed request')
        previous=read(output/'result.json')
        for name,pin in previous['artifacts'].items():
            if identity(output/name)!=pin:raise ValueError('changed completed artifact')
        return previous
    if not parameters['binding_support']['eligible']:raise ValueError('outside direct-binding template applicability')
    output.mkdir(parents=True);write(output/'request.json',request);rows=[];t,cpu=time.perf_counter(),time.process_time()
    for start in base['rows']:
        st=time.perf_counter();row={'slot':start['slot'],'seed':start['seed'],'status':'failed','pose':None}
        try:
            mol=Chem.SDMolSupplier(str(baseline/start['pose']),removeHs=False)[0]
            result,details,trace,xyz=refine(mol,parameters,method)
            if legacy.old.canonical_smiles(result)!=legacy.old.canonical_smiles(mol):raise ValueError('chemical identity changed')
            name=f"pose-{start['slot']:03d}.sdf"
            with Chem.SDWriter(str(output/name)) as w:w.write(result)
            np.savez_compressed(output/name.replace('.sdf','.npz'),xyz=xyz,angles=trace)
            saved=Chem.SDMolSupplier(str(output/name),removeHs=False)[0]
            row.update(status='completed',pose=name,pose_identity=identity(output/name),graph_exact=True,**details,
                **legacy.metrics(saved,legacy_parameters(parameters)))
        except Exception as exc:row['error']={'type':type(exc).__name__,'message':str(exc)}
        row['cost']={'wall_seconds':time.perf_counter()-st};rows.append(row);write(output/'progress.json',{'rows':rows})
    result={'schema':'snd1-torsion-execution/1','rows':rows,'mapping':bundle['subject']['mapping'],'request':request,
        'cost':{'wall_seconds':time.perf_counter()-t,'cpu_seconds':time.process_time()-cpu},
        'consumption':consumption_receipt(compiled,consumed_targets=TARGETS),
        'geometry_subconsumption':{'dofs':'applied','soft':'applied','directions':'applied' if method=='torsion_directional' else 'deliberately disabled in declared ablation'},
        'artifacts':{p.name:identity(p) for p in output.iterdir() if p.suffix in {'.sdf','.npz'}}}
    write(output/'result.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ('bundle','baseline','output','method'):p.add_argument('--'+key,required=True)
    args=p.parse_args();r=generate(args.bundle,args.baseline,args.output,args.method)
    print(json.dumps({'completed':sum(x['status']=='completed' for x in r['rows']),'converged':sum(x.get('convergence')==0 for x in r['rows']),'cost':r['cost']}))
