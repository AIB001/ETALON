"""Synthetic counterexamples for same-loss versus ensemble pose objectives."""
import pytest
from etalon.research.pose_selection import selection_diagnostic


def test_minimax_single_pose_differs_from_ensemble_and_preserves_ties():
    rows = [dict(id='a',slot=1,admitted=True,score=-2.,mode_rmsd_A={'A':0.,'B':4.}),
            dict(id='b',slot=2,admitted=True,score=-2.,mode_rmsd_A={'A':4.,'B':0.}),
            dict(id='c',slot=3,admitted=True,score=-1.,mode_rmsd_A={'A':2.,'B':2.}),
            dict(id='failed',slot=4,admitted=False)]
    report = selection_diagnostic(rows)
    assert report['ensemble_worst_mode_coverage_A']==0
    assert report['single_pose_objectives']['worst_mode']['same_loss_regret_A']==2
    assert report['single_pose_objectives']['nearest_mode']['same_loss_regret_A']==0
    assert report['selected_pose_id']=='a' and report['tie_pose_ids']==['a','b']
    assert report['attempted']==4 and report['admitted']==3


def test_missing_score_retains_geometry_pool_and_mode_mismatch_rejected():
    rows=[dict(id='a',slot=1,admitted=True,score=-1.,mode_rmsd_A={'A':2.}),
          dict(id='b',slot=2,admitted=True,score=None,mode_rmsd_A={'A':0.})]
    report=selection_diagnostic(rows)
    metric=report['single_pose_objectives']['nearest_mode']
    assert metric['same_loss_regret_A']==2 and metric['scored_pool_regret_A']==0
    assert report['unscored_admitted_ids']==['b']
    rows[1]['mode_rmsd_A']={'B':0.}
    with pytest.raises(ValueError,match='reference mode'): selection_diagnostic(rows)
