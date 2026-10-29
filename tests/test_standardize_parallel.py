"""Chemical identity fixtures, actual spawned workers, resume, and bundle reuse."""
import json
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from molcascade.artifacts import ArtifactRef, ArtifactProducer, LocalArtifactStore
from molcascade.contracts import RAW_MOLECULE_V1, RAW_MOLECULE_V2
from molcascade.plugins.api import StageContext, StageInput, StageRequest
from molcascade.plugins.builtin.standardize import RDKitStandardizePlugin
from molcascade.plugins.builtin.registered_source import RegisteredParentSourcePlugin
from molcascade.parallel import StageResources, count_completed_shards
from molcascade.parallel.progress import read_progress
from molcascade.errors import PluginError


SMILES = ['CCO', 'C(C)O', 'CCN.Cl', 'C[C@H](O)C(=O)O', 'C[C@@H](O)C(=O)O',
          'invalid', 'c1ccccc1', 'CCO', '[Na+].[O-]C(=O)C', '', 'CC(=O)O', 'CCN']


def make_request(root, smiles=SMILES, v2=False, ids=None):
    root.mkdir(parents=True)
    contract = RAW_MOLECULE_V2 if v2 else RAW_MOLECULE_V1
    rows = []
    for i, smi in enumerate(smiles):
        row = {'source_record_id': ids[i] if ids else f's{i:04d}',
               'source_kind': 'fixture', 'source_index': i}
        row.update({'raw_format': 'SMILES', 'raw_structure': smi} if v2 else {'raw_smiles': smi})
        rows.append(row)
    pq.write_table(pa.Table.from_pylist(rows, schema=contract.schema), root / 'raw.parquet')
    ref = ArtifactRef(artifact_id='artifact:sha256:' + 'a' * 64, kind=contract.id)
    return StageRequest('standardize', inputs={'primary': StageInput(ref, root, contract.id)},
                        config={'identity_policy': {'tautomer_policy': 'preserve'}})


def execute(request, path, workers=1, checkpoints=None):
    resources = StageResources(workers=workers, devices=('cpu',) * workers,
                               shard_rows=3, checkpoint_dir=checkpoints)
    return RDKitStandardizePlugin().execute(request, StageContext(path, resources))


def tables(response, root):
    return {port: pa.concat_tables([pq.read_table(root / p) for p in output.file_paths])
            for port, output in response.outputs.items()}


@pytest.mark.parametrize('v2', [False, True])
def test_spawn_equivalence_global_duplicates_and_stereo(tmp_path, v2):
    req = make_request(tmp_path / 'raw', v2=v2)
    a = execute(req, tmp_path / 'one')
    b = execute(req, tmp_path / 'three', workers=3)
    ta, tb = tables(a, tmp_path / 'one'), tables(b, tmp_path / 'three')
    for port in ta:
        assert ta[port].equals(tb[port], check_metadata=True)
        assert (tmp_path / 'one' / a.outputs[port].file_paths[0]).read_bytes() == (tmp_path / 'three' / b.outputs[port].file_paths[0]).read_bytes()
    parents = tb['primary'].to_pylist()
    assert max(p['duplicate_count'] for p in parents) == 3
    stereo = [p for p in parents if '@' in p['parent_smiles']]
    assert len(stereo) == 2 and stereo[0]['parent_id'] != stereo[1]['parent_id']
    assert b.metadata['input_count'] == 12
    assert b.metadata['reject_count'] == 2
    assert b.metadata['shard_count'] == 4


