"""Shared-seam engineering fixtures, not native adoption or scientific evidence."""
import asyncio
import base64
from dataclasses import replace
import json
from pathlib import Path

import pytest

from etalon import host, skills
from etalon.active.schema import digest
from etalon.boundary.prism_v1 import file_identity
from etalon.research import MethodUse, MethodVersion, ResearchJournal
from etalon.research.evidence_refs import EvidenceResolver
from etalon.research.journal import audit_json
from test_research_journal import setup


def workspace(root, *, original, local, source=None, maps=()):
    source = source or {'source_id': 'released', 'kind': 'artifact_manifest',
                        'location': 'archive.json',
                        'identity': {'kind': 'snapshot_sha256', 'value': 'a' * 64}}
    payload = {'schema': 'etalon-evidence-workspace/1', 'workspace_id': 'fixture',
               'sources': [source], 'location_map': [
                   {'source_id': source['source_id'], 'original': original,
                    'location': local.relative_to(root).as_posix(), **file_identity(local)}, *maps]}
    path = root / 'workspace.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


def test_foreign_artifact_is_consumed_without_rewriting_identity_or_cost(tmp_path):
    store, _, artifact = setup(tmp_path)
    historical = replace(artifact, path='/old/posix/evidence.json')
    manifest = workspace(tmp_path, original=historical.path, local=Path(artifact.path))
    journal = ResearchJournal(store, resolver=EvidenceResolver(manifest, 'released'))
    before = store.balance()
    ids = audit_json(journal, historical, task_id='fixture-relocated', selectors={'metric': ('metric',)})
    assert journal.get(historical.id) == json.loads(json.dumps(historical.as_dict()))
    assert journal.get(ids[1])['values']['metric'] == 3
    events = store.events()
    assert audit_json(journal, historical, task_id='fixture-relocated', selectors={'metric': ('metric',)}) == ids
    assert store.events() == events and store.balance() == before
    Path(artifact.path).write_text('wrong bytes', encoding='utf-8')
    with pytest.raises(Exception, match='hash|mismatch'):
        journal.artifact_path(historical)


def test_mapped_skill_snapshot_checks_semantic_and_member_bytes(tmp_path):
    saved = skills.persist_snapshot('research', tmp_path / 'relocated')
    original = '/old/posix/skill/etalon-bundle-manifest.json'
    manifest = workspace(tmp_path, original=original, local=Path(saved['manifest_path']))
    resolver = EvidenceResolver(manifest, 'released')
    historical = {**saved, 'manifest_path': original}
    assert skills.verify_snapshot(historical, resolver=resolver)['manifest_sha256'] == saved['manifest_sha256']
    assert historical['manifest_path'] == original
    store, _, _ = setup(tmp_path)
    journal = ResearchJournal(store, resolver=resolver)
    method = MethodVersion('relocated fixture', ('scientific_method',), historical,
                           verification_basis='content_verified')
    assert journal.record(method) == method.id
    assert journal.get(method.id)['content_ref'] == historical
    (tmp_path / 'relocated/bundle/SKILL.md').write_text('corrupt', encoding='utf-8')
    with pytest.raises(ValueError):
        skills.verify_snapshot(historical, resolver=resolver)


def test_subject_scope_maps_only_to_the_verified_same_journal(tmp_path):
    store, _, _ = setup(tmp_path)
    original = '/old/posix/campaign.sqlite'
    manifest = workspace(tmp_path, original=original, local=store.path, source={
        'source_id': 'released', 'kind': 'journal', 'location': store.path.name,
        'identity': {'kind': 'snapshot_sha256', 'value': file_identity(store.path)['sha256']}})
    resolver = EvidenceResolver(manifest, 'released')
    journal = ResearchJournal(store, resolver=resolver)
    use = MethodUse(({'kind': 'task', 'id': 'fixture', 'scope': original},), reason='skip')
    assert journal.record(use) == use.id
    assert journal.get(use.id)['subject_refs'][0]['scope'] == original
    # The former immutable source pin is now stale after this explicit append.
    with pytest.raises(ValueError, match='hash'):
        resolver.resolve_source(original)
    with pytest.raises(ValueError):
        resolver.resolve_source('/unrelated/posix/campaign.sqlite')


def test_mcp_evidence_reads_same_ref_and_original_bytes_as_sdk(tmp_path):
    from etalon.mcp.server import register_tools, costs
    from etalon.research.evidence import query_evidence
    blob = tmp_path / 'released.txt'
    blob.write_bytes(b'released fixture original\r\n')
    record = {'path': str(blob), **file_identity(blob), 'role': 'diagnostic',
              'source_kind': 'fixture', 'bindings': {}}
    archive = tmp_path / 'archive.json'
    archive.write_text(json.dumps({'schema': 'etalon-artifact-manifest/1', 'records': [
        {'record_id': 'artifact-' + digest(record), 'record_type': 'artifact',
         'record': record, 'visibility': 'released'}]}), encoding='utf-8')
    manifest = workspace(tmp_path, original=str(blob), local=blob, source={
        'source_id': 'released', 'kind': 'artifact_manifest', 'location': 'archive.json',
        'identity': {'kind': 'snapshot_sha256', 'value': file_identity(archive)['sha256']}})
    found = {}
    class Collector:
        def tool(self):
            def save(function):
                found[function.__name__] = function
                return function
            return save
    register_tools(Collector())
    query = json.loads(found['etalon_evidence_query'](str(manifest)))
    assert query['items'] == query_evidence(manifest)['items']
    result = json.loads(found['etalon_evidence_read'](str(manifest), query['items'][0]['ref'], view='artifact'))
    assert base64.b64decode(result['fragment']['content']) == blob.read_bytes()
    assert result['verification']['artifact'] == 'verified'
    assert costs()['etalon_evidence_read'] == costs()['etalon_evidence_query'] == 'free'
    assert not json.loads(found['etalon_evidence_query'](str(manifest), filters={'unknown': []}))['ok']


def test_host_export_guides_are_separate_verified_files(tmp_path):
    target = tmp_path / 'export'
    exported = host.export(target)
    guides = {p.name for p in skills.ROLE_PATH.with_name('host-guides').glob('*.md')}
    assert guides
    assert all('host-guides/' + name in exported['files'] for name in guides)
    assert 'HOST_SETUP.md' in exported['files']
    assert host.verify(target)['matches_current_source']
    (target / 'host-guides/unregistered.md').write_text('extra', encoding='utf-8')
    assert host.verify(target)['unexpected'] == ['host-guides/unregistered.md']


def test_mcp_host_resource_preserves_crlf_bytes(tmp_path, monkeypatch):
    from etalon.mcp import server
    role = tmp_path / 'HOST.md'
    role.write_bytes(b'fixture\r\nrole\r\n')
    monkeypatch.setattr(server, 'ROLE_PATH', role)
    mcp = server.build()
    async def read():
        result = await mcp.read_resource('etalon://host')
        return next(iter(result)).content
    assert asyncio.run(read()).encode() == role.read_bytes()
