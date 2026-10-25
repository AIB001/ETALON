import copy
import pytest

from etalon.research.pose_ranking import order_features


def row(i, energy, polar=0, drop=0):
    return {"pose_index": i, "empirical_score": energy, "polar_coverage": polar,
            "buried_unsatisfied_polar": 0, "severe_clash_pairs": 0,
            "relaxation_energy_drop_kcal_mol": drop, "error": None}


def test_window_prevents_polar_contacts_overriding_large_empirical_gap():
    result = order_features([row(0, -8), row(1, -7.6, polar=3), row(2, -6, polar=100)])
    assert result["ordered_pose_indices"] == [1, 0, 2]
    assert result["eligible_pose_indices"] == [0, 1]


def test_incomplete_relaxation_retains_baseline_and_exposes_missingness():
    rows = [row(0, -8), row(1, -7.9, polar=4)]
    rows[1].pop("relaxation_energy_drop_kcal_mol")
    result = order_features(rows)
    assert result["fallback"] and result["fallback_reasons"]
    assert result["ordered_pose_indices"] == [0, 1]


def test_reference_and_verifier_fields_cannot_affect_selection():
    rows = [row(0, -8), row(1, -7.9, polar=2)]
    before = copy.deepcopy(rows)
    result = order_features(rows)
    rows[0].update(rmsd_A=0.0, physical_valid=True, CNNscore=100)
    rows[1].update(rmsd_A=100.0, physical_valid=False, CNNscore=-100)
    assert order_features(rows) == result
    assert before[0]["polar_coverage"] == 0


def test_nonfinite_empirical_or_incomplete_slot_coverage_rejected():
    with pytest.raises(ValueError):
        order_features([row(0, float("nan"))])
    with pytest.raises(ValueError):
        order_features([row(1, -8)])


def test_relaxation_penalty_and_stable_tie_break():
    assert order_features([row(0, -8, drop=10), row(1, -7.9)])["ordered_pose_indices"] == [1, 0]
    assert order_features([row(0, -8), row(1, -8)])["ordered_pose_indices"] == [0, 1]


def test_real_rdkit_features_preserve_the_native_pool(tmp_path):
    Chem = pytest.importorskip('rdkit.Chem')
    from rdkit.Chem import AllChem
    from etalon.research.pose_ranking import rank_pool
    from etalon.research.scientific_input import identity
    mol = Chem.AddHs(Chem.MolFromSmiles('CCO'))
    assert AllChem.EmbedMolecule(mol, randomSeed=41) == 0
    mol.SetProp('minimizedAffinity','-4.0')
    pool, rec = tmp_path/'pool.sdf', tmp_path/'rec.pdb'
    with Chem.SDWriter(str(pool)) as writer:
        writer.write(mol)
    rec.write_text('ATOM      1  O   ALA A   1       4.000   0.000   0.000  1.00 20.00           O  \nEND\n',encoding='ascii')
    pins = {'pool':identity(pool),'receptor':identity(rec)}
    out = rank_pool(pool,rec,expected_pool=pins['pool'],expected_receptor=pins['receptor'])
    assert out['ordered_pose_indices']==[0]
    assert out['reference_input'] is None
    assert identity(pool)==pins['pool'] and identity(rec)==pins['receptor']
    assert out['features'][0]['error'] is None
    assert out['features'][0]['relaxation_energy_drop_kcal_mol'] >= 0
