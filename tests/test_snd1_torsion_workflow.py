import copy
from pathlib import Path
import sys
import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem,rdMolTransforms
sys.path.insert(0,str(Path(__file__).parents[1]/'examples'))
import snd1_torsion_workflow as w
from etalon.research.geometry_input import SCHEMA,validate_geometry,geometry_evidence_ids


def fixture():
    mol=Chem.AddHs(Chem.MolFromSmiles('CCOc1ccccc1'))
    assert AllChem.EmbedMolecule(mol,randomSeed=19)==0
    # Explicit synthetic ethoxy fixture; not a SND1 experimental result.
    axis=[3,2];moving=w.component(mol,*axis);n=mol.GetNumAtoms()
    g={'schema':SCHEMA,'representation':'bounded_bond_rotations','atom_count':n,'atom_order_digest':w.atom_order(mol),
        'coordinate_frame':{'sha256':'a'*64,'bytes':1},'chemical_state':{'assumptions':['synthetic neutral fixture']},
        'hard':{'fixed_indices':sorted(set(range(n))-set(moving)),'reference':'per_execution_input','preserve':'bond_lengths_angles_and_rings',
                'position_tolerance_A':1e-8,'bond_tolerance_A':1e-8,'angle_tolerance_deg':1e-5},
        'dofs':[{'id':'ethoxy','axis':axis,'moving_indices':moving,'bounds_degrees':[-30,30],'zero':'input_pose','evidence_ids':['fixture'],'rationale':'synthetic test'}],
        'soft_positions':[{'index':0,'xyz':mol.GetConformer().GetPositions()[0].tolist(),'flat_bottom_A':.1,'k_kcal_mol_A2':5.,'evidence_ids':['fixture']}],
        'soft_distances':[],'directions':[],'direction_exclusions':[],'scope':'synthetic fixture'}
    return mol,g


def test_rotation_preserves_independently_measured_geometry_and_changes_only_named_dihedral():
    mol,g=fixture();w.compile_geometry(g,mol);start=mol.GetConformer().GetPositions();q=np.array([.3]);end=w.rotate(start,g['dofs'],q)
    moved=Chem.Mol(mol)
    for i,x in enumerate(end):moved.GetConformer().SetAtomPosition(i,x)
    assert np.array_equal(start[g['hard']['fixed_indices']],end[g['hard']['fixed_indices']])
    before=rdMolTransforms.GetDihedralRad(mol.GetConformer(),4,3,2,1)
    after=rdMolTransforms.GetDihedralRad(moved.GetConformer(),4,3,2,1)
    assert abs(after-before)==pytest.approx(.3,abs=1e-9)
    for bond in mol.GetBonds():
        a,b=bond.GetBeginAtomIdx(),bond.GetEndAtomIdx()
        assert rdMolTransforms.GetBondLength(mol.GetConformer(),a,b)==pytest.approx(rdMolTransforms.GetBondLength(moved.GetConformer(),a,b),abs=1e-10)
    assert w.geometry_invariants(mol,start,end,g,q)['valid']
    with pytest.raises(ValueError,match='bound'):w.rotate(start,g['dofs'],[1.])


def test_wrong_graph_cut_ring_and_atom_identity_are_rejected():
    mol,g=fixture();bad=copy.deepcopy(g);bad['dofs'][0]['moving_indices'].remove(0);bad['hard']['fixed_indices'].append(0)
    with pytest.raises(ValueError,match='component'):w.compile_geometry(bad,mol)
    with pytest.raises(ValueError,match='ring'):w.component(mol,3,4)
    changed=Chem.Mol(mol);changed.GetAtomWithIdx(0).SetIsotope(13)
    with pytest.raises(ValueError,match='identity'):w.compile_geometry(g,changed)


def test_soft_radius_is_not_a_hard_limit_and_ambiguous_fields_fail():
    mol,g=fixture();validate_geometry(g,available_evidence_ids={'fixture'})
    bad=copy.deepcopy(g);bad['soft_positions'][0]['max_displacement_A']=.1
    with pytest.raises(ValueError,match='soft position'):validate_geometry(bad)
    bad=copy.deepcopy(g);bad['dofs'][0]['bounds_degrees']=[30,-30]
    with pytest.raises(ValueError,match='rotation'):validate_geometry(bad)
    with pytest.raises(ValueError,match='lineage'):validate_geometry(g,available_evidence_ids={'different'})
    bad=copy.deepcopy(g);bad['hard']['fixed_indices'].append(g['dofs'][0]['moving_indices'][0])
    with pytest.raises(ValueError,match='conflicts'):validate_geometry(bad)


def test_direction_rejects_wrong_chemical_type_and_requires_hydrogen_origin():
    mol,g=fixture()
    row={'id':'fixture-angle','kind':'explicit_DHA_proxy','acceptor_index':0,'acceptor_type':'graph_typed_acceptor',
        'donor_key':['1','A','1','','ARG','NH2'],'donor_xyz':[4.,0.,0.],'hydrogen_xyz':[3.,0.,0.],
        'hydrogen_origin':'constructed_planar','donor_state_assumption':'fixture assumption','target_DHA_degrees':180,
        'k_DHA_kcal_mol_rad2':2.,'acceptor_neighbors':[],'k_axis_kcal_mol_rad2':0.,'evidence_ids':['fixture']}
    g['directions']=[row]
    with pytest.raises(ValueError,match='chemical graph'):w.compile_geometry(g,mol)
    row['acceptor_index']=2;w.compile_geometry(g,mol)
    row['hydrogen_origin']='unknown'
    with pytest.raises(ValueError,match='hydrogen provenance'):w.compile_geometry(g,mol)


def test_direction_distinguishes_equal_distance_geometries_and_constructed_h_is_explicit():
    donor=np.array([0.,0.,0.]);parent=np.array([-1.,0.,0.]);plane=np.array([-1.,1.,0.])
    h=w.virtual_hydrogen(donor,parent,plane,np.array([2.,2.,0.]))
    assert np.linalg.norm(h-donor)==pytest.approx(1.01)
    assert np.rad2deg(np.arccos(w.unit(h-donor)@w.unit(parent-donor)))==pytest.approx(120.)
    row={'id':'angle','acceptor_index':0,'donor_xyz':donor.tolist(),'hydrogen_xyz':[1.,0.,0.],
        'k_DHA_kcal_mol_rad2':2.,'k_axis_kcal_mol_rad2':0.}
    g={'directions':[row]}
    assert w.direction_terms(np.array([[3.,0.,0.]]),g)[0]['energy']==pytest.approx(0.)
    assert w.direction_terms(np.array([[0.,3.,0.]]),g)[0]['energy']>0.1


def test_subconstraint_cannot_cite_unbound_or_evaluation_evidence():
    mol,g=fixture()
    bundle={'sources':[{'id':'fixture','role':'conditioning'},{'id':'answer','role':'evaluation_only'}],
        'observations':[],'interpretations':[],'bindings':[{'target':'geometry','use':'constraint','basis_ids':['fixture']}]}
    assert geometry_evidence_ids(bundle)=={'fixture'}
    g['dofs'][0]['evidence_ids']=['answer']
    with pytest.raises(ValueError,match='lineage'):validate_geometry(g,available_evidence_ids=geometry_evidence_ids(bundle))
    bundle['bindings'][0]['basis_ids']=['answer']
    with pytest.raises(ValueError,match='conditioning'):geometry_evidence_ids(bundle)
