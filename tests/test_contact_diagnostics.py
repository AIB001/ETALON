"""Geometry fixture comparing the batched method to the established frame loop."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest
from etalon.rescore.contacts import geometry, diagnose


@pytest.mark.parametrize('tilt',[0.,2.3])
def test_periodic_geometry_matches_frame_loop_and_keeps_angular_distance_losses_distinct(tilt):
    path=Path(__file__).parents[1]/'examples/snd1_paired_relaxation.py'
    spec=importlib.util.spec_from_file_location('contact_loop',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    rng=np.random.default_rng(27)
    xyz=rng.uniform(0,12,(40,9,3));boxes=np.broadcast_to(np.diag([12.,13.,14.]),(40,3,3)).copy()
    boxes[:,1,0]=tilt
    triples=[[0,1,2],[3,4,5],[6,7,8]];names=['ASN281','ARG255','SER254']
    distance,angle=geometry(xyz,boxes,triples)
    expected=module.contact_series(xyz,boxes,triples,names)
    for f,row in enumerate(expected):
        for t,name in enumerate(names):
            assert abs(distance[f,t]-row[name]['best_DA_A'])<1e-10
            assert abs(angle[f,t]-row[name]['best_DHA_deg'])<1e-10
    result=diagnose([[2.8],[2.9],[4.]],[[170],[120],[170]],['ASN281'],[0,1,2])['ASN281']
    assert result['all']==dict(contact=1,angular_loss=1,distance_loss=1,frames=3)
    assert result['first_loss_ps']==1 and result['first_loss_kind']=='angular_loss'
