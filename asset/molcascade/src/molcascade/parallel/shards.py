"""Split a stage's input into deterministic pieces, run them, remember which finished.

Parallelism and resumption are the same problem here, which is why they are one
module.  Once a stage's input is cut into fixed, contiguous row ranges, a range
is both a unit of work that can go to another process or another GPU *and* a
unit of progress that survives the run being killed.  Solving either one
separately would have produced most of this code twice.

The rule that makes it safe: **shard boundaries are a function of the input, not
of the machine.**  A 2M-row input is forty 50k shards on a four-core laptop and
forty 50k shards on a sixty-four-core node; only how many run at once differs.
If the boundaries tracked worker count instead, the same ``DETERMINISTIC``
computation would commit a different file layout per machine, and every cache
entry would become machine-local.

Ranges are contiguous, never round-robin, and shard *i* always writes the *i*-th
output file.  Concatenation order is therefore fixed by shard index and never by
completion order -- which is load-bearing, because ``Cardinality.ONE_TO_ONE``
stages and the positional join in the AiZynthFinder adapter both depend on row
order surviving parallel execution.

Nothing here imports :mod:`molcascade.plugins`.  ``plugins.registry`` imports
``plugins.builtin`` at its module bottom, so a builtin adapter reaching back
into the plugin package root would close a cycle.  Callers resolve their
``StageInput`` to paths with ``chemistry.datasets.discover_contract_files``
first and pass plain values in.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import re
import shutil
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from pydantic import JsonValue

from molcascade.artifacts.hashing import sha256_file
from molcascade.errors import MolCascadeError, PluginError
from molcascade.io.atomic import atomic_write_bytes
from molcascade.parallel.devices import pinned_device
from molcascade.parallel.models import ShardedResult, ShardOutcome, ShardTask, StageResources

#: Marker suffix for a shard whose outputs are complete and digest-verified.
DONE_SUFFIX = ".done"

#: Where shards live when the runner gave the stage no durable checkpoint
#: directory.  Inside staging, and removed before the artifact is inventoried,
#: so a resume-less run leaves nothing behind and commits nothing extra.
SCRATCH_DIRNAME = ".shards"

#: Written inside a shard's directory by the worker, read back by the parent.
#: The worker's return value cannot be trusted across the pool boundary for the
#: row counts -- those are re-read from the footers -- but a stage also counts
#: things no footer records, and a file beside the outputs is the one channel
#: that survives both the process boundary and a resume.
OUTCOME_FILENAME = "outcome.json"

#: Output names most adapters already use for the first file of a dataset.
#: Recognised so a split stage extends the sequence instead of producing
#: ``part-00000-part-00001.parquet``.
_PART_STEM = re.compile(r"part-\d{1,10}")

#: Set by the pool initializer in each worker process, read by
#: :func:`_execute_shard` so the device recorded in metadata is the one the
#: process was actually pinned to rather than the one the parent guessed.
_WORKER_DEVICE = "cpu"


class ShardError(PluginError):
    """A shard failed, or a checkpoint could not be trusted."""


def plan_shards(
    input_files: Sequence[Path],
    shard_rows: int,
) -> tuple[tuple[int, int], ...]:
    """Cut the concatenation of ``input_files`` into contiguous row ranges.

    Reads Parquet footers only -- ``num_rows`` per file, no data pages touched --
    so planning a 2M-row input costs a handful of seeks.

    An empty input yields exactly one empty shard rather than none, because
    adapters still have to write a correctly-typed output file for a stage that
    received nothing, and a zero-shard plan would leave the schema unwritten.
    """

    if shard_rows < 1:
        raise ValueError("shard_rows must be positive")
    total = 0
    for path in input_files:
        try:
            total += pq.ParquetFile(path).metadata.num_rows
        except (OSError, pa.ArrowInvalid) as error:
            raise ShardError(
                f"cannot read the Parquet footer of {path}",
                code="SHARD_INPUT_UNREADABLE",
                context={"path": str(path)},
            ) from error
    if total == 0:
        return ((0, 0),)
    return tuple(
        (offset, min(shard_rows, total - offset)) for offset in range(0, total, shard_rows)
    )


def iter_shard_batches(
    task: ShardTask,
    *,
    batch_size: int | None = None,
) -> Iterator[pa.RecordBatch]:
    """Stream just this shard's rows, in input order, without reading the rest.

    Row groups that fall entirely outside the range are never opened, and the
    two partial groups at the ends are trimmed after reading.  A shard in the
    middle of a 2M-row input therefore costs its own rows plus at most two
    row-group reads, not a scan from row zero.
    """

    size = batch_size or task.batch_size
    remaining = task.row_count
    cursor = 0  # first row of the current file, in whole-input coordinates
    for path in task.input_files:
        if remaining <= 0:
            break
        handle = pq.ParquetFile(path)
        rows = handle.metadata.num_rows
        start = max(task.row_offset - cursor, 0)
        stop = min(task.row_offset + task.row_count - cursor, rows)
        cursor += rows
        if start >= stop:
            continue

        groups: list[int] = []
        group_start = 0
        first_group_offset = 0
        for index in range(handle.num_row_groups):
            group_rows = handle.metadata.row_group(index).num_rows
            if group_start < stop and group_start + group_rows > start:
                if not groups:
                    first_group_offset = group_start
                groups.append(index)
            group_start += group_rows
        if not groups:
            continue

        # Position within the concatenation of the *selected* groups, which is
        # what iter_batches will hand back -- hence the offset correction.
        head = start - first_group_offset
        want = stop - start
        position = 0
        for batch in handle.iter_batches(batch_size=size, row_groups=groups):
            if want <= 0:
                break
            length = batch.num_rows
            begin = max(head - position, 0)
            take = min(length - begin, want)
            position += length
            if take <= 0:
                continue
            trimmed = batch.slice(begin, take)
            want -= take
            remaining -= take
            yield trimmed


def read_side_input(
    task: ShardTask,
    name: str,
    *,
    keys: Sequence[str],
    key_column: str = "parent_id",
    columns: Sequence[str] | None = None,
) -> pa.Table:
    """Read the rows of a side input that belong to this shard's molecules.

    The filter is pushed into the Parquet reader, so a shard pays for the column
    chunks it needs rather than for the whole evidence table -- but it does pay
    per shard, because the two datasets are joined on identity and neither one
    is indexed.  That is the right trade for the adapters that need this: an
    engine spending seconds per ligand will not notice a scan, and a cheap stage
    has no business asking for evidence it could recompute.
    """

    files = task.side_inputs.get(name)
    if not files:
        raise ShardError(
            f"shard {task.index} was given no files for side input {name!r}",
            code="SHARD_SIDE_INPUT_MISSING",
            context={"shard_index": task.index, "side_input": name},
        )
    dataset = ds.dataset([str(path) for path in files], format="parquet")
    wanted = pa.array(list(dict.fromkeys(keys)), type=pa.string())
    return dataset.to_table(
        columns=list(columns) if columns is not None else None,
        filter=ds.field(key_column).isin(wanted),
    )


def _shard_directory(work_dir: Path, index: int) -> Path:
    return work_dir / f"shard-{index:05d}"


def _done_marker(work_dir: Path, index: int) -> Path:
    return work_dir / f"shard-{index:05d}{DONE_SUFFIX}"


def _read_checkpoint(work_dir: Path, index: int, ports: Sequence[str]) -> ShardOutcome | None:
    """Return a shard's recorded outcome, or ``None`` if it cannot be trusted.

    A marker alone is not evidence.  Every declared output must still exist and
    still digest to the value recorded when it was written, because the failure
    that produced the interruption is exactly the kind that truncates a file
    mid-write.  Anything that does not verify is treated as absent and recomputed;
    nothing here repairs or complains, since recomputing is always correct.
    """

    marker = _done_marker(work_dir, index)
    if not marker.is_file():
        return None
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    digests = record.get("digests")
    rows_out = record.get("rows_out")
    if not isinstance(digests, dict) or not isinstance(rows_out, dict):
        return None
    if set(digests) != set(ports):
        # The stage's output ports changed since this checkpoint was written.
        return None
    directory = _shard_directory(work_dir, index)
    for port, expected in digests.items():
        path = directory / f"{port}.parquet"
        if path.is_symlink() or not path.is_file():
            return None
        try:
            if sha256_file(path) != expected:
                return None
        except (OSError, ValueError):
            return None
    return ShardOutcome(
        rows_in=int(record.get("rows_in") or 0),
        rows_out={port: int(count) for port, count in rows_out.items()},
        metadata=dict(record.get("metadata") or {}),
    )


def _write_checkpoint(work_dir: Path, index: int, outcome: ShardOutcome) -> None:
    """Record a shard as complete, after its bytes are on disk.

    Written by the parent rather than the worker, so the marker cannot outlive
    the parent's knowledge of the result, and written atomically, so a marker is
    never half a file.
    """

    directory = _shard_directory(work_dir, index)
    digests = {
        port: sha256_file(directory / f"{port}.parquet") for port in sorted(outcome.rows_out)
    }
    payload = json.dumps(
        {
            "rows_in": outcome.rows_in,
            "rows_out": dict(sorted(outcome.rows_out.items())),
            "digests": digests,
            "metadata": dict(outcome.metadata),
        },
        sort_keys=True,
    ).encode("utf-8")
    atomic_write_bytes(_done_marker(work_dir, index), payload)


def _pin_worker(queue: Any) -> None:
    """Give this worker process one card, before any CUDA library is loaded.

    ``CUDA_VISIBLE_DEVICES`` rather than an API call because it is the only
    mechanism every backend obeys -- including the several that expose no device
    argument at all.  It must be set before the framework initialises, which is
    why it happens in the pool initializer and not in the task.

    A worker that finds the queue empty (the pool replaced a dead process, and
    every lane is already claimed) stays on CPU rather than doubling up on a
    card that is already busy.
    """

    global _WORKER_DEVICE
    try:
        device = queue.get_nowait()
    except Exception:
        device = "cpu"
    _WORKER_DEVICE = device
    if device.startswith("cuda:"):
        os.environ["CUDA_VISIBLE_DEVICES"] = device.removeprefix("cuda:")


def _execute_shard(
    worker: Callable[[ShardTask], ShardOutcome],
    task: ShardTask,
) -> ShardOutcome:
    """Run one shard in whichever process picked it up.

    The task is re-stamped with the device this process was actually pinned to.
    The parent assigns lanes round-robin when it builds the tasks, but the pool
    decides which worker runs which shard, so the parent's guess is a plan and
    this is the fact.
    """

    directory = task.output_paths[next(iter(task.output_paths))].parent
    directory.mkdir(parents=True, exist_ok=True)
    sidecar = directory / OUTCOME_FILENAME
    for path in (*task.output_paths.values(), sidecar):
        path.unlink(missing_ok=True)
    pinned = task if task.device == _WORKER_DEVICE else _replace_device(task, _WORKER_DEVICE)
    try:
        outcome = worker(pinned)
        if outcome.metadata:
            # After the outputs, never before: a sidecar without its parquet
            # would make a failed shard look like it had something to say.
            atomic_write_bytes(
                sidecar,
                json.dumps(dict(outcome.metadata), sort_keys=True).encode("utf-8"),
            )
        return outcome
    except BaseException:
        # A half-written parquet that a later attempt might mistake for progress
        # is worse than no file at all.  The digest check would catch it, but
        # only by reading bytes that never had to exist.
        for path in (*task.output_paths.values(), sidecar):
            path.unlink(missing_ok=True)
        raise


def _replace_device(task: ShardTask, device: str) -> ShardTask:
    return ShardTask(
        index=task.index,
        input_files=task.input_files,
        row_offset=task.row_offset,
        row_count=task.row_count,
        output_paths=task.output_paths,
        stage_id=task.stage_id,
        config=task.config,
        device=device,
        batch_size=task.batch_size,
    )


def _staged_name(relative: str, index: int, total: int) -> str:
    """Name shard ``index``'s copy of the output declared at ``relative``.

    A single-shard stage keeps the historical name exactly, so every small run
    and every existing test still commits the same file at the same path.

    Most adapters already declare ``.../part-00000.parquet``, which is the
    conventional name for the first file of a multi-file dataset; those simply
    continue the sequence.  Anything else grows a ``-part-00000`` suffix.
    """

    if total <= 1:
        return relative
    path = PurePosixPath(relative)
    if _PART_STEM.fullmatch(path.stem):
        stem = f"part-{index:05d}"
    else:
        stem = f"{path.stem}-part-{index:05d}"
    return str(path.with_name(f"{stem}{path.suffix}"))


def run_sharded(
    *,
    worker: Callable[[ShardTask], ShardOutcome],
    input_files: Sequence[Path],
    output_paths: Mapping[str, str],
    staging_root: Path,
    resources: StageResources,
    stage_id: str = "",
    config: Mapping[str, Any] | None = None,
    shard_rows: int | None = None,
    side_inputs: Mapping[str, Sequence[Path]] | None = None,
) -> ShardedResult:
    """Run ``worker`` over every shard of ``input_files`` and stage the results.

    ``shard_rows`` is an adapter's *ceiling*, not an override.  An engine whose
    per-row cost is orders of magnitude above the default declares one so an
    interruption cannot cost hours of work; a caller asking for something
    smaller still gets it, because more parquet footers is the only price of a
    finer shard and losing less work is always the safer direction.

    ``worker`` must be a module-level function: ``spawn`` re-imports it in a
    fresh interpreter by qualified name, so a closure or a bound method fails at
    the process boundary.  It receives a :class:`ShardTask` and must write
    exactly the files named in ``task.output_paths``.

    ``output_paths`` maps each output port to the relative path the stage would
    have written single-threaded (``{"primary": "parent.parquet"}``).  The
    returned :attr:`ShardedResult.file_paths` gives the actual staged names per
    port, in shard order, ready to hand to ``PendingOutput``.

    ``side_inputs`` names datasets every shard receives whole rather than
    sliced -- evidence a worker looks a molecule up in, not a population it
    divides.  They are passed as paths and joined by the worker, because
    slicing them by the same row range would silently assume the two datasets
    are aligned, which a filter between the two stages is free to break.

    Shards that already carry a verified checkpoint are not re-run.  Shards that
    fail leave every completed sibling's checkpoint intact, which is the whole
    point: the next attempt resumes rather than restarts.
    """

    if not output_paths:
        raise ValueError("run_sharded requires at least one output port")
    if not input_files:
        raise ValueError("run_sharded requires at least one input file")

    ports = tuple(sorted(output_paths))
    rows_per_shard = min(shard_rows or resources.shard_rows, resources.shard_rows)
    ranges = plan_shards(input_files, rows_per_shard)
    total = len(ranges)
    lanes = resources.devices or ("cpu",)

    staging_root.mkdir(parents=True, exist_ok=True)
    scratch: Path | None = None
    if resources.checkpoint_dir is not None:
        work_dir = resources.checkpoint_dir
    else:
        work_dir = staging_root / SCRATCH_DIRNAME
        scratch = work_dir
    work_dir.mkdir(parents=True, exist_ok=True)

    tasks = tuple(
        ShardTask(
            index=index,
            input_files=tuple(Path(path) for path in input_files),
            row_offset=offset,
            row_count=count,
            output_paths={
                port: _shard_directory(work_dir, index) / f"{port}.parquet" for port in ports
            },
            side_inputs={
                name: tuple(Path(path) for path in paths)
                for name, paths in (side_inputs or {}).items()
            },
            stage_id=stage_id,
            config=dict(config or {}),
            device=lanes[index % len(lanes)],
            batch_size=resources.batch_size,
        )
        for index, (offset, count) in enumerate(ranges)
    )

    try:
        outcomes: dict[int, ShardOutcome] = {}
        pending: list[ShardTask] = []
        for task in tasks:
            reused = (
                _read_checkpoint(work_dir, task.index, ports)
                if resources.reuse_checkpoints
                else None
            )
            if reused is None:
                pending.append(task)
            else:
                outcomes[task.index] = reused
        reused_count = total - len(pending)

        if pending:
            durable = resources.checkpoint_dir is not None

            def record(task: ShardTask) -> None:
                """Bank one shard the moment it lands, not when the batch ends.

                Called per completion rather than after the loop, because the
                run that needs this most is the one that fails: a stage that
                dies on shard 5 of 40 must still have shards 0-4 -- and 6-20,
                which were in flight -- marked done, or the retry pays for them
                again and resume has bought nothing.
                """

                outcome = _collect(task, ports)
                if durable:
                    _write_checkpoint(work_dir, task.index, outcome)
                outcomes[task.index] = outcome

            _run_tasks(
                pending,
                worker=worker,
                lanes=lanes,
                workers=resources.workers,
                on_complete=record,
            )

        return _stage_outputs(
            tasks,
            outcomes,
            output_paths=output_paths,
            staging_root=staging_root,
            work_dir=work_dir,
            lanes=lanes,
            reused_count=reused_count,
            notes=resources.notes,
        )
    finally:
        if scratch is not None:
            # Never committed: the artifact inventory must not see a shard tree.
            shutil.rmtree(scratch, ignore_errors=True)


def _run_tasks(
    tasks: Sequence[ShardTask],
    *,
    worker: Callable[[ShardTask], ShardOutcome],
    lanes: Sequence[str],
    workers: int,
    on_complete: Callable[[ShardTask], None],
) -> None:
    """Execute the pending shards, inline or across a spawned pool.

    ``on_complete`` runs in this process for each shard that succeeded, even
    after another shard has already failed.  Work that finished is worth
    keeping regardless of what happened beside it.
    """

    parallel = min(workers, len(lanes), len(tasks))
    if parallel <= 1:
        # One lane still has to be pinned -- a single-GPU machine is the common
        # case, and running unpinned there would leave the card unused.
        global _WORKER_DEVICE
        previous = _WORKER_DEVICE
        with pinned_device(lanes[0]):
            _WORKER_DEVICE = lanes[0]
            try:
                for task in tasks:
                    try:
                        _execute_shard(worker, task)
                    except Exception as error:
                        raise _shard_failure(task, error, len(tasks)) from error
                    on_complete(task)
            finally:
                _WORKER_DEVICE = previous
        return

    # Spawn, not fork: the runner holds a threading.RLock and the adapters have
    # already imported RDKit and possibly Torch. Forking that is a documented
    # deadlock source, and CUDA contexts do not survive a fork at all.
    context = mp.get_context("spawn")
    queue = context.Queue()
    for device in lanes[:parallel]:
        queue.put(device)
    executor = ProcessPoolExecutor(
        max_workers=parallel,
        mp_context=context,
        initializer=_pin_worker,
        initargs=(queue,),
    )
    failure: tuple[ShardTask, BaseException] | None = None
    try:
        futures = {executor.submit(_execute_shard, worker, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            if future.cancelled():
                continue
            error = future.exception()
            if error is not None:
                if failure is None:
                    failure = (task, error)
                    # Shards not yet started are pointless once the stage is
                    # doomed; ones already running are left to finish and be
                    # banked, since their cost is already paid.
                    for queued in futures:
                        queued.cancel()
                continue
            on_complete(task)
    finally:
        executor.shutdown(wait=True)

    if failure is not None:
        task, error = failure
        raise _shard_failure(task, error, len(tasks)) from error


def _shard_failure(task: ShardTask, error: BaseException, total: int) -> MolCascadeError:
    """One message for both execution paths.

    A user should not be able to tell from the error whether their stage ran
    inline or across a pool -- that is a machine detail, and the instruction
    that follows from the failure is the same either way.

    An adapter's own diagnosis is passed through rather than wrapped.  When a
    stage says a parent cannot be parsed or a score came back non-finite, it has
    reached a verdict about the data; which shard the row happened to fall in is
    a detail worth recording in the context and no reason to replace a precise
    code with a generic execution failure.  Sharding is meant to be invisible to
    everything except throughput.
    """

    location: dict[str, JsonValue] = {"shard_index": task.index, "device": task.device}
    if isinstance(error, MolCascadeError) and not isinstance(error, ShardError):
        return type(error)(
            error.message,
            code=error.code,
            hint=error.hint,
            retryable=error.retryable,
            context={**error.context, **location},
        )
    return ShardError(
        f"shard {task.index} of {total} failed: {error}",
        code="SHARD_EXECUTION_FAILED",
        hint=(
            "Completed shards were kept, so starting the run again with --resume "
            "continues from where this stopped rather than from the first molecule."
        ),
        context=location,
    )


def _collect(task: ShardTask, ports: Sequence[str]) -> ShardOutcome:
    """Read back what a shard actually wrote, rather than trusting its report.

    The worker's own ``ShardOutcome`` never crosses back for the row counts:
    ``_run_tasks`` discards it, because in the pool case the counts and the
    files are two separate claims and only the files get committed.  Reading
    the footers costs a seek per port and makes the two agree by construction.
    """

    rows_out: dict[str, int] = {}
    directory: Path | None = None
    for port in ports:
        path = task.output_paths[port]
        directory = path.parent
        if path.is_symlink() or not path.is_file():
            raise ShardError(
                f"shard {task.index} did not write its '{port}' output",
                code="SHARD_OUTPUT_MISSING",
                context={"shard_index": task.index, "port": port, "path": str(path)},
            )
        try:
            rows_out[port] = pq.ParquetFile(path).metadata.num_rows
        except (OSError, pa.ArrowInvalid) as error:
            raise ShardError(
                f"shard {task.index} wrote an unreadable '{port}' output",
                code="SHARD_OUTPUT_INVALID",
                context={"shard_index": task.index, "port": port, "path": str(path)},
            ) from error
    return ShardOutcome(
        rows_in=task.row_count,
        rows_out=rows_out,
        metadata=_read_outcome(directory) if directory is not None else {},
    )


def _read_outcome(directory: Path) -> dict[str, Any]:
    """Whatever the worker counted, or nothing if it counted nothing.

    Absence is not an error: most stages report only rows, and a shard that
    wrote no sidecar simply had no extra numbers to add.
    """

    path = directory / OUTCOME_FILENAME
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _stage_outputs(
    tasks: Sequence[ShardTask],
    outcomes: Mapping[int, ShardOutcome],
    *,
    output_paths: Mapping[str, str],
    staging_root: Path,
    work_dir: Path,
    lanes: Sequence[str],
    reused_count: int,
    notes: Sequence[str],
) -> ShardedResult:
    """Copy every shard's outputs into staging under their committed names.

    Copied, not linked and not moved.  ``ArtifactStore`` rejects a staged file
    with ``st_nlink != 1``, so hard-linking is out; and moving would leave the
    ``.done`` markers pointing at files that no longer exist if the commit then
    failed, turning a recoverable interruption into a corrupt checkpoint tree.
    Copying costs one pass over the output bytes and keeps the invariant that
    the checkpoint directory owns its shards until the artifact is committed.
    """

    total = len(tasks)
    staged: dict[str, list[str]] = {port: [] for port in output_paths}
    rows_out: dict[str, int] = {port: 0 for port in output_paths}
    shard_metadata: list[Mapping[str, Any]] = []
    rows_in = 0

    for task in tasks:
        outcome = outcomes[task.index]
        rows_in += outcome.rows_in
        shard_metadata.append(outcome.metadata)
        for port, relative in output_paths.items():
            name = _staged_name(relative, task.index, total)
            destination = staging_root / PurePosixPath(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise ShardError(
                    f"staged shard output already exists: {name}",
                    code="PLUGIN_STAGING_NOT_EMPTY",
                    context={"path": name},
                )
            shutil.copyfile(task.output_paths[port], destination)
            staged[port].append(name)
            rows_out[port] += outcome.rows_out.get(port, 0)

    metadata: dict[str, Any] = {}
    if notes:
        metadata["execution_notes"] = list(notes)
    if work_dir.name != SCRATCH_DIRNAME:
        metadata["resume_supported"] = True
    return ShardedResult(
        file_paths={port: tuple(names) for port, names in staged.items()},
        rows_in=rows_in,
        rows_out=rows_out,
        shard_count=total,
        reused_count=reused_count,
        devices=tuple(lanes),
        metadata=metadata,
        shard_metadata=tuple(shard_metadata),
    )


def count_completed_shards(checkpoint_dir: Path) -> tuple[int, int]:
    """``(complete, total)`` for a stage in progress, for ``molcascade status``.

    Cheap on purpose -- it counts markers and shard directories, and verifies
    nothing.  A status command that digested every checkpoint would read the
    whole intermediate dataset just to print a fraction.
    """

    if not checkpoint_dir.is_dir():
        return (0, 0)
    done = sum(1 for path in checkpoint_dir.glob(f"*{DONE_SUFFIX}") if path.is_file())
    started = sum(1 for path in checkpoint_dir.glob("shard-*") if path.is_dir())
    return (done, max(done, started))


__all__ = [
    "DONE_SUFFIX",
    "OUTCOME_FILENAME",
    "SCRATCH_DIRNAME",
    "ShardError",
    "count_completed_shards",
    "iter_shard_batches",
    "plan_shards",
    "read_side_input",
    "run_sharded",
]
