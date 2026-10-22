"""SND1 evidence-grounded local refinement of existing poses.

Protein exclusion and reported contacts enter the optimization gradient. The
energy is an explicitly heuristic restraint objective, NOT binding free energy.
Existing identity, template mapping and receptor-frame evaluation are reused.
"""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path
import numpy as np
import scipy
from scipy.optimize import minimize
from rdkit import Chem
from rdkit.Chem import AllChem
import snd1_pose_workflow as old
from etalon.research.scientific_input import SCHEMA, compile_input, consumption_receipt, identity

TARGETS={'pocket':'objective','contacts':'constraint','mobility':'constraint',
         'binding_support':'selection','optimizer':'objective'}


def read(p): return json.loads(Path(p).read_text('utf-8'))
def write(p,x): old.write(p,x)


def direct_binding_supported(activity, molecule_id):
    return bool(activity.get('molecule_id')==molecule_id and activity.get('target')=='SND1'
        and activity.get('endpoint')=='Kd' and activity.get('assay','').startswith('MST')
        and activity.get('status')=='quantified' and activity.get('relation')=='='
        and activity.get('unit') in {'uM','nM','M'}
        and type(activity.get('value')) in (float,int) and np.isfinite(activity['value']) and activity['value']>0)


def build_input(input_dir, activity_path, paper_path, table_path, protocol_path, *, task_id):
    root=Path(input_dir); body=read(root/'input.json')
    template=Chem.SDMolSupplier(str(root/'template.sdf'),removeHs=False)[0]
    target=Chem.MolFromSmiles(body['target_smiles']); match=old.mapping(template,target)
    if not match['applicable']: raise ValueError('outside template applicability')
    mapped={j:i for i,j in match['pairs']}
    names={n:i for i,n in enumerate(body['extraction']['atom_names'])}
    xyz=template.GetConformer().GetPositions(); receptor=read(root/'receptor.json')
    # Keep exact chain/model/residue/atom identity; exclude lattice-neighbor contacts.
    atoms=[a for a in receptor['atoms'] if a['element']!='H' and np.min(np.linalg.norm(xyz-np.array(a['xyz']),axis=1))<=8.0]
    contacts=[]
    for lig,res,atom in [('N','255','NE'),('N2','255','NH2'),('O','281','ND2')]:
        hits=[a for a in atoms if a['key'][2]==res and a['key'][-1]==atom]
        if len(hits)!=1 or names[lig] not in mapped: raise ValueError('ambiguous interaction grounding')
        d=float(np.linalg.norm(xyz[names[lig]]-hits[0]['xyz']))
        contacts.append({'ligand_index':mapped[names[lig]],'template_atom_name':lig,
            'protein_key':hits[0]['key'],'protein_xyz':hits[0]['xyz'],'observed_distance_A':d,
            'lower_A':max(2.4,d-.30),'upper_A':d+.30,'force_constant':10.0,
            'measurement':'heavy-atom distance; hydrogen-bond direction/protonation not validated'})
    # Anchor the chloromethoxyphenyl ring in the known W401 pocket. Release the
    # remaining mapped heavy atoms with a finite flat-bottom position restraint.
    rings=[list(r) for r in template.GetRingInfo().AtomRings() if len(r)==6 and all(template.GetAtomWithIdx(i).GetAtomicNum()==6 for i in r)]
    if len(rings)!=1: raise ValueError('ambiguous phenyl anchor')
    anchors=sorted(mapped[j] for j in rings[0])
    core=[{'index':i,'xyz':xyz[j].tolist()} for i,j in match['pairs']]
    activity=next(x for x in read(activity_path)['observations'] if x['molecule_id']==body['extraction']['ccd'])
    supported=direct_binding_supported(activity,body['extraction']['ccd'])
    scope='SND1 C26 near-neighbor, single chain, retrospective structural refinement'
    def source(rid,path,locator):return {'id':rid,'path':str(Path(path).absolute()),**identity(path),'locator':locator,'role':'conditioning','scope':scope}
    sources=[source('template',root/'template.sdf','one experimental template ligand, named atoms'),
        source('receptor',root/'receptor.json','chain '+receptor['chain']),source('graph',root/'input.json','target graph and exact template mapping'),
        source('paper',paper_path,'Results: C26s block SND1 pocket; MST Methods'),
        source('table',table_path,activity['source_cells']),source('activity-source',activity_path,'glyph-aware extraction and exact source cells'),
        source('protocol',protocol_path,'frozen host-selected numerical assumptions')]
    observations=[{'id':'pocket-observation','kind':'structure','role':'conditioning','scope':scope,'basis_ids':['template','receptor','graph'],
                   'data':{'radius_A':8.0,'atom_count':len(atoms),'excluded':'waters, other chains and crystal symmetry contacts'}},
        {'id':'contact-observation','kind':'interaction','role':'conditioning','scope':scope,'basis_ids':['paper','template','receptor','graph'],
         'data':contacts},
        {'id':'activity-observation','kind':'activity','role':'conditioning','scope':scope,'basis_ids':['table','paper','activity-source'],'data':activity}]
    interpretations=[{'id':'local-hypothesis','role':'conditioning','scope':scope,'basis_ids':['pocket-observation','contact-observation','protocol'],
        'assumption':'A phenyl anchor plus finite positional restraints and template contacts may allow useful local relaxation; constants are host assumptions, not fitted affinity.'},
        {'id':'template-support','role':'conditioning','scope':scope,'basis_ids':['activity-observation','contact-observation'],
         'assumption':'Reported direct binding supports trying contact-guided refinement for this exact template, with low-SNR caution; no relative affinity claim.'}]
    parameters={'pocket':{'atoms':atoms,'steric_scale':.75,'force_constant':20.0},'contacts':contacts,
        'mobility':{'fixed_indices':anchors,'core':core,'max_displacement_A':.75,'force_constant':5.0},
        'binding_support':{'eligible':supported,'molecule_id':activity['molecule_id'],'endpoint':'Kd','assay':activity['assay'],
                           'use':'template eligibility only; magnitude is not a force weight'},
        'optimizer':{'maxiter':500,'ftol':1e-9,'gtol':1e-4,'maxls':40}}
    basis={'pocket':['pocket-observation','protocol'],'contacts':['contact-observation','protocol'],
        'mobility':['local-hypothesis','graph'],'binding_support':['template-support'],'optimizer':['protocol']}
    uses={'pocket':'objective','contacts':'constraint','mobility':'constraint','binding_support':'selection','optimizer':'objective'}
    return {'schema':SCHEMA,'task_id':task_id,'scope':scope,'subject':{'target':'SND1','graph':body['target_smiles'],'template_ccd':body['extraction']['ccd'],
            'coordinate_frame':identity(root/'receptor.json'),'mapping':match},
        'sources':sources,'observations':observations,'interpretations':interpretations,
        'bindings':[{'id':'bind-'+k,'target':k,'use':uses[k],'basis_ids':basis[k],'value':v,
                     'rationale':'Explicit domain grounding; see source observations and host assumptions'} for k,v in parameters.items()]}


