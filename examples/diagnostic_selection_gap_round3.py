"""Next diagnostic on explicitly released feedback: coverage versus selected-pose error.

This is seen-data reanalysis, not another independent validation or producer run.
The original independent artifact role is preserved; a new derived diagnostic
artifact explicitly records the exposure transition and its parent.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
from etalon.active import CampaignStore
from etalon.active.schema import canonical,digest
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Artifact,Coupling,Experiment,Observation,ResearchJournal


def released_groups(independent):
    results={}
    for group,values in independent['auxiliary_structure']['groups'].items():
        selected=values['proxy_selected_pose']['e1']
        worst=min(6.,max(m['heavy_rmsd_angstrom'] for m in selected['modes']))
        coverage=values['J_structure_A']
        if worst+1e-9<coverage:raise ValueError('selected pose cannot outperform same-mode oracle coverage')
        results[group]={'coverage_worst_mode_A':coverage,'selected_worst_mode_A':worst,
                        'single_pose_vs_ensemble_coverage_gap_A':max(0.,worst-coverage),
                        'selected_best_mode_A':selected['best_mode_rmsd_A']}
    return results


def analyze_released(result_path:Path,release_path:Path,archive_path:Path,output:Path):
    """Standalone actual evaluator-side audit, without rewriting a transported journal."""
    wall=time.perf_counter();cpu=time.process_time()
    result=json.loads(result_path.read_text());release=json.loads(release_path.read_text())
    if file_identity(result_path)['sha256']!=release['result_sha256'] or result['predictions_sha256']!=release['predictions_sha256']:
        raise ValueError('released result identity mismatch')
    report={'schema':'etalon-released-selection-gap-audit/2','groups':released_groups(result),
            'decision_sha256':release['decision_sha256'],'predictions_sha256':release['predictions_sha256'],
            'parent_H_archive':{'path':str(archive_path.resolve()),**file_identity(archive_path)},
            'result':{'path':str(result_path.resolve()),**file_identity(result_path)},
            'release':{'path':str(release_path.resolve()),**file_identity(release_path)},
            'model_challenge':'coverage gain does not establish score-selected pose gain',
            'metric_scope':'single-pose worst-mode loss minus ensemble mode coverage; different objectives, not ranking regret',
            'ranking_regret':None,
            'correction':'v1 selection_regret_A label deprecated; numeric difference retained under explicit different-objective name',
            'research_state':'additional_tasks_required','new_producer_executions':0,
            'phi_learned':False,'expected_gain':None,'exposure':release['future_scope'],
            'scope':'seen-feedback reanalysis addendum; parent archive and original scientific ledger unchanged',
            'python':__import__('sys').executable,'source':{'path':str(Path(__file__).resolve()),**file_identity(__file__)},
            'local_overhead':{'wall_second':time.perf_counter()-wall,'cpu_second':time.process_time()-cpu}}
    with output.open('x',encoding='utf-8') as f:f.write(canonical(report)+'\n')
    return report


def analyze(database:Path,feedback:Path,output:Path):
    wall=time.perf_counter();cpu=time.process_time()
    if output.exists():raise FileExistsError(output)
    report=json.loads(feedback.read_text());validation=report['validation']
    journal=ResearchJournal(CampaignStore(database));balance=journal.store.balance()
    recorded=journal.get(validation['id'])
    if recorded!={k:v for k,v in validation.items() if k!='id'}:raise ValueError('feedback validation differs from journal')
    source_id=recorded['evidence'][0];source=journal.get(source_id)
    with journal.store.connection() as db:journal._artifacts(db,(source_id,))
    independent=json.loads(Path(source['path']).read_text())
    groups=independent['auxiliary_structure']['groups']
    diagnostic=Artifact(source['path'],source['sha256'],source['bytes'],source['source_kind'],'diagnostic',
        {**source['bindings'],'exposure':'explicitly_released_seen_feedback',
         'historical_role':'independent_validation','release_record':recorded['release_evidence']},(source_id,))
    journal.record(diagnostic)
    observations=[];results={}
    for group,values in groups.items():
        selected=values['proxy_selected_pose']['e1']
        selected_worst=min(6.,max(m['heavy_rmsd_angstrom'] for m in selected['modes']))
        coverage=values['J_structure_A']
        if selected_worst+1e-9<coverage:raise ValueError('selected pose cannot outperform oracle coverage under same modes')
        facts={'coverage_worst_mode_A':coverage,'selected_worst_mode_A':selected_worst,
               'single_pose_vs_ensemble_coverage_gap_A':max(0.,selected_worst-coverage),
               'selected_best_mode_A':selected['best_mode_rmsd_A']}
        ex=Experiment(independent['task_id'],'reanalysis',{'group':group,'decision_sha256':recorded['decision_sha256'],
            'probe_id':'released-coverage-selection-gap','exposure':'seen_feedback'},(diagnostic.id,),
            'single-pose-versus-ensemble-coverage-gap/2',digest([validation['id'],group]),tuple(facts),
            Coupling((diagnostic.id,),(),(),('same frozen pose pool and released structural oracle; correlated descriptive errors',)))
        journal.record(ex)
        obs=Observation(ex.id,'measured',facts,{k:'angstrom' for k in facts},(diagnostic.id,));journal.record(obs)
        observations.append(obs.id);results[group]={'values':facts,'observation_id':obs.id}
    challenge={'schema':'etalon-seen-feedback-diagnostic/2','validation_id':validation['id'],
        'decision_sha256':recorded['decision_sha256'],'probe_id':'released-coverage-selection-gap',
        'observation_ids':observations,'groups':results,
        'model_challenge':'coverage improvement does not imply score-selected pose improvement',
        'metric_scope':'different single-pose and ensemble objectives; not same-loss ranking regret',
        'ranking_regret':None,
        'hypotheses':['score_ranking_mismatch','finite_pose_pool','receptor_or_solvent_omission','chemical_state_uncertainty'],
        'mechanism_posterior':None,'expected_gain':None,'phi_learned':False,
        'research_state':'additional_tasks_required','promotion':'none','new_producer_executions':0,
        'exposure':'this task and 3HT6/3HT9 are seen feedback; never reused as unseen validation',
        'next_q':{'question':'Does score ranking remain wrong on new independent task groups at a fixed pose pool?',
                  'required_data':['new held-out task groups','paired policy/query losses','actual query costs'],
                  'possible_controls':['fixed pose pool with alternative scorer','reviewed receptor/state/solvent variants'],
                  'capability_status':'alternative scorers/state variants not registered in this run',
                  'wall_second_quote':None,'dispatch':'not_executable_missing_capability_and_quote'},
        'budget':balance}
    with journal.store.connection(write=True) as db:journal._put(db,'diagnosticfeedback-'+digest(challenge),challenge)
    assert journal.store.balance()==balance
    challenge['local_reanalysis_overhead']={'wall_second':time.perf_counter()-wall,'cpu_second':time.process_time()-cpu,
        'scope':'postprocessing measured separately; no new producer execution or repeated scientific charge'}
    with output.open('x',encoding='utf-8') as f:f.write(canonical(challenge)+'\n')
    return challenge


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('database','feedback','released-result','release','archive'):p.add_argument('--'+name,type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.released_result:
        if not a.release or not a.archive or a.database or a.feedback:p.error('released audit requires release+archive, no journal arguments')
        r=analyze_released(a.released_result,a.release,a.archive,a.output)
    else:
        if not a.database or not a.feedback:p.error('journal reanalysis requires database+feedback')
        r=analyze(a.database,a.feedback,a.output)
    print(json.dumps({'groups':len(r['groups']),'state':r['research_state'],'new_producer_executions':0}))
