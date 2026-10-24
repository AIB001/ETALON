"""Synthetic protocol and selection tests; no scientific success asserted."""
from pathlib import Path
import sys

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

sys.path.insert(0,str(Path(__file__).parents[1]/'examples'))
import snd1_controlled_ablation as a
import snd1_scale_methods as w


def test_seen_selection_matches_exact_strata_and_deduplicates_controls():
    conditions=[];panel=[]
    for seed,f,t,angle in [(1,False,False,10),(2,False,False,9),(3,True,True,8),(4,False,True,7)]:
        cid=f'Q-receptor-large-{seed:02d}'
        conditions.append({'condition_id':cid,'system_id':'Q-receptor','family':'large',
                           'seed':seed,'angles_degrees':[angle,0,0,0],'coordinate_sha256':str(seed)})
        for config,success in [('fixed_core',f),('torsion_distance_cartesian_chain',t)]:
            panel.append({'request_id':cid+'--'+config,'verification':{'success':success}})
    selection=a.select_conditions(conditions,panel)
    assert selection['historical_common_failure_count']==2
    assert selection['matched_slots']==4 and selection['unique_conditions']==4
    assert sorted(selection['control_reuse'].values())==[2,2]
    assert all(p['control'].split('-')[-1] in ['03','04'] for p in selection['matches'])
    with pytest.raises(ValueError,match='no exact-stratum'):a.select_conditions(conditions[:-1],panel)


def test_pocket_off_retains_positional_energy_and_exact_gradient():
    xyz=np.array([[0.,0.,0.],[1.,0.,0.]])
    p={'geometry':{'dofs':[{'bounds_degrees':[-10,10]}],
                   'soft_positions':[{'index':0,'xyz':[1.,1.,0.],'flat_bottom_A':.25,'k_kcal_mol_A2':5}],
                   'soft_distances':[{'ligand_index':1,'protein_xyz':[1.,0.,0.],
                                      'lower_A':2.4,'upper_A':3.4,'k_kcal_mol_A2':10}]},
       'pocket':{'atoms':[{'element':'C','xyz':[1.1,0.,0.]}],'steric_scale':.75,'force_constant':20}}
    off=w.controlled_torsion_parameters(p,half_width_degrees=[30],pocket_terms=False)
    terms,grad=w.torsion.legacy.external_terms(xyz,[6,6],w.torsion.legacy_parameters(off))
    assert terms['protein_exclusion']==terms['contact_distance']==0
    assert terms['position_restraint']>0
    eps=1e-6
    for atom in range(2):
        for axis in range(3):
            plus=xyz.copy();minus=xyz.copy();plus[atom,axis]+=eps;minus[atom,axis]-=eps
            e=lambda x:sum(w.torsion.legacy.external_terms(x,[6,6],w.torsion.legacy_parameters(off))[0].values())
            assert grad[atom,axis]==pytest.approx((e(plus)-e(minus))/(2*eps),abs=1e-7)


def test_diagnostic_distinguishes_fixed_atom_and_pocket_pair():
    mol=Chem.AddHs(Chem.MolFromSmiles('CCO'));assert AllChem.EmbedMolecule(mol,randomSeed=71)==0
    xyz=mol.GetConformer().GetPositions()
    params={'geometry':{'hard':{'fixed_indices':[0]},'dofs':[]},
            'pocket':{'atoms':[{'element':'O','xyz':(xyz[0]+[.1,0,0]).tolist(),'key':['A','1','O']}]}}
    diagnostic=a.pair_diagnostics(mol,params)
    assert diagnostic['ligand_atom_fixed'] and diagnostic['ligand_atom_index']==0
    assert diagnostic['distance_A']==pytest.approx(.1)
    assert diagnostic['torsions_with_atom_in_moving_group']==[]


def test_frozen_gate_has_scientific_and_cost_obligations():
    p=a.protocol();gate=p['prespecified_candidate_gate']
    assert len(p['cells'])==4
    assert gate['minimum_rescues_among_historical_17']==3
    assert gate['maximum_regressions_among_previously_passing_unique_controls']==0
    assert gate['maximum_solver_cpu_ratio_wide_on_over_narrow_on']==2
    assert p['r4b']=='paused'


def check_bridge_fixture(valid=True):
    from etalon.research.result_integrity import freeze_check_contract
    contract=freeze_check_contract({'modules':[{'name':'Synthetic','chosen_binary_test_output':['geometry','loaded']}]},
        version='fixture',implementation={'explicit_synthetic_fixture':{}})
    pin={'sha256':'a'*64,'bytes':1}
    raw={'schema':'etalon-pose-verification/1','protocol':'fixed-receptor-frame-heavy-symmetry/1',
        'threshold_A':2.,'posebusters_version':'fixture','requested_slots':1,
        'inputs':{'predicted':pin,'reference':pin,'receptor':pin},
        'top1_success':valid,'any_success':valid,
        'rows':[{'pose_index':0,'request_id':'fixture--narrow_off','checks':{'geometry':valid,'loaded':True},
                 'physical_valid':valid,'rmsd_A':.5,'success':valid,'error':None}]}
    kwargs={'check_contract':contract,'pool':pin,'reference':pin,'receptor':pin,
        'evaluation_request':{'freeze':pin},'expected_request_ids':['fixture--narrow_off']}
    return raw,kwargs


