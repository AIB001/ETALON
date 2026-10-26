import importlib.util
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

EXAMPLES=Path(__file__).parents[1]/'examples'
def load(name):
    spec=importlib.util.spec_from_file_location(name,EXAMPLES/(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_nested_combined_rotations_match_numerical_coordinate_jacobian():
    candidate=load('snd1_exact_jacobian');shared=load('reusable_mmff')
    # Rooted chain with a disjoint branch; multiple coupled rotations, no optimizer.
    initial=np.array([[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[2.,1.,1.],[3.,1.,2.],[-1.,1.,0.],[-2.,1.,1.]])
    dofs=[{'axis':[0,1],'moving_indices':[2,3,4],'bounds_degrees':[-60,60]},
          {'axis':[1,2],'moving_indices':[3,4],'bounds_degrees':[-60,60]},
          {'axis':[0,5],'moving_indices':[6],'bounds_degrees':[-60,60]}]
    g={'dofs':dofs};legacy=SimpleNamespace(rotate=shared.rotate)
    weights=np.arange(1,initial.size+1,dtype=float).reshape(initial.shape)/7
    target=np.random.default_rng(91).normal(size=initial.shape)
    for q in [np.zeros(3),np.array([.3,-.6,.4]),np.array([-.8,.7,-.5])]:
        xyz=shared.rotate(initial,dofs,q);gradient=2*weights*(xyz-target)
        analytic=candidate.pullback(legacy,initial,g,q,gradient)
        numeric=[]
        for k in range(3):
            a=q.copy();b=q.copy();a[k]+=1e-6;b[k]-=1e-6
            numeric.append((np.sum(weights*(shared.rotate(initial,dofs,a)-target)**2)-
                            np.sum(weights*(shared.rotate(initial,dofs,b)-target)**2))/2e-6)
        assert np.allclose(analytic,numeric,atol=1e-7,rtol=1e-7)


def test_unsupported_order_does_not_silently_reuse():
    candidate=load('snd1_exact_jacobian')
    with pytest.raises(ValueError,match='reroot'):
        candidate.validate_rooted_geometry({'dofs':[{'axis':[0,1],'moving_indices':[2]},
                                                  {'axis':[2,3],'moving_indices':[0]}]})
