"""Bounded accelerator production followed by CPU work on existing shard files.

No scheduler service or new scientific ledger. The existing parent commits a
checkpoint only after both phases succeed. A producer can start another batch
while CPU consumers finish prior batches; backpressure caps unfinished batches.
"""
from __future__ import annotations

import json
import hashlib
import multiprocessing as mp
from collections import deque
from dataclasses import replace
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from molcascade.io.atomic import atomic_write_bytes


def _finish(worker, task, input_identity=None):
    from dataclasses import replace
    from molcascade.parallel.progress import report_activity
    from molcascade.parallel.shards import OUTCOME_FILENAME
    task = replace(task, device='cpu')
    directory = next(iter(task.output_paths.values())).parent
    try:
        outcome = worker(task)
        if input_identity is not None:
            outcome = replace(outcome, metadata={**outcome.metadata,
                'postprocess_input_identity': input_identity})
        atomic_write_bytes(directory / OUTCOME_FILENAME,
                           json.dumps(dict(outcome.metadata), sort_keys=True).encode())
        report_activity(task, "idle", rows_completed=task.row_count)
        return outcome
    except BaseException:
        report_activity(task, "failed")
        raise


def run_pipeline(tasks, *, worker, postprocess, lanes, workers, on_complete,
                 split=None, join=None, reuse_checkpoints=True):
    if split is not None:
        if join is None:
            raise ValueError('split postprocessing requires an ordered join')
        return _run_split_pipeline(tasks, worker=worker, postprocess=postprocess,
            lanes=lanes, workers=workers, on_complete=on_complete,
            split=split, join=join, reuse_checkpoints=reuse_checkpoints)
    from molcascade.parallel.shards import _execute_shard, _pin_worker, _shard_failure
    gpu_count = min(workers, len(lanes), len(tasks))
    cpu_count = min(workers, len(tasks))
    capacity = gpu_count + 2 * cpu_count
    context = mp.get_context("spawn")
    queue = context.Queue()
    for device in lanes[:gpu_count]:
        queue.put(device)
    ready = deque()
    remaining = iter(tasks)
    exhausted = False
    failure = None
    producers, consumers = {}, {}
    with (ProcessPoolExecutor(max_workers=gpu_count, mp_context=context,
                              initializer=_pin_worker, initargs=(queue,)) as gpu,
          ProcessPoolExecutor(max_workers=cpu_count, mp_context=context) as cpu):
        while True:
            while ready and len(consumers) < cpu_count:
                task = ready.popleft()
                consumers[cpu.submit(_finish, postprocess, task)] = task
            while (not exhausted and failure is None and len(producers) < gpu_count
                   and len(producers) + len(consumers) + len(ready) < capacity):
                task = next(remaining, None)
                if task is None:
                    exhausted = True
                    break
                producers[gpu.submit(_execute_shard, worker, task)] = task
            if not producers and not consumers and not ready:
                break
            done, _ = wait([*producers, *consumers], return_when=FIRST_COMPLETED)
            for future in done:
                is_producer = future in producers
                task = (producers if is_producer else consumers).pop(future)
                try:
                    future.result()
                    if is_producer:
                        ready.append(task)
                    else:
                        on_complete(task)
                except Exception as error:
                    if failure is None:
                        failure = (task, error)
                    # Drain already-started useful work, but never launch more inputs.
        if failure is not None:
            task, error = failure
            raise _shard_failure(task, error, len(tasks)) from error


