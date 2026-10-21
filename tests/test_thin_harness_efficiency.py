"""V checks for optional local discovery and durable read-only batching."""
import hashlib
import json
from pathlib import Path

from etalon import skills
from etalon.runtime import api
from test_local_sdk_batch import build_batch_fixture, example


def test_v_scoped_catalog_never_resolves_unselected_bundles_and_roundtrips(monkeypatch):
    real_root = skills.bundle_root
    seen = []
    def local_only(slug):
        seen.append(slug)
        assert slug == 'campaign', 'unselected bundle accessed'
        return real_root(slug)
    monkeypatch.setattr(skills, 'bundle_root', local_only)
    compact = skills.catalog(detail='summary', skills=['campaign'])
    assert seen == ['campaign']
    row = compact['skills'][0]
    assert 'files' not in row and row['entrypoint'] == 'SKILL.md'
    full = skills.catalog(detail='full', skills=['campaign'])
    assert row == {k: v for k, v in full['skills'][0].items() if k != 'files'}
    read = skills.read_member('campaign', row['entrypoint'])
    assert read['manifest_sha256'] == row['manifest_sha256']
    assert hashlib.sha256(read['content'].encode('utf8')).hexdigest() == read['sha256']
    seen.clear()
    assert skills.catalog(detail='summary', skills=[])['skills'] == []
    assert seen == []


def test_v_default_catalog_legacy_shape_and_selected_all_bytes_agree():
    old = skills.catalog()
    explicit = skills.catalog(detail='full', skills=list(skills.WORKFLOWS))
    assert set(old) == {'schema', 'skills', 'catalog_sha256', 'usage_method', 'load_evidence'}
    assert {k: explicit[k] for k in old} == old
    names = {row['slug'] for row in old['skills']}
    assert {'campaign', 'sweep', 'gate-calibration', 'generation-planning',
            'monitoring', 'install'} <= names
    assert names == set(skills.WORKFLOWS)
    assert all(row['files'] for row in old['skills'])


def test_v_external_revision_remains_directly_readable_and_original_snapshot_immutable(tmp_path, monkeypatch):
    def no_builtin(*args, **kwargs):
        raise AssertionError('external bundle needs no built-in lookup')
    monkeypatch.setattr(skills, 'bundle_root', no_builtin)
    root = tmp_path/'external'
    root.mkdir()
    (root/'SKILL.md').write_text('---\nname: local-choice\ndescription: task-local alternative\n---\nRead only if relevant.\n', encoding='utf8')
    member = root/'local.py'
    member.write_bytes(b'VALUE = 1\n')
    ref = {'root': str(root)}
    old, _ = skills.snapshot('independent-choice', bundle_ref=ref)
    saved = skills.persist_snapshot('independent-choice', tmp_path/'saved', bundle_ref=ref)
    member.write_bytes(b'VALUE = 2\n')
    new, _ = skills.snapshot('independent-choice', bundle_ref=ref)
    assert new['manifest_sha256'] != old['manifest_sha256']
    read = skills.read_member('independent-choice', 'local.py', bundle_ref={**ref, 'manifest_sha256': new['manifest_sha256']})
    assert read['content'] == 'VALUE = 2\n'
    assert skills.verify_snapshot(saved) == old


def test_v_batch_separate_workspaces_do_not_alias_and_failed_read_cannot_erase_unknown(tmp_path, monkeypatch):
    left, a = build_batch_fixture(tmp_path/'left')
    right, b = build_batch_fixture(tmp_path/'right')
    before = [{key: rt.get(key) for key in handles} for rt, handles in ((left,a),(right,b))]
    # No capability/skill call is needed to read already-known legal handles.
    def forbidden(*args, **kwargs):
        raise AssertionError('direct result read attempted skill discovery')
    monkeypatch.setattr(skills, 'catalog', forbidden)
    invalid = {**a['unknown'], 'plan_id': 'incorrect'}
    handles = [a['paid-failure'], b['paid-failure'], invalid, a['unknown'], a['paid-failure']]
    rows = example.read_action_results(handles, tmp_path/'snapshots')
    assert rows[0]['same_execution_as_item'] is None
    assert rows[1]['same_execution_as_item'] is None
    assert rows[4]['same_execution_as_item'] == 1
    assert rows[2]['read_status'] == 'failed' and rows[2]['execution_state'] == 'unknown'
    assert rows[2]['accounting_refs'] is None
    assert rows[3]['execution_state'] == 'unresolved'
    costs = rows[3]['accounting_refs'][0]['costs']['fixture_credit']
    assert costs['spent'] is None and costs['reserved'] == 2
    assert rows[0]['accounting_refs'][0]['costs']['fixture_credit']['spent'] == 1.25
    for handle, row in zip(handles, rows, strict=True):
        ref = row['full_result']; raw = Path(ref['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref['sha256']
        if row['read_status'] == 'ok':
            original = json.loads(raw)['result']
            assert original == api.read_action_result(handle)
            assert row['native_status'] == original['native_status']
            assert row['verification'] == original['verification']
            assert row['recovery'] == original['recovery']
    assert [{key: rt.get(key) for key in handles} for rt, handles in ((left,a),(right,b))] == before
