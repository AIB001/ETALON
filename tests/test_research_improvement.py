"""Behavioral boundaries of optional improvement; all inputs are explicit fixtures."""
import copy
import json
from pathlib import Path
import pytest

from etalon.research import MethodVersion, MethodUse, TaskSplit
from etalon.research import improvement as imp
from etalon.research.composition import node_step, WORKFLOW
from etalon.research.components import record_json
from etalon.research.continuation import STEP, record_content, resume_research
from etalon.research.method_revision import journal_ref
from etalon.research.evidence_refs import EvidenceError
from etalon.active.store import StateError
from test_research_continuation import case, publish, save, checkpoint


@pytest.fixture
def prepared(case):
    j, problem, _, root = case
    ref = lambda rid: journal_ref(j, rid, source_id='research')
    artifact = lambda obj: ref(record_json(j, obj, root/'artifacts', source_kind='fixture'))
    base = ref(j.record(MethodVersion('base', ('usage_method',), {'artifact_ref':artifact({'read':'typed'})['record_id']})))
    candidate = ref(j.record(MethodVersion('candidate', ('usage_method',), {'artifact_ref':artifact({'read':'generic'})['record_id']}, parents=(base['record_id'],))))
    conditions = {'problem_kind':'reader-schema', 'endpoint':'released-evidence/1'}
    ctx = imp.build_revision_context(publish(case), task_id='case', scope='fixture', conditions=conditions,
        target_ref=base, problem_refs=[problem])
    context_ref = save(case, ctx)
    opened = imp.register_opportunity(j, context_ref, root/'content', source_id='research', source_kind='fixture')
    before = {'schema':WORKFLOW,'name':'read','scope':'fixture','nodes':[
        {'node_id':'read','capability_id':None,'method_refs':[base],'views':['E'],
         'purpose':'read generic evidence','skip_when':'already read'}], 'edges':[], 'method_refs':[base], 'supersedes':[]}
    wf = save(case, before)
    edits = [{'collection':'method_refs','before':base,'after':candidate},
             {'collection':'nodes','before':before['nodes'][0], 'after':{**before['nodes'][0], 'method_refs':[candidate]}}]
    built = imp.build_method_patch(j, context_ref=context_ref, opportunity_ref=opened['opportunity_ref'],
        base_method_ref=base,candidate_method_ref=candidate,expected_change='route generic records',source_id='research',
        workflow_before_ref=wf,workflow_edits=edits)
    new_wf = save(case, built['workflow'])
    patch = save(case, {**built['patch'], 'workflow_after_ref':new_wf})
    initial = artifact({'conditions':conditions})
    protocol = artifact({'purpose':'fixture normal execution'})
    split = ref(j.record(TaskSplit(('seen',),('unused-validation',),('unused-transfer',),
        {'seen':'seen','unused-validation':'sealed','unused-transfer':'sealed'}, 'fixture')))
    plan = save(case, dict(schema=STEP,task_id='case',question='read correctly',branch_id='improvement',operation='evaluation_plan',
        reason='fixture',input_refs=[patch],output_refs=[],method_refs=[base,candidate],accounting_refs=[],source_watermarks=[],supersedes=[],
        detail={'target':'workflow_composition','subjects':[candidate],'comparators':[base], 'protocol_ref':protocol,
                'task_split_ref':split,'initial_conditions_ref':initial,'use_refs':[],'trigger_refs':[problem],
                'metrics':[{'name':'read-errors','family':'regression','unit':'count','denominator':'actual reads','missing_policy':'unknown'}]}))
    return dict(j=j, root=root, ref=ref, artifact=artifact, base=base, candidate=candidate,
                context_ref=context_ref, ctx=ctx, opened=opened, patch=patch,new_wf=new_wf,plan=plan,conditions=conditions)


