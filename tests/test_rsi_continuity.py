"""Synthetic integration cases; no model invocation or scientific result."""
import copy
import pytest

from etalon.research import improvement as imp
from etalon.research import opportunity_state as state
from etalon.research.evidence_refs import EvidenceError
from test_opportunity_state import case, close, save, reopen, SID
from test_defect_family import case as family_case


def register(p, **kw):
    return imp.register_opportunity(p['j'], p['context_ref'], p['root']/'notes',
                                    source_id=SID, source_kind='fixture', **kw)


def test_old_entry_registration_recovers_no_change_and_reopen_episode(case):
    terminal=save(case,close(case))
    before=len(case['j'].store.events())
    one=register(case); two=register(case)
    assert one==two and one['state']=='terminal'
    assert one['proposal_permission']=='not_granted' and one['patch_refs']==[]
    assert len(case['j'].store.events())==before
    new=case['artifact']({'fixture_new_observation':12})
    new_state=save(case,reopen(case,terminal,new))
    result=register(case)
    assert result['state']=='reopened' and result['lifecycle']['episode_ref']==new_state
    assert result['request_id']!=one['request_id'] and register(case)==result
    assert case['negative'] in result['lifecycle']['retained_counter_refs']
    assert not case['j'].store.actions()


def test_waiting_cannot_be_mistaken_for_proposal_success(case):
    save(case,close(case,outcome='awaiting_evidence'))
    result=register(case)
    assert result['state']=='awaiting_evidence'
    assert not result['lifecycle']['terminal'] and result['proposal_permission']=='not_granted'


def test_new_annotations_project_as_reviewed_judgments():
    from etalon.research.graph_memory import _project_node
    ref={'schema':'etalon-evidence-ref/1','source_id':'fixture','record_id':'artifact-'+'0'*64,'record_digest':'0'*64}
    content={'schema':state.DISPOSITION,'task_id':'fixture-task','reason':'No justified candidate',
             'outcome':'no_change','counter_refs':[ref],'reconsider_when':[],'judgment':'bounded_judgment'}
    row={'projected_content':content,'record':{},'record_type':'artifact'}
    node=_project_node(ref,row,task_id='fixture-task',home_overrides={})
    assert node['home']=='Judge' and node['kind']=='J'
    assert node['revision_note']['outcome']=='no_change'
    assert node['revision_note']['measured_improvement']=='not_inferred'


@pytest.mark.parametrize('changed_problem',[False,True])
def test_family_reuses_identical_inputs_but_changed_problem_requires_review(family_case,changed_problem):
    p=family_case
    first=p['member']()
    note=state.build_opportunity_disposition(p['journal'],opportunity_ref=first['opportunity_ref'],
        outcome='no_change',reason='Fixture lacks a justified patch',reviewer='fixture-host',
        support_refs=[p['evidence']],counter_refs=[p['problem']],
        reconsider_when=[{'condition_id':'changed','condition':'New original data changes the diagnosis'}],source_id='research')
    p['save'](note)
    other=p['artifact']('another-problem.txt','Different scientific failure' if changed_problem else 'Synthetic diagnosis label')
    second=p['member'](problem_ref=other)
    group=p['save'](p['build']([first,second]))
    result=imp.register_opportunity(p['journal'],second['context_ref'],p['root']/'content',source_id='research',
        source_kind='fixture',family_ref=group,external_workspace=p['workspace']())
    assert result['state']==('family_reconsideration_required' if changed_problem else 'family_terminal')
    assert result['proposal_permission']=='not_granted'
