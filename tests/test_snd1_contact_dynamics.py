import importlib.util
from pathlib import Path
import numpy as np
import pytest

spec=importlib.util.spec_from_file_location('contact_dynamics',Path(__file__).parents[1]/'examples/snd1_contact_dynamics.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_directional_contact_is_periodic_and_translation_invariant():
    box=np.diag([10.,10.,10.]);xyz=np.array([[9.,0,0],[0.,0,0],[1.8,0,0]])
    d,a=module.contact_geometry(xyz,box,[[0,1,2]])
    assert d[0]==pytest.approx(2.8) and a[0]==pytest.approx(180.)
    d2,a2=module.contact_geometry(xyz+np.array([13.,-4.,7.]),box,[[0,1,2]])
    np.testing.assert_allclose(d,d2);np.testing.assert_allclose(a,a2)


def test_backbone_alignment_removes_rotation_without_reflection():
    points=np.array([[0.,0,0],[1,0,0],[0,2,0],[0,0,3]])
    rotation=np.array([[0.,-1,0],[1,0,0],[0,0,1]])
    moved=points@rotation+np.array([6.,-2.,4.])
    r,t=module.rigid_fit(moved,points)
    np.testing.assert_allclose(moved@r+t,points,atol=1e-12)
    assert np.linalg.det(r)==pytest.approx(1.)


def test_degenerate_hydrogen_is_rejected():
    with pytest.raises(ValueError,match='Degenerate'):
        module.contact_geometry(np.array([[0.,0,0],[0.,0,0],[2.,0,0]]),np.eye(3)*10,[[0,1,2]])


def test_reference_image_preserves_contact_under_rigid_alignment():
    box=np.eye(3)*10
    xyz=np.array([[9.,5,5],[0.,5,5],[1.8,5,5],[1,6,5],[1,5,6],[2,6,6]])
    base=module.local_reference(xyz,box,2,3)
    theta=.19;rotation=np.array([[np.cos(theta),-np.sin(theta),0],[np.sin(theta),np.cos(theta),0],[0,0,1]])
    moved=(base@rotation+np.array([.2,.1,.3]))%10
    whole=base+module.minimum_image(moved-base,box)
    r,t=module.rigid_fit(whole[3:],base[3:])
    aligned=whole@r+t
    d,a=module.contact_geometry(moved,box,[[0,1,2]])
    da,an=module.contact_geometry(aligned,box,[[0,1,2]])
    np.testing.assert_allclose(d,da,atol=1e-10)
    np.testing.assert_allclose(a,an,atol=1e-6)


def test_periodic_native_energy_keeps_units_and_phase():
    angles=np.radians(np.array([0.,90.,180.]))
    np.testing.assert_allclose(module.periodic_torsion_energy(angles,180.,.4184,2),[0.,.8368,0.],atol=1e-12)
    # Three evenly spaced side groups cancel the second-harmonic dependence.
    phi=np.radians(np.arange(360)[:,None]+np.array([0.,120.,240.]))
    energy=module.periodic_torsion_energy(phi,180.,.4184,2).sum(axis=1)
    np.testing.assert_allclose(energy,3*.4184,atol=1e-12)
