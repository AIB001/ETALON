"""Fixture-only archive counterexamples; no solver, science or model execution.

All inputs live under pytest temporary directories and explicitly identify
themselves as fixtures. These tests establish software binding/replay behavior,
never a SND1 scientific result.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'examples'))
import snd1_integrity_research as w
from etalon.research import Artifact
from etalon.active.store import StateError
from etalon.research.components import record_json
from etalon.research.continuation import record_content
from etalon.research.evidence_refs import file_identity
from etalon.research.publication import resume_publication


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf-8')


@pytest.fixture
def archive_fixture(tmp_path,monkeypatch):
    """A synthetic34-condition archive with17 historical failures, no science."""
    monkeypatch.setattr(w,'TASK','fixture-snd1-integrity')
    monkeypatch.setattr(w,'SID','fixture-snd1-integrity-source')
    monkeypatch.setattr(w,'QUESTION','Fixture archive identity and replay obligations')
    monkeypatch.setattr(w,'SCOPE','Fixture only; software behavior, no scientific claim')
    original=w.Archive

    class FixtureArchive(original):
        def obj(self,value,kind='evidence'):
            return self.ref(record_json(self.journal,value,self.content,source_kind='fixture',
                bindings={'task_id':w.TASK,'kind':kind,'fixture':True}))

        def file(self,path,kind='original'):
            path=Path(path).absolute()
            return self.ref(self.journal.record(Artifact(str(path),**file_identity(path),
                source_kind='fixture',role='diagnostic',bindings={'task_id':w.TASK,'kind':kind,'fixture':True})))

        def save(self,value):
            return self.ref(record_content(self.journal,value,self.content,source_id=w.SID,source_kind='fixture'))

    monkeypatch.setattr(w,'Archive',FixtureArchive)
    inp=tmp_path/'fixture-inputs';root=tmp_path/'fixture-archive';inp.mkdir()
    protocol=inp/'protocol.json';selection=inp/'selection.json';freeze=inp/'freeze.json'
    save(protocol,{'fixture':True,'schema':'snd1-torsion-range-pocket-protocol/1',
        'candidate_id':'snd1-wide-torsion-pocket-v1','baseline':'narrow_on','candidate':'wide_on',
        'cells':{'narrow_on':{'half_width_degrees':[10,10,10,20],'pocket_terms':True},
                 'wide_on':{'half_width_degrees':[30,30,30,60],'pocket_terms':True}},
        'prespecified_candidate_gate':{'minimum_rescues_among_historical_17':3,
            'maximum_regressions_among_previously_passing_unique_controls':0,
            'maximum_new_hard_violations':0,'maximum_solver_cpu_ratio_wide_on_over_narrow_on':2.,
            'scope':'Fixture seen matched panel only'},
        'reproduction_tolerance':{'maximum_atom_delta_A':.001,'maximum_objective_delta_kcal_mol':.0001},
        'falsifiers':['Fewer than3/17 rescues','Any control regression or new hard violation',
                      'Solver CPU ratio greater than2','Narrow/on does not reproduce historical T0 within frozen numerical tolerance']})
    conditions=[{'condition_id':f'fixture-case-{i:02d}','seed':i+1,'coordinate_sha256':w.digest({'fixture':i}),
        'historical_stratum':'common_failure' if i<17 else 'common_success' if i<24 else 'rescue'} for i in range(34)]
    save(selection,{'fixture':True,'conditions':conditions,'unique_conditions':34,
        'unique_coordinate_hashes':34,'historical_common_failure_count':17})
    save(freeze,{'fixture':True,'schema':'snd1-controlled-ablation-freeze/1',
        'candidate_id':'snd1-wide-torsion-pocket-v1','frozen_before_new_execution':True,
        'expected_execution_calls':136,'documents':{'protocol.json':file_identity(protocol),'selection.json':file_identity(selection)}})
    prepared=w.prepare(root,protocol)
    comparison=inp/'summary.json';receipt=inp/'run-receipt.json';recovery=inp/'recovery.json'
    raw_metrics=inp/'evaluation-metrics.json';panel=inp/'panel-results.json';metrics=inp/'evaluation-metrics-for-rsi.json'
    save(comparison,{'fixture':True,'schema':'snd1-controlled-summary/1','candidate_id':'snd1-wide-torsion-pocket-v1','unique_conditions':34,
        'execution_calls':136,'completed':136,'failure_stratum_success':{'narrow_off':0,'narrow_on':0,'wide_off':17,'wide_on':17},
        'success_counts':{'narrow_off':17,'narrow_on':17,'wide_off':34,'wide_on':34},
        'paired_effects':[{'effect':'pocket_at_wide','rescues':0,'regressions':0}],
        'gate':{'narrow_on_reproduction':True,'rescues_of17':17,'control_regressions':0,
                'hard_violations_or_unavailable':0,'solver_cpu_ratio':1.5,'all_completed_and_evaluated':True,'passed':True},
        'cost':{'fixture_cpu_second':1.}})
    save(raw_metrics,{'fixture':True,'matched_cases':34,'failure_cases':17})
    save(panel,{'fixture':True,'executions':136})
    save(receipt,{'fixture':True,'schema':'snd1-controlled-run-receipt/1','status':'completed',
        'freeze':file_identity(freeze),'artifacts':{p.name:file_identity(p) for p in [comparison,raw_metrics,panel]}})
    save(recovery,{'fixture':True,'state':'published','automatic_retry':False,'native_reexecutions':0})
    payload={'fixture':True,'schema':'snd1-controlled-evaluation-metrics-for-rsi/1',
        'candidate_id':'snd1-wide-torsion-pocket-v1','protocol_sha256':file_identity(protocol)['sha256'],
        'matched_cases':34,'failure_cases':17,'recovered_failures':17,'control_regressions':0,
        'hard_invariant_failures':0,'candidate_cpu_ratio':1.5,'denominator_complete':True,
        'original_pins_verified':True,'required_checks_complete':True,'baseline_reproduction_pass':True,
        'conclusion':'Fixture comparison only','freeze':file_identity(freeze),
        'source_artifacts':{str(p.absolute()):file_identity(p) for p in [raw_metrics,comparison,freeze,receipt,panel]}}
    save(metrics,payload)
    return {'root':root,'input':inp,'protocol':protocol,'selection':selection,'freeze':freeze,
        'comparison':comparison,'receipt':receipt,'recovery':recovery,'metrics':metrics,
        'raw_metrics':raw_metrics,'panel':panel,'payload':payload,'prepared':prepared}


def complete(fixture,**kw):
    return w.complete(fixture['root'],fixture['metrics'],fixture['receipt'],fixture['recovery'],
        comparison_path=fixture['comparison'],**kw)


def refresh_metrics_dependencies(fixture):
    value=w.read(fixture['metrics'])
    value['source_artifacts']={p:file_identity(p) for p in value['source_artifacts']}
    save(fixture['metrics'],value)


def test_fixture_prepare_replay_has_no_new_proposal_events(archive_fixture):
    f=archive_fixture;a=w.Archive(f['root']);before=a.store.events()
    assert w.prepare(f['root'],f['protocol'])==f['prepared']
    assert a.store.events()==before and a.store.actions()==[]


@pytest.mark.parametrize('target',['protocol','selection','freeze'])
def test_fixture_prepared_original_byte_drift_is_rejected(archive_fixture,target):
    f=archive_fixture;f[target].write_text(f[target].read_text('utf-8')+'\n',encoding='utf-8')
    with pytest.raises((ValueError,StateError)):w.prepare(f['root'],f['protocol'])
    with pytest.raises((ValueError,StateError)):complete(f,outcome='adopt')
    assert not (f['root']/'completed.json').exists()


@pytest.mark.parametrize('field,value',[('matched_cases',33),('failure_cases',16),('matched_cases',None)])
def test_fixture_wrong_denominator_cannot_adopt(archive_fixture,field,value):
    f=archive_fixture;p=w.read(f['metrics']);p[field]=value;save(f['metrics'],p)
    with pytest.raises(ValueError,match='denominators'):complete(f,outcome='adopt')
    assert not (f['root']/'completed.json').exists()


def test_fixture_wrong_summary_dependency_is_rejected(archive_fixture):
    f=archive_fixture;p=w.read(f['metrics']);del p['source_artifacts'][str(f['comparison'].absolute())];save(f['metrics'],p)
    with pytest.raises(ValueError,match='dependency closure'):complete(f,outcome='adopt')


def test_fixture_dependency_byte_drift_is_rejected(archive_fixture):
    f=archive_fixture;save(f['raw_metrics'],{'fixture':True,'changed':True})
    with pytest.raises(ValueError,match='original identity changed'):complete(f,outcome='adopt')


@pytest.mark.parametrize('missing',['denominator_complete','original_pins_verified','required_checks_complete','baseline_reproduction_pass'])
def test_fixture_missing_required_check_never_adopts(archive_fixture,missing):
    f=archive_fixture;p=w.read(f['metrics']);p.pop(missing);save(f['metrics'],p)
    with pytest.raises(ValueError,match='cannot adopt'):complete(f,outcome='adopt')
    assert not (f['root']/'completed.json').exists()


@pytest.mark.parametrize('value',[False,None])
def test_fixture_false_or_unknown_reproduction_never_adopts(archive_fixture,value):
    f=archive_fixture;p=w.read(f['metrics']);p['baseline_reproduction_pass']=value;save(f['metrics'],p)
    with pytest.raises(ValueError,match='cannot adopt'):complete(f,outcome='adopt')


@pytest.mark.parametrize('field,value',[('recovered_failures',2),('control_regressions',1),('hard_invariant_failures',1),('candidate_cpu_ratio',2.1)])
def test_fixture_quality_or_cost_failure_never_adopts(archive_fixture,field,value):
    f=archive_fixture;p=w.read(f['metrics']);p[field]=value;save(f['metrics'],p)
    with pytest.raises(ValueError,match='cannot adopt'):complete(f,outcome='adopt')


def test_fixture_completion_publication_and_opportunity_replay_do_not_launch_or_propose(archive_fixture):
    f=archive_fixture;first=complete(f,outcome='retain');assert first['report']['status']=='criteria_passed'
    a=w.Archive(f['root']);before=a.store.events()
    assert complete(f,outcome='retain')==first and a.store.events()==before
    p=w.publish(f['root']);first_published=resume_publication(p['intent_path'])
    published_pin=file_identity(f['root']/'published-entry.json')
    assert w.publish(f['root'])==p
    again=resume_publication(p['intent_path'])
    assert again['status']==first_published['status']=='published'
    assert file_identity(f['root']/'published-entry.json')==published_pin
    result=w.audit(f['root'])
    assert result['new_journal_events']==0 and not result['scientific_execution_by_archive']
    assert a.store.events()==before and a.store.actions()==[]


def test_fixture_scoped_adoption_retains_counterevidence_and_changes_no_default(archive_fixture):
    f=archive_fixture;result=complete(f,outcome='adopt');a=w.Archive(f['root'])
    assert result['report']['status']=='criteria_passed'
    assert result['resolution']['selected_method_ref']==f['prepared']['candidate_ref']
    assert result['resolution']['default_changed'] is False
    artifact=Artifact(**a.journal.get(result['counter_ref']['record_id']))
    counter=w.read(a.journal.artifact_path(artifact))
    assert counter['success_counts']=={'narrow_off':17,'narrow_on':17,'wide_off':34,'wide_on':34}
    assert counter['paired_effects'][0]['rescues']==0
    assert 'not established' in counter['interpretation']
    assert a.store.actions()==[]


def test_fixture_another_run_freeze_cannot_be_smuggled_through_self_consistent_dependencies(archive_fixture):
    """Regression: mutable result declarations cannot replace the prepared freeze."""
    f=archive_fixture;r=w.read(f['receipt']);r['freeze']={'sha256':'f'*64,'bytes':1};save(f['receipt'],r)
    refresh_metrics_dependencies(f)
    with pytest.raises(ValueError):complete(f,outcome='adopt')


def test_fixture_receipt_must_authenticate_the_selected_raw_comparison(archive_fixture):
    f=archive_fixture;r=w.read(f['receipt']);r['artifacts']['summary.json']={'sha256':'f'*64,'bytes':1};save(f['receipt'],r)
    refresh_metrics_dependencies(f)
    with pytest.raises(ValueError):complete(f,outcome='adopt')


def test_fixture_completed_replay_rechecks_transitive_originals(archive_fixture):
    f=archive_fixture;complete(f,outcome='retain')
    save(f['raw_metrics'],{'fixture':True,'changed_after_completion':True})
    with pytest.raises(ValueError):complete(f,outcome='retain')


def test_fixture_mutable_saved_thresholds_cannot_relax_the_frozen_protocol(archive_fixture):
    f=archive_fixture;p=w.read(f['metrics']);p['recovered_failures']=0;save(f['metrics'],p)
    state=w.read(f['root']/'prepared.json')
    state['rules'][0]['threshold']=0;save(f['root']/'prepared.json',state)
    with pytest.raises(ValueError):complete(f,outcome='adopt')


@pytest.mark.parametrize('count',[18,3.5])
def test_fixture_recovered_count_must_be_integral_and_within_failure_denominator(archive_fixture,count):
    f=archive_fixture;p=w.read(f['metrics']);p['recovered_failures']=count;save(f['metrics'],p)
    with pytest.raises(ValueError):complete(f,outcome='adopt')


def test_fixture_derived_count_must_agree_with_authenticated_raw_comparison(archive_fixture):
    f=archive_fixture;p=w.read(f['metrics']);p['recovered_failures']=3;save(f['metrics'],p)
    # This is within17 and satisfies>=3, but the authenticated original records17.
    with pytest.raises(ValueError):complete(f,outcome='adopt')
