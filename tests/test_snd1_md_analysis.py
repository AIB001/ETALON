"""Discriminating readout checks: rigid motion must not hide ligand drift."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np

PATH = Path(__file__).parents[1] / "examples/analyze_snd1_md.py"
spec = importlib.util.spec_from_file_location("snd1_md_analysis", PATH)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def test_protein_fit_preserves_ligand_translation_after_complex_rotation():
    protein = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
    ligand = np.array([[2., 2., 2.], [2., 3., 2.], [2., 2., 3.]])
    reference = np.concatenate((protein, ligand))
    displaced = reference.copy(); displaced[4:, 0] += 0.7
    rotation = np.array([[0., 1., 0.], [-1., 0., 0.], [0., 0., 1.]])
    rotated = displaced @ rotation + np.array([7., -9., 3.])
    fitted = analysis.aligned_coordinates(rotated, reference, range(4))
    assert analysis.rmsd_fixed(fitted, reference, range(4)) < 1e-12
    assert np.isclose(analysis.rmsd_fixed(fitted, reference, range(4, 7)), 0.7)
    # A second ligand fit would erase exactly the signal of interest.
    ligand_fit = analysis.aligned_coordinates(fitted[4:], ligand, range(3))
    assert analysis.rmsd_fixed(ligand_fit, ligand, range(3)) < 1e-12


def test_fit_uses_proper_rotation_and_rejects_degenerate_selection():
    xyz = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
    mirror = xyz.copy(); mirror[:, 0] *= -1
    fitted = analysis.aligned_coordinates(mirror, xyz, range(4))
    assert analysis.rmsd_fixed(fitted, xyz, range(4)) > 0.1
    with unittest.TestCase().assertRaisesRegex(ValueError, "collinear"):
        analysis.aligned_coordinates(np.array([[0., 0., 0.], [1., 0., 0.], [2., 0., 0.]]), np.array([[0., 0., 0.], [1., 0., 0.], [2., 0., 0.]]), range(3))


def test_triclinic_nearest_image_and_continuous_ligand_translation():
    box = np.array([[3., 0., 0.], [0., 3., 0.], [1.5, 1.5, 2.2]])
    target = np.array([0.1, -0.2, 0.3])
    displacement = target + 2 * box[0] - box[1] + box[2]
    shift = analysis.nearest_image_shift(displacement, box)
    assert np.allclose(displacement - shift, target)
    # Lattice shifts must not alter an internal bond.
    xyz = np.array([[0., 0., 0.], [0.13, 0., 0.]]) + displacement
    assert np.isclose(np.linalg.norm((xyz - shift)[1] - (xyz - shift)[0]), 0.13)


def test_circular_angles_and_window_denominator():
    summary = analysis.circular_summary([179., -179.])
    assert abs(abs(summary["mean_deg"]) - 180) < 1e-10
    assert summary["resultant_length"] > 0.999
    assert np.isclose(analysis.principal_angle_delta(-179., 179.), 2.)
    masks = analysis.window_masks(np.arange(0., 20001., 10.))
    assert [int(masks[w].sum()) for w in ("0_5ns", "5_10ns", "10_15ns", "15_20ns")] == [500, 500, 500, 501]
    assert sum(masks[k].astype(int) for k in masks if k != "full").min() == 1


def test_native_unique_ligand_renumbering_preserves_atom_identity():
    reference = [("CA", "ALA", 17), ("N1", "MOL", 1), ("C1", "MOL", 1)]
    native = [("CA", "ALA", 17), ("N1", "MOL", 331), ("C1", "MOL", 331)]
    assert analysis.indexed_solute_binding(reference, native)["native_ligand_resid"] == 331
    with unittest.TestCase().assertRaisesRegex(ValueError, "indexed"):
        analysis.indexed_solute_binding(reference, [native[0], native[2], native[1]])
    with unittest.TestCase().assertRaisesRegex(ValueError, "protein"):
        analysis.indexed_solute_binding(reference, [("CA", "ALA", 18), *native[1:]])


if __name__ == "__main__":
    checks = [value for name, value in globals().items() if name.startswith("test_")]
    for check in checks:
        check()
    print(f"{len(checks)} readout checks passed (framework-free)")
