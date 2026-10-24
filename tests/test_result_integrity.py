"""Synthetic counterexamples for typed observation and lineage checks."""
import copy
import json

import pytest

from etalon.active.schema import digest
from etalon.research.result_integrity import (confirmation_eligibility, freeze_check_contract,
    integrity_required_checks, required_check_names, validate_check_contract, validate_native_receipt)
from etalon.research.scientific_input import identity
from test_research_continuation import case


def test_requirements_come_from_configuration_not_observed_subset():
    config={'modules':[{'name':'Distance','chosen_binary_test_output':['near','clash'],
                       'rename_outputs':{'near':'Maximum distance'},'rename_suffix':'_protein'}]}
    assert required_check_names(config)==['clash_protein','maximum_distance']
    contract=freeze_check_contract(config,version='fixture',implementation={'fixture':{}})
    contract['required_checks']=['maximum_distance']
    with pytest.raises(ValueError,match='configuration-derived'):
        validate_check_contract(contract)
    with pytest.raises(ValueError,match='duplicate'):
        required_check_names({'modules':[{'name':'A','chosen_binary_test_output':['a','a']}]})


def native_fixture(tmp_path):
    output=tmp_path/'pose.sdf';output.write_text('synthetic output')
    compiled={'case_id':'fixture','method':{'engine':'fixture'},'files':{'ligand':{'sha256':'l'}}}
    protocol={'sha256':'p','bytes':2}
    key=digest({'compiled':compiled,'stage':'empirical','freeze':protocol})
    request={'request_id':key,'compiled':compiled,'stage':'empirical','freeze':protocol,'pool':None,'attempt_id':'a'}
    receipt={'request_id':key,'case_id':'fixture','stage':'empirical','attempt_id':'a','status':'completed',
             'native_resource':{'exit_status':0},'execution_mode':'new_execution','error':None,
             'artifacts':{str(output):identity(output)}}
    args={'compiled':compiled,'stage':'empirical','protocol':protocol,'pool':None,'output':output}
    return receipt,request,args


@pytest.mark.parametrize('change',['method','protocol','input','request_id','output','artifact','attempt','pool','execution_evidence'])
def test_native_cache_reuses_hashes_but_rejects_wrong_context(tmp_path,change):
    receipt,request,args=native_fixture(tmp_path)
    assert validate_native_receipt(receipt,request,**args)==receipt
    if change=='method':request['compiled']=copy.deepcopy(args['compiled']);request['compiled']['method']['engine']='other'
    elif change=='protocol':request['freeze']={'sha256':'other'}
    elif change=='input':request['compiled']=copy.deepcopy(args['compiled']);request['compiled']['files']['ligand']={}
    elif change=='request_id':receipt['request_id']='other'
    elif change=='output':args['output']=tmp_path/'other.sdf'
    elif change=='artifact':args['output'].write_text('mutated')
    elif change=='attempt':receipt['attempt_id']='b'
    elif change=='pool':request['pool']={'sha256':'other'}
    elif change=='execution_evidence':receipt['native_resource']=None
    with pytest.raises(ValueError):validate_native_receipt(receipt,request,**args)


def eligibility_fixture(tmp_path, frozen=10):
    panel={'sha256':'a'*64,'bytes':10}
    declaration={'policy':{'selection':'empirical'},'panel':panel,'frozen_at_ns':frozen}
    path=tmp_path/'historical-freeze.json';path.write_text(json.dumps(declaration),encoding='utf-8')
    candidate={'schema':'etalon-candidate-confirmation-freeze/1','panel':panel,
        'candidate_digest':digest(declaration),'frozen_at_ns':frozen,
        'decisions_frozen_before_execution':True,'outcomes_visible_at_freeze':False,
        'seen_series_ids':[],'parent_ids':['related','new'], 'freeze_evidence':{'path':str(path),**identity(path)}}
    overlay={'schema':'snd1-seen-series-visibility-overlay/1','source_panel':panel,'original_split_rewritten':False,
        'status':'activated_after_audit','activated_at_ns':20,'rows':[{'series_id':'seen'}],
        'remaining_parent_ids':['new']}
    parent={'id':'related','series_id':'seen','split':'confirmation'}
    return parent,dict(candidate_freeze=candidate,overlay=overlay,panel_identity=panel)


def test_historical_candidate_freeze_keeps_candidate_relative_role(tmp_path):
    parent,args=eligibility_fixture(tmp_path)
    assert confirmation_eligibility(parent,**args)['eligible'] is True
    assert parent['split']=='confirmation'


def test_new_post_exposure_candidate_cannot_restore_unseen_series_by_flags(tmp_path):
    parent,args=eligibility_fixture(tmp_path,frozen=30)
    result=confirmation_eligibility(parent,**args)
    assert result['eligible'] is False and 'series_feedback_seen' in result['reasons']
    parent.update(id='new',series_id='unseen')
    assert confirmation_eligibility(parent,**args)['eligible'] is True


@pytest.mark.parametrize('missing',['freeze','overlay','evidence','panel','candidate','chronology'])
def test_missing_eligibility_evidence_never_passes(tmp_path,missing):
    parent,args=eligibility_fixture(tmp_path)
    if missing=='freeze':args['candidate_freeze']=None
    elif missing=='overlay':args['overlay']=None
    elif missing=='evidence':args['candidate_freeze'].pop('freeze_evidence')
    elif missing=='panel':args['candidate_freeze']['panel']={}
    elif missing=='candidate':args['candidate_freeze']['candidate_digest']='new-candidate'
    elif missing=='chronology':args['candidate_freeze']['frozen_at_ns']=True
    assert confirmation_eligibility(parent,**args)['eligible'] is False


def test_integrity_unknown_flows_into_existing_evaluation_contract(case):
    from etalon.research.components import record_json
    from etalon.research.method_revision import journal_ref
    from etalon.research.evaluation_contract import build_evaluation_contract,evaluate_contract
    from test_research_continuation import publish
    journal,_,_,root=case
    rid=record_json(journal,{'coverage_complete':None,'identity_complete':True,'count':1,'error':0,'protocol':'fixture'},
                    root/'integrity',source_kind='fixture')
    ref=journal_ref(journal,rid,source_id='research')
    contract=build_evaluation_contract(task_id='integrity',scope='synthetic',conditions={'checker':'fixture'},
        protocol_ref=ref,initial_conditions_ref=ref,inputs=[{'name':'integrity','ref':ref,'kind':'result'}],
        metrics=[{'name':'error','family':'regression','measure':{'operation':'value',
            'left':{'input':'integrity','pointer':'/error'},'right':None},'operator':'le','threshold':0,
            'unit':'count','denominator':'one synthetic result','denominator_selector':{'input':'integrity','pointer':'/count'},
            'missing_policy':'insufficient'}],required_checks=integrity_required_checks(),
        comparability=[{'check_id':'protocol','selector':{'input':'integrity','pointer':'/protocol'},'expected':'fixture'}],
        provenance={'mode':'retrospective','note':'synthetic fixture','basis_refs':[]})
    assert evaluate_contract(publish(case),contract)['status']=='insufficient'
