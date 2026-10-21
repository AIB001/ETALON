"""Explicit contract fixtures only; these tests do not run or validate a simulation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from etalon.active import ActiveCampaign, CampaignSpec, CampaignStore, Candidate, Endpoint, Evaluation
from etalon.active.prism import PrismV1Executor
from etalon.active.runner import ExecutionPending
from etalon.active.schema import digest
from etalon.authority.grant import NotAuthorized, authorize
from etalon.boundary.prism_v1 import (
    CLITransport, ContractError, ENDPOINT, IDENTITIES, RemoteError, SCHEMA, TransportError,
    execution_cost, file_identity, protocol_sha256, validate_request, validate_result,
)


def request_fixture(tmp_path):
    inputs = {}
    for name in ('protein', 'ligand'):
        path = tmp_path / name
        path.write_text('CONTRACT FIXTURE NOT SCIENTIFIC INPUT ' + name)
        inputs[name] = {'path': str(path), 'sha256': file_identity(path)['sha256']}
    return {**{key: key + '-fixture' for key in IDENTITIES}, 'schema_version': SCHEMA,
            'operation': 'new_run', 'replicate': 0, 'seed': 1, 'inputs': inputs,
            'resources': {'threads': 1, 'wall_time_limit_seconds': 300}, 'endpoint': ENDPOINT,
            'workflow_spec': {'md_steps': 1000, 'build': {
                'forcefield': 'amber14sb', 'ligand_forcefield': 'gaff2', 'water_model': 'tip3p',
                'protonation': 'propka', 'ligand_charge': 0, 'production_ns': .001,
                'nvt_ps': 1, 'npt_ps': 1, 'dt': .001, 'em_steps': 20000, 'em_tolerance': 200,
                'temperature': 310, 'ph': 7, 'salt_concentration': .15, 'box_distance': 1,
                'box_shape': 'dodecahedron', 'pressure': 1, 'nterm_met': 'keep'}}}


def result_fixture(request, tmp_path, value=2.):
    artifacts = {'inputs': [{**a, 'bytes': Path(a['path']).stat().st_size}
                            for a in request['inputs'].values()], 'outputs': []}
    for name in ('frame_by_frame_contacts.csv', 'contact_proportions.csv'):
        path = tmp_path / name
        path.write_text('explicit contract fixture; not computed contacts')
        artifacts['outputs'].append({'path': str(path), **file_identity(path)})
    return {**{k: request[k] for k in ('schema_version', *IDENTITIES, 'seed', 'replicate')},
            'request_sha256': digest(request), 'protocol_sha256': protocol_sha256(request),
            'job_id': 'job-fixture', 'job_directory': str(tmp_path), 'status': 'succeeded',
            'scope': 'engineering_only', 'execution_id': 'execution-fixture-1',
            'cost': {'wall_seconds': 3.5, 'threads': 1, 'compute_executed': True, 'reused': True,
                     'executions': [{'execution_id': 'execution-fixture-1', 'started': 10.,
                                     'finished': 13.5, 'wall_seconds': 3.5, 'operation': 'new_run'}]},
            'observation': {'name': 'mean_residue_contacts', 'units': 'count', 'value': value,
                            'uncertainty': None, 'n_frames': 11, 'time_range_ps': [0, 1],
                            'definition': {'contact_cutoff_nm': .4, 'comparison': '<',
                                'pbc': 'periodic minimum image', 'ligand_selection': 'fixture LIG non-H',
                                'protein_selection': 'fixture protein non-H', 'residue_identity': 'fixture',
                                'averaging': 'all production frames'},
                            'checks': {k: True for k in ('manifest_verified', 'frame_coverage',
                                'aggregate_agreement', 'zero_contact_frames_included', 'production_grid_verified')}},
            'artifacts': artifacts, 'provenance': {'context': {'sources': {'fixture': 'sha'},
                'software': ['fixture'], 'tools': {'gmx': 'fixture'}, 'executable': 'fixture'}}}


class FixtureTransport:
    python, cwd, host, request_root = 'fixture-python', 'fixture-cwd', None, 'fixture-requests'

    def __init__(self, root):
        self.root, self.requests, self.result, self.calls = root, {}, None, []
        self.results = {}
        self.disconnect = False

    def verify(self, artifact):
        actual = file_identity(artifact['path'])
        if actual['sha256'] != artifact['sha256'] or artifact.get('bytes', actual['bytes']) != actual['bytes']:
            raise ContractError('artifact identity differs')

    def stage_request(self, request):
        self.request = request
        return 'fixture-request.json'

    def call(self, operation, **kwargs):
        self.calls.append(operation)
        if self.disconnect:
            raise TransportError('fixture disconnected')
        if operation == 'submit':
            key = self.request['request_id']
            if key in self.requests and self.requests[key] != self.request:
                raise RemoteError({'code': 'REQUEST_CONFLICT', 'message': 'fixture conflict', 'retryable': False})
            if key not in self.requests:
                self.requests[key] = deepcopy(self.request)
                root = self.root / key
                root.mkdir()
                self.result = result_fixture(self.request, root)
                self.results[str(root)] = self.result
            else:
                self.result = self.results[str(self.root / key)]
            return deepcopy(self.result)
        return deepcopy(self.results.get(kwargs.get('job'), self.result))


def setup(tmp_path):
    request = request_fixture(tmp_path)
    handoff = {'parent_id': request['candidate_id'], 'parent_smiles': 'c1ccccc1',
        'stereo_smiles': 'c1ccccc1', 'status': 'OK', 'coordinate_source': 'SUPPLIED_POSE',
        'receptor_id': request['inputs']['protein']['sha256'], 'hydrogens': 'EXPLICIT_ALL',
        'hydrogen_count': 6, 'formal_charge': 0, 'protonation_state_id': 'neutral-benzene-fixture',
        'chemical_state_id': request['chemical_state_id'], 'pose_id': request['pose_id'],
        'ligand_sha256': request['inputs']['ligand']['sha256']}
    endpoint = Endpoint(request['endpoint_id'], 'fixture', 'mean_residue_contacts', 'count',
                        request['protocol_id'], 10, max_replicates=3)
    candidate = Candidate(request['candidate_id'], 'c1ccccc1', (1.,), handoff=handoff)
    store = CampaignStore(tmp_path / 'campaign.sqlite')
    store.configure(CampaignSpec(endpoint.id, 100, 'wall_second', 'fixture', batch_size=1), [endpoint])
    store.add_candidates([candidate])
    transport = FixtureTransport(tmp_path)
    executor = PrismV1Executor(store, transport, request_template=request, job_root=str(tmp_path),
                               receptor_path=request['inputs']['protein']['path'], seeds=[11, 22])
    campaign = ActiveCampaign(store, executor, receptor_path=request['inputs']['protein']['path'])
    return request, store, executor, campaign, candidate, endpoint


def test_valid_request_and_protocol_independent_of_seed_paths(tmp_path):
    request = request_fixture(tmp_path)
    validate_request(request)
    changed = deepcopy(request)
    changed.update(seed=99, replicate=1, action_id='other')
    changed['inputs']['protein']['path'] = '/different/host/path'
    assert digest(changed) != digest(request)
    assert protocol_sha256(changed) == protocol_sha256(request)


def test_fixture_submission_is_idempotent_and_conflicts_are_explicit(tmp_path):
    request = request_fixture(tmp_path)
    transport = FixtureTransport(tmp_path)
    transport.stage_request(request)
    first = transport.call('submit')
    assert transport.call('submit')['job_id'] == first['job_id']
    assert len(transport.requests) == 1
    changed = {**request, 'seed': 2}
    transport.stage_request(changed)
    with pytest.raises(RemoteError, match='REQUEST_CONFLICT'):
        transport.call('submit')


def test_failed_result_has_measured_cost_and_no_observation(tmp_path):
    request = request_fixture(tmp_path)
    result = result_fixture(request, tmp_path)
    result['status'] = 'failed'
    result.pop('observation')
    validate_result(request, result, FixtureTransport(tmp_path))
    assert execution_cost(result) == 3.5
    result['observation'] = {'value': 0}
    with pytest.raises(ContractError, match='failed task'):
        validate_result(request, result, FixtureTransport(tmp_path))


@pytest.mark.parametrize('mutation', ['unknown', 'build_unknown', 'units', 'seed', 'threads', 'duration'])
def test_request_negatives(tmp_path, mutation):
    request = deepcopy(request_fixture(tmp_path))
    if mutation == 'unknown': request['extra'] = True
    if mutation == 'build_unknown': request['workflow_spec']['build']['new'] = 1
    if mutation == 'units': request['endpoint']['units'] = 'kcal/mol'
    if mutation == 'seed': request['seed'] = True
    if mutation == 'threads': request['resources']['threads'] = 3
    if mutation == 'duration': request['workflow_spec']['build']['production_ns'] = .0005
    with pytest.raises(ValueError): validate_request(request)


@pytest.mark.parametrize('mutation', ['identity', 'protocol', 'nonterminal', 'corrupt', 'nonfinite', 'checks', 'units'])
def test_result_negatives(tmp_path, mutation):
    request = request_fixture(tmp_path)
    result = result_fixture(request, tmp_path)
    if mutation == 'identity': result['chemical_state_id'] = 'other'
    if mutation == 'protocol': result['protocol_sha256'] = '0' * 64
    if mutation == 'nonterminal': result['status'] = 'needs_resume'
    if mutation == 'corrupt': Path(result['artifacts']['outputs'][0]['path']).write_text('corrupt')
    if mutation == 'nonfinite': result['observation']['value'] = float('nan')
    if mutation == 'checks': result['observation']['checks']['frame_coverage'] = False
    if mutation == 'units': result['observation']['units'] = 'kcal/mol'
    with pytest.raises(ValueError): validate_result(request, result, FixtureTransport(tmp_path))


def test_submit_admit_repeated_read_charged_once(tmp_path):
    request, store, executor, campaign, candidate, endpoint = setup(tmp_path)
    outcome = campaign.run_round()
    assert outcome['admitted_total'] == 1
    obs = store.observations()[0]
    action = store.actions()[0]
    from etalon.active.schema import Action
    action = Action(**{k: action[k] for k in Action.__dataclass_fields__})
    raw = executor.transport.result
    executor.evaluation(action, raw['request_id'] and obs['result']['provenance']['request'], raw)
    store.resolve(action.id, Evaluation.from_dict(obs['result']), idempotent=True)
    assert store.balance()['spent'] == 3.5
    assert len(store.observations()) == 1
    model, choices, _ = campaign.plan()
    assert model.training_action_ids == [action.id]
    assert choices[0].candidate_id == candidate.id


def test_transport_error_keeps_pending_reservation(tmp_path):
    _, store, executor, campaign, _, _ = setup(tmp_path)
    executor.transport.disconnect = True
    with pytest.raises(ExecutionPending): campaign.run_round()
    assert store.actions()[0]['status'] == 'running'
    assert store.balance()['reserved'] == 10
    assert store.balance()['spent'] == 0
    assert store.observations() == []


def test_corrupt_result_retains_real_cost_without_admitting_label(tmp_path):
    _, store, executor, campaign, _, _ = setup(tmp_path)
    original_call = executor.transport.call
    def call(operation, **kwargs):
        result = original_call(operation, **kwargs)
        if operation == 'result':
            Path(result['artifacts']['outputs'][0]['path']).write_text('damaged contract fixture')
        return result
    executor.transport.call = call
    with pytest.raises(ExecutionPending):
        campaign.run_round()
    assert store.balance()['spent'] == 3.5
    assert store.actions()[0]['status'] == 'running'
    assert store.observations() == []


@pytest.mark.parametrize('malformation', ['job', 'structure'])
def test_post_submit_bad_response_never_becomes_inferred_failed_cost(tmp_path, malformation):
    _, store, executor, campaign, _, _ = setup(tmp_path)
    original_call = executor.transport.call
    def call(operation, **kwargs):
        result = original_call(operation, **kwargs)
        if operation == 'result':
            if malformation == 'job':
                result['job_id'] = 'other-job'
            else:
                result['cost'] = []
        return result
    executor.transport.call = call
    with pytest.raises(ExecutionPending):
        campaign.run_round()
    assert store.actions()[0]['status'] == 'running'
    assert store.balance()['spent'] == 0
    assert store.balance()['reserved'] == 10
    assert store.observations() == []


def test_partial_recovery_costs_are_not_dropped_or_duplicated(tmp_path):
    _, store, executor, campaign, _, endpoint = setup(tmp_path)
    action = store.reserve(store.start_round({}), 'candidate_id-fixture', endpoint.id, {})
    first = {'execution_id': 'first', 'wall_seconds': 7.}
    second = {'execution_id': 'recovery', 'wall_seconds': 5.}
    store.record_execution_costs(action.id, [first])
    store.record_execution_costs(action.id, [first, second])
    store.record_execution_costs(action.id, [first, second])
    assert store.balance()['spent'] == 12
    with pytest.raises(Exception, match='discard'):
        store.resolve(action.id, Evaluation(action.candidate_id, action.endpoint_id, None, 'count', 5, status='failed'))
    store.resolve(action.id, Evaluation(action.candidate_id, action.endpoint_id, None, 'count', 12, status='failed'))
    assert store.balance()['spent'] == 12 and store.balance()['reserved'] == 0
    assert not store.observations()[0]['admitted']


def test_requires_real_spend_authorization_and_receptor_binding(tmp_path):
    _, store, executor, _, candidate, endpoint = setup(tmp_path)
    action = store.reserve(store.start_round({}), candidate.id, endpoint.id, {})
    with pytest.raises(NotAuthorized): executor(action, candidate, endpoint, None)
    Path(executor.receptor_path).write_text('changed receptor')
    assert not authorize([candidate.handoff], receptor_path=executor.receptor_path).grants
    assert executor.transport.calls == []


def test_ssh_quotes_arguments_and_distinguishes_task_error(monkeypatch):
    captured = []
    def run(args, **kwargs):
        captured.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, b'{"status":"failed","error":{"code":"TASK_FAILED"}}', b'')
    monkeypatch.setattr(subprocess, 'run', run)
    transport = CLITransport('/env/python', "/source with ' quotes", '/requests', host='configured-host')
    result = transport.call('status', job='/jobs/$(touch should-not-exist)')
    assert result['status'] == 'failed'
    command = captured[0][0][-1]
    assert "'/jobs/$(touch should-not-exist)'" in command
    assert captured[0][1].get('shell', False) is False


def test_real_local_transport_file_mapping_and_request_staging(tmp_path):
    request = request_fixture(tmp_path)
    transport = CLITransport(sys.executable, str(tmp_path), str(tmp_path / 'requests'))
    path = transport.stage_request(request)
    assert json.loads(Path(path).read_text()) == request
    assert transport.stage_request(request) == path
    artifact = request['inputs']['protein']
    assert transport.map_input(artifact['path'], artifact['path']) == artifact


def test_fixture_feedback_driver_stops_at_two_and_persists_plan(tmp_path, monkeypatch):
    request, _, _, _, candidate, _ = setup(tmp_path)
    module_spec = importlib.util.spec_from_file_location('round1_driver', Path(__file__).parents[1] / 'examples/prism_round1.py')
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    transport = FixtureTransport(tmp_path)
    monkeypatch.setattr(module, 'CLITransport', lambda **kw: transport)
    config = {'request_template': request, 'transport': {}, 'job_root': str(tmp_path),
              'receptor_path': request['inputs']['protein']['path'], 'handoff': candidate.handoff,
              'seeds': [11, 22], 'budget': 100, 'quote_wall_seconds': 10}
    report = module.run(config, tmp_path / 'driver')
    assert report['unique_molecules'] == 1 and report['independent_seed_repetitions'] == 2
    assert len(report['after']['training_action_ids']) == 2
    assert report['after']['choices'] and report['next_plan_executed'] is False
    assert report['before']['model']['training_hash'] != report['after']['model']['training_hash']
    assert all(item['budget_unchanged'] for item in report['duplicate_reads'])
    assert len(transport.requests) == 2
