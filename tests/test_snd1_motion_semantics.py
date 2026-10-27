"""Small synthetic coordinate tests, not a new SND1 run or efficacy evidence."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "examples"))
import snd1_motion_diagnosis as producer
from etalon.research.result_semantics import bind_scientific_result, validate_result_binding
from etalon.rescore import motion


def coordinates():
    # Environment 0 is not a contact atom; three non-collinear ligand core atoms.
    reference = np.array([[8., 0., 0.], [0., 3., 0.], [3., 3., 0.], [0., 3., 3.],
                          [0., 0., 0.], [1., 0., 0.],
                          [2., 2., 0.], [3., 2., 0.], [2., 2., 1.], [2.8, 0., 0.]])
    current = reference.copy()
    current[0] = [2.9, 0., 0.]  # unrelated atom now clashes with ligand acceptor
    return reference, current, np.array([6, 7, 8, 9]), np.array([6, 7, 8])


def test_source_factorial_records_whole_supplied_environment_reset():
    reference, current, ligand, core = coordinates()
    result = motion.factorial_contacts([reference, current], [np.eye(3)*40]*2,
        ligand, core, [1, 2, 3], [[4, 5, 9]], ["fixture-anchor"])
    operation = result["operations"][4]
    assert operation["environment_reset_indices"] == list(range(6))
    assert 0 not in [4, 5, 9]  # the reset is broader than the contact triple
    changed, actual = motion.reset_coordinates(current, reference, ligand, core, 4,
                                               return_operation=True)
    assert actual == operation
    np.testing.assert_array_equal(changed[0], reference[0])
    assert motion.FACTORS[2] == "protein_contact_atoms"  # old artifacts stay readable
    assert producer.operation_summary(actual)["environment_reset_indices"]["count"] == 6


def test_successor_overlap_uses_actual_reset_environment_and_reports_that_source():
    reference, current, ligand, core = coordinates()
    args = (current, reference, ligand, core, current[ligand])
    rest = (np.arange(6), np.arange(4), np.array([0]), np.ones(4), np.ones(6))
    unreset, current_ratio, current_op = producer.successor_candidate(*args, 0, *rest)
    reset, reference_ratio, reference_op = producer.successor_candidate(*args, 4, *rest)
    assert current_ratio == pytest.approx(.05)
    assert reference_ratio > 2
    assert current_op["qc_current_indices"] == [0]
    assert current_op["qc_reference_indices"] == []
    assert reference_op["qc_current_indices"] == []
    assert reference_op["qc_reference_indices"] == [0]
    np.testing.assert_array_equal(unreset[0], current[0])
    np.testing.assert_array_equal(reset[0], reference[0])
    for xyz, ratio in ((unreset, current_ratio), (reset, reference_ratio)):
        assert ratio == pytest.approx(np.linalg.norm(xyz[ligand]-xyz[0], axis=1).min()/2)


def test_producer_profile_binds_all_atom_residual_and_mixed_environment_definition():
    profile = producer.result_profile({"schema": "synthetic-linker-fixture"}, successor=True)
    assert "including explicit H" in profile["metrics"]["residual_from_full_internal_A"]["definition"]
    assert "actual mixed coordinate" in profile["metrics"]["min_vdw_ratio_by_reset"]["definition"]
    reference, current, ligand, core = coordinates()
    _, _, operation = producer.successor_candidate(current, reference, ligand, core,
        current[ligand], 4, np.arange(6), np.arange(4), np.array([0]), np.ones(4), np.ones(6))
    result = bind_scientific_result(profile, subjects=[{"id": "synthetic-only"}],
        operation={"resets": [producer.operation_summary(operation)]},
        conditions={"selection": {"ligand": producer.selection_identity(ligand)},
                    "frame_window": {"frames": 1},
                    "qc_environment": producer.selection_identity([0])},
        observations={"residual_from_full_internal_A": None})
    assert validate_result_binding(result, profile)["host_interpretation"] is None


def test_internal_residual_uses_every_supplied_ligand_atom():
    reference, _, ligand, core = coordinates()
    reference = np.vstack([reference, [3., 2., 2.]])
    current = reference.copy()
    current[-1, 2] += 3
    # Last atom represents an explicit H in this mathematical fixture. Its
    # displacement must enter all-atom RMSD despite unchanged heavy coordinates.
    ligand = np.append(ligand, len(reference)-1)
    result = motion.factorial_contacts([reference, current], [np.eye(3)*40]*2,
        ligand, core, [1, 2, 3], [[4, 5, 9]], ["fixture-anchor"])
    assert result["metrics"]["internal_rmsd_A"][1] == pytest.approx(np.sqrt(9/5))
    heavy_residual = result["aligned"][1, ligand[:-1]]-result["reference"][ligand[:-1]]
    assert np.linalg.norm(heavy_residual) < 1e-10
