"""Local algorithm invariants and scoped feedback; no fixture efficacy claims."""
import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'examples'))
import snd1_feedback_search as method
import snd1_anchor_intervention as anchor


def test_coupled_generation_keeps_bonds_angles_and_exact_budget():
    from rdkit import Chem
    from rdkit.Chem import AllChem
    mol=Chem.AddHs(Chem.MolFromSmiles('c1ccccc1S(=O)(=O)Nc1ccccc1'))
    assert AllChem.EmbedMolecule(mol,randomSeed=42)==0
    atoms=[dict(name=str(i)) for i in range(mol.GetNumAtoms())]
    bonds=[(b.GetBeginAtomIdx(),b.GetEndAtomIdx()) for b in mol.GetBonds()]
    chemical=anchor.molecular_model(mol,atoms,bonds)
    xyz=mol.GetConformer().GetPositions()
    protein=np.array([[0.,0,0],[1.,0,0],[0,4,0],[1,4,0]])
    ctx=dict(ligand=xyz,chemical=chemical,offset=4,xyz=np.concatenate([protein,xyz]),
             box=np.eye(3)*50,case=dict(triples=np.array([[0,1,4],[2,3,5]]),
             triple_residues=['ASN281','ARG255']))
    poses,info=method.proposals(ctx,budget=96)
    assert len(poses)==len(info['candidate_metadata'])==96
    for pose in poses:
        bond,angle,stereo=anchor.shape_check(pose,xyz,chemical)
        assert bond<1e-10 and angle<1e-9 and stereo
    base=np.linalg.norm(xyz[:,None]-xyz[None,:],axis=-1)
    assert any(np.max(np.abs(np.linalg.norm(p[:,None]-p[None,:],axis=-1)-base))>.1 for p in poses)


def test_native_selection_preserves_endpoint_and_ignores_failed_minimum():
    rows=[dict(index=0,converged=True,quality={'acceptable':True},contacts={'ASN_ARG':True},
               potential_kJ_mol=-10.,core_movement_A=2.),
          dict(index=1,converged=True,quality={'acceptable':True},contacts={'ASN_ARG':False},
               potential_kJ_mol=-100.,core_movement_A=1.),
          dict(index=2,converged=False,quality={'acceptable':True},contacts={'ASN_ARG':True},
               potential_kJ_mol=-200.,core_movement_A=1.)]
    assert method.select_relaxed(rows)['selected']==0
    rows[0]['input_coordinate_identity']='existing'
    rows[1]['input_coordinate_identity']='different'
    assert method.select_relaxed(rows,exclude_input_identities=['existing'])['selected']==1


def test_negative_normal_feedback_selects_reanalysis_not_identical_md():
    rows=[dict(mode=m,seed=s,status='succeeded',dual_retained=False)
          for m in ['rigid','coupled'] for s in [10,11]]
    decision=method.followup(rows)
    assert decision['action']=='analyze_competing_anchor_states' and decision['outcome']=='retain'
    rows[-1]['dual_retained']=True
    assert method.followup(rows)['action']=='test_local_candidate_in_additional_state'
    assert method.followup(rows[:-1])['outcome']=='unresolved'
