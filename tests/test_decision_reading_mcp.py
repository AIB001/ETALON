"""Read-only MCP integration: scoped navigation is not scientific admission."""
import json

import pytest

from etalon.active.schema import canonical
from etalon.research.evidence_refs import file_identity
from etalon.research.graph_memory import node_identity
from test_mcp import Collector
from test_research_graph_memory import graph_case


def reader():
    from etalon.mcp.evidence import register
    collector = Collector()
    register(collector)
    return collector.tools['etalon_research_context']


def test_reading_is_existing_tool_and_exact_original_navigation(graph_case):
    c = graph_case
    before = file_identity(c['store'].path)
    read = reader()
    args = dict(entry=str(c['entry']), view='reading', question='Which fixture route is supported?',
                scope={'root_refs': [c['claim']], 'ordinary_hops': 0},
                view_options={'reading_mode': 'decision'}, max_bytes=4096, limit=2)
    pages = []
    while True:
        text = read(**args)
        assert len(text.encode('utf-8')) <= 4096
        page = json.loads(text)
        assert page['ok'], page
        assert page['execution_permission'] == 'not_granted'
        assert page['automatic_retry'] is False
        assert page['counterevidence']['original_bodies_read_by_host'] == 'not_established'
        pages.append(page)
        if page['next_cursor'] is None:
            break
        args.update(entry=page['read_context'], cursor=page['next_cursor'])
        assert len(pages) < 100
    serialized = canonical(pages)
    assert c['counter']['record_id'] in serialized
    assert c['claim']['record_id'] in serialized
    assert 'PRIVATE_SENTINEL_must_not_inject' not in serialized
    assert file_identity(c['store'].path) == before and not c['store'].actions()


@pytest.mark.parametrize('extra', [
    {'node_id': 'some-node'},
    {'view_options': {'top_k': 1}},
    {'branch_id': 'fixed-core', 'scope': {'root_refs': []}},
    {'question': None},
    {'max_bytes': 65536},
])
def test_reading_rejects_ambiguous_or_unbounded_requests(graph_case, extra):
    args = dict(entry=str(graph_case['entry']), view='reading', question='Fixture question')
    result = json.loads(reader()(**{**args, **extra}))
    assert result['ok'] is False


def test_existing_summary_menu_exposes_optional_new_view(graph_case):
    result = json.loads(reader()(str(graph_case['entry']), view='card'))
    assert result['ok']
    recipe = result['reading_menu']['decision_reading']
    assert recipe['view'] == 'reading'
    assert recipe['view_options']['reading_mode'] == 'decision'
    old = json.loads(reader()(str(graph_case['entry']), view='manifest',
                              node_id=node_identity(graph_case['counter'])))
    assert old['ok'] and old['items'][0]['value']['ref'] == graph_case['counter']
