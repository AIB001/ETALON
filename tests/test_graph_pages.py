"""Lossless bounded delivery contracts; fixture bodies are not scientific evidence."""
import copy
import json
from pathlib import Path

import pytest

from etalon.active.schema import canonical, digest
from etalon.mcp import evidence
from etalon.research.evidence import StaleCursorError
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.graph_memory import _build_research_graph, build_research_graph
from etalon.research.graph_scope import assemble_graph_pages, audit_graph_pages, graph_page
from etalon.research.research_context import build_research_context
from test_mcp import Collector
from test_research_graph_memory import graph_case


def collect(call):
    pages, cursor = [], None
    for _ in range(300):
        page = call(cursor)
        pages.append(page)
        cursor = page['next_cursor']
        if cursor is None:
            return pages
    raise AssertionError('pagination did not terminate')


def test_page_transcript_requires_every_consecutive_byte_and_preserves_originals(graph_case):
    c = graph_case
    scope = {'branch_id': 'fixed-core', 'ordinary_hops': 1}
    before = file_identity(c['store'].path)
    graph = _build_research_graph(c['workspace'], checkpoint_ref=c['checkpoint'], scope=scope)
    pages = collect(lambda cursor: build_research_graph(c['workspace'], checkpoint_ref=c['checkpoint'],
        scope=scope, max_bytes=4096, cursor=cursor))
    saved = canonical(pages)
    assert all(len(canonical(dict(p, ok=True)).encode()) <= 4096 for p in pages)
    assert assemble_graph_pages(pages)['graph'] == graph
    assert canonical(pages) == saved
    partial = audit_graph_pages(pages[:1])
    assert not partial['all_pages_delivered'] and partial['next_cursor']
    complete = audit_graph_pages(pages)
    assert complete['all_pages_delivered'] and complete['original_bodies_read_by_host'] == 'not_established'
    for transcript in (pages[1:], pages[:-1], [pages[0], *pages], [pages[1], pages[0], *pages[2:]]):
        with pytest.raises(EvidenceError, match='transcript'):
            assemble_graph_pages(transcript)
    changed = copy.deepcopy(pages)
    changed[-1]['items'][-1]['value']['value'] = 'changed'
    changed[-1]['context_id'] = 'graph-page-' + digest({k: v for k, v in changed[-1].items() if k != 'context_id'})
    with pytest.raises(EvidenceError, match='projection'):
        assemble_graph_pages(changed)
    assert file_identity(c['store'].path) == before and not c['store'].actions()


def test_large_unicode_field_uses_existing_byte_fragments_without_loss(graph_case):
    c = graph_case
    graph = _build_research_graph(c['workspace'], checkpoint_ref=c['checkpoint'],
        scope={'root_refs': [c['decision']], 'ordinary_hops': 0})
    # Exercise an oversized indivisible value without fabricating source claims.
    controls = {'host_question': '分子条件与反证🙂' * 1500}
    pages = collect(lambda cursor: graph_page(graph, context=controls, cursor=cursor, max_bytes=4096))
    assert any('fragment' in item for p in pages for item in p['items'])
    assert assemble_graph_pages(pages) == {'graph': graph, 'context': controls}
    assert all(len(canonical(dict(p, ok=True)).encode()) <= 4096 for p in pages)


def test_requested_root_precedes_bulk_history_and_context_without_changing_projection(graph_case):
    c = graph_case
    graph = _build_research_graph(c['workspace'], checkpoint_ref=c['checkpoint'],
        scope={'root_refs': [c['decision']], 'ordinary_hops': 0})
    context = {'host_question': 'large context control ' * 3000}
    pages = collect(lambda cursor: graph_page(graph, context=context, cursor=cursor, max_bytes=16384))
    first = [item.get('value', {}) for item in pages[0]['items']]
    assert any(row.get('path') == ['graph', 'nodes', 0] and row['value']['ref'] == c['decision'] for row in first)
    assert any(row.get('path') == ['graph', 'continuation'] for row in first)
    positions = {tuple(item['value']['path']): (page_index, item_index)
        for page_index, page in enumerate(pages) for item_index, item in enumerate(page['items']) if 'value' in item}
    assert positions['graph', 'nodes', 0] < positions['graph', 'branches', 0]
    assert positions['graph', 'scope_receipt', 'counter_relations', 0] < positions['graph', 'branches', 0]
    assert assemble_graph_pages(pages) == {'graph': graph, 'context': context}


def test_scope_change_and_later_original_tampering_cannot_resume(graph_case):
    c = graph_case
    args = dict(checkpoint_ref=c['checkpoint'], scope={'root_refs': [c['decision']], 'ordinary_hops': 0}, max_bytes=4096)
    first = build_research_graph(c['workspace'], **args)
    with pytest.raises(StaleCursorError):
        build_research_graph(c['workspace'], **{**args, 'scope': {'root_refs': [c['claim']], 'ordinary_hops': 0}}, cursor=first['next_cursor'])
    Path(c['journal'].get(c['counter']['record_id'])['path']).write_text('changed counter original')
    with pytest.raises(EvidenceError, match='identity|unavailable|hash'):
        build_research_graph(c['workspace'], **args, cursor=first['next_cursor'])


def test_context_and_mcp_scoped_overflow_have_bounded_complete_continuation(graph_case):
    c = graph_case
    collector = Collector()
    evidence.register(collector)
    scope = {'root_refs': [c['decision']], 'ordinary_hops': 0}
    graph = _build_research_graph(c['workspace'], checkpoint_ref=c['checkpoint'], scope=scope, max_nodes=128)
    for tool_name, kwargs in (
        ('etalon_research_graph', {'workspace': str(c['workspace']), 'checkpoint_ref': c['checkpoint']}),
        ('etalon_research_context', {'entry': str(c['entry']), 'view': 'summary'}),
    ):
        def call(cursor):
            raw = collector.tools[tool_name](**kwargs, scope=scope, cursor=cursor, max_bytes=4096)
            assert len(raw.encode()) <= 4096
            value = json.loads(raw)
            assert value['ok'], value
            if 'read_context' in value:
                kwargs['entry'] = value['read_context']
            return value
        pages = collect(call)
        assert assemble_graph_pages(pages)['graph'] == graph
        assert audit_graph_pages(pages)['all_pages_delivered']
        if tool_name.endswith('context'):
            assert assemble_graph_pages(pages)['context']['requested_view'] == 'summary'
            changed = json.loads(collector.tools[tool_name](**kwargs, scope=scope, max_bytes=4096,
                cursor=pages[0]['next_cursor'], question='changed question'))
            assert not changed['ok']
    assert build_research_context(c['entry'], view='card')['view'] == 'card'


def test_partial_source_scope_retains_coverage_gaps_in_paged_projection(graph_case):
    c = graph_case
    config = json.loads(c['workspace'].read_text())
    config['sources'].append({'source_id': 'missing', 'kind': 'artifact_manifest',
        'location': str(c['entry'].parent / 'missing.json'),
        'identity': {'kind': 'snapshot_sha256', 'value': '0' * 64}})
    c['workspace'].write_text(json.dumps(config))
    pages = collect(lambda cursor: build_research_graph(c['workspace'], checkpoint_ref=c['checkpoint'],
        scope={'root_refs': [c['decision']]}, max_bytes=4096, cursor=cursor))
    graph = assemble_graph_pages(pages)['graph']
    assert not graph['scope_receipt']['counter_search_complete']
    assert not graph['scope_receipt']['reference_coverage_complete']
    assert graph['scope_receipt']['issues']
