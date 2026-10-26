"""Small reusable MMFF torsion algorithm, extracted from SND1's solver idea.

The native Cartesian chain gradient and bounded Rodrigues rotations follow
snd1_torsion_workflow.py. This example deliberately defines a NEW MMFF-only
objective and graph-selected degrees of freedom, so historical SND1 adoption
does not silently apply. It is neither docking nor an affinity estimator.
"""
from __future__ import annotations
import hashlib
import json
import time
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, Lipinski
from scipy.optimize import minimize


def graph_identity(mol):
    body={'atoms':[(a.GetAtomicNum(),a.GetFormalCharge(),a.GetIsotope(),str(a.GetChiralTag())) for a in mol.GetAtoms()],
          'bonds':[(b.GetBeginAtomIdx(),b.GetEndAtomIdx(),str(b.GetBondType()),str(b.GetStereo()),list(b.GetStereoAtoms())) for b in mol.GetBonds()]}
    return hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()


def downstream(mol,a,b):
    seen={b};stack=[b]
    while stack:
        i=stack.pop()
        for atom in mol.GetAtomWithIdx(i).GetNeighbors():
            j=atom.GetIdx()
            if {i,j}=={a,b}:continue
            if j not in seen:seen.add(j);stack.append(j)
    if a in seen:raise ValueError('ring rotation forbidden')
    return sorted(seen-{b})


def graph_torsions(mol,limit=6):
    """Deterministic acyclic single bonds; smaller heavy-atom side moves."""
    rows=[]
    for a,b in mol.GetSubstructMatches(Lipinski.RotatableBondSmarts):
        choices=[]
        for left,right in [(a,b),(b,a)]:
            indices=downstream(mol,left,right)
            count=sum(mol.GetAtomWithIdx(i).GetAtomicNum()>1 for i in indices)
            choices.append((count,left,right,indices))
        count,a,b,indices=min(choices)
        if not count:continue
        rows.append({'axis':[a,b],'moving_indices':indices,'moving_heavy_atoms':count})
    rows.sort(key=lambda r:(r['moving_heavy_atoms'],r['axis']))
    return rows[:limit]


def rotate(initial,dofs,angles):
    xyz=np.array(initial,copy=True)
    for row,angle in zip(dofs,angles):
        a,b=row['axis'];axis=xyz[b]-xyz[a];axis=axis/np.linalg.norm(axis)
        indices=row['moving_indices'];v=xyz[indices]-xyz[b];co,si=np.cos(angle),np.sin(angle)
        xyz[indices]=xyz[b]+v*co+np.cross(axis,v)*si+np.outer(v@axis,axis)*(1-co)
    return xyz


def chain_gradient(initial,dofs,q,gradient,bounds,epsilon=1e-6):
    values=[]
    for k,(lo,hi) in enumerate(bounds):
        a=np.array(q,copy=True);b=a.copy();a[k]=min(hi,q[k]+epsilon);b[k]=max(lo,q[k]-epsilon)
        width=a[k]-b[k]
        if width<=0:raise ValueError('nonzero torsion interval required')
        values.append(float(np.sum(gradient*(rotate(initial,dofs,a)-rotate(initial,dofs,b)))/width))
    return np.array(values)


class NativeMMFF:
    """One native parameterization/model shared by explicitly matching requests."""
    def __init__(self,mol):
        t,c=time.perf_counter(),time.process_time()
        self.mol=Chem.Mol(mol);self.graph=graph_identity(self.mol)
        self.properties=AllChem.MMFFGetMoleculeProperties(self.mol,mmffVariant='MMFF94s')
        if self.properties is None:raise ValueError('MMFF94s parameters unavailable')
        self.ff=AllChem.MMFFGetMoleculeForceField(self.mol,self.properties)
        if self.ff is None:raise ValueError('native force field unavailable')
        self.ff.Initialize()
        self.setup_cost={'wall_seconds':time.perf_counter()-t,'cpu_seconds':time.process_time()-c}

    def optimize(self,initial,dofs,mode,*,half_width_degrees=30.,maxiter=200):
        if mode not in {'finite_difference','cartesian_chain'}:raise ValueError('unknown method')
        if not dofs:raise ValueError('no eligible torsions; use retained input')
        initial=np.asarray(initial);bounds=[(-np.deg2rad(half_width_degrees),np.deg2rad(half_width_degrees))]*len(dofs)
        counts={'mmff_energy_calls':0,'mmff_gradient_calls':0}
        def objective(q):
            xyz=rotate(initial,dofs,q);counts['mmff_energy_calls']+=1
            return self.ff.CalcEnergy(tuple(xyz.ravel()))
        def joint(q):
            xyz=rotate(initial,dofs,q);flat=tuple(xyz.ravel())
            counts['mmff_energy_calls']+=1;counts['mmff_gradient_calls']+=1
            return self.ff.CalcEnergy(flat),chain_gradient(initial,dofs,q,np.array(self.ff.CalcGrad(flat)).reshape(-1,3),bounds)
        q0=np.zeros(len(dofs));initial_energy=objective(q0)
        t,c=time.perf_counter(),time.process_time()
        result=minimize(joint if mode=='cartesian_chain' else objective,q0,method='L-BFGS-B',
            jac=True if mode=='cartesian_chain' else '3-point',bounds=bounds,
            options={'maxiter':maxiter,'maxls':40,'ftol':1e-10,'gtol':1e-5})
        costs={'wall_seconds':time.perf_counter()-t,'cpu_seconds':time.process_time()-c}
        xyz=rotate(initial,dofs,result.x)
        bonds=[(b.GetBeginAtomIdx(),b.GetEndAtomIdx()) for b in self.mol.GetBonds()]
        error=max(abs(float(np.linalg.norm(initial[a]-initial[b])-np.linalg.norm(xyz[a]-xyz[b]))) for a,b in bonds)
        if error>1e-8 or not np.isfinite(xyz).all():raise ValueError('hard bond/finite-coordinate invariant failed')
        return xyz,{'method':mode,'converged':bool(result.success),'optimizer_status':int(result.status),
            'message':str(result.message),'iterations':int(result.nit),'initial_energy_kcal_mol':initial_energy,
            'final_energy_kcal_mol':float(result.fun),'torsion_delta_degrees':np.rad2deg(result.x).tolist(),
            'max_bond_error_A':error,'work_counts':counts,'solver_cost':costs}


def with_coordinates(mol,xyz):
    out=Chem.Mol(mol)
    for i,p in enumerate(xyz):out.GetConformer().SetAtomPosition(i,p)
    return out