def _run_split_pipeline(tasks, *, worker, postprocess, lanes, workers,
                        on_complete, split, join, reuse_checkpoints):
    """Preserve engine calls; share finer CPU tasks and the existing digest checkpoints."""
    from molcascade.parallel.shards import (
        _execute_shard, _pin_worker, _shard_failure, _read_checkpoint,
        _write_checkpoint, _collect, OUTCOME_FILENAME,
    )
    from molcascade.parallel.progress import write_progress, read_progress
    from molcascade.artifacts.hashing import sha256_file
    context = mp.get_context('spawn')
    gpu_count = min(workers, len(lanes), len(tasks))
    queue = context.Queue()
    for device in lanes[:gpu_count]:
        queue.put(device)
    capacity = gpu_count + 2 * workers
    remaining = iter(tasks)
    ready = deque()
    producers, consumers, active = {}, {}, {}
    exhausted = False
    failure = None
    metrics = dict(producer_batches_completed=0, producer_batches_reused=0,
                   quality_chunks_completed=0, quality_chunks_reused=0,
                   quality_rows_completed=0, quality_rows_reused=0)
    root = tasks[0].progress_dir.parent.parent if tasks[0].progress_dir else None
    root_reused = read_progress(root).get('rows_reused', 0)
    metrics.update(quality_rows_completed=root_reused, quality_rows_reused=root_reused)

    def report():
        write_progress(root, **metrics, cpu_postprocess_workers=workers,
            cpu_active_chunks=len(consumers), cpu_queued_chunks=len(ready),
            producer_active_batches=len(producers), pipeline_capacity=capacity)

    def finish_parent(index):
        task, produced, chunks, pending = active[index]
        if pending:
            return
        outcome = join(task, produced, chunks)
        directory = next(iter(task.output_paths.values())).parent
        atomic_write_bytes(directory / OUTCOME_FILENAME,
            json.dumps(dict(outcome.metadata), sort_keys=True).encode())
        on_complete(task)
        del active[index]

    def accept_production(task, produced):
        metrics['producer_batches_completed'] += 1
        chunks = tuple(split(task, produced))
        pending = set()
        for chunk in chunks:
            chunk_root = next(iter(chunk.output_paths.values())).parent.parent
            identity = hashlib.sha256(json.dumps(
                [sha256_file(p) for p in chunk.input_files]).encode()).hexdigest()
            old = _read_checkpoint(chunk_root, chunk.index, tuple(chunk.output_paths)) if reuse_checkpoints else None
            if old is not None and old.metadata.get('postprocess_input_identity') == identity:
                metrics['quality_chunks_completed'] += 1
                metrics['quality_chunks_reused'] += 1
                metrics['quality_rows_completed'] += old.rows_in
                metrics['quality_rows_reused'] += old.rows_in
            else:
                pending.add(chunk.index)
                ready.append((task.index, chunk, identity))
        active[task.index] = (task, produced, chunks, pending)
        finish_parent(task.index)

    with (ProcessPoolExecutor(max_workers=gpu_count, mp_context=context,
            initializer=_pin_worker, initargs=(queue,)) as gpu,
          ProcessPoolExecutor(max_workers=workers, mp_context=context) as cpu):
        while True:
            while ready and len(consumers) < workers:
                index, chunk, identity = ready.popleft()
                consumers[cpu.submit(_finish, postprocess, chunk, identity)] = (index, chunk)
            while (not exhausted and failure is None and len(producers) < gpu_count
                   and len(active) + len(producers) < capacity):
                task = next(remaining, None)
                if task is None:
                    exhausted = True
                    break
                directory = next(iter(task.output_paths.values())).parent
                prod_root = directory / 'production'
                produced = replace(task, output_paths={
                    p: prod_root / f'shard-{task.index:05d}' / f'{p}.parquet'
                    for p in task.output_paths})
                old = _read_checkpoint(prod_root, task.index, tuple(task.output_paths)) if reuse_checkpoints else None
                if old is not None:
                    metrics['producer_batches_reused'] += 1
                    accept_production(task, produced)
                else:
                    producers[gpu.submit(_execute_shard, worker, produced)] = (task, produced)
            report()
            if not producers and not consumers:
                if ready:
                    continue
                break
            done, _ = wait([*producers, *consumers], return_when=FIRST_COMPLETED)
            for future in done:
                producing = future in producers
                item = (producers if producing else consumers).pop(future)
                task = item[0] if producing else active[item[0]][0]
                try:
                    future.result()
                    if producing:
                        produced = item[1]
                        prod_root = next(iter(produced.output_paths.values())).parent.parent
                        _write_checkpoint(prod_root, produced.index, _collect(produced, tuple(produced.output_paths)))
                        accept_production(task, produced)
                    else:
                        index, chunk = item
                        chunk_root = next(iter(chunk.output_paths.values())).parent.parent
                        outcome = _collect(chunk, tuple(chunk.output_paths))
                        _write_checkpoint(chunk_root, chunk.index, outcome)
                        metrics['quality_chunks_completed'] += 1
                        metrics['quality_rows_completed'] += outcome.rows_in
                        active[index][3].remove(chunk.index)
                        finish_parent(index)
                except Exception as error:
                    if failure is None:
                        failure = (task, error)
            report()
        if failure is not None:
            task, error = failure
            raise _shard_failure(task, error, len(tasks)) from error
