"""Validation-driver contract fixtures; all bridge responses are explicitly synthetic."""
import importlib.util
import json
from pathlib import Path

import pytest

from etalon.active import ActiveCampaign
from etalon.active.schema import canonical
from etalon.runtime.executors import build_executor, register_executor
from test_runtime_prism_bridge import prepared


spec = importlib.util.spec_from_file_location('round2_validation',
    Path(__file__).parents[1] / 'examples/validate_round2.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
BUNDLE = Path(__file__).parent / 'fixtures/prism_design_bundle_v1/bundle.json'


def historical_fixture(tmp_path, monkeypatch):
    record, store, transport, _ = prepared(tmp_path, monkeypatch)
    register_executor(store.path, record, 'explicit synthetic validation fixture')
    executor, receptor = build_executor(store.path, tmp_path / 'initial')
    ActiveCampaign(store, executor, receptor_path=receptor).run_round()
    with store.connection(write=True) as db:
        db.execute("DELETE FROM metadata WHERE key LIKE 'runtime:bridge-authorization:%'")
    return record, store, transport


def test_driver_uses_registered_runtime_recovery_and_public_pool_cli(tmp_path, monkeypatch):
    record, store, transport = historical_fixture(tmp_path, monkeypatch)
    source_before = driver.file_identity(store.path)
    recovery_calls = []
    original = driver.recover_bridge_action
    def recover(database, action):
        recovery_calls.append(action.id)
        return original(database, action)
    monkeypatch.setattr(driver, 'recover_bridge_action', recover)
    result = driver.run(store.path, record['configuration'], BUNDLE, tmp_path / 'validation')
    assert driver.file_identity(store.path) == source_before
    assert len(recovery_calls) == 2
    assert [c['operation'] for c in result['read_only_bridge_calls']] == ['status', 'result'] * 2
    assert transport.calls.count('submit') == 1 and 'resume' not in transport.calls
    assert result['historical_authorization_backfill'][0]['inserted']
    assert result['public_pool_cli']['candidate_count'] == 1
    assert result['public_pool_cli']['feature_dimensions'] == [1033]
    with store.connection() as db:
        assert not db.execute("SELECT 1 FROM metadata WHERE key LIKE 'runtime:bridge-authorization:%'").fetchone()


@pytest.mark.parametrize('corruption', ['missing', 'record_digest', 'conflicting_runtime'])
def test_backfill_refuses_unproven_or_conflicting_authorization(tmp_path, monkeypatch, corruption):
    _, store, _ = historical_fixture(tmp_path, monkeypatch)
    observation = store.observations()[0]
    with store.connection(write=True) as db:
        if corruption == 'conflicting_runtime':
            db.execute('INSERT INTO metadata VALUES (?,?)',
                       ('runtime:bridge-authorization:' + observation['action_id'], canonical({'wrong': True})))
        else:
            result = observation['result']
            if corruption == 'missing': result['provenance'].pop('authorization')
            else: result['provenance']['authorization']['record_sha256'] = '0' * 64
            db.execute('UPDATE observations SET body=? WHERE action_id=?',
                       (canonical(result), observation['action_id']))
    with pytest.raises(ValueError, match='authorization'):
        driver.copy_historical_authorizations(store)
