"""Panel provenance/refusal tests. All chemistry and scores in this file are fixtures."""
from dataclasses import replace
import pytest
from etalon.active.store import StateError
from etalon.research import Artifact, Coupling, Experiment, Observation
from etalon.research.panel import (PanelSpec, freeze_panel, record_cell, summarize_panel,
    choose_development_policy, propose_joint_revision, preparation_contrast, freeze_selection, execute_frozen_policies)
from test_research_journal import setup


def spec(role='development'):
    return PanelSpec(role, role, {'a': {'smiles':'C','chemical_state_id':'state-a'},
                                 'b': {'smiles':'CC','chemical_state_id':'state-b'}},
        {'P0': {'fixed':True}, 'P1': {'fixed':False}},
        {e: {'kind':'docking_score','unit':'kcal/mol','direction':'min',
             'method_id':e,'source_identity':'fixture'} for e in ('E0','E1')},
        2,'receptor','frame',{'ranking':'independent experimental pair order; held out'})


def fill(journal, panel, artifact, *, failed=False):
    for c in ('a','b'):
        for p in ('P0','P1'):
            for e in ('E0','E1'):
                slots=[]
                for slot in (1,2):
                    ex=Experiment(panel.task_id,'reanalysis',{'chemical_state_id':'state-'+c,
                        'receptor_state_id':'receptor','coordinate_frame_id':'frame','slot':str(slot)},
                        (artifact.id,),e,c+p+e+str(slot),('energy',),Coupling())
                    journal.record(ex)
                    obs=Observation(ex.id,'failed' if failed else 'measured',
                        {} if failed else {'energy':-slot-(c=='b')},
                        {} if failed else {'energy':'kcal/mol'},(artifact.id,), 'fixture failure' if failed else '')
                    journal.record(obs)
                    slots.append({'slot':slot,'observation_id':obs.id,'readout':'energy',
                                  'pose_id':c+p+str(slot),'mapping_id':'fixture-map'})
                record_cell(journal,panel.id,candidate=c,preparation=p,evaluation=e,
                            slots=slots,execution_ids=())


def test_missing_and_failed_cells_never_become_good_scores(tmp_path):
    _,j,a=setup(tmp_path); panel=spec();freeze_panel(j,panel)
    assert len(summarize_panel(j,panel.id)['missing_cells'])==8
    fill(j,panel,a,failed=True)
    summary=summarize_panel(j,panel.id)
    assert not summary['missing_cells']
    assert all(p['selected_candidate'] is None for p in summary['policies'].values())
    assert all(c['failed_or_missing']==2 and c['score'] is None for c in summary['cells'].values())


def test_nonempty_revision_freeze_then_execute_rank_without_labels(tmp_path):
    _,j,a=setup(tmp_path); dev=spec(); target=spec('validation')
    freeze_panel(j,dev);freeze_panel(j,target);fill(j,dev,a)
    selection=choose_development_policy(j,dev.id,losses={'P0/E0':2,'P0/E1':1,'P1/E0':1,'P1/E1':0},
                                        evidence=(a.id,),loss_definition={'kind':'fixture independent loss'})
    config={'design':{'pose_admission':'fixed'},'preparation':{'family':'fixed'},'evaluation':{'method':'E0'}}
    contrast=preparation_contrast(j,dev.id,candidate_order=('a','b'),evaluation='E0')
    revision=propose_joint_revision(j,task_id=dev.task_id,configuration=config,
                updated_fields={'design':{'pose_admission':'transform'},'preparation':{'family':'finite'},
                                'evaluation':{'method':'E1'}},evidence=(a.id,),contrast_ids=(contrast,))
    assert revision['configuration']['design']['pose_admission']=='transform'
    frozen=freeze_selection(j,development_panel_id=dev.id,selection=selection,
                            revision_receipt=revision,validation_panel_id=target.id)
    fill(j,target,a)
    result=execute_frozen_policies(j,frozen['freeze_sha256'])
    assert result['predictions']['selected_workflow']['selected_candidate']=='b'
    assert result['labels_accessed'] is False and result['predictions']['selected_workflow']['policy']=='P1/E1'
    with pytest.raises(StateError,match='precede'):
        freeze_selection(j,development_panel_id=dev.id,selection=selection,
                         revision_receipt=revision,validation_panel_id=target.id)
    with pytest.raises(StateError,match='cannot select'):
        choose_development_policy(j,target.id,losses=selection['losses'],evidence=(a.id,),loss_definition={'x':1})


