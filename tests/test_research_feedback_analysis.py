"""Released-feedback reanalysis fixture: preserve original role and full archive."""
import importlib.util
import json
from pathlib import Path
from etalon.active.schema import digest
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Artifact
from etalon.research.validation import attach_decision_validation
from test_research_journal import setup


def example(name):
    path=Path(__file__).parents[1]/'examples'/name
    spec=importlib.util.spec_from_file_location(name.replace('.','_'),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_seen_feedback_next_query_and_archive_preserve_validation_and_budget(tmp_path):
    store,journal,_=setup(tmp_path)
    decision={'task_id':'dev','independent_validation_accessed':False,'decision':{'delta':{'fixture':'changed'}}}
    sha=digest(decision);decision['freeze_sha256']=sha
    with store.connection(write=True) as db:journal._put(db,'decision-'+sha,decision)
    data={'task_id':'previously-held-out','auxiliary_structure':{'groups':{'fixture':{
        'J_structure_A':1.,'proxy_selected_pose':{'e1':{'best_mode_rmsd_A':2.,
        'modes':[{'heavy_rmsd_angstrom':2.},{'heavy_rmsd_angstrom':3.}]}}}}}}
    path=tmp_path/'released.json';path.write_text(json.dumps(data))
    artifact=Artifact(str(path),**file_identity(path),source_kind='fixture',role='independent_validation',bindings={'decision_sha256':sha})
    journal.record(artifact)
    validation=attach_decision_validation(journal,decision_sha256=sha,evidence=(artifact.id,),
        release_evidence=artifact.id,protocol={'method':'fixture'},comparison=data)
    feedback=tmp_path/'feedback.json';feedback.write_text(json.dumps({'validation':validation}))
    balance=store.balance()
    report=example('diagnostic_selection_gap_round3.py').analyze(tmp_path/'campaign.sqlite',feedback,tmp_path/'gap.json')
    assert report['groups']['fixture']['values']['single_pose_vs_ensemble_coverage_gap_A']==2.
    assert report['ranking_regret'] is None
    assert journal.get(artifact.id)['role']=='independent_validation' and store.balance()==balance
    assert journal.get('decision-'+sha)==decision and report['phi_learned'] is False
    archive=example('archive_joint_feedback.py').archive(tmp_path/'campaign.sqlite',tmp_path/'archive')
    diagnostic=[a for a in archive['artifacts'] if a['parents']==[artifact.id]]
    assert len(diagnostic)==1 and diagnostic[0]['role']=='diagnostic'
    assert archive['research_records'] and archive['event_count']>0
