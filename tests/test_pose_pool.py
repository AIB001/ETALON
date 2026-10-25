"""Synthetic feature-leakage, coordinate and identity counterexamples."""
import pytest

Chem = pytest.importorskip("rdkit.Chem")
from rdkit.Chem import AllChem
from etalon.research.pose_pool import diagnose_cpu_pool
from etalon.research.evidence_refs import file_identity


@pytest.fixture
def pool(tmp_path):
    mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    assert AllChem.EmbedMolecule(mol, randomSeed=31) == 0
    a, b = Chem.Mol(mol), Chem.Mol(mol)
    a.SetProp("minimizedAffinity", "-7")
    b.SetProp("minimizedAffinity", "-6")
    for i, pos in enumerate(b.GetConformer().GetPositions()):
        b.GetConformer().SetAtomPosition(i, tuple(float(v) for v in pos + [5, 0, 0]))
    path = tmp_path / "cpu.sdf"
    with Chem.SDWriter(str(path)) as writer:
        writer.write(a); writer.write(b)
    rec = tmp_path / "receptor.pdb"
    rec.write_text("ATOM      1  O   ALA A   1       3.000   0.000   0.000  1.00 20.00           O  \nEND\n", encoding="ascii")
    return path, rec, [a, b]


def read(pool, **kwargs):
    path, rec, _ = pool
    return diagnose_cpu_pool(path, rec, expected_pool=file_identity(path),
                             expected_receptor=file_identity(rec), pocket_center=[0, 0, 0], **kwargs)


def test_fixed_frame_and_contact_features_are_not_quality_claims(pool):
    result = read(pool)
    assert result["values"]["pool_median_rmsd_from_cpu_top_A"] == pytest.approx(2.5)
    assert result["values"]["empirical_top_gap"] == 1
    assert result["values"]["top_contact_residues_le_4_A"] == 1
    assert result["decision_order_verified"] is False
    assert "not directional" in result["interpretation"]["polar_pairs"]


def test_answer_and_cnn_properties_never_affect_features(pool):
    before = read(pool)
    path, _, molecules = pool
    for m in molecules:
        m.SetProp("CNNscore", "999")
        m.SetProp("RMSD", "0")
        m.SetProp("physical_valid", "true")
    with Chem.SDWriter(str(path)) as writer:
        for m in molecules: writer.write(m)
    after = read(pool)
    assert before["values"] == after["values"]
    assert before["inputs"] != after["inputs"]


def test_pin_and_bounds_fail_without_silent_truncation(pool):
    path, rec, _ = pool
    with pytest.raises(ValueError, match="identity mismatch"):
        diagnose_cpu_pool(path, rec, expected_pool={"bytes": 1, "sha256": "a" * 64},
                          expected_receptor=file_identity(rec), pocket_center=[0, 0, 0])
    with pytest.raises(ValueError, match="no silent truncation"):
        read(pool, maximum_poses=1)


def test_missing_score_never_becomes_zero(pool):
    path, _, molecules = pool
    molecules[1].ClearProp("minimizedAffinity")
    with Chem.SDWriter(str(path)) as writer:
        for m in molecules: writer.write(m)
    with pytest.raises(ValueError, match="empirical minimizedAffinity"):
        read(pool)


def test_different_molecule_not_a_diverse_pose(pool):
    path, _, molecules = pool
    m = Chem.AddHs(Chem.MolFromSmiles("CCN"))
    AllChem.EmbedMolecule(m, randomSeed=31)
    m.SetProp("minimizedAffinity", "-6")
    with Chem.SDWriter(str(path)) as writer:
        writer.write(molecules[0]); writer.write(m)
    with pytest.raises(ValueError, match="chemical identity"):
        read(pool)
