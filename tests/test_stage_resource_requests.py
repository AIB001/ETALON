"""Resource independence and real RDKit output/reuse across CPU budgets."""
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from molcascade.environment.models import AcceleratorVendor, GpuDevice
from molcascade.errors import ConfigError
from molcascade.parallel import StageResources, resource_request, ShardOutcome
from molcascade.parallel.devices import FrameworkProbe, resolve_framework_lanes
from molcascade.parallel.shards import iter_shard_batches, run_sharded


def environment():
    return SimpleNamespace(
        cpu=SimpleNamespace(usable_cores=96),
        gpus=tuple(GpuDevice(index=i, vendor=AcceleratorVendor.NVIDIA, name='fixture GPU')
                   for i in range(8)),
        plan=SimpleNamespace(recommended_batch_size=2048),
    )


def test_requested_cpu_budget_is_not_capped_by_gpu_count():
    request = resource_request(environment(), device='cuda:0,1,2,3,4,5', workers=84)
    assert request.workers == 84
    assert request.devices == tuple(f'cuda:{i}' for i in range(6))
    assert request.on_cpu().devices == ('cpu',) * 84
    assert request.on_cpu().workers == 84
    assert resource_request(environment(), device='cpu', workers=7).devices == ('cpu',) * 7
    with pytest.raises(ConfigError):
        resource_request(environment(), device='cuda:12', workers=84)


def test_framework_fallback_preserves_the_requested_budget(monkeypatch):
    import molcascade.parallel.devices as module
    monkeypatch.setattr(module, 'probe_framework_gpu', lambda _: FrameworkProbe('torch', False))
    result = resolve_framework_lanes(StageResources(workers=3, devices=('cuda:0',), device_request='auto'), module='torch')
    assert result.workers == 3 and result.devices == ('cpu',) * 3


def test_cli_passes_cpu_budget_and_gpu_selection_independently(monkeypatch):
    from molcascade import cli
    measured = environment()
    monkeypatch.setattr(cli, 'detect_environment', lambda: measured)
    resources, observed = cli._stage_resources(workers=48, device='cuda:0,1')
    assert observed is measured
    assert resources.workers == 48
    assert resources.devices == ('cuda:0', 'cuda:1')


def fixture_accelerator_worker(task):
    """Device binding only; this fixture never executes accelerator code."""
    assert task.device in ('cuda:6', 'cuda:7')
    rows = pa.Table.from_batches(list(iter_shard_batches(task)))
    pq.write_table(rows, task.output_paths['primary'])
    return ShardOutcome(task.row_count, {'primary': task.row_count}, {'fixture': True})


def test_gpu_dispatch_still_uses_the_selected_devices(tmp_path):
    source = tmp_path/'source.parquet'
    pq.write_table(pa.table({'n': list(range(8))}), source)
    result = run_sharded(worker=fixture_accelerator_worker, input_files=[source],
        output_paths={'primary': 'rows.parquet'}, staging_root=tmp_path/'out',
        resources=StageResources(workers=8, devices=('cuda:6','cuda:7'), shard_rows=2))
    assert result.rows_in == 8 and result.shard_count == 4
    actual = pa.concat_tables([pq.read_table(tmp_path/'out'/p) for p in result.file_paths['primary']])
    assert actual.column('n').to_pylist() == list(range(8))


def test_real_conformers_use_cpu_budget_and_reuse_unchanged_shards(tmp_path):
    from molcascade.artifacts import ArtifactRef
    from molcascade.chemistry.identity import ParentStandardizer
    from molcascade.contracts import PARENT_V1
    from molcascade.plugins.api import StageContext, StageInput, StageRequest
    from molcascade.plugins.builtin.docking.ligand_prep import RDKitLigandConformerPlugin

    source = tmp_path/'parents'; source.mkdir()
    standardizer = ParentStandardizer()
    parents = [standardizer.standardize(raw_smiles=s)
               for s in ['CCO','CCCO','CCCCO','CCN','CCCN','c1ccccc1','CC(=O)O','CCOC']]
    rows = [{name: getattr(parent, name) for name in PARENT_V1.schema.names
             if name != 'duplicate_count'} for parent in parents]
    # Registration returns identity fields; the parent contract adds occurrence count.
    rows = [dict(r, duplicate_count=1) for r in rows]
    pq.write_table(pa.Table.from_pylist(rows, schema=PARENT_V1.schema), source/'part.parquet')
    request = StageRequest('conformers', inputs={'parents': StageInput(
        ArtifactRef(artifact_id='artifact:sha256:'+'a'*64, kind=PARENT_V1.id), source, PARENT_V1.id)},
        config={'conformers_per_molecule': 1, 'embed_attempts': 3, 'minimize': True})
    plugin = RDKitLigandConformerPlugin()
    first = plugin.execute(request, StageContext(tmp_path/'one', StageResources(
        workers=1, devices=('cpu',), shard_rows=2, checkpoint_dir=tmp_path/'saved')))
    reused = plugin.execute(request, StageContext(tmp_path/'reused', StageResources(
        workers=4, devices=('cuda:6','cuda:7'), shard_rows=2, checkpoint_dir=tmp_path/'saved')))
    fresh = plugin.execute(request, StageContext(tmp_path/'four', StageResources(
        workers=4, devices=('cuda:6','cuda:7'), shard_rows=2)))
    assert reused.metadata['shards_reused'] == 4
    assert fresh.metadata['execution_lanes'] == ['cpu'] * 4
    for port in first.outputs:
        def read(result, folder):
            return pa.concat_tables([pq.read_table(folder/f) for f in result.outputs[port].file_paths])
        expected = read(first, tmp_path/'one')
        assert expected.equals(read(reused, tmp_path/'reused'), check_metadata=True)
        assert expected.equals(read(fresh, tmp_path/'four'), check_metadata=True)