@pytest.mark.parametrize('valid',[True,False])
def test_check_bridge_preserves_complete_measured_pass_or_rejection(valid):
    raw,kwargs=check_bridge_fixture(valid)
    assert a.audit_pose_verification(raw,**kwargs)['status']=='complete'
    assert raw['rows'][0]['physical_valid'] is valid


@pytest.mark.parametrize('fault',['single_true_check','nonboolean','wrong_pool','wrong_rmsd','wrong_success','missing_row'])
def test_check_bridge_rejects_incomplete_raw_observations_even_when_summary_claims_complete(fault):
    raw,kwargs=check_bridge_fixture()
    raw['required_checks_complete']=True
    if fault=='single_true_check':raw['rows'][0]['checks']={'geometry':True}
    elif fault=='nonboolean':raw['rows'][0]['checks']['loaded']=1
    elif fault=='wrong_pool':raw['inputs']['predicted']={'sha256':'b'*64,'bytes':1}
    elif fault=='wrong_rmsd':raw['rows'][0]['rmsd_A']=None
    elif fault=='wrong_success':raw['rows'][0]['success']=False
    elif fault=='missing_row':raw['rows']=[]
    audit=a.audit_pose_verification(raw,**kwargs)
    assert audit['status']=='unknown' and audit['coverage_complete'] is None


def test_future_evaluate_checks_cached_raw_fields_without_reexecuting_verifier(tmp_path,monkeypatch):
    raw,kwargs=check_bridge_fixture();raw['rows'][0]['checks']={'geometry':True}
    root=tmp_path;run=root/'evaluation/S--narrow_off';run.mkdir(parents=True)
    condition={'condition_id':'fixture','system_id':'S'}
    rid='fixture--narrow_off';native=root/'executions'/rid;native.mkdir(parents=True)
    w.write(native/'receipt.json',{'status':'completed','request_id':rid})
    (run/'poses.sdf').write_text('explicit synthetic pool; no chemistry invoked')
    (root/'reference.sdf').write_text('fixture ref');(root/'receptor.pdb').write_text('fixture receptor')
    raw['inputs']={k:w.pin(p) for k,p in [('predicted',run/'poses.sdf'),('reference',root/'reference.sdf'),('receptor',root/'receptor.pdb')]}
    w.write(root/'freeze.json',{'check_contract':kwargs['check_contract']})
    w.write(run/'verification.json',raw)
    def completed(folder,request):return {**request,'status':'completed'}
    monkeypatch.setattr(w,'completed_receipt',completed)
    def forbidden(*args):raise AssertionError('cached observation audit must not rerun native/PoseBusters')
    monkeypatch.setattr(a,'verifier',forbidden)
    with pytest.raises(ValueError,match='check contract incomplete'):
        a.evaluate(root,{'conditions':[condition]}, {'S':{'reference':'reference.sdf','receptor_pdb':'receptor.pdb'}})


def test_future_driver_refuses_legacy_freeze_without_complete_check_contract(tmp_path,monkeypatch):
    _,kwargs=check_bridge_fixture()
    monkeypatch.setattr(a,'freeze_posebusters_dock',lambda:kwargs['check_contract'])
    with pytest.raises(ValueError,match='old runs require separate retrospective audit'):
        a.verify_freeze(tmp_path,{'schema':'snd1-controlled-ablation-freeze/1'})


@pytest.mark.parametrize('valid',[True,False])
def test_future_evaluate_accepts_complete_cached_measurements_without_PoseBusters(tmp_path,monkeypatch,valid):
    raw,kwargs=check_bridge_fixture(valid)
    root=tmp_path;run=root/'evaluation/S--narrow_off';run.mkdir(parents=True)
    condition={'condition_id':'fixture','system_id':'S'};rid='fixture--narrow_off'
    native=root/'executions'/rid;native.mkdir(parents=True)
    w.write(native/'receipt.json',{'status':'completed','request_id':rid})
    for p in (run/'poses.sdf',root/'reference.sdf',root/'receptor.pdb'):
        p.write_text('explicit synthetic identity fixture; chemistry is not executed')
    raw['inputs']={k:w.pin(p) for k,p in [('predicted',run/'poses.sdf'),('reference',root/'reference.sdf'),('receptor',root/'receptor.pdb')]}
    w.write(root/'freeze.json',{'check_contract':kwargs['check_contract']});w.write(run/'verification.json',raw)
    monkeypatch.setattr(w,'completed_receipt',lambda folder,request:{**request,'status':'completed'})
    monkeypatch.setattr(a,'CELLS',{'narrow_off':a.CELLS['narrow_off']})
    def forbidden(*args):raise AssertionError('must not execute PoseBusters for cached measurements')
    monkeypatch.setattr(a,'verifier',forbidden)
    rows=a.evaluate(root,{'conditions':[condition]},{'S':{'reference':'reference.sdf','receptor_pdb':'receptor.pdb'}})
    assert len(rows)==1 and rows[0]['physical_valid'] is valid and rows[0]['success'] is valid
