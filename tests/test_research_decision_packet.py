"""Delivery projection preserves negative evidence and task-switch semantics."""
import copy
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'examples'))
from read_research_work import decision_packet


def fixture():
    work=dict(task_id='fixture',branch_id='one',goal='test',question='choose',branch_state='active',
        residuals=['negative result remains unexplained'],next_segment=None,status_guards=[],
        completed_refs=[{'historical':i} for i in range(500)])
    return dict(work=dict(work=work,entry='/fixture/entry',checkpoint_ref={'id':'checkpoint'},
                         work_ref={'id':'work'}),experiences=[],branches=[],pending_handles=[],
                counter_originals=[{'ref':{'id':'counter'},'body_read':True,'bytes_read':100}])


def test_delta_keeps_counter_coverage_and_changed_residual():
    result=fixture();full,receipt=decision_packet(result)
    assert 'completed_refs' not in full['packet']
    delta,_=decision_packet(result,previous=receipt)
    assert delta['changed']=={} and delta['counter_bodies_rechecked']
    result['work']['work']['residuals']=['a new competing mechanism']
    updated,_=decision_packet(result,previous=receipt)
    assert updated['changed']['residuals']==['a new competing mechanism']
    assert updated['counter_coverage']==result['counter_originals']


def test_task_switch_requires_full_packet_and_damaged_receipt_fails():
    result=fixture();_,previous=decision_packet(result)
    result['work']['work']['task_id']='other'
    switched,_=decision_packet(result,previous=previous)
    assert switched['delivery']=='scope_changed_full_packet'
    previous['packet']['residuals']=['tampered']
    with pytest.raises(ValueError,match='receipt changed'):
        decision_packet(result,previous=previous)


def test_current_feedback_and_predecessor_link_survive_compact_delivery():
    result=fixture()
    result['work']['work'].update(feedback={'conditions':{'fixture':'local'},
        'untested':[{'measurement':None}], 'selected_action':None},
        previous_work={'path':'/fixture/previous.json','identity':{'sha256':'fixture','bytes':123}})
    full,receipt=decision_packet(result)
    assert full['packet']['feedback']==result['work']['work']['feedback']
    assert full['packet']['previous_work']==result['work']['work']['previous_work']
    result['work']['work']['feedback']={'counter_cases':['new negative evidence'], 'selected_action':None}
    delta,_=decision_packet(result,previous=receipt)
    assert delta['changed']['feedback']['counter_cases']==['new negative evidence']