def test_resume_after_interrupted_map_and_corrupt_shard(tmp_path, monkeypatch):
    import molcascade.parallel.shards as shards
    req = make_request(tmp_path / 'raw')
    checkpoints = tmp_path / 'checkpoints'
    real = shards._run_tasks

    def partial(tasks, **kwargs):
        real(tasks[:2], **kwargs)
        raise RuntimeError('simulated host exit after two committed shards')

    monkeypatch.setattr(shards, '_run_tasks', partial)
    with pytest.raises(RuntimeError):
        execute(req, tmp_path / 'interrupted', checkpoints=checkpoints)
    assert count_completed_shards(checkpoints) == (2, 4)
    assert read_progress(checkpoints)['rows_completed'] == 6
    monkeypatch.setattr(shards, '_run_tasks', real)
    # Completed original is reverified; a corrupt shard is recomputed, never accepted.
    (checkpoints / 'shard-00001/registrations.parquet').write_bytes(b'corrupt fixture')
    response = execute(req, tmp_path / 'resumed', checkpoints=checkpoints)
    assert response.metadata['shards_reused'] == 1
    assert read_progress(checkpoints)['phase'] == 'ready_to_publish'
    clean = execute(req, tmp_path / 'clean')
    for port, table in tables(clean, tmp_path / 'clean').items():
        assert table.equals(tables(response, tmp_path / 'resumed')[port])


def test_checkpoint_identity_and_duplicate_source_ids(tmp_path):
    req = make_request(tmp_path / 'raw')
    checkpoints = tmp_path / 'checkpoints'
    execute(req, tmp_path / 'first', checkpoints=checkpoints)
    other = StageRequest('standardize', inputs=req.inputs, config={})
    with pytest.raises(PluginError, match='different inputs or policy'):
        execute(other, tmp_path / 'other', checkpoints=checkpoints)
    duplicate = make_request(tmp_path / 'duplicate', ids=['same'] * len(SMILES))
    with pytest.raises(PluginError) as exc:
        execute(duplicate, tmp_path / 'bad')
    assert exc.value.code == 'STANDARDIZE_DUPLICATE_SOURCE_ID'


def committed_bundle(tmp_path):
    req = make_request(tmp_path / 'raw')
    store = LocalArtifactStore(tmp_path / 'store')
    with store.staging_area() as path:
        response = execute(req, path)
        manifest = store.commit(path, kind='stage-output/standardize', outputs=response.artifact_outputs,
            producer=ArtifactProducer(plugin_id='chemistry.rdkit_standardize', plugin_version='0.1.0', api_version='1'),
            metadata={'response_metadata': dict(response.metadata)})
    return store, manifest, req


def test_registered_reuse_keeps_negative_evidence_no_chemistry(tmp_path, monkeypatch):
    from molcascade.chemistry.identity import ParentStandardizer
    store, manifest, req = committed_bundle(tmp_path)
    request = StageRequest('reuse', config={'path': str(tmp_path / 'store'),
        'artifact_id': manifest.artifact_id, **dict(req.config)})
    def unexpected(*args, **kwargs):
        raise AssertionError('must not recompute chemistry on verified reuse')
    monkeypatch.setattr(ParentStandardizer, 'standardize', unexpected)
    result = RegisteredParentSourcePlugin().execute(request, StageContext(tmp_path / 'reused'))
    assert result.metadata['chemistry_rows_computed'] == 0
    for port, output in result.outputs.items():
        original = manifest.dataset_ref(port)
        root = store.artifact_directory(manifest.artifact_id)
        assert (root / original.file_paths[0]).read_bytes() == (tmp_path / 'reused' / output.file_paths[0]).read_bytes()
    decisions = tables(result, tmp_path / 'reused')['decisions'].to_pylist()
    assert len([d for d in decisions if d['outcome'] == 'REJECT']) == 2
    with pytest.raises(PluginError) as exc:
        RegisteredParentSourcePlugin().execute(StageRequest('reuse', config={
            'path': str(tmp_path / 'store'), 'artifact_id': manifest.artifact_id}), StageContext(tmp_path / 'mismatch'))
    assert exc.value.code == 'REGISTERED_SOURCE_IDENTITY_MISMATCH'
    root = store.artifact_directory(manifest.artifact_id)
    (root / manifest.dataset_ref('primary').file_paths[0]).write_bytes(b'corrupt fixture')
    with pytest.raises(Exception):
        RegisteredParentSourcePlugin().execute(request, StageContext(tmp_path / 'corrupt'))


