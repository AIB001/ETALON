"""Numerical correctness, not molecular benefit, on explicit synthetic geometry."""
import numpy as np
import pytest
from rdkit.Chem import AllChem
from test_snd1_torsion_workflow import fixture
import snd1_torsion_workflow as w


@pytest.mark.parametrize('angle',[-np.pi/6,0.12,np.pi/6])
def test_chain_gradient_matches_independent_energy_difference_at_bounds(angle):
    mol,g=fixture();xyz=mol.GetConformer().GetPositions();q=np.array([angle])
    props=AllChem.MMFFGetMoleculeProperties(mol,mmffVariant='MMFF94s')
    ff=AllChem.MMFFGetMoleculeForceField(mol,props);ff.Initialize()
    x=w.rotate(xyz,g['dofs'],q)
    grad=np.array(ff.CalcGrad(tuple(x.ravel()))).reshape(-1,3)
    actual=w.cartesian_chain_gradient(xyz,g,q,grad)[0]
    h=1e-6;a=min(np.pi/6,angle+h);b=max(-np.pi/6,angle-h)
    expected=(ff.CalcEnergy(tuple(w.rotate(xyz,g['dofs'],[a]).ravel()))-
              ff.CalcEnergy(tuple(w.rotate(xyz,g['dofs'],[b]).ravel())))/(a-b)
    assert actual==pytest.approx(expected,abs=2e-4,rel=1e-5)


def test_default_and_optional_solver_preserve_geometry_and_objective():
    mol,g=fixture()
    p={'geometry':g,'pocket':{'atoms':[{'xyz':[8.,8.,8.],'element':'C'}],
        'steric_scale':.7,'force_constant':5.},'optimizer':{'maxiter':200,'ftol':1e-12,'gtol':1e-5}}
    results=[w.refine(mol,p,'torsion_distance',gradient_mode=m) for m in ['finite_difference','cartesian_chain']]
    assert all(r[1]['hard_invariants']['valid'] for r in results)
    assert sum(results[0][1]['final_terms'].values())==pytest.approx(sum(results[1][1]['final_terms'].values()),abs=1e-4)
    assert np.max(np.abs(results[0][3]-results[1][3]))<1e-4
    assert results[1][1]['work_counts']['mmff_energy_calls']<results[0][1]['work_counts']['mmff_energy_calls']


def test_directional_proxy_gradient_is_included_not_silently_dropped():
    mol,g=fixture();xyz=mol.GetConformer().GetPositions();q=np.array([.13])
    g['directions']=[{'id':'synthetic-donor','acceptor_index':0,'donor_xyz':[4.,1.,1.],
        'hydrogen_xyz':[3.,1.,1.],'target_DHA_degrees':180,'k_DHA_kcal_mol_rad2':2.,
        'acceptor_neighbors':[],'k_axis_kcal_mol_rad2':0.}]
    def energy(a):return sum(r['energy'] for r in w.direction_terms(w.rotate(xyz,g['dofs'],a),g))
    actual=w.cartesian_chain_gradient(xyz,g,q,np.zeros_like(xyz),directional=True)[0]
    expected=(energy(q+1e-5)-energy(q-1e-5))/2e-5
    assert abs(expected)>1e-4
    assert actual==pytest.approx(expected,abs=1e-5,rel=1e-5)
