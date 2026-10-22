import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'examples'))
import snd1_evidence_workflow as recipe


def parameters():
    return {'pocket':{'atoms':[{'xyz':[0.,0.,0.],'element':'C'}],'steric_scale':.75,'force_constant':20.},
        'contacts':[{'ligand_index':0,'protein_xyz':[5.,0.,0.],'lower_A':2.,'upper_A':3.,'force_constant':10.}],
        'mobility':{'core':[{'index':0,'xyz':[3.,2.,1.]}],'max_displacement_A':.5,'force_constant':5.}}


def test_actual_pocket_and_contact_gradients_match_finite_difference():
    p=parameters();x=np.array([[1.1,.2,.3],[2.5,2.,1.]])
    energy,g=recipe.external_terms(x,[6,8],p)
    assert all(v>0 for v in energy.values())
    for i in range(2):
        for j in range(3):
            a=x.copy();b=x.copy();a[i,j]+=1e-6;b[i,j]-=1e-6
            finite=(sum(recipe.external_terms(a,[6,8],p)[0].values())-sum(recipe.external_terms(b,[6,8],p)[0].values()))/2e-6
            assert g[i,j]==pytest.approx(finite,abs=1e-5)
    # Receptor coordinates and interaction endpoints must actually affect forces.
    p['pocket']['atoms'][0]['xyz']=[30.,30.,30.]
    assert not np.allclose(g,recipe.external_terms(x,[6,8],p)[1])


def test_activity_gate_does_not_confuse_cell_ic50_with_direct_binding():
    a={'molecule_id':'QOY','target':'SND1','endpoint':'Kd','assay':'MST RED-tris-NTA His-tag','status':'quantified','relation':'=','unit':'uM','value':9.}
    assert recipe.direct_binding_supported(a,'QOY')
    for change in [{'endpoint':'IC50'},{'assay':'cell viability'},{'unit':'unknown'},{'status':'unknown'},{'value':None},{'value':float('nan')},{'molecule_id':'QOV'}]:
        assert not recipe.direct_binding_supported({**a,**change},'QOY')


def test_negative_experience_changes_branch_without_overriding_conflicts():
    a={'source_id':'fixture','record_id':'a','record_digest':'1'}
    b={'source_id':'fixture','record_id':'b','record_digest':'2'}
    old={'ref':a,'experience':{'outcome':'adopt','supersedes':[]}}
    new={'ref':b,'experience':{'outcome':'retain','supersedes':[a]}}
    assert recipe.choose_branch({'matches':[old]},True)=='local_contact'
    assert recipe.choose_branch({'matches':[old,new]},True)=='fixed_core'
    assert recipe.choose_branch({'matches':[old]},False)=='fixed_core'
    new['experience']['supersedes']=[]
    with pytest.raises(ValueError,match='conflicting'):recipe.choose_branch({'matches':[old,new]},True)


def test_receptor_alignment_reports_rmsd_not_sum_of_squares():
    from snd1_evidence_evaluation import receptor_fit
    fixed=np.array([[0.,0.,0.],[1.,0.,0.],[0.,2.,0.],[0.,0.,3.]])
    moving=fixed+np.array([4.,5.,6.]);moving[3,2]+=.4
    rms,transform=receptor_fit(fixed,moving)
    actual=moving@transform[:3,:3].T+transform[:3,3]
    assert rms==pytest.approx(np.sqrt(np.mean(np.sum((actual-fixed)**2,axis=1))),abs=1e-7)
    assert 0<rms<.4