def external_terms(xyz, atomic_numbers, parameters):
    """Return separate heuristic energies and their exact Cartesian gradient."""
    grad=np.zeros_like(xyz); energies={}
    p=parameters['pocket']; protein=np.array([a['xyz'] for a in p['atoms']])
    table=Chem.GetPeriodicTable(); heavy=np.flatnonzero(np.array(atomic_numbers)>1)
    radii=np.array([table.GetRvdw(int(atomic_numbers[i])) for i in heavy])
    pr=np.array([table.GetRvdw(table.GetAtomicNumber(a['element'])) for a in p['atoms']])
    delta=xyz[heavy,None,:]-protein[None,:,:];d=np.maximum(np.linalg.norm(delta,axis=2),1e-8)
    overlap=np.minimum(d-p['steric_scale']*(radii[:,None]+pr[None,:]),0)
    energies['protein_exclusion']=float(.5*p['force_constant']*np.sum(overlap**2))
    grad[heavy]+=np.sum(p['force_constant']*overlap[:,:,None]*delta/d[:,:,None],axis=1)
    contact_energy=0.
    for c in parameters['contacts']:
        i=c['ligand_index'];v=xyz[i]-c['protein_xyz'];dist=max(float(np.linalg.norm(v)),1e-8)
        displacement=dist-min(max(dist,c['lower_A']),c['upper_A'])
        contact_energy+=.5*c['force_constant']*displacement**2
        grad[i]+=c['force_constant']*displacement*v/dist
    energies['contact_distance']=float(contact_energy)
    m=parameters['mobility'];position_energy=0.
    for a in m['core']:
        i=a['index'];v=xyz[i]-a['xyz'];dist=max(float(np.linalg.norm(v)),1e-8)
        displacement=max(0,dist-m['max_displacement_A'])
        position_energy+=.5*m['force_constant']*displacement**2
        grad[i]+=m['force_constant']*displacement*v/dist
    energies['position_restraint']=float(position_energy)
    return energies,grad


