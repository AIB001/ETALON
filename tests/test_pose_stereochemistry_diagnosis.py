"""Diagnostic controls do not turn unspecified stereo into scientific success."""
import importlib.util
from pathlib import Path
import unittest

from rdkit import Chem
from rdkit.Chem import AllChem

path = Path(__file__).parents[1] / "examples" / "diagnose_pose_stereochemistry.py"
spec = importlib.util.spec_from_file_location("stereo_diagnostic", path)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


class StereoDiagnosticTests(unittest.TestCase):
    def test_tetrahedral_inversion_is_reported(self):
        a, b = Chem.MolFromSmiles("F[C@H](Cl)Br"), Chem.MolFromSmiles("F[C@@H](Cl)Br")
        result = diagnostic.correspondence(a, b)
        self.assertTrue(result["graph_equal_without_stereo"])
        self.assertTrue(any(d["field"] == "chiral_tag" for d in result["atom_differences"]))

    def test_isotope_and_charge_are_not_erased_by_graph_mapping(self):
        for a, b in [("[13CH3]CN", "CCN"), ("CC[NH3+]", "CCN")]:
            self.assertFalse(diagnostic.correspondence(Chem.MolFromSmiles(a), Chem.MolFromSmiles(b))["graph_equal_without_stereo"])

    def test_unknown_imine_flag_blocks_geometric_stereo_without_coordinate_change(self):
        mol = Chem.AddHs(Chem.MolFromSmiles("N=C(N)c1ccccc1"))
        self.assertEqual(AllChem.EmbedMolecule(mol, randomSeed=41), 0)
        block = Chem.MolToMolBlock(mol)
        parsed = diagnostic.read_mol(block)
        self.assertEqual(str(parsed.GetBondWithIdx(0).GetStereo()), "STEREOANY")
        control = diagnostic.flag_counterfactual(block, 0, 0)
        self.assertTrue(control["coordinates_unchanged"])
        self.assertIn(control["signature"]["double_bonds"][0]["stereo"], {"STEREOE", "STEREOZ"})
        self.assertEqual(len(control["signature"]["remaining_h_indices"]), 1)

    def test_mapping_handles_atom_reordering_without_reporting_chemistry_change(self):
        a = Chem.MolFromSmiles("CC(=O)NC")
        b = Chem.RenumberAtoms(a, list(reversed(range(a.GetNumAtoms()))))
        result = diagnostic.correspondence(a, b)
        self.assertTrue(result["graph_equal_without_stereo"])
        self.assertEqual(result["atom_differences"], [])
        self.assertEqual(result["bond_differences"], [])

    def test_blank_molfile_stereo_field_is_read_as_zero(self):
        block = Chem.MolToMolBlock(Chem.MolFromSmiles("CC"))
        lines = block.splitlines()
        lines[6] = lines[6][:9] + "   " + lines[6][12:]
        self.assertEqual(diagnostic.molfile_fields("\n".join(lines))["bonds"][0]["stereo"], 0)


if __name__ == "__main__":
    unittest.main()
