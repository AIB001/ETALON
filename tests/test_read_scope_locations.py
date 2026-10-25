"""Frozen-manifest and path-race fixtures, not scientific runs."""
from pathlib import Path

import pytest

from etalon.active.schema import canonical
from etalon.research import evidence, evidence_refs, read_scope
from etalon.research.evidence_refs import EvidenceError, file_identity, load_workspace
from etalon.research.graph_memory import build_research_graph, node_identity
from etalon.research.read_scope import verified_read
from etalon.research.research_context import build_research_context
from test_research_graph_memory import graph_case
from test_verified_read_scope import _same_size_change
from test_research_publication import case


@pytest.fixture
def manifest_case(graph_case, tmp_path):
    c = graph_case
    ws = load_workspace(c['workspace'])
    rows, _ = evidence._source_uncached(ws, ws.source('science'))
    path = tmp_path / 'published.json'
    path.write_text(canonical({'schema': 'etalon-artifact-manifest/1', 'records': [
        {**{k: row[k] for k in ('record_id', 'record_type', 'record')}, 'visibility': 'released'}
        for row in rows.values()]}), encoding='utf-8')
    manifest = ws.manifest
    manifest['sources'] = [{'source_id': 'science', 'kind': 'artifact_manifest',
        'location': str(path), 'identity': {'kind': 'snapshot_sha256', 'value': file_identity(path)['sha256']}}]
    c['workspace'].write_text(canonical(manifest), encoding='utf-8')
    return c, path


def test_frozen_manifest_reuse_preserves_counters_and_policy(manifest_case, monkeypatch):
    c, _ = manifest_case
    decoded = []
    original = evidence._source_uncached
    def decode(ws, source):
        decoded.append(source['source_id'])
        return original(ws, source)
    monkeypatch.setattr(evidence, '_source_uncached', decode)
    with verified_read() as first:
        a = build_research_context(c['entry'], view='summary')
        assert build_research_context(c['entry'], view='summary') == a
    assert decoded == ['science']
    with verified_read(first.snapshot()):
        wide = build_research_graph(c['workspace'], checkpoint_ref=c['checkpoint'])
        narrow = build_research_graph(c['workspace'], checkpoint_ref=c['checkpoint'],
            policy={'record_rules': [{'source_id': 'science', 'record_id': c['unrelated']['record_id'],
                                     'allowed_purposes': []}]})
    assert any(edge['kind'] == 'refutes' for edge in wide['edges'])
    assert node_identity(c['unrelated']) not in {n['node_id'] for n in narrow['nodes']}
    assert c['unrelated']['record_id'] not in canonical(narrow)


@pytest.mark.parametrize('damage', ['source', 'projection'])
def test_manifest_and_projected_bytes_rechecked_before_return(manifest_case, damage):
    c, path = manifest_case
    if damage == 'projection':
        path = Path(c['journal'].get(c['claim']['record_id'])['path'])
    with pytest.raises(EvidenceError):
        with verified_read():
            build_research_context(c['entry'], view='summary')
            _same_size_change(path)


def test_cached_path_is_re_resolved_before_return(tmp_path, monkeypatch):
    # Deterministically model a junction retarget even without Windows link privilege.
    old, new = tmp_path/'old', tmp_path/'new'
    old.mkdir(); new.mkdir()
    link = tmp_path/'link'
    target = [old]
    resolve = Path.resolve
    calls = []
    def redirected(path, *args, **kwargs):
        if path == link:
            calls.append(True)
            return target[0]
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', redirected)
    with pytest.raises(EvidenceError, match='location changed'):
        with verified_read():
            assert evidence_refs._local(tmp_path, 'link', confined=True) == old
            assert evidence_refs._local(tmp_path, 'link', confined=True) == old
            assert len(calls) == 1
            target[0] = new
    assert len(calls) == 2
    assert read_scope.CURRENT.get() is None
    assert evidence_refs._local(tmp_path, 'link', confined=True) == new


def test_path_cache_never_grants_unconfined_access(tmp_path):
    outside = tmp_path.parent / 'outside'
    with verified_read():
        assert evidence_refs._local(tmp_path, str(outside)) == outside
        with pytest.raises(EvidenceError, match='escapes'):
            evidence_refs._local(tmp_path, str(outside), confined=True)


def test_full_path_cache_falls_back_to_normal_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(read_scope, 'MAX_CACHE_BYTES', 0)
    with verified_read() as scope:
        assert evidence_refs._local(tmp_path, 'ordinary') == tmp_path/'ordinary'
        assert not scope.locations


def test_card_reuse_keeps_manifest_independent_of_host_conditions(case, tmp_path):
    from test_meta_context import workset
    from etalon.research.meta_context import publish_task_workset
    from etalon.research.publication import resume_publication
    chosen = workset(case)
    intent = publish_task_workset(case['entry'], chosen, parent_entry=case['parent'],
        operation_id='activate-fixture', directory=tmp_path/'activation')
    resume_publication(intent['intent_path'])
    neutral = build_research_context(case['entry'], view='card')
    changed = build_research_context(case['entry'], view='card', conditions={'target': 'other'})
    assert neutral['task_workset']['status'] == 'active'
    assert changed['task_workset']['status'] == 'review_required'
    assert changed['task_workset']['digest'] == neutral['task_workset']['digest']
    # Host-specific applicability must not contaminate the pinned manifest.
    assert neutral['manifest']['digest'] == changed['manifest']['digest']
