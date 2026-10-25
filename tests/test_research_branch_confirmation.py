import importlib.util
from pathlib import Path

import pytest

source=Path(__file__).parents[1]/'examples/research_branch_confirmation.py'
if not source.exists():source=Path(__file__).parent/'research_branch_confirmation.py'
spec=importlib.util.spec_from_file_location('research_branch_confirmation',source)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_gate_boundary_single_pose_and_missing_score():
    assert module.gate_from_ranking({'scores':[-9.,-8.5],'ordered_pose_indices':[1,0]})['request_cnn']
    assert not module.gate_from_ranking({'scores':[-9.,-8.499],'ordered_pose_indices':[1,0]})['request_cnn']
    one=module.gate_from_ranking({'scores':[-9.],'ordered_pose_indices':[0]})
    assert one['gap_kcal_mol'] is None and not one['request_cnn']
    for ranking in ({'scores':[],'ordered_pose_indices':[]},{'scores':[float('nan')],'ordered_pose_indices':[0]},
                    {'scores':[-8.,-9.],'ordered_pose_indices':[0,1]},{'scores':[-9.,-8.],'ordered_pose_indices':[0,0]}):
        with pytest.raises(ValueError):module.gate_from_ranking(ranking)


def test_unknown_pair_is_not_a_win_or_zero_loss_observation():
    rows=[{'top1':{'candidate':{'valid_and_RMSD_le_2A':True},'cnn':{'valid_and_RMSD_le_2A':None}}},
          {'top1':{'candidate':{'valid_and_RMSD_le_2A':False},'cnn':{'valid_and_RMSD_le_2A':True}}}]
    result=module.paired(rows,'cnn')
    assert result['wins']==0 and result['losses']==1 and result['unknown_pairs']==1
    assert result['observed_pairs']==1 and result['net_gain']==-1 and result['one_sided_exact_improvement_p']==1.


def test_physical_gain_elsewhere_cannot_cancel_a_regression(tmp_path):
    import json
    def save(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')
    save(tmp_path/'panel.json',{'cases':[{'id':'A','dataset':'fixture'},{'id':'B','dataset':'fixture'}],'preparation_wall_seconds':1.})
    save(tmp_path/'execution-freeze.json',{'fixture':True})
    for cid,base_valid in [('A',True),('B',False)]:
        top=lambda valid:{'physical_valid':valid,'valid_and_RMSD_le_2A':False}
        save(tmp_path/'outputs'/cid/'result.json',{'case_id':cid,'dataset':'fixture','status':'completed','integrity':{'status':'complete'},
            'top1':{'empirical':top(base_valid),'cnn':top(base_valid),'candidate':top(not base_valid)},
            'gate':{'request_cnn':False,'cpu_seconds':.001}})
        save(tmp_path/'outputs'/cid/'verifier.json',{'wall_seconds':1.,'cpu_seconds':1.})
        for stage in ('empirical','cnn'):
            save(tmp_path/'receipts'/(cid+stage)/'receipt.json',{'stage':stage,'case_id':cid,'wall_seconds':1.,'cpu_second':1.,'status':'completed'})
    value=module.evaluate(tmp_path)
    assert all(v['physical_valid_top1']==1 for v in value['metrics'].values())
    assert value['physical_valid_regressions_vs_either']==['A']
    assert value['adoption_checks']['physical_valid_retention'] is False


def test_unknown_success_uses_identification_bounds_not_binomial_interval(tmp_path):
    import json
    def save(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')
    save(tmp_path/'panel.json',{'cases':[{'id':'A','dataset':'fixture'},{'id':'B','dataset':'fixture'}],'preparation_wall_seconds':1.})
    save(tmp_path/'execution-freeze.json',{'fixture':True})
    for cid,success in [('A',True),('B',None)]:
        top={'physical_valid':success,'valid_and_RMSD_le_2A':success}
        save(tmp_path/'outputs'/cid/'result.json',{'case_id':cid,'dataset':'fixture','status':'completed','integrity':{'status':'complete' if success else 'unknown'},
            'top1':{m:top for m in ('empirical','cnn','candidate')},'gate':{'request_cnn':False,'cpu_seconds':.001}})
        save(tmp_path/'outputs'/cid/'verifier.json',{'wall_seconds':1.,'cpu_seconds':1.})
        for stage in ('empirical','cnn'):
            save(tmp_path/'receipts'/(cid+stage)/'receipt.json',{'stage':stage,'case_id':cid,'wall_seconds':1.,'cpu_second':1.,'status':'completed'})
    value=module.evaluate(tmp_path)
    for metric in value['metrics'].values():
        assert metric['wilson95_descriptive'] is None
        assert metric['known_success']==1 and metric['unknown_top1']==1
        assert metric['success_fraction_identification_bounds']==[.5,1.]
    assert value['paired']['cnn']['observed_pairs']==1 and value['paired']['cnn']['unknown_pairs']==1
    assert value['decision']=='retain' and value['quality_superiority'] is False
