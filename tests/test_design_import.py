"""Consumer checks over an immutable, producer-exported structural fixture; no generation/MD."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.adapters import MOLECULAR_REPRESENTATION
from etalon.active.schema import canonical, digest
from etalon.data.design import SCHEMA_SHA256, design_index, import_bundle, read_bundle, verify_import
from etalon.runtime import operations

FIXTURE = Path(__file__).parent / 'fixtures/prism_design_bundle_v1'


def bundle_copy(tmp_path):
    root = tmp_path / 'bundle'
    shutil.copytree(FIXTURE, root)
    return root / 'bundle.json'


def save(path, bundle):
    bundle['bundle_id'] = 'bundle-' + digest({k: v for k, v in bundle.items() if k != 'bundle_id'})
    path.write_text(canonical(bundle), encoding='utf-8')


def campaign(tmp_path, *, representation=MOLECULAR_REPRESENTATION):
    store = CampaignStore(tmp_path / 'campaign.sqlite')
    endpoint = Endpoint('unmeasured', 'fixture', 'engineering_endpoint', 'count', 'fixture/1', 1,
                        requires_handoff=True)
    store.configure(CampaignSpec(endpoint.id, 20, 'wall_second', representation), [endpoint])
    return store


def test_producer_fixture_pool_dedup_preserves_poses_rejections_and_provenance(tmp_path):
    store = campaign(tmp_path)
    result = import_bundle(store, FIXTURE / 'bundle.json')
    assert result['schema_sha256'] == SCHEMA_SHA256
    assert result['added_candidates'] == 1 and result['indexed_slots'] == 4
    index = design_index(store)
    assert index['counts']['molecule'] == index['counts']['state'] == 1
    assert index['counts']['pose'] == 2 and index['counts']['occurrence'] == 4
    assert store.observations() == [] and store.balance()['spent'] == 0
    assert all(not c.handoff for c in store.candidates().values())
    events = store.events()
    repeated = import_bundle(store, FIXTURE / 'bundle.json')
    assert not repeated['imported'] and repeated['added_candidates'] == 0
    assert store.events() == events
    verify_import(store, FIXTURE / 'bundle.json')


def test_index_only_does_not_manufacture_features_or_change_representation(tmp_path):
    store = campaign(tmp_path, representation='existing-other-representation')
    with pytest.raises(ValueError, match='representation'):
        import_bundle(store, FIXTURE / 'bundle.json')
    result = import_bundle(store, FIXTURE / 'bundle.json', mode='index_only')
    assert result['eligible_molecules'] == 1 and result['pool_candidate_ids'] == []
    assert not store.candidates() and not store.observations()
    assert design_index(store)['counts']['pose'] == 2


def test_index_only_can_later_promote_without_rewriting_source_identity(tmp_path):
    store = campaign(tmp_path)
    import_bundle(store, FIXTURE / 'bundle.json', mode='index_only')
    before = {k: v for k, v in design_index(store)['records'].items() if k.startswith('occurrence:')}
    assert not store.candidates()
    result = import_bundle(store, FIXTURE / 'bundle.json', mode='pool')
    after = {k: v for k, v in design_index(store)['records'].items() if k.startswith('occurrence:')}
    assert before == after and result['added_candidates'] == 1
    assert all('pool_candidate_id' not in value for value in after.values())
    verify_import(store, FIXTURE / 'bundle.json', mode='index_only')
    verify_import(store, FIXTURE / 'bundle.json', mode='pool')


def test_two_fixture_origins_with_same_slot_names_keep_distinct_actions(tmp_path):
    from etalon.boundary.prism_v1 import file_identity
    path = bundle_copy(tmp_path)
    first = read_bundle(path)['bundle']
    second = deepcopy(first)
    source = second['source']
    result_path = path.parent / source['results']['path']
    result = json.loads(result_path.read_text()); result['summary']['seed'] += 1
    result_path.write_text(canonical(result))
    source['results'].update(file_identity(result_path)); source['seed'] += 1
    preimage = source['identity_derivation']['action_preimage']
    preimage.update(seed=source['seed'], results_sha256=source['results']['sha256'])
    second['generation_action_id'] = 'generation-' + digest(preimage)
    remap = {row['candidate_id']: 'candidate-' + digest({'generation_action_id': second['generation_action_id'],
                                                        'slot': row['slot']}) for row in second['candidates']}
    for row in second['candidates']:
        row['candidate_id'] = remap[row['candidate_id']]
        if row['duplicate_of']: row['duplicate_of'] = remap[row['duplicate_of']]
    save(path, second)
    store = campaign(tmp_path)
    import_bundle(store, FIXTURE / 'bundle.json')
    import_bundle(store, path)
    index = design_index(store)
    assert len(store.candidates()) == 1 and index['counts']['generation'] == 2
    assert index['counts']['pose'] == 2 and index['counts']['occurrence'] == 8
    assert {r['source_slot_id'] for r in first['candidates']} == {r['source_slot_id'] for r in second['candidates']}


@pytest.mark.parametrize('mutation', ['unknown', 'escape', 'corrupt', 'unknown_eligible', 'identity',
                                      'units', 'transformation', 'count', 'label', 'legacy'])
def test_strict_bundle_negatives_leave_journal_unchanged(tmp_path, mutation):
    path = bundle_copy(tmp_path)
    bundle = json.loads(path.read_text())
    row = bundle['candidates'][0]
    if mutation == 'unknown': bundle['future_semantics'] = True
    if mutation == 'escape': row['raw']['path'] = '../outside.sdf'
    if mutation == 'corrupt': (path.parent / row['raw']['path']).write_bytes(b'corrupt fixture')
    if mutation == 'unknown_eligible': row['validation']['checks'][0]['status'] = 'not_evaluated'
    if mutation == 'identity': row['identity']['pose_id'] = 'pose-wrong'
    if mutation == 'units': bundle['receptor']['length_unit'] = 'nm'
    if mutation == 'transformation': row['transformations'][0]['target'] = row['raw']
    if mutation == 'count': bundle['counts']['requested'] += 1
    if mutation == 'label': bundle['observations'] = [{'value': 0}]
    if mutation == 'legacy': bundle = {'manifest': [], 'status': 'COMPLETED'}
    save(path, bundle)
    store = campaign(tmp_path)
    events = store.events()
    with pytest.raises(ValueError): import_bundle(store, path)
    assert store.events() == events and not store.candidates()


def test_import_transaction_rolls_back_identity_and_candidates_together(tmp_path, monkeypatch):
    store = campaign(tmp_path)
    original = store._event
    def fail(db, kind, value):
        if kind == 'design_bundle_imported': raise OSError('fixture journal write failure')
        return original(db, kind, value)
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(OSError): import_bundle(store, FIXTURE / 'bundle.json')
    assert not store.candidates() and not design_index(store)['records']


def test_runtime_operation_freezes_refs_and_verifies_import_receipt(tmp_path):
    store = campaign(tmp_path)
    class Context:
        root = tmp_path / 'operation'
    params = {'database': str(store.path), 'bundle_path': str((FIXTURE / 'bundle.json').resolve())}
    preflight = operations.preflight('design.import', params, {})
    assert len(preflight['inputs']) == 27
    result = operations.execute('design.import', params, Context())
    assert operations.verify('design.import', params, result, workspace=Context.root)['indexed_slots'] == 4
    assert operations.accounting('design.import', result, {}) == ({}, {})


def test_public_cli_reads_portable_fixture_and_diagnostics(tmp_path):
    store = campaign(tmp_path, representation='index-only-test')
    command = [sys.executable, '-m', 'etalon', 'design', 'import', '--bundle', str(FIXTURE / 'bundle.json'),
               '--database', str(store.path), '--mode', 'index_only']
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['indexed_slots'] == 4
    assert len(store.candidates()) == 0
