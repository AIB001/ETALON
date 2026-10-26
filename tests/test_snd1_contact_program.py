import importlib.util
from pathlib import Path
import unittest
import numpy as np

spec=importlib.util.spec_from_file_location('contact_program',Path(__file__).parents[1]/'examples/snd1_contact_program.py')
method=importlib.util.module_from_spec(spec);spec.loader.exec_module(method)


class ContactProgramTest(unittest.TestCase):
    def test_donor_ball_contains_exact_cone_shell_including_boundaries(self):
        d=np.array([1.,2.,3.]);h=d+[1.,0.,0.]
        ball=method.donor_region(d,h)
        for radius in np.linspace(2.5,3,19):
            for angle in np.linspace(0,np.pi/6,17):
                r=-np.cos(angle)+np.sqrt(radius*radius-np.sin(angle)**2)
                for azimuth in [0,.4,2.4,5.]:
                    a=h+r*np.array([np.cos(angle),np.sin(angle)*np.cos(azimuth),np.sin(angle)*np.sin(azimuth)])
                    self.assertLessEqual(np.linalg.norm(a-ball['center']),ball['radius']+1e-12)

    def test_region_does_not_treat_one_ideal_point_as_entire_contact_domain(self):
        regions=[dict(center=[0,0,0],radius=1),dict(center=[4,0,0],radius=1)]
        self.assertTrue(method.compatibility([[0,0,0],[5.5,0,0]],regions)['necessary_condition_passes'])
        self.assertFalse(method.compatibility([[0,0,0],[6.1,0,0]],regions)['necessary_condition_passes'])

    def test_known_rigid_witness_is_never_excluded(self):
        rng=np.random.default_rng(27)
        for _ in range(100):
            p=rng.normal(size=(4,3));q=p@[ [0,-1,0],[1,0,0],[0,0,1] ]+[5,-1,2]
            delta=rng.normal(size=(4,3))*.2
            regions=[dict(center=c+e,radius=np.linalg.norm(e)) for c,e in zip(q,delta)]
            self.assertTrue(method.compatibility(p,regions)['necessary_condition_passes'])

    def test_pair_fit_preserves_distances_and_handedness(self):
        p=np.array([[0.,0,0],[2,0,0],[0,1,0],[0,0,1]])
        q=np.array([[4.,2,3],[4,4,3]])
        for twist in [0,90,215]:
            result=method.pair_motion(p,[0,1],q,twist_deg=twist)
            np.testing.assert_allclose(result[:2],q,atol=1e-12)
            np.testing.assert_allclose(np.linalg.norm(result[:,None]-result[None],axis=-1),np.linalg.norm(p[:,None]-p[None],axis=-1),atol=1e-12)
            self.assertGreater(np.linalg.det(result[1:]-result[0]),0)

    def test_antiparallel_and_repeated_anchors(self):
        p=np.array([[0.,0,0],[1,0,0],[0,1,0]])
        result=method.pair_motion(p,[0,1],[[0,0,0],[-1,0,0]])
        np.testing.assert_allclose(result[:2],[[0,0,0],[-1,0,0]],atol=1e-12)
        with self.assertRaises(ValueError):method.pair_motion(p,[0,0],[[0,0,0],[1,0,0]])

    def test_unphysical_or_nonfinite_region_is_rejected(self):
        for kwargs in [dict(distance_shell=(.5,3)),dict(angle_min=80),dict(distance_shell=(2.5,float('nan')))]:
            with self.assertRaises(ValueError):method.donor_region([0,0,0],[1,0,0],**kwargs)


if __name__=='__main__':unittest.main()
