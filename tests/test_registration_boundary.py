"""The ETALON producer/consumer boundary uses the component's existing status and bundles."""
import json
from datetime import datetime, timezone
from pathlib import Path
from etalon.boundary.screen import Screen
from etalon.screening import plan_screen, screen_status, _journal


def test_registered_subset_retains_rejections_and_selection_identity(tmp_path):
    import hashlib
    import pytest
    from molcascade.plugins.registry import create_builtin_registry
    from molcascade.runtime.runner import LocalRunner
    old = tmp_path / 'old'
    raw = tmp_path / 'raw.csv'
    raw.write_text('id,smiles\na,CCO\nb,C(C)O\nc,CCC\nbad,invalid\n')
    runner = LocalRunner(old, plugins=create_builtin_registry())
    state = runner.run({'schema_version': 1, 'name': 'original', 'stages': [
        {'id':'source','slot':'source','plugin':'source.delimited_smiles@0.1.0','config':{'path':str(raw)}},
        {'id':'standardize','slot':'standardize','plugin':'chemistry.rdkit_standardize@0.1.0','config':{}},
    ]}, run_id='old')
    assert str(state.status) == 'SUCCEEDED', state.error
    aid = state.output_ref.artifact_id
    original = Screen(old).read(aid, contract_id='parent/v1')
    selected = original[0]['parent_id']
    selection = tmp_path / 'selected.tsv'
    selection.write_text('parent_id\n' + selected + '\n')
    config = tmp_path / 'cascade.json'
    def write_config():
        config.write_text(json.dumps({'kind':'cascade','name':'reuse','library':{'format':'registered'},
            'ingest':{'id':'registered','backend':'source.registered_parents@0.1.0','settings':{
                'artifact_id':aid,'selection_path':str(selection),
                'selection_sha256':hashlib.sha256(selection.read_bytes()).hexdigest()}},
            'standardize':None,
            'finalize':{'steps':[{'id':'properties','backend':'features.rdkit_properties@0.1.0'}]}}))
    write_config()
    report = plan_screen(config, old, tmp_path / 'new')
    assert str(selection) in report['request']['inputs']
    screen = Screen(tmp_path / 'new')
    result = screen.run(screen.plan(config, old), run_id='subset', workers=1, devices=('cpu',))
    assert result.status == 'SUCCEEDED', result
    assert screen.read(result.stages[0].artifact_id, contract_id='parent/v1') == [original[0]]
    mapping = screen.read(result.stages[0].artifact_id, contract_id='parent_source_map/v1')
    assert {row['parent_id'] for row in mapping} == {selected}
    assert any(row['outcome']=='REJECT' for row in screen.read(result.stages[0].artifact_id, contract_id='decision/v1'))
    # Same config cannot silently consume a changed selection.
    selection.write_text('parent_id\nmissing\n')
    failed = screen.run(screen.plan(config, old), run_id='changed', workers=1, devices=('cpu',))
    assert failed.status == 'FAILED'
    write_config()
    missing = screen.run(screen.plan(config, old), run_id='missing', workers=1, devices=('cpu',))
    assert missing.status == 'FAILED'


def test_registered_plan_pins_bundle_files_and_runs_without_standardizing(tmp_path):
    from molcascade.plugins.registry import create_builtin_registry
    from molcascade.runtime.runner import LocalRunner
    old = tmp_path / 'old'
    raw = tmp_path / 'raw.csv'
    raw.write_text('id,smiles\none,CCO\ntwo,C(C)O\nbad,invalid\n')
    runner = LocalRunner(old, plugins=create_builtin_registry())
    state = runner.run({'schema_version': 1, 'name': 'original', 'stages': [
        {'id':'source','slot':'source','plugin':'source.delimited_smiles@0.1.0','config':{'path':str(raw)}},
        {'id':'standardize','slot':'standardize','plugin':'chemistry.rdkit_standardize@0.1.0','config':{}},
    ]},run_id='old')
    assert str(state.status) == 'SUCCEEDED', state.error
    aid = state.output_ref.artifact_id
    config = tmp_path / 'cascade.json'
    config.write_text(json.dumps({'kind':'cascade','name':'reuse','library':{'format':'registered'},
        'ingest':{'id':'registered','backend':'source.registered_parents@0.1.0','settings':{'artifact_id':aid}},
        'standardize':None,
        'finalize':{'steps':[{'id':'properties','backend':'features.rdkit_properties@0.1.0'}]}}))
    report = plan_screen(config, old, tmp_path / 'new')
    inputs = report['request']['inputs']
    bundle = runner.store.artifact_directory(aid)
    assert str(bundle/'artifact.json') in inputs
    manifest = runner.store.get_manifest(aid)
    assert all(str(bundle/f.path) in inputs for f in manifest.files)
    assert str(old) not in inputs
    screen = Screen(tmp_path / 'new')
    result = screen.run(screen.plan(config, old), run_id='reuse', workers=1, devices=('cpu',))
    assert result.status == 'SUCCEEDED', result
    assert [s.stage_id for s in result.stages] == ['registered','properties']
    reused = screen.read(result.stages[0].artifact_id,contract_id='decision/v1')
    assert any(row['outcome']=='REJECT' for row in reused)


def test_screen_status_reports_checkpoint_progress_without_starting_work(tmp_path):
    from molcascade.runtime.models import RunState, RunStatus, StageRunState, StageRunStatus
    from molcascade.runtime.cache import checkpoint_directory_name
    from molcascade.parallel.progress import write_progress
    now=datetime.now(timezone.utc); key='cache:sha256:'+'b'*64
    state=RunState(run_id='live',revision_id='c'*64,status=RunStatus.RUNNING,started_at=now,updated_at=now,
        stages=(StageRunState(stage_id='standardize',plugin_key='chemistry.rdkit_standardize@0.1.0',
                             status=StageRunStatus.RUNNING,cache_key=key),))
    (tmp_path/'runs').mkdir()
    from molcascade.artifacts import canonical_json_bytes
    (tmp_path/'runs/live.json').write_bytes(canonical_json_bytes(state))
    write_progress(tmp_path/'checkpoints'/checkpoint_directory_name(key),phase='map',
                   shards_complete=2,shards_total=8,rows_completed=5000,rows_total=20000,
                   effective_workers=4,requested_workers=84)
    with _journal(tmp_path,create=True) as db:
        db.execute('INSERT INTO screen_jobs VALUES (?,?,?,?,?,?,?,?,?,?)',
                   ('live','plan','{}','running',123,str(now),str(now),None,None,'fixture.log'));db.commit()
    report=screen_status(tmp_path,'live')
    assert report['progress']['detail']['rows_completed']==5000
    assert report['progress']['detail']['effective_workers']==4
    repeated = Screen(tmp_path).progress('live')
    assert report['progress']['detail'] == repeated['detail']
    assert report['progress']['execution_feedback']['observation_key'] == repeated['execution_feedback']['observation_key']
    assert not (tmp_path/'artifacts').exists() or not list((tmp_path/'artifacts').iterdir())
