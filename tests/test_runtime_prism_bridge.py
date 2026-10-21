"""Registry/recovery contract fixtures; no PRISM simulation is started."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from etalon.active import ActiveCampaign, CampaignSpec, CampaignStore, Candidate, Endpoint
from etalon.active.runner import ExecutionPending
from etalon.active.schema import Action
from etalon.boundary.prism_v1 import CLITransport, TransportError
from etalon.runtime import operations
from etalon.runtime.executors import build_executor, prepare_executor, register_executor, registered_executors
from test_prism_v1 import setup, FixtureTransport


def prepared(tmp_path, monkeypatch):
    request, _, _, _, candidate, endpoint = setup(tmp_path)
    source = tmp_path / 'source'
    (source / 'prism/bridge').mkdir(parents=True)
    (source / 'prism/__init__.py').write_text('"""Contract fixture only."""')
    (source / 'prism/bridge/__main__.py').write_text('raise RuntimeError("fixture never simulates")')
    configuration = {'request_template': request, 'transport': {'python': sys.executable,
        'cwd': str(source), 'request_root': str(tmp_path / 'requests')}, 'job_root': str(tmp_path / 'jobs'),
        'receptor_path': request['inputs']['protein']['path'], 'seeds': [11, 22], 'wait_seconds': 1}
    record = prepare_executor('prism_bridge_v1', configuration,
        {'id': endpoint.id, 'target': 'fixture', 'cost': 10})
    store = CampaignStore(tmp_path / 'runtime-campaign.sqlite')
    store.configure(CampaignSpec(endpoint.id, 100, 'wall_second', 'fixture', batch_size=1),
                    [Endpoint(**record['endpoint'])])
    store.add_candidates([candidate])
    fixture = FixtureTransport(tmp_path)
    def call(self, operation, **kwargs):
        if operation == 'submit':
            fixture.stage_request(json.loads(Path(kwargs['request']).read_text()))
        return fixture.call(operation, **kwargs)
    monkeypatch.setattr(CLITransport, 'call', call)
    return record, store, fixture, source


def test_registry_build_dispatch_and_duplicate_are_idempotent(tmp_path, monkeypatch):
    record, store, transport, _ = prepared(tmp_path, monkeypatch)
    assert register_executor(store.path, record, 'explicit contract fixture')['registered']
    assert not register_executor(store.path, record, 'repeat registration')['registered']
    executor, receptor = build_executor(store.path, tmp_path / 'work')
    ActiveCampaign(store, executor, receptor_path=receptor).run_round()
    before = store.balance()
    executor, _ = build_executor(store.path, tmp_path / 'restart')
    row = store.actions()[0]
    action = Action(**{k: row[k] for k in Action.__dataclass_fields__})
    endpoint = Endpoint(**record['endpoint'])
    result = executor(action, store.candidates()[action.candidate_id], endpoint, None)
    assert result.value == 2 and store.balance() == before
    assert transport.calls.count('submit') == 1
    assert len(store.observations()) == 1 and before['spent'] == 3.5


def test_frozen_source_input_and_configuration_drift_refused(tmp_path, monkeypatch):
    record, store, _, source = prepared(tmp_path, monkeypatch)
    register_executor(store.path, record, 'fixture')
    (source / 'prism/bridge/__main__.py').write_text('changed fixture source')
    with pytest.raises(ValueError, match='changed'):
        build_executor(store.path, tmp_path / 'work')
    altered = deepcopy(record)
    altered['configuration']['seeds'][0] = 33
    with pytest.raises(ValueError, match='identity'):
        register_executor(store.path, altered, 'drift')


def test_new_kind_does_not_broaden_benzene_capability(tmp_path, monkeypatch):
    record, store, transport, _ = prepared(tmp_path, monkeypatch)
    register_executor(store.path, record, 'fixture')
    executor, _ = build_executor(store.path, tmp_path / 'work')
    endpoint = Endpoint(**record['endpoint'])
    candidate = Candidate('new-designed-molecule', 'CCO', (1.,))
    result = executor(Action('not-dispatched', 1, candidate.id, endpoint.id, 0, 10), candidate, endpoint, None)
    assert result.status == 'blocked' and result.cost == 0 and 'UNSUPPORTED' in result.provenance['error']
    assert not transport.calls


def test_runtime_recovery_reads_existing_result_without_submit_or_resume(tmp_path, monkeypatch):
    record, store, transport, _ = prepared(tmp_path, monkeypatch)
    register_executor(store.path, record, 'fixture')
    class Context:
        root = tmp_path / 'runtime'
        preflight = {'registry_hash': __import__('etalon.active.schema', fromlist=['digest']).digest(registered_executors(store.path))}
        def __init__(self): self.values = {}
        def receipt(self, key, value): self.values[key] = value
        def receipts(self): return self.values
        def checkpoint(self): pass
        def process_observer(self, pid): pass
    ctx = Context()
    original = transport.call
    def disconnect_after_submit(operation, **kwargs):
        if operation == 'status': raise TransportError('fixture connection dropped after acknowledged submit')
        return original(operation, **kwargs)
    transport.call = disconnect_after_submit
    params = {'database': str(store.path), 'max_rounds': 1}
    with pytest.raises(ExecutionPending): operations.execute('active.run', params, ctx)
    assert store.status()['pending'] and store.balance()['reserved'] == 10
    transport.call = original
    recovered = operations.recover('active.run', params, ctx)
    assert recovered['new_admitted'] == 1 and recovered['cost'] == 3.5
    assert transport.calls.count('submit') == 1 and 'resume' not in transport.calls
    again = operations.recover('active.run', params, ctx)
    assert again['cost'] == 3.5 and len(store.observations()) == 1
