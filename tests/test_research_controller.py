"""Synthetic controller branch tests; no inferred scientific mechanism probabilities."""
from etalon.active.schema import digest
from etalon.research.controller import register_controller,next_segment,record_segment,independent_feedback
from test_research_journal import setup


def test_failed_smoke_stops_and_static_control_does_not_claim_learning(tmp_path):
    _,j,a=setup(tmp_path)
    schedule=[{'id':'smoke','stage':'smoke','request_sha256':'a'*64,'quote_wall_second':1,'depends_on':[]},
              {'id':'dev','stage':'development','request_sha256':'b'*64,'quote_wall_second':1,'depends_on':['smoke']}]
    controller=register_controller(j,name='fixture candidate',schedule=schedule,source_evidence=(a.id,))
    receipt={'request_sha256':'a'*64,'result_sha256':'b'*64,'stage':'smoke','result_artifact_id':a.id,'execution_id':'fixture',
             'slots':[{'observations':{'E0':{'status':'measured'},'E1':{'status':'failed'}}}],
             'cost':{'wall_second':.2}}
    assert next_segment(j,controller)['next']['id']=='smoke'
    record=record_segment(j,controller,'smoke',receipt)
    assert record['stop'] and next_segment(j,controller)['status']=='stop_model_challenge'
    assert record_segment(j,controller,'smoke',receipt)==record
    static=register_controller(j,name='fixture static',schedule=schedule,source_evidence=(a.id,),mode='static_schedule')
    record_segment(j,static,'smoke',receipt)
    assert next_segment(j,static)['next']['id']=='dev'
    assert j.get(static)['learning_status']=='not_learned'


def test_independent_counterexample_reenters_diagnosis(tmp_path):
    _,j,a=setup(tmp_path)
    controller=register_controller(j,name='fixture',schedule=[{'id':'x','stage':'development',
        'request_sha256':'a'*64,'quote_wall_second':1,'depends_on':[]}],source_evidence=(a.id,))
    receipt={'decision_sha256':'b'*64,'comparison':{'frozen_policy_loss':1,
        'policies':{p:{'loss':1} for p in ('P0/E0','P0/E1','P1/E0','P1/E1')}}}
    identifier='decisionvalidation-'+digest(receipt)
    with j.store.connection(write=True) as db:
        j._put(db,identifier,receipt)
        # Historical real snapshot steps did not carry admission_record_id.
        j._put(db,'controllerstep-legacy-fixture',{'controller_id':controller,'evidence':a.id,
            'execution_id':'old-execution','admission_sha256':'c'*64})
    transition=independent_feedback(j,controller_id=controller,decision_sha256='b'*64,validation_id=identifier)
    assert transition['research_state']=='diagnosis_required'
    assert transition['counterexample']['all_predeclared_policies_wrong']
    assert transition['expected_gain'] is None and transition['phi_learned'] is False
    old=transition['archive']['step_records'][0]
    assert old['admission_record_id'] is None and old['admission_lookup'].startswith('legacy')
