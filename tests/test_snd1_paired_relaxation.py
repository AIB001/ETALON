import importlib.util
from pathlib import Path
import numpy as np
import pytest

spec=importlib.util.spec_from_file_location('paired',Path(__file__).parents[1]/'examples/snd1_paired_relaxation.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_pair_mapping_rejects_different_frame_and_keeps_lattice():
    ref=np.array([[1.,0,0],[2,0,0],[3,0,0]]);box=np.eye(3)*10
    full=ref+np.array([10,0,0]);candidate=ref[1:]+[0,1,0]
    placed,proof=m.place_candidate(full,ref,candidate,[1,2],box)
    np.testing.assert_allclose(placed,candidate+[10,0,0])
    with pytest.raises(ValueError,match='does not match'):
        m.place_candidate(full+.1,ref,candidate,[1,2],box)


def test_pbc_typed_contact_direction():
    # D-H-A points straight across the periodic boundary; reversing H breaks it.
    xyz=np.array([[[9.,0,0],[0.,0,0],[1.8,0,0]],[[9.,0,0],[8.,0,0],[1.8,0,0]]])
    rows=m.contact_series(xyz,np.repeat((np.eye(3)*10)[None],2,axis=0),[[0,1,2]],['ASN281'])
    assert rows[0]['ASN281']['primary']
    assert not rows[1]['ASN281']['primary']


def test_no_contact_or_ligand_restraint_and_equal_mdp_budget():
    mdp=m.relaxation_mdp(mode='dynamics',steps=50000,seed=4)
    assert 'nsteps = 50000' in mdp and 'pcoupl = no' in mdp
    assert 'pull' not in mdp and 'POSRES_MOL' not in mdp
    text,count=m.scale_protein_restraints('[ position_restraints ]\n1 1 1000 1000 1000\n',100)
    assert count==1 and '1 1 100 100 100' in text


def test_failed_pairs_cannot_win_successor_selection():
    rows=[dict(run_id='failed',arm='candidate',status='failed')]
    assert m.choose_successor(rows)['new_jobs']==0


def test_native_gro_identity_labels_are_preserved():
    text='example\n2\n    1THR     H1    1   1.000   2.000   3.000\n    2SOL    HW1    2   4.000   5.000   6.000\n  10 10 10\n'
    changed=m.replace_gro_coordinates(text,{1:[7,8,9]})
    assert [x[:20] for x in changed.splitlines()[2:4]]==[x[:20] for x in text.splitlines()[2:4]]
    assert '7.000000' in changed and '1.000000' in changed