def refine_pose(mol, parameters):
    mol=Chem.Mol(mol);conf=mol.GetConformer();initial=conf.GetPositions().copy()
    props=AllChem.MMFFGetMoleculeProperties(mol,mmffVariant='MMFF94s')
    if props is None: raise ValueError('MMFF properties missing')
    ff=AllChem.MMFFGetMoleculeForceField(mol,props);ff.Initialize()
    fixed=set(parameters['mobility']['fixed_indices']);free=[i for i in range(mol.GetNumAtoms()) if i not in fixed]
    numbers=[a.GetAtomicNum() for a in mol.GetAtoms()]
    def objective(values):
        xyz=initial.copy();xyz[free]=values.reshape(-1,3);flat=tuple(xyz.ravel())
        energy=ff.CalcEnergy(flat);gradient=np.array(ff.CalcGrad(flat)).reshape(-1,3)
        extra,g=external_terms(xyz,numbers,parameters)
        return energy+sum(extra.values()),(gradient+g)[free].ravel()
    initial_energy=objective(initial[free].ravel())[0]
    result=minimize(objective,initial[free].ravel(),method='L-BFGS-B',jac=True,options=parameters['optimizer'])
    xyz=initial.copy();xyz[free]=result.x.reshape(-1,3)
    for i,pos in enumerate(xyz): conf.SetAtomPosition(i,pos)
    extra,grad=external_terms(xyz,numbers,parameters)
    return mol,{'convergence':0 if result.success else 1,'optimizer_status':int(result.status),'message':str(result.message),
        'iterations':int(result.nit),'initial_objective':initial_energy,'final_objective':float(result.fun),
        'internal_mmff_kcal_mol':float(ff.CalcEnergy(tuple(xyz.ravel()))),'restraint_terms':extra,
        'max_fixed_displacement_A':float(np.linalg.norm(xyz[list(fixed)]-initial[list(fixed)],axis=1).max()),
        'scope':'heuristic preparation objective; not docking score or binding energy'}


def metrics(mol, parameters):
    xyz=Chem.RemoveHs(mol).GetConformer().GetPositions();p=parameters['pocket']
    minimum=float(np.linalg.norm(xyz[:,None,:]-np.array([a['xyz'] for a in p['atoms']])[None,:,:],axis=2).min())
    distances=[float(np.linalg.norm(xyz[c['ligand_index']]-c['protein_xyz'])) for c in parameters['contacts']]
    satisfied=[c['lower_A']-1e-3<=d<=c['upper_A']+1e-3 for c,d in zip(parameters['contacts'],distances)]
    return {'minimum_protein_heavy_distance_A':minimum,'severe_clash':minimum<1.5,
            'contact_distances_A':distances,'contacts_in_window':sum(satisfied),'contacts_total':len(satisfied)}