def test_unavailable_loss_abstains_and_independent_evidence_cannot_train(tmp_path):
    _,j,a=setup(tmp_path);panel=spec();freeze_panel(j,panel);fill(j,panel,a)
    losses={p:None for p in summarize_panel(j,panel.id)['policies']}
    result=choose_development_policy(j,panel.id,losses=losses,evidence=(a.id,),loss_definition={'x':1})
    assert result['selected_workflow']=='P0/E0' and result['status'].startswith('abstain')
    held=replace(a,role='independent_validation');j.record(held)
    with pytest.raises(StateError,match='holdout'):
        choose_development_policy(j,panel.id,losses=losses,evidence=(held.id,),loss_definition={'x':1})


def test_requested_target_identity_survives_all_missing_poses_and_single_candidate_failure(tmp_path):
    _,j,a=setup(tmp_path);panel=spec();freeze_panel(j,panel)
    fill(j,panel,a,failed=True)
    # Existing immutable cell cannot be rewritten; fresh panel tests missing-pose binding.
    panel=replace(panel,task_id='missing-target');freeze_panel(j,panel)
    for candidate in ('a','b'):
        slots=[]
        for slot in (1,2):
            ex=Experiment(panel.task_id,'reanalysis',{'chemical_state_id':'state-'+candidate,
                'receptor_state_id':'receptor','coordinate_frame_id':'frame'},(a.id,),
                'E0',candidate+str(slot),('energy',),Coupling());j.record(ex)
            obs=Observation(ex.id,'missing' if candidate=='a' else 'measured',
                {} if candidate=='a' else {'energy':-2},{} if candidate=='a' else {'energy':'kcal/mol'},
                (a.id,),'target not produced' if candidate=='a' else '');j.record(obs)
            slots.append({'slot':slot,'observation_id':obs.id,'readout':'energy',
                          'mapping_id':'target-map','pose_id':None if candidate=='a' else 'real-fixture-pose'})
        record_cell(j,panel.id,candidate=candidate,preparation='P0',evaluation='E0',slots=slots,execution_ids=())
    summary=summarize_panel(j,panel.id)
    assert summary['cells']['a/P0/E0']['failed_or_missing']==2
    assert summary['policies']['P0/E0']['selected_candidate']=='b'
    assert summary['policies']['P0/E0']['coverage_complete'] is False


def test_count_max_and_score_min_are_separate_and_ties_use_frozen_tolerance(tmp_path):
    _,j,a=setup(tmp_path);base=spec()
    panel=replace(base,evaluations={**base.evaluations,'E0':{'kind':'polar_pair_count','unit':'heavy_atom_pair_count',
        'direction':'max','method_id':'E0','source_identity':'fixture'}});freeze_panel(j,panel)
    for candidate,values in [('a',(0,2)),('b',(1,2.0000005))]:
        slots=[]
        for slot,value in enumerate(values,1):
            ex=Experiment(panel.task_id,'reanalysis',{'chemical_state_id':'state-'+candidate,'receptor_state_id':'receptor',
                'coordinate_frame_id':'frame'},(a.id,),'E0',candidate+str(slot),('count',),Coupling());j.record(ex)
            obs=Observation(ex.id,'measured',{'count':value},{'count':'heavy_atom_pair_count'},(a.id,));j.record(obs)
            slots.append({'slot':slot,'observation_id':obs.id,'readout':'count','pose_id':candidate+str(slot),'mapping_id':'map'})
        record_cell(j,panel.id,candidate=candidate,preparation='P0',evaluation='E0',slots=slots,execution_ids=())
    summary=summarize_panel(j,panel.id)
    assert summary['cells']['a/P0/E0']['score']==2
    assert summary['policies']['P0/E0']['top_tie'] and summary['policies']['P0/E0']['selected_candidate'] is None
