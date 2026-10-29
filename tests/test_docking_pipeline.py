"""Execution fixtures only; real chemistry/GPU comparisons live in integration."""
import json
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from molcascade.parallel import StageResources, ShardTask, ShardOutcome
from molcascade.parallel.shards import plan_shards, plan_batch_shards, iter_shard_batches, run_sharded
from molcascade.parallel.progress import report_activity, stage_progress, write_progress


def producer(task):
    report_activity(task, 'gpu_search_and_decode')  # fixture, no actual accelerator
    rows = pa.concat_tables([pa.Table.from_batches([b]) for b in iter_shard_batches(task)])
    pq.write_table(rows, task.output_paths['primary'])
    directory = task.output_paths['primary'].parent
    (directory / 'produced.json').write_text(json.dumps({'time': time.time()}))
    return ShardOutcome(task.row_count, {'primary': task.row_count}, {'quality_checked': 0})


def checker(task):
    assert task.device == 'cpu'
    report_activity(task, 'pose_check', poses_total=task.row_count)
    if task.index == 1 and Path(task.config['fail_flag']).exists():
        raise RuntimeError('fixture quality failure')
    time.sleep(0.15)
    directory = task.output_paths['primary'].parent
    (directory / 'checked.json').write_text(json.dumps({'time': time.time()}))
    return ShardOutcome(task.row_count, {'primary': task.row_count}, {'quality_checked': task.row_count})


def test_batch_planning_preserves_old_engine_calls_across_files_and_groups(tmp_path):
    files=[]; offset=0
    for size, group in [(17, 5), (21, 7), (2, 1)]:
        p=tmp_path/f'{offset}.parquet'
        pq.write_table(pa.table({'n': list(range(offset,offset+size))}), p, row_group_size=group)
        files.append(p);offset+=size
    def batches(ranges, coalesce=False):
        out=[]
        for i,(start,count) in enumerate(ranges):
            task=ShardTask(i,tuple(files),start,count,{'primary':tmp_path/'unused'})
            parts=[b.column('n').to_pylist() for b in iter_shard_batches(task,batch_size=6)]
            out.extend([sum(parts,[])] if coalesce else parts)
        return out
    old=batches(plan_shards(files,20))
    new=batches(plan_batch_shards(files,20,6),coalesce=True)
    assert old==new
    assert sum(map(len,new))==40


def test_bounded_pipeline_checks_before_checkpoint_and_resumes(tmp_path):
    source=tmp_path/'input.parquet';pq.write_table(pa.table({'n':list(range(12))}),source)
    flag=tmp_path/'fail';flag.touch()
    ck=tmp_path/'checkpoints'
    resources=StageResources(workers=2,devices=('cpu','cpu'),checkpoint_dir=ck)
    def run():
        return run_sharded(worker=producer, postprocess=checker, input_files=[source],
            output_paths={'primary':'parent.parquet'}, staging_root=tmp_path/'out',
            resources=resources,config={'fail_flag':str(flag)},shard_rows=12,batch_shards=2)
    with pytest.raises(Exception,match='fixture quality failure'):
        run()
    assert not (ck/'shard-00001.done').exists()
    completed={p.name:p.read_bytes() for p in ck.glob('*.done')}
    assert completed
    flag.unlink()
    result=run()
    assert result.reused_count==len(completed)
    assert result.total('quality_checked')==12
    assert all((ck/n).read_bytes()==v for n,v in completed.items())
    assert pa.concat_tables([pq.read_table(tmp_path/'out'/p) for p in result.file_paths['primary']]).column('n').to_pylist()==list(range(12))
    timestamps=[(json.loads((p/'produced.json').read_text())['time'],
                 json.loads((p/'checked.json').read_text())['time']) for p in ck.glob('shard-*') if p.is_dir()]
    assert any(a<c<b for a,b in timestamps for c,d in timestamps if c!=a)


def test_stage_status_shows_inner_phase_without_promoting_results(tmp_path):
    from types import SimpleNamespace
    from molcascade.runtime.cache import checkpoint_directory_name
    key='cache:sha256:'+'a'*64;root=tmp_path/checkpoint_directory_name(key)
    activity=root/'activity'/'attempt1'
    task=ShardTask(0,(tmp_path/'input',),0,10,{'primary':tmp_path/'unused'},progress_dir=activity)
    write_progress(root,activity_dir=str(activity),rows_completed=0,phase='map')
    report_activity(task,'pose_relax',poses_total=90,poses_completed=12)
    value=stage_progress(tmp_path,SimpleNamespace(cache_key=key,status='RUNNING'))
    assert value['rows_completed']==0
    assert value['worker_phases']=={'pose_relax':1}
    assert value['worker_sample'][0]['poses_completed']==12
    assert not list(root.glob('*.done'))