def generate(bundle_path, base_dir, output):
    t,cpu=time.perf_counter(),time.process_time();bundle=read(bundle_path);compiled=compile_input(bundle,allowed_targets=TARGETS)
    parameters=compiled['parameters'];base_dir=Path(base_dir);output=Path(output);base=read(base_dir/'result.json')
    for name,pin in base['artifacts'].items():
        if identity(base_dir/name)!=pin:raise ValueError('baseline pose changed')
    request={'bundle':identity(bundle_path),'baseline':identity(base_dir/'result.json'),'runtime':old.runtime(),
             'scipy':{'version':scipy.__version__,'path':scipy.__file__,**identity(scipy.__file__)},
             'recipe':identity(__file__),'compiler':identity(__import__('etalon.research.scientific_input',fromlist=['x']).__file__)}
    if output.exists():
        if read(output/'request.json')!=request:raise ValueError('changed execution request')
        prior=read(output/'result.json')
        for n,pin in prior['artifacts'].items():
            if identity(output/n)!=pin:raise ValueError('changed completed pose')
        return prior
    if not parameters['binding_support']['eligible']:raise ValueError('contact refinement lacks declared direct-binding support')
    output.mkdir(parents=True);write(output/'request.json',request)
    rows=[]
    for start in base['rows']:
        st=time.perf_counter();row={'slot':start['slot'],'seed':start['seed'],'status':'failed','pose':None}
        try:
            if start['status']!='completed':raise ValueError('baseline slot failed')
            mol=Chem.SDMolSupplier(str(base_dir/start['pose']),removeHs=False)[0]
            result,optimization=refine_pose(mol,parameters)
            if old.canonical_smiles(result)!=old.canonical_smiles(mol):raise ValueError('molecular graph changed')
            name=f"pose-{start['slot']:03d}.sdf"
            with Chem.SDWriter(str(output/name)) as writer:writer.write(result)
            saved=Chem.SDMolSupplier(str(output/name),removeHs=False)[0]
            row.update(status='completed',pose=name,pose_identity=identity(output/name),graph_exact=True,
                **optimization,**metrics(saved,parameters))
        except Exception as e:row['error']={'type':type(e).__name__,'message':str(e)}
        row['cost']={'wall_seconds':time.perf_counter()-st};rows.append(row);write(output/'progress.json',{'rows':rows})
    result={'schema':'snd1-evidence-execution/1','request':request,'mapping':bundle['subject']['mapping'],'rows':rows,
        'consumption':consumption_receipt(compiled,consumed_targets=TARGETS),
        'cost':{'wall_seconds':time.perf_counter()-t,'cpu_seconds':time.process_time()-cpu},
        'artifacts':{p.name:identity(p) for p in output.glob('pose-*.sdf')},
        'scientific_scope':'retrospective constrained preparation, not affinity or independent generalization'}
    write(output/'result.json',result);return result


def evaluate(*args, **kwargs):
    from snd1_evidence_evaluation import evaluate as independent_evaluate
    return independent_evaluate(*args, **kwargs)


def disposition(base, candidate, process_wall):
    valid=base['median_non_anchor_rmsd_A'] is not None and candidate['median_non_anchor_rmsd_A'] is not None
    gates={'noninferior_successes':candidate['successes']>=base['successes'],
        'non_anchor_gain_at_least_0_10A':valid and base['median_non_anchor_rmsd_A']-candidate['median_non_anchor_rmsd_A']>=.10,
        'full_rmsd_regression_at_most_0_05A':valid and candidate['median_rmsd_A']<=base['median_rmsd_A']+.05,
        'no_extra_clashes':candidate['severe_clashes']<=base['severe_clashes'],
        'at_most_one_extra_failure':candidate['execution_failures']<=base['execution_failures']+1,
        'bounded_new_cost':process_wall<=120.}
    return {'outcome':'adopt' if all(gates.values()) else 'retain','gates':gates}


def choose_branch(retrieved, eligible):
    """Use scoped dispositions; do not silently pick among conflicting lessons."""
    if not eligible:return 'fixed_core'
    key=lambda r:(r['source_id'],r['record_id'],r['record_digest'])
    superseded={key(r) for m in retrieved['matches'] for r in m['experience']['supersedes']}
    active=[m for m in retrieved['matches'] if key(m['ref']) not in superseded]
    if len(active)>1:raise ValueError('conflicting experiences require host review')
    return 'local_contact' if active and active[0]['experience']['outcome']=='adopt' else 'fixed_core'


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--baseline',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();out=generate(args.bundle,args.baseline,args.output)
    print(json.dumps({'slots':len(out['rows']),'completed':sum(r['status']=='completed' for r in out['rows']),'cost':out['cost']}))
