"""Append bounded R4 transfer feedback, keeping the independent prediction freeze intact."""
import json
from pathlib import Path
from etalon.active.schema import digest
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from .rows import verify_dataset,_file_artifact
from .transfer import unpack,evaluate_policies,probability_assessment


def attach_transfer_feedback(journal,policy,*,rows_path:Path,predictions_path:Path,
                             independent_release_path:Path,calibration_report_path:Path,seen_audit_path:Path,
                             assay_source_audit_path:Path|None=None,transfer_audit_path:Path|None=None):
    split,_,model=unpack(policy)
    release=json.loads(independent_release_path.read_text())
    prediction_hash=release.get('predictions_sha256')
    if prediction_hash is None:
        hashes={e.get('sha256') for e in release.get('evidence',[])
            if isinstance(e,dict) and e.get('kind')=='online_predictions_frozen_before_label_read'}
        if len(hashes)==1:prediction_hash=hashes.pop()
    if (release.get('policy_sha256')!=policy['policy_sha256'] or release.get('split_sha256')!=split.sha256
            or prediction_hash!=file_identity(predictions_path)['sha256']
            or not release.get('evidence')):
        raise StateError('test release must bind exact policy, split and frozen online prediction file')
    frozen=json.loads(predictions_path.read_text())
    if frozen['policy_sha256']!=policy['policy_sha256'] or frozen['labels_accessed'] is not False:
        raise StateError('prediction freeze does not match the tested policy')
    if journal.get('onlinepredictions-'+digest(frozen['run_id']))!=frozen:
        raise StateError('frozen prediction differs from immutable journal')
    calibration=json.loads(calibration_report_path.read_text())
    if calibration['final_policy_sha256']!=policy['policy_sha256']:
        raise StateError('calibration selection report belongs to another policy')
    if policy['source_kind']=='measured':
        if journal.get('learnedpolicy-'+policy['policy_sha256'])!=policy:
            raise StateError('feedback requires the original registered policy')
        training=journal.get('learnedpolicy-'+policy['training_policy_sha256'])
        unpack(training)
        if (training['model']!=policy['training_model'] or
                calibration['training_policy_sha256']!=training['policy_sha256']):
            raise StateError('training/calibration lineage mismatch')
    rows=json.loads(rows_path.read_text())
    dataset=verify_dataset(journal,rows,role='test',split_sha256=split.sha256)
    evaluation=evaluate_policies(policy,rows,role='test',release=release)
    probability=probability_assessment(policy,rows,role='test',release=release)
    for item in evaluation['results']:
        if item['mode']!='phi':continue
        prediction=frozen['tasks'][item['task_id']]
        assessment=dataset['scientific_assessment'][item['task_id']][item['decision']]
        if (prediction['decision']!=item['decision'] or prediction['candidate_scores']!=assessment['candidate_scores']
                or [r['probe_id'] for r in prediction['query_history']]!=item['queries']
                or abs(prediction['actual_path_cost']-item['actual_resource_cost'])>1e-9):
            raise StateError('offline phi differs from actual frozen online choices/costs')
    modes={}
    for mode in ('phi0','phi','copy_best','static','myopic','nonmyopic'):
        group_values=[r for r in evaluation['group_means'] if r['mode']==mode]
        modes[mode]={field:sum(r[field] for r in group_values)/len(group_values)
                     for field in ('scientific_loss','actual_resource_cost','objective')}
    common_weight=(policy.get('calibration_selection') or {}).get('selection_cost_weight',policy['cost_weight'])
    for metrics in modes.values():
        metrics['common_tradeoff_objective']=metrics['scientific_loss']+common_weight*metrics['actual_resource_cost']
    joint=[{'task_id':r['task_id'],'group':r['independence_group'],
        'joint_probability':sum(w.probability for w in model.worlds if w.outcomes==r['outcomes'])} for r in rows]
    joint_failure=any(r['joint_probability']==0 for r in joint)
    seen=json.loads(seen_audit_path.read_text())
    support_failure=any(r['support_failure'] for r in probability['predictions'])
    no_gain_over_copy_best=modes['phi']['scientific_loss']>=modes['copy_best']['scientific_loss']
    no_cost_gain_over_phi0=modes['phi']['common_tradeoff_objective']>=modes['phi0']['common_tradeoff_objective']
    refs=[_file_artifact(journal,rows_path,role='independent_validation',bindings={'role':'test','policy_sha256':policy['policy_sha256']}),
        _file_artifact(journal,predictions_path,bindings={'kind':'frozen_online_predictions'}),
        _file_artifact(journal,independent_release_path,role='independent_validation',bindings={'role':'test'}),
        _file_artifact(journal,calibration_report_path,bindings={'kind':'calibration_selection_not_independent_assessment'}),
        _file_artifact(journal,seen_audit_path,bindings={'kind':'released_seen_receptor_intervention_audit'})]
    source_correction=None
    if assay_source_audit_path is not None:
        source_correction=json.loads(assay_source_audit_path.read_text())
        refs.append(_file_artifact(journal,assay_source_audit_path,
            role='independent_validation',bindings={'kind':'append_only_assay_source_audit','purpose':'evaluation_provenance_only'}))
    transfer_audit=None
    if transfer_audit_path is not None:
        transfer_audit=json.loads(transfer_audit_path.read_text())
        if (transfer_audit['frozen_policy_sha256']!=policy['policy_sha256'] or
                transfer_audit['predictions_file_sha256']!=file_identity(predictions_path)['sha256']):
            raise StateError('coordinator audit must bind this policy/prediction freeze')
        for mode,metrics in modes.items():
            reference=transfer_audit['comparators'][mode]
            if (abs(metrics['scientific_loss']-reference['group_mean_scientific_loss'])>1e-12 or
                    abs(metrics['actual_resource_cost']-reference['group_mean_path_cost'])>1e-9):
                raise StateError('coordinator audit comparison differs from admitted rows')
        refs.append(_file_artifact(journal,transfer_audit_path,role='independent_validation',
            bindings={'kind':'coordinator_independent_transfer_audit','policy_sha256':policy['policy_sha256']}))
    elif policy['source_kind']=='measured':
        raise StateError('measured feedback requires coordinator scientific review audit')
    body={'schema':'etalon-r4-transfer-feedback/1','policy_sha256':policy['policy_sha256'],
        'split_sha256':split.sha256,'independent_test_rows_sha256':dataset['rows_sha256'],
        'training_policy_sha256':policy.get('training_policy_sha256'),
        'training_rows_sha256':policy.get('training_rows_sha256'),
        'calibration_rows_sha256':policy.get('calibration_rows_sha256'),
        'online_run_id':frozen['run_id'],'taskrows_id':'taskrows-'+digest(rows),
        'evidence':refs,'online_prediction_sha256':file_identity(predictions_path)['sha256'],
        'group_results':evaluation['group_means'],'group_equal_macro':modes,
        'independent_test_probability_assessment':probability,
        'independent_test_joint_support':joint,
        'coordinator_before_after_test_assessment':None if transfer_audit is None else {
            k:transfer_audit[k] for k in ('before_calibration','after_calibration')},
        'common_tradeoff_weight':common_weight,
        'calibration_selection':{'mixture_weight':policy.get('mixture_weight'),'cost_weight':policy['cost_weight'],
            'training_baseline_cost_weight':policy.get('training_cost_weight',policy['cost_weight']),
            'selection_scores_are_independent_test':False,
            'supported_joint_mass':calibration.get('supported_calibration_joint_mass'),
            'unsupported_joint_mass':calibration.get('unsupported_joint_mass'),
            'before_after_probability_candidates':calibration.get('probability_candidates',[]),
            'loss_cost_curve':calibration.get('loss_cost_curve',[])},
        'phi_learned':policy['source_kind']=='measured',
        'research_state':'diagnosis_required' if support_failure or joint_failure or no_gain_over_copy_best or no_cost_gain_over_phi0 else 'additional_tasks_required',
        'feedback_scope':'coordinator-released retrospective evaluation after frozen online predictions',
        'future_use':'seen_evaluation_feedback_only; cannot validate a later revision as unseen',
        'independent_evidence_role':'independent_validation; not diagnostic training input',
        'frozen_policy_and_predictions_unchanged':True,
        'decision_basis':{'probability_support_failure':support_failure,
            'joint_support_failure':joint_failure,'no_common_tradeoff_gain_over_phi0':no_cost_gain_over_phi0,
            'no_scientific_loss_gain_over_copy_best':no_gain_over_copy_best,
            'phi_minus_copy_best_loss':modes['phi']['scientific_loss']-modes['copy_best']['scientific_loss'],
            'phi_minus_copy_best_cost':modes['phi']['actual_resource_cost']-modes['copy_best']['actual_resource_cost']},
        'promotion':'none','complete_RSI':False,'mechanism_posterior':None,'expected_generalization_gain':None,
        'model_challenges':['three train/calibration/test families each; descriptive retrospective pilot',
            'prepared input poses and fixed protonation/receptor preparation, not blind pose generation',
            'finite empirical joint support; unobserved histories abstain',
            'copy-best/static train-only model and cost weight; phi equals finite nonmyopic optimizer',
            'seen original-receptor scorer switch did not resolve selection; reviewed geometry hybrid changed it',
            'unrelaxed hybrid/default protonation/known holo information; not unique causal mechanism identification'],
        'seen_intervention_metrics':seen.get('groups',{}),
        'assay_source_audit':source_correction,
        'assay_source_scope':'append-only coordinator provenance audit; other groups remain at their recorded read depth; no label rewrite or refit',
        'next_q_requirements':{'candidate':'reviewed receptor-model diagnostic on future frozen independent task groups',
            'tuned_on_this_test':False,'needs':['registered receptor intervention','identity/coupling/real cost contract','fresh task split'],
            'questions':['does q1 rank change predict which scorer has lower scientific loss across families?',
                'how should unobserved joint outcomes be handled?', 'which receptor/preparation factors alter scientific ranking?'],
            'dispatch':'not_authorized_by_this_feedback','predicted_value':None},
        'cost_accounting':{'budget_at_feedback':journal.store.balance(),
            'path_costs':'actual unique measured resource costs; offline controls share the same panel',
            'failed_engineering_and_retries':'retained in global ledger; not subtracted from collection cost',
            'amortization':'collection/training/calibration/processing receipts must be reported separately'},
        'new_producer_executions':0}
    with journal.store.connection(write=True) as db:
        journal._put(db,'controllerfeedback-r4-'+digest(body),body)
    return body
