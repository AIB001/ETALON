"""Independent bounded evaluation of retained B and the fixed P-attempt comparator."""
import importlib.util
import json
from pathlib import Path
from etalon.active.schema import digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from .retained_baseline import paid_score
from .preparation import paid_preparation,preparation_cost_attribution
from .rows import _file_artifact
from .fixed_pool import subset_rank_outcome
from .support import loss_interval


def _prediction_binding(release):
    if release.get('predictions_sha256'):return release['predictions_sha256']
    values={e.get('sha256') for e in release.get('evidence',[]) if isinstance(e,dict)
            and e.get('kind')=='online_predictions_frozen_before_label_read'}
    return values.pop() if len(values)==1 else None


def evaluate_retained(journal,*,policy_path,predictions_path,diagnostics_path,label_release_path,
                      independent_release_path,oracle_path):
    policy=json.loads(Path(policy_path).read_text());release=json.loads(Path(independent_release_path).read_text())
    if (release.get('policy_sha256')!=policy['policy_sha256'] or release.get('split_sha256')!=policy['split_sha256']
            or _prediction_binding(release)!=file_identity(predictions_path)['sha256'] or not release.get('evidence')):
        raise StateError('independent release must bind retained policy, split and predictions before label read')
    if journal.get('baselinepolicy-'+policy['policy_sha256'])!=policy:raise StateError('retained policy differs from same H')
    predictions=json.loads(Path(predictions_path).read_text());diagnostics=json.loads(Path(diagnostics_path).read_text())
    run=predictions['run_id']
    if (journal.get('onlinepredictions-'+digest(run))!=predictions or
            journal.get('baselinediagnostics-'+digest(run))!=diagnostics):raise StateError('prediction/diagnostic H mismatch')
    if diagnostics['prediction_sha256']!=file_identity(predictions_path)['sha256']:
        raise StateError('P diagnostics precede or differ from prediction freeze')
    labels=json.loads(Path(label_release_path).read_text())
    tasks={t for t,e in policy['split']['tasks'].items() if e['role']=='test'}
    if (labels['role']!='test' or set(labels['tasks'])!=tasks or labels['input_freeze_sha256']!=policy['input_freeze_sha256']
            or labels['script_sha256']!=file_identity(oracle_path)['sha256']):
        raise StateError('test labels/oracle must match the original structural freeze and new groups')
    spec=importlib.util.spec_from_file_location('coordinator_r4b_oracle',oracle_path)
    oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    results={}
    for task in sorted(tasks):
        pred=predictions['tasks'][task];diag=diagnostics['tasks'][task]
        p0=paid_preparation(journal,pred['preparation_admission_id']);p1=paid_preparation(journal,diag['preparation_admission_id'])
        p0cost=preparation_cost_attribution(journal,pred['preparation_admission_id'])
        p1cost=preparation_cost_attribution(journal,diag['preparation_admission_id'])
        bcost=p0cost['wall_second'];bscores={c:None for c in pred['candidate_scores']}
        for aid in pred['score_admissions'].values():
            a=paid_score(journal,aid);bcost+=a['cost']['wall_second'];bscores.update({s['candidate_id']:s['score'] for s in a['slots']})
        if bscores!=pred['candidate_scores'] or abs(bcost-pred['attributed_path_cost'])>1e-9:
            raise StateError('frozen B score/path cost differs from real receipts')
        b=oracle.evaluate(bscores,labels['tasks'][task]);pcost=p1cost['wall_second'];pscores=None;outcomes={'q1':None,'q2':None}
        if p1['status']=='PREPARED':
            pscores={}
            if set(diag['score_admissions'])!={'r1','r2'}:raise StateError('ready P requires both real score admissions')
            for rid,aid in diag['score_admissions'].items():
                a=paid_score(journal,aid);pcost+=a['cost']['wall_second'];chunk={s['candidate_id']:s['score'] for s in a['slots']}
                pscores.update(chunk)
                outcomes['q1' if rid=='r1' else 'q2']=subset_rank_outcome(candidate_ids=tuple(chunk),
                    vina={c:bscores.get(c) for c in chunk},vinardo=chunk)['outcome']
            if pscores!=diag['raw_P_scores']:raise StateError('raw P scores differ from actual diagnostics')
            raw=oracle.evaluate(pscores,labels['tasks'][task]);effective=raw;executed='P'
        else:
            if diag['score_admissions'] or diag['raw_P_scores'] is not None or diag['score_cost'] is not None:
                raise StateError('unavailable P must not invent scores or measured zero scoring cost')
            raw={'loss':None,'status':p1['status'],'candidate_scores':None};effective=b;executed='B'
        interval=lambda value:loss_interval(value,[0,1],state='measured' if value is not None else 'unavailable_label')
        raw_interval=interval(raw['loss'])
        if p1['status']!='PREPARED':raw_interval['state']='not_applicable' if p1['status']=='NOT_APPLICABLE' else 'preparation_failed'
        results[task]={'group':policy['split']['tasks'][task]['group'],'B':b,'raw_P':raw,
            'raw_P_loss_interval':raw_interval,'P_preparation_status':p1['status'],'query_outcomes':outcomes,
            'query_observation_state':'measured_scores' if pscores is not None else 'not_observed',
            'fixed_attempt_P':{'effective_output':executed,'loss':effective['loss'],
                'attributed_common_start_cost':bcost+pcost,'P_prepared':p1['status']=='PREPARED',
                'P_scores_complete':pscores is not None and all(v is not None for v in pscores.values())},
            'preparation_cost_attribution':{'p0':p0cost,'p1':p1cost},
            'B_attributed_cost':bcost,'P_incremental_attributed_cost':pcost,
            'B_loss_interval':interval(b['loss']),'fixed_attempt_P_loss_interval':interval(effective['loss']),
            'B_objective_interval':{k:interval(b['loss'])[k]+.001*bcost for k in ('lower','upper')},
            'fixed_attempt_P_objective_interval':{k:interval(effective['loss'])[k]+.001*(bcost+pcost) for k in ('lower','upper')},
            'cold_start_P_attempt_cost':pcost+p0cost['wall_second'] if pscores is not None else bcost+pcost,
            'cold_start_scope':'root-frozen dependency includes p0+p1; ready P can omit B scoring only in counterfactual attribution; matched B-start is primary',
            'no_extra_physical_preparation_savings_claimed':True}
    grouped={};interval_fields=('B_objective_interval','fixed_attempt_P_objective_interval',
        'B_loss_interval','fixed_attempt_P_loss_interval','raw_P_loss_interval')
    for group in sorted({r['group'] for r in results.values()}):
        rows=[r for r in results.values() if r['group']==group]
        grouped[group]={key:{bound:sum(r[key][bound] for r in rows)/len(rows) for bound in ('lower','upper')}
            for key in interval_fields}
    macro={key:{bound:sum(g[key][bound] for g in grouped.values())/len(grouped) for bound in ('lower','upper')}
        for key in interval_fields}
    cost_macro={field:sum(sum(r[field] for r in results.values() if r['group']==g)/
        sum(r['group']==g for r in results.values()) for g in grouped)/len(grouped)
        for field in ('B_attributed_cost','P_incremental_attributed_cost')}
    refs=[_file_artifact(journal,Path(path),role='independent_validation',bindings={'kind':kind,'policy_sha256':policy['policy_sha256']})
        for path,kind in [(label_release_path,'released_new_test_labels'),(independent_release_path,'independent_release'),
                          (predictions_path,'frozen_B_predictions'),(diagnostics_path,'post_freeze_P_diagnostics')]]
    refs.append(_file_artifact(journal,Path(oracle_path),bindings={'kind':'coordinator_original_oracle'}))
    result={'schema':'etalon-r4b-retained-evaluation/1','policy_sha256':policy['policy_sha256'],
        'prediction_sha256':file_identity(predictions_path)['sha256'],'test_results':results,'group_means':grouped,
        'group_equal_macro':macro,'evaluation_cost_weight':.001,'evidence':refs,
        'group_equal_costs':cost_macro,
        'phi_learning_not_established':True,'promotion':'none','complete_RSI':False,
        'strong_comparator_status':{k:'not_identifiable: only one applicable seen training family; no fitted P selector'
            for k in ('learned_phi','copy_best','myopic','nonmyopic')},
        'scope':'three new prepared-input families; fixed B and post-freeze fixed P-attempt, not learned transfer superiority',
        'preparation_scope':'all preparation physically precompiled; attributed path costs are correlated counterfactual accounting',
        'global_budget':journal.store.balance(),'new_producer_executions':0}
    with journal.store.connection(write=True) as db:journal._put(db,'baselineevaluation-'+digest(result),result)
    return result