def observed(case, p, *, conditions=None, state='completed'):
    receipt = p['artifact']({'fixture':True,'output':'read'})
    cost = p['ref'](record_json(p['j'], {'wall_seconds':0.01},p['root']/'artifacts',source_kind='fixture',bindings={'kind':'accounting'}))
    use = p['ref'](p['j'].record(MethodUse(({'kind':'task','id':'case','scope':str(p['j'].store.path)},),
        ({'method_version_ref':p['candidate']['record_id'],'scope':'fixture'},),
        host_conditions=p['conditions'] if conditions is None else conditions, reason='fixture normal use')))
    return save(case, node_step(task_id='case',question='read correctly',branch_id='normal',node_id='read',
        workflow_ref=p['new_wf'],state=state,reason='normal use',output_refs=[receipt],receipt_refs=[receipt],
        method_refs=[p['candidate']],use_refs=[use],accounting_refs=[cost]))


def test_context_reads_generic_artifacts_without_typed_schema_or_actions(case, prepared):
    p=prepared
    payload=p['artifact']({'schema':'custom-result/1','value':7})
    assert imp.read_material(publish(case),payload)['content']['value']==7
    ctx=imp.build_revision_context(publish(case), task_id='another-chat',scope='fixture',conditions=p['conditions'],
        target_ref=p['base'],problem_refs=[case[1]])
    assert ctx['opportunity_key']==p['ctx']['opportunity_key']
    before=len(p['j'].store.events())
    result=imp.register_opportunity(p['j'], save(case,ctx), p['root']/'content',source_id='research',source_kind='fixture')
    assert result['state']=='already_proposed' and result['patch_refs']==[p['patch']]
    assert result['request_id']==p['opened']['request_id']
    count=len(p['j'].store.events())
    assert imp.register_opportunity(p['j'], save(case,ctx), p['root']/'content',source_id='research',source_kind='fixture')==result
    assert len(p['j'].store.events())==count and count==before+1
    assert p['j'].store.actions()==[]


def test_unfinished_opportunity_resumes_and_incomplete_context_does_not_trigger(case, prepared):
    p=prepared
    ctx={**p['ctx'], 'scope':'other'}
    from etalon.active.schema import digest
    ctx['opportunity_key']=digest({k:ctx[k] for k in ('scope','conditions','target_ref','problem_refs','evidence_refs')})
    ref=save(case,ctx)
    one=imp.register_opportunity(p['j'],ref,p['root']/'content',source_id='research',source_kind='fixture')
    two=imp.register_opportunity(p['j'],ref,p['root']/'content',source_id='research',source_kind='fixture')
    assert one['state']=='new' and two['state']=='resume_pending' and one['request_id']==two['request_id']
    missing={**ctx,'missing':['missing source'],'status':'incomplete'}
    assert imp.register_opportunity(p['j'],save(case,missing),p['root']/'content',source_id='research',source_kind='fixture')['state']=='insufficient_evidence'


def test_wait_then_assess_actual_normal_use_and_reject_condition_mismatch(case, prepared):
    p=prepared
    assess=lambda refs: imp.assess_revision(p['j'],patch_ref=p['patch'],plan_ref=p['plan'],observed_step_refs=refs,source_id='research')
    waiting=assess([])
    assert waiting['status']=='awaiting_evidence' and waiting['result_refs']==[]
    save(case,waiting)
    wrong_step=observed(case,p,conditions={'problem_kind':'other'})
    wrong=assess([wrong_step])
    assert wrong['status']=='awaiting_evidence'
    result=assess([observed(case,p)])
    assert result['status']=='ready_for_evaluation' and result['result_refs'] and result['accounting_refs']
    saved=save(case,result)
    tampered={**waiting,'status':'ready_for_evaluation','missing':[]}
    with pytest.raises(EvidenceError,match='actual use'):
        save(case,tampered)
    cp=save(case,checkpoint(case,p['plan'],counter_refs=[saved,p['patch'],wrong_step]))
    ctx=resume_research(publish(case),cp)
    assert not ctx['issues'] and saved in ctx['checked_refs']


