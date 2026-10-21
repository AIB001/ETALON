"""Real local SDK checks on synthetic, caller-owned archive copies; no science run."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Artifact, ResearchJournal
from etalon.research.controller import register_controller
from etalon.research.validation import attach_decision_validation


SCRIPT = Path(__file__).resolve().parents[1] / '.claude/skills/etalon-research/scripts/released_feedback.py'


def logical_snapshot(path):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: sorted(db.execute('SELECT * FROM "' + name + '"').fetchall(), key=repr)
                for name in tables}


def make_fixture(root):
    root.mkdir(parents=True)
    original = root / 'original.sqlite'
    store = CampaignStore(original)
    store.configure(CampaignSpec('unmeasured', 10, 'credit', 'released-feedback-fixture'),
                    [Endpoint('unmeasured', 'test', 'not_measured', 'count', 'fixture/1', 1, queryable=False)])
    journal = ResearchJournal(store)
    evidence = root / 'evidence.json'
    evidence.write_text('{"source_kind":"fixture"}', encoding='utf8')
    artifact = Artifact(str(evidence), **file_identity(evidence), source_kind='fixture',
                        role='diagnostic', bindings={})
    journal.record(artifact)
    controller = register_controller(journal, name='fixture controller', source_evidence=(artifact.id,),
        schedule=[{'id': 'unused', 'stage': 'development', 'request_sha256': 'a' * 64,
                   'quote_wall_second': 1, 'depends_on': []}])
    decision = {'task_id': 'i3-fixture', 'decision': {'status': 'unresolved'},
                'independent_validation_accessed': False}
    decision_sha = digest(decision)
    # Fixture preparation only: this is not a new public decision-freeze API.
    with store.connection(write=True) as db:
        journal._put(db, 'decision-' + decision_sha, {**decision, 'freeze_sha256': decision_sha})
    released = Artifact(str(evidence), **file_identity(evidence), source_kind='fixture',
                        role='independent_validation', bindings={'decision_sha256': decision_sha})
    journal.record(released)
    comparison = {'source_kind': 'fixture', 'frozen_policy_loss': None,
                  'policies': {'baseline': {'loss': None}},
                  'observations': [{'status': 'failed', 'value': None, 'cost': .65},
                                   {'status': 'missing', 'value': None, 'cost': None}],
                  'historical_costs': {'known_subtotal': .65, 'total': None, 'unit': 'fixture_credit',
                                       'basis': 'synthetic declarations, not measured scientific costs'}}
    validation = attach_decision_validation(journal, decision_sha256=decision_sha,
        evidence=(released.id,), release_evidence=released.id,
        protocol={'method': 'synthetic released comparison'}, comparison=comparison)
    copy = root / 'writable-copy.sqlite'
    with sqlite3.connect(original.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    return {'database': copy, 'original': original, 'controller': controller, 'decision': decision_sha,
            'validation': validation['id'], 'comparison': comparison, 'evidence': evidence,
            'artifact': released.id}


def invoke(fixture, output, *, script=SCRIPT, **overrides):
    args = {k: str(fixture[k]) for k in ('database', 'controller', 'decision', 'validation')}
    args.update({k: str(v) for k, v in overrides.items()})
    command = [sys.executable, str(script)]
    for key, value in {**args, 'output': str(output)}.items():
        command.extend(['--' + key, value])
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    # Bind subprocess imports to the package actually imported by this test/probe.
    import etalon
    env['PYTHONPATH'] = str(Path(etalon.__file__).resolve().parent.parent)
    run = subprocess.run(command, cwd=output.parent, env=env, capture_output=True, text=True, timeout=30)
    return {'command': command, 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr,
            'receipt': json.loads(output.read_text()) if output.is_file() else None}


def assert_append_only(before, after, added):
    assert set(before) == set(after)
    for table in before:
        assert set(before[table]) <= set(after[table]) if table != 'sqlite_sequence' else True
        if table not in ('metadata', 'events', 'sqlite_sequence'):
            assert before[table] == after[table]
    assert len(after['events']) - len(before['events']) == added
    assert len(after['metadata']) - len(before['metadata']) == added


def test_normal_wrong_decision_replay_and_receipt_preservation(tmp_path):
    f = make_fixture(tmp_path / 'fixture')
    original = f['original'].read_bytes()
    before = logical_snapshot(f['database'])
    balance = CampaignStore(f['database'], read_only=True).balance()
    first = invoke(f, tmp_path / 'first.json')
    assert first['exit_code'] == 0, first
    assert first['receipt']['retained_comparison'] == f['comparison']
    assert first['receipt']['monetary_cost'] is None
    after = logical_snapshot(f['database'])
    assert_append_only(before, after, 1)
    wrong = invoke(f, tmp_path / 'wrong.json', decision='0' * 64)
    assert wrong['exit_code'] == 2 and wrong['receipt']['status'] == 'not_completed'
    assert logical_snapshot(f['database']) == after
    replay = invoke(f, tmp_path / 'replay.json')
    assert replay['exit_code'] == 0
    assert replay['receipt']['feedback_id'] == first['receipt']['feedback_id']
    assert logical_snapshot(f['database']) == after
    saved = (tmp_path / 'first.json').read_bytes()
    conflict = invoke(f, tmp_path / 'first.json')
    assert conflict['exit_code'] != 0 and 'FileExistsError' in conflict['stderr']
    assert (tmp_path / 'first.json').read_bytes() == saved
    assert logical_snapshot(f['database']) == after
    assert CampaignStore(f['database'], read_only=True).balance() == balance
    assert f['original'].read_bytes() == original


@pytest.mark.parametrize('case', ['evidence_changed', 'evidence_missing', 'identity_changed',
                                  'freeze_changed', 'wrong_role', 'wrong_controller', 'missing_validation'])
def test_mismatches_fail_without_appending(tmp_path, case):
    f = make_fixture(tmp_path / 'fixture')
    overrides = {}
    if case == 'evidence_changed':
        f['evidence'].write_text('changed')
    elif case == 'evidence_missing':
        f['evidence'].unlink()
    elif case in ('identity_changed', 'freeze_changed', 'wrong_role'):
        key = {'identity_changed': f['validation'], 'freeze_changed': 'decision-' + f['decision'],
               'wrong_role': f['artifact']}[case]
        with sqlite3.connect(f['database']) as db:
            value = json.loads(db.execute('SELECT body FROM metadata WHERE key=?', ('research:' + key,)).fetchone()[0])
            if case == 'identity_changed':
                value['comparison']['frozen_policy_loss'] = 1
            elif case == 'freeze_changed':
                value['task_id'] = 'corrupted fixture'
            else:
                value['role'] = 'diagnostic'
            db.execute('UPDATE metadata SET body=? WHERE key=?', (json.dumps(value), 'research:' + key))
    elif case == 'wrong_controller':
        overrides['controller'] = f['validation']
    else:
        overrides['validation'] = 'decisionvalidation-' + '0' * 64
    before = logical_snapshot(f['database'])
    result = invoke(f, tmp_path / 'rejected.json', **overrides)
    assert result['exit_code'] == 2, result
    assert result['receipt']['status'] == 'not_completed'
    assert 'feedback_id' not in result['receipt']
    assert logical_snapshot(f['database']) == before


def test_unconfigured_database_is_not_initialized(tmp_path):
    path = tmp_path / 'empty.sqlite'
    sqlite3.connect(path).close()
    before = path.read_bytes()
    result = invoke({'database': path, 'controller': 'absent', 'decision': 'absent', 'validation': 'absent'},
                    tmp_path / 'error.json')
    assert result['exit_code'] == 2 and result['receipt']['status'] == 'not_completed'
    assert path.read_bytes() == before
