"""Read-only force/geometry decomposition of a completed SND1 diagnosis."""
import json
from pathlib import Path
import sys
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem
import snd1_evidence_workflow as science

root=Path(sys.argv[1]); raw=science.read(root/'execution/result.json')
row=next(r for r in raw['rows'] if r['name']=='full_long-001')
mol=Chem.SDMolSupplier(row['initial_pose'],removeHs=False)[0]
xyz=np.load(root/'execution'/row['trace'])['xyz'];p=row['parameters']
free=[i for i in range(mol.GetNumAtoms()) if i not in p['mobility']['fixed_indices']]
numbers=[a.GetAtomicNum() for a in mol.GetAtoms()]
terms=['Bond','Angle','StretchBend','Oop','Torsion','VdW','Ele']
fields={}
for selected in terms+['all']:
    prop=AllChem.MMFFGetMoleculeProperties(mol,mmffVariant='MMFF94s')
    for term in terms:getattr(prop,'SetMMFF'+term+'Term')(selected=='all' or term==selected)
    ff=AllChem.MMFFGetMoleculeForceField(mol,prop);ff.Initialize();fields[selected]=ff
points={label:xyz[i] for label,i in [('initial',0),('iteration50',50),('iteration500',500),('final',len(xyz)-1)]}
energies={label:{term:ff.CalcEnergy(tuple(pos.ravel())) for term,ff in fields.items()} for label,pos in points.items()}
for values in energies.values():assert abs(sum(values[t] for t in terms)-values['all'])<1e-8
all_contacts=[];all_pocket=[];position=[]
for pos in xyz:
    e,g=science.external_terms(pos,numbers,p)
    all_contacts.append(e['contact_distance']);all_pocket.append(e['protein_exclusion']);position.append(e['position_restraint'])
other=np.load(root/'execution/no_contacts-001.npz')['xyz']
assert np.array_equal(xyz,other),'not identical full trajectory'
ff=fields['all']
def gradient(pos):
    # Match the production objective's energy-then-gradient call order; the
    # native ForceField caches distances used by its gradient implementation.
    ff.CalcEnergy(tuple(pos.ravel()))
    return np.array(ff.CalcGrad(tuple(pos.ravel()))).reshape(-1,3)+science.external_terms(pos,numbers,p)[1]
def energy(pos):return ff.CalcEnergy(tuple(pos.ravel()))+sum(science.external_terms(pos,numbers,p)[0].values())
# Test the combined gradient again in the actual runtime at the new endpoint.
final=xyz[-1];eps=1e-5;numeric=[]
for index in range(final.size):
    plus,minus=final.copy(),final.copy();plus.flat[index]+=eps;minus.flat[index]-=eps
    numeric.append((energy(plus)-energy(minus))/(2*eps))
grad_error=float(np.max(np.abs(np.array(numeric)-gradient(final).ravel())))
assert grad_error<1e-5,grad_error
# Local curvature diagnoses stiff/soft Cartesian modes; no optimization/labels.
hessian=[];delta=1e-4
for atom in free:
    for axis in range(3):
        plus,minus=final.copy(),final.copy();plus[atom,axis]+=delta;minus[atom,axis]-=delta
        hessian.append(((gradient(plus)-gradient(minus))/(2*delta))[free].ravel())
hessian=np.array(hessian).T;asym=float(np.max(np.abs(hessian-hessian.T)))
eigen=np.linalg.eigvalsh((hessian+hessian.T)/2)
positive=eigen[eigen>1e-6]
atom_info=[{'index':a.GetIdx(),'element':a.GetSymbol(),'neighbors':[n.GetIdx() for n in a.GetNeighbors()],
    'formal_charge':a.GetFormalCharge(),'mmff_type':AllChem.MMFFGetMoleculeProperties(mol,mmffVariant='MMFF94s').GetMMFFAtomType(a.GetIdx())} for a in mol.GetAtoms()]
gradients={label:{'heavy_free_norm':float(np.linalg.norm(gradient(pos)[[i for i in free if numbers[i]>1]])),
    'hydrogen_norm':float(np.linalg.norm(gradient(pos)[[i for i in free if numbers[i]==1]]))} for label,pos in points.items()}
binding=[]
for c in p['contacts']:
    d=np.linalg.norm(xyz[:,c['ligand_index']]-np.array(c['protein_xyz']),axis=1)
    binding.append({'ligand_index':c['ligand_index'],'name':c['template_atom_name'],'protein_key':c['protein_key'],
        'lower_A':c['lower_A'],'upper_A':c['upper_A'],'observed_min_A':float(d.min()),'observed_max_A':float(d.max())})
restraints=[]
for a in p['mobility']['core']:
    d=float(np.linalg.norm(final[a['index']]-a['xyz']))
    restraints.append({'index':a['index'],'distance_A':d,'active':d>p['mobility']['max_displacement_A'],
        'penalty':.5*p['mobility']['force_constant']*max(0,d-p['mobility']['max_displacement_A'])**2})
result={'schema':'snd1-refinement-forensics/1','source':{'path':__file__,**science.identity(__file__)},
    'optimization_calls':0,'mmff_components':energies,'gradient_groups':gradients,
    'accepted_iterations_checked':len(xyz)-1,'protein_penalty_max':max(all_pocket),'contact_penalty_max':max(all_contacts),
    'no_contacts_identical_full_precision_trace':True,'contacts':binding,'position_restraints':restraints,'atom_info':atom_info,
    'gradient_finite_difference_max_error':grad_error,'local_hessian':{'dimension':len(eigen),'min_eigenvalue':float(eigen.min()),
        'max_eigenvalue':float(eigen.max()),'negative_eigenvalues':int(sum(eigen< -1e-6)),
        'positive_condition_ratio':float(positive.max()/positive.min()),'max_asymmetry':asym,
        'scope':'finite-difference local free-coordinate curvature; does not prove unique basin'}}
science.write(root/'analysis/forensics.json',result)
print(json.dumps({k:v for k,v in result.items() if k not in ('atom_info','position_restraints')},indent=2))