def test_workflow_conflicts_and_forged_patch_are_rejected(case,prepared):
    p=prepared
    patch=imp._local(p['j'],p['patch'],'research',imp.PATCH)
    original=imp._local(p['j'],patch['workflow_before_ref'],'research',WORKFLOW)
    edits=copy.deepcopy(patch['workflow_edits'])
    edits[0]['before']={**p['base'],'source_id':'absent'}
    with pytest.raises(EvidenceError,match='stale'):
        imp.apply_workflow_edits(original,edits,base_ref=patch['workflow_before_ref'])
    with pytest.raises(EvidenceError,match='context'):
        save(case,{**patch,'conditions':{'endpoint':'different'}})
    with pytest.raises(EvidenceError,match='exact edits'):
        save(case,{**patch,'workflow_after_ref':patch['workflow_before_ref']})


def test_experience_reads_negative_conditions_and_supersession_without_promotion(case,prepared):
    p=prepared
    kwargs=dict(patch_ref=p['patch'],outcome='unresolved',lesson='No execution yet; do not assume improvement',
        invalid_when='protocol changes',capability_id='etalon.evidence',support_refs=[case[1]],
        directory=p['root']/'content',source_id='research',source_kind='fixture')
    exp=imp.record_experience(p['j'],**kwargs)
    newer=imp.record_experience(p['j'],**{**kwargs,'lesson':'Keep counterevidence', 'counter_refs':[case[1]],'supersedes':[exp]})
    ws=publish(case)
    result=imp.retrieve_experience(ws,source_ids=['research'],capability_id='etalon.evidence',conditions=p['conditions'])
    assert {r['ref']['record_id'] for r in result['matches']}=={exp['record_id'],newer['record_id']}
    assert imp.retrieve_experience(ws,source_ids=['research'],capability_id='etalon.evidence',conditions={'endpoint':'other'})['matches']==[]
    with pytest.raises(EvidenceError,match='evaluation'):
        imp.record_experience(p['j'],**{**kwargs,'outcome':'adopt'})


def test_related_experience_is_explicit_hypothesis_not_an_applicable_match(case,prepared):
    p=prepared
    exp=imp.record_experience(p['j'],patch_ref=p['patch'],outcome='unresolved',lesson='Fixture hypothesis only',
        invalid_when='different protocol',capability_id='etalon.evidence',support_refs=[case[1]],
        directory=p['root']/'content',source_id='research',source_kind='fixture')
    conditions={**p['conditions'],'endpoint':'different-endpoint'}
    exact=imp.retrieve_experience(publish(case),source_ids=['research'],capability_id='etalon.evidence',conditions=conditions)
    assert exact['matches']==[] and exact['related']==[]
    related=imp.retrieve_experience(publish(case),source_ids=['research'],capability_id='etalon.evidence',conditions=conditions,
                                    related_condition_keys=['problem_kind'])
    assert related['matches']==[] and related['related'][0]['ref']==exp
    assert related['related'][0]['unmatched_conditions']==['endpoint']
    assert 'hypothesis_only' in related['related'][0]['applicability']
    with pytest.raises(EvidenceError,match='query conditions'):
        imp.retrieve_experience(publish(case),source_ids=['research'],capability_id='etalon.evidence',conditions=conditions,
                                related_condition_keys=['undeclared'])


def test_changed_method_bytes_are_not_accepted_as_a_patch(case,prepared):
    p=prepared
    patch=imp._local(p['j'],p['patch'],'research',imp.PATCH)
    method=p['j'].get(p['candidate']['record_id'])
    artifact=p['j'].get(method['content_ref']['artifact_ref'])
    Path(artifact['path']).write_text('changed method')
    with pytest.raises(StateError, match='artifact'):
        save(case,patch)


def test_failed_normal_execution_remains_evaluable_without_becoming_success(case,prepared):
    p=prepared
    failed=observed(case,p,state='failed')
    assessment=imp.assess_revision(p['j'],patch_ref=p['patch'],plan_ref=p['plan'],observed_step_refs=[failed],source_id='research')
    assert assessment['status']=='ready_for_evaluation'
    assert imp._local(p['j'],failed,'research')['detail']['state']=='failed'
    assert p['j'].store.actions()==[]