def test_both_versions_registered_and_new_catalog_uses_pipeline():
    from molcascade.plugins.registry import create_builtin_registry
    from molcascade.backends.preflight import _BY_PLUGIN_REF
    registry=create_builtin_registry()
    assert not registry.get('docking.unidock@0.2.0').pipelined
    assert registry.get('docking.unidock@0.3.0').pipelined
    assert registry.get('docking.unidock@0.4.0').split_quality
    assert _BY_PLUGIN_REF['docking.unidock@0.2.0']==_BY_PLUGIN_REF['docking.unidock@0.3.0']


def test_deep_activity_path_is_readable_without_shortening_workspace(tmp_path):
    from types import SimpleNamespace
    from molcascade.runtime.cache import checkpoint_directory_name
    checkpoints=tmp_path/('deep-'+'x'*100)/('y'*100)
    key='cache:sha256:'+'b'*64
    root=checkpoints/checkpoint_directory_name(key)
    activity=root/'activity'/'attempt1'
    task=ShardTask(0,(tmp_path/'input',),0,2,{'primary':tmp_path/'unused'},progress_dir=activity)
    write_progress(root,activity_dir=str(activity),phase='map')
    report_activity(task,'pose_check',poses_total=18)
    assert stage_progress(checkpoints,SimpleNamespace(cache_key=key,status='RUNNING'))['worker_phases']=={'pose_check':1}


def split_fixture(task, produced):
    from dataclasses import replace
    data = pq.read_table(produced.output_paths['primary'])
    for i in range(0, data.num_rows, 2):
        root = task.output_paths['primary'].parent / 'chunks' / f'shard-{i//2:05d}'
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'primary.parquet'
        raw = root / 'raw.parquet'
        pq.write_table(data.slice(i, 2), raw)
        yield replace(task, index=i//2, row_count=min(2, data.num_rows-i),
                      input_files=(raw,), output_paths={'primary':path}, device='cpu')


def chunk_checker(task):
    outcome = checker(task)
    pq.write_table(pq.read_table(task.input_files[0]), task.output_paths['primary'])
    return outcome


def join_fixture(task, produced, chunks):
    pq.write_table(pa.concat_tables([pq.read_table(c.output_paths['primary']) for c in chunks]),
                   task.output_paths['primary'])
    return ShardOutcome(task.row_count, {'primary': task.row_count}, {'quality_checked':task.row_count})


def test_fine_cpu_chunks_resume_without_redocking(tmp_path):
    source=tmp_path/'input.parquet';pq.write_table(pa.table({'n':list(range(12))}),source)
    flag=tmp_path/'fail';flag.touch()
    ck=tmp_path/'checkpoints'
    resources=StageResources(workers=3,devices=('cpu',),checkpoint_dir=ck)
    def run():
        return run_sharded(worker=producer, postprocess=chunk_checker,
            postprocess_split=split_fixture, postprocess_join=join_fixture,
            input_files=[source], output_paths={'primary':'parent.parquet'},
            staging_root=tmp_path/'out', resources=resources,
            config={'fail_flag':str(flag)},shard_rows=12,batch_shards=12)
    with pytest.raises(Exception, match='fixture quality failure'):
        run()
    production = next(ck.glob('shard-*/production/shard-*/produced.json'))
    produced_time = production.read_bytes()
    completed = {str(p):p.read_bytes() for p in ck.glob('shard-*/chunks/*.done')}
    assert completed and not (ck/'shard-00000.done').exists()
    # A corrupted completed chunk is recomputed; healthy siblings and native
    # production remain reusable, rather than trusting a completion marker.
    broken = next(iter(completed))
    Path(broken).with_suffix('').joinpath('primary.parquet').write_bytes(b'corrupt')
    completed.pop(broken)
    flag.unlink()
    result = run()
    assert production.read_bytes() == produced_time
    assert all(Path(p).read_bytes()==b for p,b in completed.items())
    assert result.total('quality_checked') == 12
    assert pq.read_table(tmp_path/'out'/result.file_paths['primary'][0]).column('n').to_pylist()==list(range(12))
    progress=json.loads((ck/'progress.json').read_text())
    assert progress['producer_batches_reused']==1
    assert progress['quality_chunks_reused']==len(completed)
    assert progress['cpu_postprocess_workers']==3
    assert progress['pipeline_capacity']==7
