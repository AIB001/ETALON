"""Discriminating checks of exact substitutions and unsupported fallbacks."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest

spec = importlib.util.spec_from_file_location("adaptive", Path(__file__).parents[1]/"examples/research_adaptive_methods.py")
adaptive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adaptive)


def test_fingerprint_bounds_preserve_ties_zero_vectors_and_duplicates():
    from rdkit import DataStructs
    def fp(bits):
        result=DataStructs.ExplicitBitVect(64)
        for bit in bits: result.SetBit(bit)
        return result
    values=[fp([]), fp([1]), fp([1,2]), fp([1,2]), fp(range(30))]
    for threshold in (0., .5, 1.):
        a=adaptive.fingerprint_threshold(values, values, threshold)
        b=adaptive.fingerprint_threshold(values, values, threshold, method="popcount")
        assert a["hits"] == b["hits"]
    assert b["skipped_pairs"] > 0


def test_count_fingerprint_falls_back_without_treating_counts_as_bits():
    from rdkit import DataStructs
    a,b=DataStructs.UIntSparseIntVect(32),DataStructs.UIntSparseIntVect(32)
    a[1]=4; b[1]=2
    result=adaptive.fingerprint_threshold([a], [b], method="popcount")
    assert result["fallback"] == "unsupported_fingerprint_type"
    assert result["hits"] == adaptive.fingerprint_threshold([a],[b])["hits"]


def test_contact_boundary_and_random_predicates():
    rng=np.random.default_rng(52)
    points=rng.normal(size=(5000,3,3))
    boundary=np.array([[0.,0.,0.],[1.,0.,0.],[3.,0.,0.]])
    points=np.concatenate([points,boundary[None]])
    for distance in (0., 2.5, 3.):
        for angle in (0.,90.,150.,180.):
            a=adaptive.contact_hits(points,distance_A=distance,angle_deg=angle)
            b=adaptive.contact_hits(points,distance_A=distance,angle_deg=angle,method="bounded")
            np.testing.assert_array_equal(a["hit"],b["hit"])
    assert b["boundary_fallbacks"] > 0


def test_translation_exclusion_preserves_every_proposal_and_topology_fallback():
    xyz=np.array([[[0.,0.,0.],[1.,0.,0.],[2.8,0.,0.]],
                  [[0.,0.,0.],[1.,0.,0.],[9.,0.,0.]]])
    args=(xyz,[[0,1,2]],["anchor"],[2],[[0.,0.,0.],[.2,0.,0.],[-.2,0.,0.]])
    a=adaptive.translation_contact_search(*args)
    b=adaptive.translation_contact_search(*args,method="bounded")
    np.testing.assert_array_equal(a["joint"],b["joint"])
    assert b["excluded_frames"].tolist()==[False,True]
    unsupported=(*args[:3],[0,1,2],args[-1])
    c=adaptive.translation_contact_search(*unsupported,method="bounded")
    assert c["fallback"]
    np.testing.assert_array_equal(c["joint"],adaptive.translation_contact_search(*unsupported)["joint"])


def test_degenerate_far_contact_is_never_hidden_by_pruning():
    points=np.array([[[0.,0.,0.],[0.,0.,0.],[99.,0.,0.]]])
    for method in ("exact","bounded"):
        with pytest.raises(ValueError,match="Degenerate"):
            adaptive.contact_hits(points,method=method)


def test_ligand_donor_and_protein_donor_share_same_exact_bound():
    xyz=np.array([[[0.,0.,0.],[1.,0.,0.],[2.8,0.,0.],
                   [0.,2.,0.],[1.,2.,0.],[2.8,2.,0.]],
                  [[0.,0.,0.],[1.,0.,0.],[9.,0.,0.],
                   [0.,2.,0.],[1.,2.,0.],[9.,2.,0.]]])
    args=(xyz,[[0,1,2],[3,4,5]],["A","B"],[2,3,4],[[0.,0.,0.],[.2,0.,0.],[-.2,0.,0.]])
    a=adaptive.translation_contact_search(*args)
    b=adaptive.translation_contact_search(*args,method="bounded")
    np.testing.assert_array_equal(a["joint"],b["joint"])
    assert b["fallback"] is None
    assert b["excluded_frames"].tolist()==[False,True]


def test_anchor_projection_inside_apex_ball_and_rim_certificates():
    dh=np.array([0.,0.,-1.])
    for w,expected in [([0,0,1],0.),([0,0,4],2.),([0,0,-1],1.),([2,0,1],None),([4,0,4],None)]:
        result=adaptive.anchor_projection(w,dh)
        q=result["projection_A"]; normal=result["dual_unit_normal"]
        assert result["primal_residual_A"] <= 1e-8
        assert abs(result["primal_dual_gap_A"]) <= 1e-8
        assert np.linalg.norm(np.asarray(w)-q)==pytest.approx(result["upper_bound_A"])
        if expected is not None: assert result["lower_bound_A"]==pytest.approx(expected)
        # Independent feasible cone/ball points must respect the dual support.
        for z in np.linspace(0.,2.,17):
            radius=min(z/np.sqrt(3),np.sqrt(max(9-(z+1)**2,0)))
            for phi in np.linspace(0.,2*np.pi,11):
                feasible=np.array([radius*np.cos(phi),radius*np.sin(phi),z])
                assert normal @ feasible <= result["dual_support_A"]+1e-8


def test_anchor_projection_exposes_thresholds_and_rotation_equivariance():
    w=np.array([2.,0.,1.]); dh=np.array([0.,0.,-1.])
    strict=adaptive.anchor_projection(w,dh)
    wide=adaptive.anchor_projection(w,dh,angle_deg=120.)
    assert wide["upper_bound_A"] < strict["lower_bound_A"]
    assert adaptive.anchor_projection([0,0,4],dh,distance_A=4.)["upper_bound_A"]==pytest.approx(1.)
    rotation=np.array([[0.,1.,0.],[0.,0.,1.],[1.,0.,0.]])
    rotated=adaptive.anchor_projection(w@rotation,dh@rotation)
    np.testing.assert_allclose(rotated["projection_A"],strict["projection_A"]@rotation,atol=1e-12)
    assert rotated["lower_bound_A"]==pytest.approx(strict["lower_bound_A"])


@pytest.mark.parametrize("kwargs",[{"angle_deg":90.},{"angle_deg":180.},{"distance_A":1.},{"tolerance":0.}])
def test_anchor_projection_unsupported_regime_never_certifies(kwargs):
    with pytest.raises(ValueError):
        adaptive.anchor_projection([0.,0.,4.],[0.,0.,-1.],**kwargs)


def _contact_at(w, dh=(0., 0., -1.)):
    return np.array([dh, [0., 0., 0.], w], float)


def test_joint_conflicting_distance_anchors_have_checkable_certificate():
    # Each single contact needs a 0.4 shift, but in opposite directions.
    points = [_contact_at([0., 0., 2.4]), _contact_at([0., 0., -2.4], [0., 0., 1.])]
    result = adaptive.joint_anchor_feasibility(points, ['left', 'right'], [[0,0,1]]*2)
    assert result['status'] == 'certified_unreachable'
    assert result['reason'] == 'all_joint_combinations_certified'
    assert not result['single_contact_exclusions']
    cert = result['certificates'][0]
    weights = np.array(cert['nonnegative_weights'])
    A = np.array([p['normal_translation'] for p in cert['planes']])
    b = np.array([p['offset_A'] for p in cert['planes']])
    assert (weights >= 0).all()
    assert weights @ b+.5*np.linalg.norm(weights @ A) < -1e-8


def test_joint_search_finds_common_interior_and_handles_donor_orientation():
    p = [_contact_at([.5, 0., 1.]), _contact_at([.4, 0., 1.])]
    for moving in ([[0,0,1]]*2, [[1,1,0]]*2):
        result = adaptive.joint_anchor_feasibility(p, ['a','b'], moving)
        assert result['status'] == 'witness'
        t = np.array(result['witness']['translation_A'])
        hit = adaptive.contact_hits(np.array(p)+t*np.array(moving)[...,None])['hit']
        assert hit.all() and np.linalg.norm(t) <= .5
        assert result['witness']['displacement_margin_A'] > .01


def test_union_alternatives_and_three_required_labels():
    p = [_contact_at([0.,0.,9.]), _contact_at([0.,0.,1.]),
         _contact_at([.1,0.,1.]), _contact_at([-.1,0.,1.])]
    result = adaptive.joint_anchor_feasibility(p, ['a','a','b','c'], [[0,0,1]]*4)
    assert result['status'] == 'witness'
    assert result['surviving_contacts']['a'] == [1]


def test_cached_lower_bounds_cannot_forge_exclusion():
    p = np.array([_contact_at([.1, 0., 1.]), _contact_at([-.1, 0., 1.])])
    previous = dict(dual_unit_normal=np.array([[0.,0.,1.],[0.,0.,1.]]),
                    lower_bound_A=np.full(2, 999.), minimizing_translations=np.zeros((2,3)))
    result = adaptive.joint_anchor_feasibility(p, ['a','b'], [[0,0,1]]*2, previous=previous)
    assert result['status'] == 'witness'
    assert not result['single_contact_exclusions']
    assert result['cached_lower_max_difference_A'] > 990


def test_support_function_dominates_independent_feasible_points():
    rng = np.random.default_rng(20261027)
    for angle in (100., 120., 150., 175.):
        dh = np.array([0., 0., -1.])
        beta = np.radians(180-angle)
        for normal in rng.normal(size=(40,3)):
            support = adaptive._anchor_support(normal, dh, 3., angle)
            z = rng.uniform(0,2,1000); radial = rng.uniform(0,1,1000)*z*np.tan(beta)
            azimuth = rng.uniform(-np.pi,np.pi,1000)
            q = np.c_[radial*np.cos(azimuth),radial*np.sin(azimuth),z]
            q = q[np.linalg.norm(q-dh,axis=1) <= 3.]
            assert (q @ normal <= support+1e-12).all()


def test_joint_unsupported_and_iteration_failure_retain_unknown():
    p = [_contact_at([0.,0.,2.4]), _contact_at([0.,0.,-2.4],[0.,0.,1.])]
    result = adaptive.joint_anchor_feasibility(p,['a','b'],[[0,1,0]]*2)
    assert result['status']=='unknown' and result['reason']=='unsupported_topology'
    result = adaptive.joint_anchor_feasibility(p,['a','b'],[[0,0,1]]*2,angle_deg=90.)
    assert result['status']=='unknown'
    # At the tangent budget, no positive contradiction margin is allowed.
    p = [_contact_at([0.,0.,2.5])]
    result = adaptive.joint_anchor_feasibility(p,['a'],[[0,0,1]],max_iterations=1)
    assert result['status'] in ('unknown','witness')
    assert not result['certificates']


def test_joint_cli_uses_shared_reuse_without_new_solver_on_reentry(tmp_path):
    import json
    import os
    import subprocess
    import sys
    points=np.array([[_contact_at([.1,0.,1.]),_contact_at([-.1,0.,1.])]])
    input_path=tmp_path/'input.npz'; output=tmp_path/'output'
    np.savez(input_path,points=points,labels=np.array(['x','y']),moving=np.array([[0,0,1]]*2),
             frame_ids=np.array(['synthetic:0']),time_ps=np.array([0.]),
             previous_dual_unit_normal=np.zeros((1,2,3)),previous_lower_bound_A=np.zeros((1,2)),
             previous_minimizing_translations=np.zeros((1,2,3)),
             settings_json=np.array(json.dumps({})),provenance_json=np.array(json.dumps({'kind':'synthetic'})))
    command=[sys.executable,str(Path(adaptive.__file__)),'--input',str(input_path),'--output',str(output)]
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'src'),OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
    first=json.loads(subprocess.run(command,env=env,capture_output=True,text=True,check=True).stdout)
    original=(output/'frames.json').read_bytes()
    second=json.loads(subprocess.run(command,env=env,capture_output=True,text=True,check=True).stdout)
    assert first['status']=='complete' and not first['reused']
    assert second['reused'] and second['additional_solver_calls']==0
    assert first['request_digest']==second['request_digest']
    assert first['receipt_path']==second['receipt_path']
    assert (output/'frames.json').read_bytes()==original
    assert second['counts']=={'witness':1,'certified_unreachable':0,'unknown':0}
    assert 'reuse' in second['receipt_path']


def test_separate_cone_ball_normals_certify_conflict_hidden_by_projections():
    # Synthetic cone/ball rim versus a tilted ball: individually reachable
    # inside .5, jointly conflicting. Two projection directions alone miss it.
    c=np.sqrt(3)/2; length=-c+np.sqrt(9-.25)
    q=length*np.array([.5,0.,c]); dh=np.array([0.,0.,-1.])
    ball=(q-dh)/3; cone=np.array([c,0.,-.5])
    delta=np.linalg.lstsq(np.array([ball,cone]),np.array([.02,.02]),rcond=None)[0]
    combined=.4*cone+.6*ball; opposite=-combined/np.linalg.norm(combined)
    points=np.array([[dh,np.zeros(3),q+delta],[-opposite,np.zeros(3),2.02*opposite]])
    args=(points,['rim','ball'],[[0,0,1]]*2)
    projection_only=adaptive.joint_anchor_feasibility(*args,enrich_normals=False)
    result=adaptive.joint_anchor_feasibility(*args)
    assert projection_only['status']=='unknown'
    assert result['status']=='certified_unreachable'
    assert not result['single_contact_exclusions']
    assert result['statistics']['enriched_combinations']==1
    assert result['primal_diagnostics'][0]['optimum_shared_slack_A'] < -.01
    assert len(projection_only['dual_diagnostics'])==2
    assert projection_only['primal_diagnostics'][0]['translation_A']
    certificate=result['certificates'][0]
    weights=np.array(certificate['nonnegative_weights'])
    A=np.array([p['normal_translation'] for p in certificate['planes']])
    b=np.array([p['offset_A'] for p in certificate['planes']])
    assert (weights>=0).all()
    assert weights@b+.5*np.linalg.norm(weights@A)<-.01