def test_cascade_registered_source_runs_through_existing_runner(tmp_path):
    from molcascade.cascade.models import CascadeConfig
    from molcascade.cascade.lower import lower_cascade
    from molcascade.runtime.runner import LocalRunner
    from molcascade.plugins.registry import create_builtin_registry
    store, manifest, req = committed_bundle(tmp_path)
    cascade = CascadeConfig.model_validate({
        'name': 'reuse-registration', 'library': {'format': 'registered'},
        'ingest': {'id': 'registered', 'backend': 'source.registered_parents@0.1.0',
                   'settings': {'artifact_id': manifest.artifact_id, **dict(req.config)}},
        'standardize': None,
        'finalize': {'steps': [{'id': 'properties', 'backend': 'features.rdkit_properties@0.1.0'}]},
    })
    lowered = lower_cascade(cascade, library_path=str(tmp_path / 'store'))
    runner = LocalRunner(tmp_path / 'new-workspace', plugins=create_builtin_registry())
    state = runner.run(lowered.pipeline, run_id='reuse')
    assert str(state.status) == 'SUCCEEDED', state.error
    assert [s.stage_id for s in state.stages] == ['registered', 'properties']
    original = runner.store.verify(state.stages[0].output_ref.artifact_id)
    assert original.metadata['response_metadata']['chemistry_rows_computed'] == 0


def test_cli_and_mcp_report_same_progress(tmp_path, capsys, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from molcascade.parallel.progress import write_progress, stage_progress
    from molcascade.runtime.cache import checkpoint_directory_name
    from molcascade.runtime.models import StageRunState, StageRunStatus, RunState, RunStatus
    from molcascade import cli
    from molcascade.mcp import screening
    key = 'cache:sha256:' + 'b' * 64
    stage = StageRunState(stage_id='standardize', plugin_key='chemistry.rdkit_standardize@0.1.0',
                          status=StageRunStatus.RUNNING, cache_key=key)
    checkpoint = tmp_path / 'checkpoints' / checkpoint_directory_name(key)
    write_progress(checkpoint, phase='map', requested_workers=84, effective_workers=4,
                   shards_total=10, shards_complete=0, rows_completed=0, rows_total=100)
    assert cli._shard_counts(tmp_path / 'checkpoints', stage) == (0, 10)
    assert stage_progress(tmp_path / 'checkpoints', stage)['effective_workers'] == 4
    failed = stage.model_copy(update={'status': StageRunStatus.FAILED})
    assert stage_progress(tmp_path / 'checkpoints', failed)['effective_workers'] == 0
    now = datetime.now(timezone.utc)
    state = RunState(run_id='progress', revision_id='c' * 64,
                     status=RunStatus.RUNNING, stages=(stage,), started_at=now, updated_at=now)
    (tmp_path / 'runs').mkdir()
    from molcascade.artifacts import canonical_json_bytes
    (tmp_path / 'runs/progress.json').write_bytes(canonical_json_bytes(state))
    assert cli._status('progress', workspace=tmp_path, include_events=False, as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    class Tools:
        def __init__(self):
            self.functions = {}
        def tool(self):
            def register(fn):
                self.functions[fn.__name__] = fn
                return fn
            return register
    tools = Tools()
    screening.register(tools)
    monkeypatch.setattr(screening, 'open_runner', lambda ws: SimpleNamespace(
        load_run=lambda run_id: state, checkpoints_root=tmp_path / 'checkpoints'))
    mcp = json.loads(tools.functions['run_status']('progress', str(tmp_path)))
    assert mcp['ok'] is True
    assert mcp['stages'][0]['progress'] == payload['stages'][0]['progress']
    assert payload['stages'][0]['shards'] == {'complete': 0, 'total': 10}


def test_device_rebinding_preserves_side_inputs(tmp_path):
    from molcascade.parallel.shards import ShardTask, _replace_device
    task = ShardTask(index=0, input_files=(tmp_path / 'input',), row_offset=0,
                     row_count=1, output_paths={'primary': tmp_path / 'out'},
                     side_inputs={'conformers': (tmp_path / 'conformers',)},
                     stage_id='test', config={}, device='cuda:1', batch_size=1)
    assert _replace_device(task, 'cuda:0').side_inputs == task.side_inputs
