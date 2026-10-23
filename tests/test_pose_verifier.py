"""Coordinate-frame and stereochemistry counterexamples, explicitly synthetic."""
import pytest
Chem = pytest.importorskip('rdkit.Chem')
from rdkit.Chem import AllChem
from etalon.research.pose_verifier import symmetry_rmsd


def test_translation_is_not_aligned_away():
    mol = Chem.AddHs(Chem.MolFromSmiles('CCO'))
    assert AllChem.EmbedMolecule(mol, randomSeed=31) == 0
    moved = Chem.Mol(mol)
    conf = moved.GetConformer()
    for i, xyz in enumerate(conf.GetPositions()):
        conf.SetAtomPosition(i, (float(xyz[0]+5),float(xyz[1]),float(xyz[2])))
    assert symmetry_rmsd(mol,mol) == pytest.approx(0)
    assert symmetry_rmsd(moved,mol) == pytest.approx(5)


def test_different_identity_cannot_get_a_good_pose_score():
    a,b = (Chem.AddHs(Chem.MolFromSmiles(s)) for s in ('C[C@H](O)F','C[C@@H](O)F'))
    AllChem.EmbedMolecule(a,randomSeed=31); AllChem.EmbedMolecule(b,randomSeed=31)
    with pytest.raises(ValueError,match='identity/stereochemistry'):
        symmetry_rmsd(a,b)
