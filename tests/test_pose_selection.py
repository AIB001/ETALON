"""Counterexamples: selection never learns from held-out seeds or hidden labels."""
import copy
import pytest
from etalon.research.pose_selection import FEATURES, fit_rule, grouped_diagnostics, select_with_policy
from etalon.research.pose_task import select_pose_branch


def rows():
    return [dict(case_id=str(i),seed=seed,features={f:float(i) for f in FEATURES},
                 empirical=dict(top1=i>=4,physical_valid=True),cnn=dict(top1=i<4,physical_valid=True))
            for i in range(8) for seed in (41,137)]


def test_grouped_fit_rejects_inconsistent_features_and_duplicate_seeds():
    data=rows()
    with pytest.raises(ValueError,match='unique'): fit_rule(data+[data[0]])
    bad=copy.deepcopy(data);bad[1]['features']['hbd']=99
    with pytest.raises(ValueError,match='differ'): fit_rule(bad)
    policy=fit_rule(data)
    assert policy['training_metrics']['successes']==16
    folds=grouped_diagnostics(data)['rows']
    # With one whole case held out, the 4-case minimum disallows this split.
    assert all(f['rule']['kind']=='constant' for f in folds)


def test_policy_rejects_output_features_and_falls_back_outside_support():
    policy=fit_rule(rows());features={'stage':'before_scientific_execution','values':rows()[0]['features']}
    bad=copy.deepcopy(features);bad['values']['rmsd']=.1
    with pytest.raises(ValueError,match='input-only'):select_with_policy(policy,bad)
    bad=copy.deepcopy(features);bad['stage']='after_cnn'
    with pytest.raises(ValueError,match='pre-execution'):select_with_policy(policy,bad)
    bad=copy.deepcopy(features);bad['values'][policy['rule']['feature']]=100
    assert select_with_policy(policy,bad)['selected']=='empirical'


def test_supersession_not_timestamp_resolves_conflict_but_never_missing_history():
    def item(i,outcome,prior=()):
        return {'ref':{'fixture':i},'experience':dict(outcome=outcome,scope='s',conditions={'model':'v1'},
            capability_id='ranking',supersedes=[{'fixture':j} for j in prior])}
    a,b=item(1,'retain'),item(2,'adopt',[1])
    assert select_pose_branch({'matches':[a,b]})['selected']=='cnn'
    assert select_pose_branch({'matches':[b]})['selected']=='empirical'
    assert select_pose_branch({'matches':[a,b],'next_cursor':'next'})['selected']=='empirical'
    c=item(3,'rollback',[2])
    assert select_pose_branch({'matches':[a,b,c]})['selected']=='empirical'
    assert select_pose_branch({'matches':[item(1,'adopt',[2]),item(2,'adopt',[1])]})['invalid_supersession']
    bad=copy.deepcopy(b);bad['experience']['conditions']={'model':'v2'}
    assert select_pose_branch({'matches':[a,bad]})['invalid_supersession']
    assert select_pose_branch({'matches':[a,b,item(4,'retain')]})['selected']=='empirical'


def test_input_features_ignore_coordinates_and_sdf_score_properties(tmp_path):
    Chem=pytest.importorskip('rdkit.Chem')
    from etalon.research.pose_selection import input_features
    m=Chem.MolFromSmiles('CCO');c=Chem.Conformer(m.GetNumAtoms());m.AddConformer(c)
    path=tmp_path/'conditioning.sdf'
    with Chem.SDWriter(str(path)) as w:w.write(m)
    before=input_features(path)
    m.SetProp('RMSD','0.0');m.SetProp('CNNscore','1.0')
    for i in range(m.GetNumAtoms()):m.GetConformer().SetAtomPosition(i,(100+i,200,300))
    with Chem.SDWriter(str(path)) as w:w.write(m)
    after=input_features(path)
    assert before['values']==after['values']
    assert before['source']!=after['source']
    assert after['values']['hbd']==1
