"""Revalidate round1 through the registered runtime and import an existing design bundle.

No submission, resume, MD, generation or training. All journal writes are isolated copies.
Run in the actual environment with frozen ETALON source and the original PRISM source/input paths.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import Action, canonical, digest
from etalon.active.adapters import MOLECULAR_REPRESENTATION
from etalon.authority.grant import record_digest
from etalon.boundary.prism_v1 import CLITransport, file_identity
from etalon.data.design import design_index, import_bundle, read_bundle
from etalon.runtime.executors import build_executor, prepare_executor, register_executor, recover_bridge_action


def copy_historical_authorizations(store: CampaignStore) -> list[dict]:
    """Compatibility audit on a COPY: retain original signed receipts, never mint permission.

    Historical signatures are copied verbatim, not reauthenticated with a new process key.
    These receipts only permit this driver's read-only reconciliation, never a new spend.
    """
    observations = {o['action_id']: o for o in store.observations()}
    candidates = store.candidates()
    migrations = []
    with store.connection(write=True) as db:
        for row in store.actions():
            observation = observations[row['id']]
            result = observation['result']
            authorization = result['provenance'].get('authorization')
            request_row = db.execute('SELECT body FROM metadata WHERE key=?',
                                     ('prism-v1:request:' + row['id'],)).fetchone()
            request = json.loads(request_row[0]) if request_row else None
            candidate = candidates[row['candidate_id']]
            if (not isinstance(authorization, dict) or not authorization.get('signature')
                    or authorization.get('parent_id') != row['candidate_id']
                    or authorization.get('record_sha256') != record_digest(candidate.handoff)
                    or request is None or request != result['provenance']['request']
                    or request['action_id'] != row['id']
                    or authorization.get('receptor_sha256') != request['inputs']['protein']['sha256']):
                raise ValueError('historical authorization is missing or does not bind this action/handoff/receptor')
            key = 'runtime:bridge-authorization:' + row['id']
            existing = db.execute('SELECT body FROM metadata WHERE key=?', (key,)).fetchone()
            if existing and json.loads(existing[0]) != authorization:
                raise ValueError('existing runtime authorization differs from historical evidence')
            if not existing:
                db.execute('INSERT INTO metadata VALUES (?,?)', (key, canonical(authorization)))
            migrations.append({'action_id': row['id'], 'inserted': not bool(existing),
                'source': 'existing observation.result.provenance.authorization',
                'source_result_sha256': digest(result), 'authorization_sha256': digest(authorization),
                'scope': 'historical receipt backfill in copy; not new authorization or new task'})
    return migrations


def validate_pool_cli(bundle: Path, workspace: Path) -> dict:
    """Run the public importer twice in independent processes, using real molecular features."""
    store = CampaignStore(workspace / 'design-pool.sqlite')
    store.configure(CampaignSpec('not_measured', 0, 'wall_second', MOLECULAR_REPRESENTATION),
                    [Endpoint('not_measured', 'design-bundle', 'not_measured', 'count', 'reference/1',
                              0, queryable=False, requires_handoff=False)])
    calls = []
    for number in (1, 2):
        command = [sys.executable, '-m', 'etalon', 'design', 'import', '--mode', 'pool',
                   '--bundle', str(bundle.resolve()), '--database', str(store.path.resolve())]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        (workspace / f'pool-import-{number}.stdout').write_text(completed.stdout, encoding='utf-8')
        (workspace / f'pool-import-{number}.stderr').write_text(completed.stderr, encoding='utf-8')
        receipt = {'command': command, 'exit_code': completed.returncode}
        (workspace / f'pool-import-{number}.command.json').write_text(canonical(receipt), encoding='utf-8')
        if completed.returncode:
            raise RuntimeError(f'public pool import failed; see pool-import-{number}.stdout/stderr')
        payload = json.loads(completed.stdout)
        assert payload['ok']
        calls.append({**receipt, 'result': payload})
        if number == 1:
            events = store.events()
        else:
            assert events == store.events()
    assert calls[0]['result']['added_candidates'] == 1 and not calls[1]['result']['imported']
    counts = design_index(store)['counts']
    assert counts['pose'] == 2 and counts['occurrence'] == 4 and len(store.candidates()) == 1
    assert not store.observations() and not store.actions() and store.balance()['spent'] == 0
    assert all(not candidate.handoff for candidate in store.candidates().values())
    return {'calls': calls, 'counts': counts, 'candidate_count': len(store.candidates()),
            'feature_dimensions': [len(c.features) for c in store.candidates().values()],
            'observations': 0, 'actions': 0, 'budget': store.balance()}


def run(database: Path, config: dict, bundle: Path, workspace: Path) -> dict:
    workspace.mkdir(parents=True, exist_ok=False)
    original_identity = file_identity(database)
    target = workspace / 'registered-round1-copy.sqlite'
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(target) as dest:
        source.backup(dest)
    store = CampaignStore(target)
    spec, endpoints = store.configuration()
    if store.status()['pending']:
        raise ValueError('this read-only replay requires terminal round1 actions')
    configuration = {k: v for k, v in config.items() if k in {
        'request_template', 'transport', 'job_root', 'receptor_path', 'seeds', 'wait_seconds', 'poll_seconds'}}
    endpoint = endpoints[configuration['request_template']['endpoint_id']]
    record = prepare_executor('prism_bridge_v1', configuration, endpoint.as_dict())
    registration = register_executor(store.path, record, 'Round2 read-only revalidation of existing round1 outcomes')
    executor, _ = build_executor(store.path, workspace / 'no-new-dispatch')
    before = {'budget': store.balance(), 'observations': store.observations(), 'actions': store.actions()}
    migrations = copy_historical_authorizations(store)
    results = []
    # Enforce the validation scope even if future implementation accidentally regresses.
    original_call = CLITransport.call
    calls = []
    def read_only_call(self, operation, **arguments):
        if operation not in {'status', 'result', 'capabilities'}:
            raise AssertionError('round2 revalidation forbids submission/resume')
        calls.append({'operation': operation, **arguments})
        return original_call(self, operation, **arguments)
    CLITransport.call = read_only_call
    try:
        for row in store.actions():
            action = Action(**{k: row[k] for k in Action.__dataclass_fields__})
            candidate = store.candidates()[action.candidate_id]
            one = executor(action, candidate, endpoint, None)
            two = executor(action, candidate, endpoint, None)
            assert one == two
            live = recover_bridge_action(store.path, action)
            live_again = recover_bridge_action(store.path, action)
            assert live == live_again
            assert live is not None and live.value == one.value and live.cost == one.cost
            assert live.provenance['result'] == one.provenance['result']
            assert live.provenance['authorization'] == one.provenance['authorization']
            results.append({'action_id': action.id, 'value': one.value, 'cost': one.cost,
                            'job_id': one.provenance['result']['job_id'], 'source': 'existing_real_artifact',
                            'live_status_result_revalidated': True,
                            'runtime_entry': 'etalon.runtime.executors.recover_bridge_action'})
    finally:
        CLITransport.call = original_call
    assert before == {'budget': store.balance(), 'observations': store.observations(), 'actions': store.actions()}
    design = CampaignStore(workspace / 'design-reference-index.sqlite')
    design.configure(CampaignSpec('not_measured', 0, 'wall_second', 'reference-index-only'),
                     [Endpoint('not_measured', 'design-bundle', 'not_measured', 'count', 'reference/1',
                               0, queryable=False, requires_handoff=False)])
    checked = read_bundle(bundle)
    first = import_bundle(design, bundle, mode='index_only')
    repeated = import_bundle(design, bundle, mode='index_only')
    assert not design.observations() and design.balance()['spent'] == 0
    assert not repeated['imported'] and not design.candidates()
    pool = validate_pool_cli(bundle, workspace)
    assert file_identity(database) == original_identity
    report = {'scope': 'existing round1 result revalidation + explicitly sourced bundle reference import',
              'new_simulations': 0, 'new_training_runs': 0, 'python': sys.executable,
              'imported_modules': {'etalon': __import__('etalon').__file__,
                                  'runtime_executor': sys.modules[build_executor.__module__].__file__},
              'source_database': {'path': str(database), **original_identity},
              'source_database_unchanged': True, 'historical_authorization_backfill': migrations,
              'recovery_scope': 'compatibility replay of terminal R1 jobs, not a new runtime execution',
              'prepared_executor': record, 'registration': registration['registered'],
              'results': results, 'read_only_bridge_calls': calls,
              'budget_unchanged': store.balance() == before['budget'],
              'design_bundle': {'bundle_id': checked['bundle']['bundle_id'], 'source_kind': checked['bundle']['source']['kind'],
                                'schema_sha256': checked['schema_sha256']},
              'design_import': first, 'design_repeat': repeated, 'design_index_counts': design_index(design)['counts'],
              'public_pool_cli': pool}
    (workspace / 'report.json').write_text(canonical(report) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.database, json.loads(args.config.read_text()), args.bundle, args.workspace)
    print(json.dumps({'report': str(args.workspace / 'report.json'), 'new_simulations': 0,
                      'budget_unchanged': result['budget_unchanged'], 'design': result['design_index_counts']}))
