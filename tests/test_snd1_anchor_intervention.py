import importlib.util
from pathlib import Path
import unittest
import numpy as np

spec=importlib.util.spec_from_file_location('anchor',Path(__file__).parents[1]/'examples/snd1_anchor_intervention.py')
anchor=importlib.util.module_from_spec(spec);spec.loader.exec_module(anchor)


class AnchorGeometryTest(unittest.TestCase):
    def test_nested_rotations_preserve_root_and_bonds(self):
        xyz=np.asarray([[0,1,0],[0,0,0],[1,0,0],[1,1,1],[2,2,1],[3,2,2]],float)
        bonds=[(0,1),(1,2),(2,3),(3,4),(4,5)]
        rotors=[dict(dihedral=d,moving=anchor.moving_component(bonds,d[1],d[2])) for d in [(0,1,2,3),(1,2,3,4),(2,3,4,5)]]
        pose=anchor.set_torsions(xyz,rotors,[-145,35,88])
        np.testing.assert_allclose(pose[:3],xyz[:3],atol=1e-12)
        np.testing.assert_allclose([anchor.dihedral(pose,r['dihedral']) for r in rotors],[-145,35,88],atol=1e-10)
        np.testing.assert_allclose([np.linalg.norm(pose[a]-pose[b]) for a,b in bonds],[np.linalg.norm(xyz[a]-xyz[b]) for a,b in bonds],atol=1e-12)
        angles=np.asarray([(0,1,2),(1,2,3),(2,3,4),(3,4,5)])
        np.testing.assert_allclose(anchor.bond_angles(pose,angles),anchor.bond_angles(xyz,angles),atol=1e-10)

    def test_ring_bond_rejected(self):
        with self.assertRaises(ValueError): anchor.moving_component([(0,1),(1,2),(2,0)],0,1)

    def test_fit_is_proper_rotation(self):
        x=np.asarray([[0,0,0],[2,0,0],[0,2,0],[0,0,1]],float)
        rotation=np.asarray([[0,-1,0],[1,0,0],[0,0,1]])
        y=x@rotation+[5,-3,2]
        R,t=anchor.rigid_fit(x,y)
        self.assertAlmostEqual(np.linalg.det(R),1)
        np.testing.assert_allclose(x@R+t,y,atol=1e-12)

    def test_periodic_distance(self):
        answer=anchor.minimum_image([[9.5,0,0],[-9.5,0,0]],np.eye(3)*10)
        np.testing.assert_allclose(answer,[[-.5,0,0],[.5,0,0]])

    def test_reachability_is_conditional_on_actual_moving_set(self):
        axis=dict(dihedral=[0,1,2,3],moving=[2,3,4])
        answer=anchor.reachable_anchor_actions(2,[axis],4)
        self.assertTrue(answer['donor_specific_torsion_contact_excluded'])
        self.assertEqual(answer['minimum_donor_displacement_A'],1)
        self.assertNotIn('lower_bound_required_translation_A',answer)
        self.assertFalse(anchor.reachable_anchor_actions(3,[axis],4)['donor_position_invariant'])
        self.assertFalse(anchor.reachable_anchor_actions(2,[axis],2.9)['donor_specific_torsion_contact_excluded'])


if __name__=='__main__': unittest.main()
