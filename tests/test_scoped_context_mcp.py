"""Optional scoped reads use the existing MCP interfaces and never write research."""
import json

from etalon.mcp import evidence
from etalon.research.evidence_refs import file_identity
from test_mcp import Collector
from test_research_graph_memory import graph_case


def registered():
    collector = Collector()
    evidence.register(collector)
    return collector.tools


def test_scope_is_not_an_ignored_option_on_other_views(graph_case):
    call = registered()['etalon_research_context']
    for view in ('resources', 'branch'):
        result = json.loads(call(str(graph_case['entry']), view=view,
            scope={'branch_id': 'fixed-core', 'ordinary_hops': 1}))
        assert not result['ok']
    assert not graph_case['store'].actions()


def test_existing_context_passes_exact_scope_to_pinned_read(graph_case):
    call = registered()['etalon_research_context']
    before = file_identity(graph_case['store'].path)
    result = json.loads(call(str(graph_case['entry']), view='summary', max_bytes=65536,
        scope={'branch_id': 'fixed-core', 'ordinary_hops': 1}))
    assert result['ok'], result
    assert result['scope_receipt']['global_reference_coverage_complete'] is False
    assert result['execution_permission'] == 'not_granted'
    again = json.loads(call(result['read_context'], view='summary', max_bytes=65536,
        scope=result['scope']))
    assert again['ok'], again
    assert again['scope_receipt'] == result['scope_receipt']
    assert again['checkpoint_ref'] == result['checkpoint_ref']
    assert file_identity(graph_case['store'].path) == before
    assert not graph_case['store'].actions()


def test_invalid_scope_cannot_silently_expand_or_omit_counters(graph_case):
    call = registered()['etalon_research_context']
    for scope in (
        {'branch_id': 'fixed-core', 'ordinary_hops': 2},
        {'branch_id': 'fixed-core', 'drop_counters': True},
        {'root_refs': []},
    ):
        result = json.loads(call(str(graph_case['entry']), view='summary', scope=scope))
        assert not result['ok'], result


def test_graph_tool_keeps_the_existing_node_ceiling(graph_case):
    call = registered()['etalon_research_graph']
    args = dict(workspace=str(graph_case['workspace']), checkpoint_ref=graph_case['checkpoint'],
        scope={'root_refs': [graph_case['claim']], 'ordinary_hops': 0})
    result = json.loads(call(**args))
    assert result['ok'], result
    assert result['scope_receipt']['counter_reference_coverage_complete'] is True
    assert graph_case['counter'] in result['scope_receipt']['counter_endpoint_refs']
    too_small = json.loads(call(**args, max_nodes=1))
    assert not too_small['ok']
    assert not graph_case['store'].actions()
