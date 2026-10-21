"""Executable segmented diagnostic policies, distinct from choosing a P/E workflow.

The initial policies are hand-authored controls. Their actual transitions and
diagnostic costs are evidence; they are not a learned phi or calibrated posterior.
"""
from __future__ import annotations
from etalon.active.schema import digest
from etalon.active.store import StateError
from .journal import ResearchJournal


def register_controller(journal: ResearchJournal, *, name: str, schedule: list[dict],
                        source_evidence: tuple[str, ...], mode: str = 'audit_then_compare') -> str:
    if mode not in {'audit_then_compare','static_schedule'}:
        raise ValueError('registered controller mode required')
    if not schedule or len({s['id'] for s in schedule})!=len(schedule):
        raise ValueError('unique finite diagnostic segments required')
    known=set()
    for segment in schedule:
        if segment['stage'] not in {'smoke','development','validation'} or not set(segment['depends_on'])<=known:
            raise ValueError('schedule must be ordered with explicit prior dependencies')
        if len(segment['request_sha256'])!=64 or not 0<segment['quote_wall_second']<=300:
            raise ValueError('content-bound request and per-action quote required')
        known.add(segment['id'])
    with journal.store.connection(write=True) as db:
        journal._artifacts(db,source_evidence,diagnostic_inputs=True)
        body={'schema':'etalon-improvement-controller/1','name':name,'mode':mode,'schedule':schedule,
              'source_evidence':list(source_evidence),'origin':'hand_authored_executable_control',
              'learning_status':'not_learned','mechanism_posterior':None,
              'hypotheses':['graph_or_mapping_error','pose_constraint_or_seed_allocation',
                            'evaluation_proxy_failure','receptor_or_solvent_omission','outside_model'],
              'query_model':'finite conditional schedule; no fitted predictive probabilities'}
        return journal._put(db,'controller-'+digest(body),body)


def next_segment(journal: ResearchJournal, controller_id: str, *, decision_sha256: str | None = None,
                 check_budget: bool = True) -> dict:
    policy=journal.get(controller_id);receipts={};challenges=[]
    for segment in policy['schedule']:
        key='controllerstep-'+digest([controller_id,segment['id']])
        try:receipt=journal.get(key)
        except StateError:continue
        receipts[segment['id']]=receipt
        if receipt['model_challenge']:challenges.append(receipt['model_challenge'])
        if receipt['stop'] and policy['mode']=='audit_then_compare':
            return {'status':'stop_model_challenge','next':None,'evidence':key,
                    'challenges':challenges,'requires_new_version':True}
    for segment in policy['schedule']:
        if segment['id'] in receipts:continue
        if not set(segment['depends_on'])<=receipts.keys():continue
        if segment['stage']=='validation':
            if not decision_sha256:
                return {'status':'awaiting_development_decision_freeze','next':None,'challenges':challenges}
            decision=journal.get('decision-'+decision_sha256)
            if digest({k:v for k,v in decision.items() if k!='freeze_sha256'})!=decision_sha256:
                raise StateError('decision changed')
        if check_budget and segment['quote_wall_second']>journal.store.balance()['remaining']:
            return {'status':'stop_budget','next':None,'challenges':challenges}
        return {'status':'query','next':segment,'challenges':challenges,
                'search_scope':'registered finite schedule, not fitted chemical expected information gain'}
    return {'status':'awaiting_independent_feedback','next':None,'challenges':challenges}


