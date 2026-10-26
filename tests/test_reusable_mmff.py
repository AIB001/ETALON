"""Real native gradient discriminant; separate from the scientific panel."""
import importlib.util
from pathlib import Path
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

spec=importlib.util.spec_from_file_location('reusable_mmff',Path(__file__).parents[1]/'examples/reusable_mmff.py')
science=importlib.util.module_from_spec(spec);spec.loader.exec_module(science)


def test_native_chain_matches_independent_energy_difference():
    mol=Chem.AddHs(Chem.MolFromSmiles('CCCOc1ccccc1'))
    options=AllChem.ETKDGv3();options.randomSeed=714
    assert AllChem.EmbedMolecule(mol,options)==0
    model=science.NativeMMFF(mol);xyz=mol.GetConformer().GetPositions();dofs=science.graph_torsions(mol)
    q=np.linspace(-.1,.15,len(dofs));bounds=[(-.5,.5)]*len(dofs)
    x=science.rotate(xyz,dofs,q);grad=np.array(model.ff.CalcGrad(tuple(x.ravel()))).reshape(-1,3)
    analytic=science.chain_gradient(xyz,dofs,q,grad,bounds)
    numeric=[]
    for k in range(len(q)):
        a=q.copy();b=q.copy();a[k]+=1e-5;b[k]-=1e-5
        ea=model.ff.CalcEnergy(tuple(science.rotate(xyz,dofs,a).ravel()))
        eb=model.ff.CalcEnergy(tuple(science.rotate(xyz,dofs,b).ravel()))
        numeric.append((ea-eb)/2e-5)
    assert np.allclose(analytic,numeric,atol=2e-4,rtol=2e-5)
