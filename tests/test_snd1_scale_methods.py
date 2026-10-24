"""Small synthetic contract tests; not experimental SND1 evidence."""
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

sys.path.insert(0,str(Path(__file__).parents[1]/'examples'))
import snd1_scale_methods as w


def test_ca_frame_alignment_preserves_coordinates_without_ligand_fit():
    xyz=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])
    xyz=np.tile(xyz,(15,1))+np.repeat(np.arange(15),4)[:,None]
    rows=lambda a:[{'key':['1','A',str(i),'','ALA','CA'],'xyz':p.tolist()} for i,p in enumerate(a)]
    fit=w.protein_alignment(rows(xyz+[2.,3.,4.]),rows(xyz))
    assert fit['matched_ca']==60 and fit['rmsd_A']<1e-10
    assert fit['ligand_fit'] is False
    assert np.allclose(w.transform(xyz+[2.,3.,4.],fit),xyz)
    with pytest.raises(ValueError,match='insufficient'):w.protein_alignment(rows(xyz[:10]),rows(xyz[:10]))


def test_completed_attempt_reuses_verified_receipt_and_unknown_never_reruns(tmp_path):
    request={'request_id':'synthetic','attempt_id':'trial-1'}
    w.write(tmp_path/'request.json',request)
    with pytest.raises(RuntimeError,match='UNKNOWN_OUTCOME'):w.completed_receipt(tmp_path,request)
    (tmp_path/'pose.sdf').write_text('synthetic fixture')
    receipt={'status':'failed','artifacts':{'pose.sdf':w.pin(tmp_path/'pose.sdf')}}
    w.write(tmp_path/'receipt.json',receipt)
    assert w.completed_receipt(tmp_path,request)==receipt
    with pytest.raises(ValueError,match='changed request'):w.completed_receipt(tmp_path,{'different':True})
    (tmp_path/'pose.sdf').write_text('corrupted fixture')
    with pytest.raises(ValueError,match='changed'):w.completed_receipt(tmp_path,request)


def test_native_fixed_core_keeps_declared_coordinates():
    mol=Chem.AddHs(Chem.MolFromSmiles('CCO'));assert AllChem.EmbedMolecule(mol,randomSeed=19)==0
    initial=mol.GetConformer().GetPositions().copy()
    out,details,xyz=w.fixed_core(mol,[0,1,2])
    assert np.array_equal(initial[:3],xyz[:3])
    assert details['hard_invariants']['valid']
    assert w.old.canonical_smiles(mol)==w.old.canonical_smiles(out)


def test_protocol_denominators_and_directional_gradient_are_separate():
    p=w.protocol()
    assert len(p['ligands'])*len(p['receptor_contexts'])*3*len(p['seeds'])*len(p['configurations'])==1440
    assert p['initialization_families']['existing_docking'] is None
    assert p['solver_equivalence']['maximum_atom_delta_A']==.001
    assert p['r4b']=='paused'


def test_controlled_ablation_preserves_anchors_and_input_without_mutation():
    p={'geometry':{'dofs':[{'id':'one','axis':[0,1],'moving_indices':[2],
                           'bounds_degrees':[-10,10]}],
                   'soft_positions':[{'index':0,'xyz':[1,2,3]}],
                   'soft_distances':[{'id':'contact','k_kcal_mol_A2':10}],
                   'hard':{'fixed_indices':[0,1]}},
       'pocket':{'atoms':[{'element':'C','xyz':[4,5,6]}],'force_constant':20},
       'optimizer':{'maxiter':500}}
    original=json.loads(json.dumps(p))
    enabled=w.controlled_torsion_parameters(p,half_width_degrees=[10],pocket_terms=True)
    assert enabled==p and enabled is not p
    disabled=w.controlled_torsion_parameters(p,half_width_degrees=[30],pocket_terms=False)
    assert p==original
    assert disabled['geometry']['soft_distances']==[]
    assert disabled['pocket']['force_constant']==0
    assert disabled['geometry']['dofs'][0]['bounds_degrees']==[-30,30]
    assert disabled['geometry']['soft_positions']==p['geometry']['soft_positions']
    assert disabled['geometry']['hard']==p['geometry']['hard']
    assert disabled['optimizer']==p['optimizer']
    assert disabled['pocket']['atoms']==p['pocket']['atoms']
    with pytest.raises(ValueError):w.controlled_torsion_parameters(p,half_width_degrees=[],pocket_terms=True)
    with pytest.raises(ValueError):w.controlled_torsion_parameters(p,half_width_degrees=[float('nan')],pocket_terms=True)
    with pytest.raises(ValueError):w.controlled_torsion_parameters(p,half_width_degrees=[30],pocket_terms=0)


def test_serialization_guard_accepts_representation_only_and_rejects_graph_change():
    mol=Chem.AddHs(Chem.MolFromSmiles('CS(=O)(=O)N'));assert AllChem.EmbedMolecule(mol,randomSeed=17)==0
    sulfur=next(a for a in mol.GetAtoms() if a.GetAtomicNum()==16)
    sulfur.SetChiralTag(Chem.ChiralType.CHI_TETRAHEDRAL_CW)
    serialized=Chem.MolFromMolBlock(Chem.MolToMolBlock(Chem.Mol(mol)),removeHs=False)
    checked=w.validate_serialized_identity(mol,serialized)
    assert checked['indexed_elements_charges_isotopes_aromaticity_exact']
    assert checked['removed_nonstereogenic_sulfonyl_tags']==[sulfur.GetIdx()]
    bad=Chem.Mol(serialized);bad.GetAtomWithIdx(0).SetIsotope(13)
    with pytest.raises(ValueError,match='graph changed'):w.validate_serialized_identity(mol,bad)
    # A real stereocenter cannot be silently stripped.
    chiral=Chem.AddHs(Chem.MolFromSmiles('C[C@H](F)Cl'));assert AllChem.EmbedMolecule(chiral,randomSeed=19)==0
    changed=Chem.Mol(chiral);Chem.RemoveStereochemistry(changed)
    with pytest.raises(ValueError,match='stereochemistry changed'):w.validate_serialized_identity(chiral,changed)
