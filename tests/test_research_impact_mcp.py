"""Existing MCP view and hook navigation; fixture checks, not native-host evidence."""
import copy
import json

import pytest

from etalon import research_hooks
from etalon.research.evidence_refs import file_identity
from test_mcp import Collector
from test_research_graph_memory import graph_case


def reader():
    from etalon.mcp.evidence import register
    collector = Collector()
    register(collector)
    return collector.tools['etalon_research_context']


def test_impact_reuses_tool_and_retains_counter_direction(graph_case):
    c = graph_case
    before = file_identity(c['store'].path)
    value = json.loads(reader()(str(c['entry']), view='impact',
        view_options={'changed_refs': [c['counter']]}))
    assert value['ok'], value
    assert value['schema'] == 'etalon-research-impact/1'
    assert not value['automatic_retry'] and not value['rsi_triggered']
    assert value['execution_permission'] == 'not_granted'
    assert c['claim']['record_id'] in json.dumps(value)
    assert file_identity(c['store'].path) == before and not c['store'].actions()


@pytest.mark.parametrize('extra', [
    {'scope': {'branch_id': 'x'}}, {'cursor': 'unexpected'}, {'question': 'choose for me'},
    {'view_options': {'changed_refs': [], 'top_k': 1}}, {'conditions': {'target': 'x'}},
])
def test_impact_rejects_implicit_selection(graph_case, extra):
    args = dict(entry=str(graph_case['entry']), view='impact',
                view_options={'changed_refs': [graph_case['counter']]})
    assert json.loads(reader()(**{**args, **extra}))['ok'] is False


def test_hook_offers_pinned_impact_read_without_running_it(graph_case):
    c = graph_case
    context = json.loads(reader()(str(c['entry']), view='card'))
    config = {'max_bytes': 16384, 'policy': None, 'conditions': None}
    old = copy.deepcopy(context)
    # Deliberately minimal previous delivery: no arbitrary filesystem reads.
    old = {k: old[k] for k in ('task_id', 'task_workset') if k in old}
    previous = {'usable': True, 'context': old, 'config': config}
    nav = research_hooks._impact_navigation(context, previous, config)
    assert nav and nav['automatic_review'] is False and nav['automatic_retry'] is False
    assert nav['meaning'] == 'delivery_reference_change_not_new_scientific_evidence'
    if 'read' in nav:
        assert nav['read']['arguments']['entry'] == context['read_context']
        assert json.loads(reader()(**nav['read']['arguments']))['ok']
    same = {**previous, 'context': context}
    assert research_hooks._impact_navigation(context, same, config) is None
    switched = copy.deepcopy(context)
    switched['task_id'] = 'another-task'
    assert research_hooks._impact_navigation(switched, previous, config) is None