def record_segment(journal: ResearchJournal, controller_id: str, segment_id: str,
                   admission: dict, *, decision_sha256: str | None = None) -> dict:
    next_action=next_segment(journal,controller_id,decision_sha256=decision_sha256,check_budget=False)
    key='controllerstep-'+digest([controller_id,segment_id])
    # Exact replay is allowed after advancing, but never a changed observation.
    try:old=journal.get(key)
    except StateError:old=None
    if old:
        if old['admission_sha256']!=digest(admission):raise StateError('controller segment result changed')
        return old
    if next_action['status']!='query' or next_action['next']['id']!=segment_id:
        raise StateError('segment is not the current allowed diagnostic query')
    segment=next_action['next']
    if admission['request_sha256']!=segment['request_sha256'] or admission['stage']!=segment['stage']:
        raise StateError('segment admission does not bind requested query')
    with journal.store.connection(write=True) as db:
        journal._artifacts(db,(admission['result_artifact_id'],),diagnostic_inputs=True)
        e0=sum(s['observations']['E0']['status']=='measured' for s in admission['slots'])
        e1=sum(s['observations']['E1']['status']=='measured' for s in admission['slots'])
        challenge=None
        if not e0:challenge='no_qualified_pose; graph/preparation/receptor hypotheses unresolved'
        elif not e1:challenge='scorer_unavailable; geometry cannot substitute for scientific evaluation'
        body={'controller_id':controller_id,'segment_id':segment_id,
              'admission_sha256':digest(admission),'execution_id':admission['execution_id'],
              'admission_record_id':'poseadmission-'+digest([admission['request_sha256'],admission['result_sha256']]),
              'evidence':admission['result_artifact_id'],'observed_counts':{'E0':e0,'E1':e1},
              'model_challenge':challenge,'stop':segment['stage']=='smoke' and challenge is not None,
              'cost':admission['cost'],'transition_basis':'actual admitted outcomes only'}
        journal._put(db,key,body)
        return body


def independent_feedback(journal: ResearchJournal, *, controller_id: str,
                         decision_sha256: str, validation_id: str) -> dict:
    """Released decision validation triggers a next diagnostic, not automatic promotion."""
    validation=journal.get(validation_id);journal.get(controller_id)
    if validation.get('decision_sha256')!=decision_sha256:
        raise StateError('independent feedback must bind this frozen decision')
    result=validation['comparison']
    loss=result.get('frozen_policy_loss')
    policies=result.get('policies',{})
    all_wrong=bool(policies) and all(row.get('loss')==1 for row in policies.values())
    challenge='experimental_rank_failure' if loss==1 else 'abstention' if loss==.5 else 'generalization_unresolved'
    body={'controller_id':controller_id,'decision_sha256':decision_sha256,'validation_id':validation_id,
          'model_challenge':challenge,'mechanism_posterior':None,'promotion':'none',
          'research_state':'diagnosis_required' if loss in (1,.5) else 'additional_tasks_required',
          'counterexample':{'all_predeclared_policies_wrong':all_wrong,'retained_validation_id':validation_id},
          'hypotheses':['graph_or_mapping_error','pose_constraint_or_seed_allocation','evaluation_proxy_failure',
               'receptor_or_solvent_omission','outside_model'],
          'M_limitations':['one same-target development pair','finite rigid pose family','neutral-state-only',
                           'no calibrated score-to-assay error or causal likelihood'],
          'expected_gain':None,'phi_learned':False,
          'next_diagnostic':'compare score ranking against receptor/state/solvation controls on new frozen tasks',
          'phi_update':'not_learned; insufficient independent tasks and counterfactual query costs',
          'required_controls':['copy_best','static_schedule','myopic_query_value','exact_finite_query_value']}
    body['next_q_requirements']={'capability':'new receptor/state/solvation controls, not yet registered',
                                 'wall_second_quote':None,'dispatch':'not_executable_missing_capability_and_quote',
                                 'needed_data':'independent paired task losses and real diagnostic costs'}
    with journal.store.connection(write=True) as db:
        import json
        steps=[]
        for row in db.execute("SELECT key,body FROM metadata WHERE key LIKE 'research:controllerstep-%'"):
            step=json.loads(row[1])
            if step['controller_id']==controller_id:
                steps.append({'record_id':row[0].removeprefix('research:'),
                              'result_artifact_id':step['evidence'],'execution_id':step['execution_id'],
                              'admission_sha256':step['admission_sha256'],
                              'admission_record_id':step.get('admission_record_id'),
                              'admission_lookup':('explicit_record' if step.get('admission_record_id') else
                                  'legacy step: follow result_artifact_id and execution_id; no guessed identity')})
        body['archive']={'controller_id':controller_id,'step_records':steps,
                         'decision_id':'decision-'+decision_sha256,'validation_id':validation_id,
                         'validation_evidence':validation.get('evidence',[]),
                         'scope':'full journal references retain measured/failed/missing observations and cost receipts'}
        journal._put(db,'controllerfeedback-'+digest(body),body)
    return body