def attach_retained_feedback(journal,*,evaluation_path,coordinator_audit_path,seen_gain_audit_path=None,
                             support_replay_path=None,support_replay_driver_path=None):
    evaluation=json.loads(Path(evaluation_path).read_text());audit=json.loads(Path(coordinator_audit_path).read_text())
    identifier='baselineevaluation-'+digest(evaluation)
    if journal.get(identifier)!=evaluation:raise StateError('evaluation differs from same H')
    if (audit.get('policy_sha256',audit.get('frozen_policy_sha256'))!=evaluation['policy_sha256'] or
            audit.get('prediction_sha256',audit.get('predictions_file_sha256'))!=evaluation['prediction_sha256']):
        raise StateError('coordinator scientific review must bind policy and original predictions')
    refs=[_file_artifact(journal,Path(p),role='independent_validation',bindings={'kind':'reviewed_r4b_evaluation_feedback'})
          for p in (evaluation_path,coordinator_audit_path)]
    seen=None
    if seen_gain_audit_path is not None:
        seen=json.loads(Path(seen_gain_audit_path).read_text())
        refs.append(_file_artifact(journal,Path(seen_gain_audit_path),role='independent_validation',
            bindings={'kind':'now_seen_query_gain_diagnostic','purpose':'feedback_only_not_fit_or_new_independent_validation'}))
    support=None
    if (support_replay_path is None)!=(support_replay_driver_path is None):
        raise StateError('support replay requires its exact pinned public driver')
    if support_replay_path is not None:
        support=json.loads(Path(support_replay_path).read_text())
        old_policy=journal.get('learnedpolicy-'+support['policy_sha256'])
        old_rows=journal.get('taskrows-'+support['rows_semantic_sha256'])
        driver_pins=[v for k,v in support['source_pins'].items() if k.replace('\\','/').endswith('/replay_support_round4b.py')]
        if (digest(old_policy['model'])!=support['model_sha256'] or old_rows['role']!='test'
                or support.get('original_row_role')!='test' or support.get('refitted') is not False
                or support.get('future_outcomes_passed_to_historical_controller') is not False
                or support['costs']['new_scientific_executions']!=0 or support['costs']['new_ledger_charges']!=0
                or driver_pins!=[file_identity(support_replay_driver_path)]):
            raise StateError('support replay must retain original model, roles, visibility and pinned driver')
        refs.extend(_file_artifact(journal,Path(path),role='independent_validation',bindings={
            'kind':kind,'purpose':'now_seen_model_support_challenge_only_not_fit_or_new_independent_validation'})
            for path,kind in [(support_replay_path,'old_TNKS2_support_replay'),
                              (support_replay_driver_path,'old_TNKS2_support_replay_driver')])
    body={'schema':'etalon-r4b-retained-feedback/1','policy_sha256':evaluation['policy_sha256'],
        'evaluation_id':identifier,'evidence':refs,'research_state':'diagnosis_required','promotion':'none','complete_RSI':False,
        'strategy':'retained_baseline_due_insufficient_intervention_support','phi_learning_not_established':True,
        'group_equal_macro':evaluation['group_equal_macro'],'strong_comparator_status':evaluation['strong_comparator_status'],
        'seen_outcome_gain_audit':seen,'seen_audit_scope':'old nine groups including former test are now seen; omission checks are not new independent validation',
        'model_challenges':['one applicable seen training family and no applicable calibration family',
            'NOT_APPLICABLE is an unavailable intervention, not evidence of scientific inferiority',
            'raw P loss remains unknown where unavailable; effective fallback uses real B',
            'His state sensitivity is not demonstrated correction; receptor geometry G remains unresolved',
            'preparation was precompiled, not dynamically acquired online'],
        'next_requirements':['new scientifically justified preparation/receptor catalogue with multiple independent development families',
            'then freeze a learned selector and assess net benefit on further independent families'],
        'future_use':'seen_evaluation_feedback_only; no direct diagnostic label laundering or unseen reuse',
        'new_producer_executions':0}
    if support is not None:
        body['seen_model_support_replay']=support
        body['support_replay_scope']='old model support challenge, distinct from query-outcome scientific-net-gain audit; now-seen feedback only'
    with journal.store.connection(write=True) as db:journal._put(db,'controllerfeedback-r4b-'+digest(body),body)
    return body
