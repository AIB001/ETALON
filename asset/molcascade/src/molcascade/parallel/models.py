"""The values that cross the parent/worker boundary, and nothing else.

This module is deliberately a leaf: stdlib and nothing from ``molcascade``.
``molcascade.plugins.registry`` imports ``molcascade.plugins.builtin`` at its
module bottom, so anything a builtin adapter reaches for must not reach back
into the plugin package -- and ``plugins.api`` imports :class:`StageResources`
from here.  Keeping this file dependency-free is what makes that safe.

Everything here is also picklable by construction.  ``spawn`` re-imports the
worker module in a fresh interpreter and unpickles its argument, so a task that
carried a live plugin instance, an open connection, or a ``MappingProxyType``
would fail at the boundary rather than in a test.  Paths, ints, strings and
plain dicts are all that travel.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Rows per shard when an adapter expresses no opinion.
#:
#: A *code* constant, not a setting.  Shard boundaries decide the artifact's
#: file layout, so if this tracked worker count or GPU count the same
#: deterministic computation would commit different bytes on a four-core laptop
#: than on a sixty-four-core node, and every cache entry would be machine-local.
#: An adapter whose per-molecule cost is orders of magnitude higher (docking,
#: retrosynthesis) declares its own constant instead; that is still code, still
#: machine-independent, and still covered by the plugin version already present
#: in the pipeline revision.
DEFAULT_SHARD_ROWS = 50_000

#: Cap on lanes, matching :class:`~molcascade.environment.models.ExecutionPlan`.
MAX_WORKERS = 1024


@dataclass(frozen=True, slots=True)
class StageResources:
    """How much of this machine one stage may use, decided by the runner.

    Never part of a stage's config and therefore never part of
    ``stage_cache_key``.  Two machines that disagree about worker count must
    still agree about the artifact, so these values travel on
    :class:`~molcascade.plugins.api.StageContext` instead -- runner-owned, like
    the staging root next to them.

    The default is the historical behaviour: one worker, no accelerator, no
    checkpoint directory.  Every caller that predates this module keeps working
    and keeps producing the same bytes.
    """

    workers: int = 1
    #: One entry per execution lane, in assignment order: ``"cpu"`` or
    #: ``"cuda:N"`` naming a *physical* device index.
    devices: tuple[str, ...] = ("cpu",)
    #: Exactly what the user asked for -- ``"auto"``, ``"cpu"``, ``"cuda"`` or
    #: ``"cuda:0,2"``.  Kept because the two failure directions differ: an
    #: automatic plan may quietly degrade to CPU as long as it says why, while
    #: an explicit ``--device cuda`` that cannot be honoured must stop the run.
    device_request: str = "auto"
    #: Durable directory this stage may leave completed shards in.  ``None``
    #: disables resume; the shards then live in staging and die with it.
    checkpoint_dir: Path | None = None
    #: ``False`` when the run was started with ``--force``, which must ignore
    #: checkpoints for the same reason it ignores the stage cache.
    reuse_checkpoints: bool = True
    batch_size: int = 65_536
    shard_rows: int = DEFAULT_SHARD_ROWS
    #: Human-readable reasons the plan is what it is -- above all, why a GPU
    #: that exists went unused.  Copied into the stage's response metadata so
    #: an eight-hour CPU run is never silent about having been one.
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.workers < 1 or self.workers > MAX_WORKERS:
            raise ValueError(f"workers must be between 1 and {MAX_WORKERS}")
        if not self.devices:
            raise ValueError("at least one execution lane is required")
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self.shard_rows < 1:
            raise ValueError("shard_rows must be positive")
        for device in self.devices:
            if device != "cpu" and not device.startswith("cuda:"):
                raise ValueError(f"unsupported execution lane: {device!r}")
        if self.checkpoint_dir is not None:
            object.__setattr__(self, "checkpoint_dir", Path(self.checkpoint_dir))
        object.__setattr__(self, "devices", tuple(self.devices))
        object.__setattr__(self, "notes", tuple(self.notes))

    @property
    def uses_gpu(self) -> bool:
        return any(device.startswith("cuda:") for device in self.devices)

    def with_shard_rows(self, shard_rows: int) -> StageResources:
        """Return a copy for an adapter that knows its own per-row cost."""

        return self.replace(shard_rows=shard_rows)

    def replace(self, **changes: Any) -> StageResources:
        """Return a copy with ``changes`` applied; frozen dataclass, no mutation."""

        fields = {
            "workers": self.workers,
            "devices": self.devices,
            "device_request": self.device_request,
            "checkpoint_dir": self.checkpoint_dir,
            "reuse_checkpoints": self.reuse_checkpoints,
            "batch_size": self.batch_size,
            "shard_rows": self.shard_rows,
            "notes": self.notes,
        }
        unknown = set(changes) - set(fields)
        if unknown:
            raise TypeError(f"unknown StageResources field(s): {sorted(unknown)}")
        fields.update(changes)
        return StageResources(**fields)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ShardTask:
    """One contiguous row range, and everywhere its results must be written.

    ``row_offset`` and ``row_count`` address the *concatenation* of
    ``input_files`` in the order given, which is why that order is fixed by the
    caller and never by directory iteration.
    """

    index: int
    input_files: tuple[Path, ...]
    row_offset: int
    row_count: int
    #: Output port -> absolute path this shard must create.
    output_paths: Mapping[str, Path]
    #: Name -> every file of a dataset the worker may need to *look things up*
    #: in, as opposed to slice.  A docking engine reads the conformers of the
    #: molecules in its own range and nothing else, so the range addresses the
    #: population while the geometry arrives whole and is joined on
    #: ``parent_id``.  Paths only, for the same reason as ``input_files``.
    side_inputs: Mapping[str, tuple[Path, ...]] = field(default_factory=dict)
    #: The stage this shard belongs to.  Carried because decision rows record
    #: the stage that made them, and a worker in another process has no other
    #: way to know: the field is not part of the stage's config, and putting it
    #: there would break every adapter's strict config model.
    stage_id: str = ""
    #: The stage's validated config, re-validated in the child.  Cheap, and it
    #: keeps a worker fail-closed instead of trusting whatever was pickled.
    config: Mapping[str, Any] = field(default_factory=dict)
    #: The lane this shard was assigned, for metadata and for adapters that
    #: take an explicit device argument.  ``CUDA_VISIBLE_DEVICES`` is already
    #: set in the worker; this is the record of it, not the mechanism.
    device: str = "cpu"
    batch_size: int = 65_536

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("shard index must not be negative")
        if self.row_offset < 0 or self.row_count < 0:
            raise ValueError("shard row range must not be negative")
        if not self.input_files:
            raise ValueError("shard must name at least one input file")
        if not self.output_paths:
            raise ValueError("shard must declare at least one output path")
        object.__setattr__(self, "input_files", tuple(Path(p) for p in self.input_files))
        object.__setattr__(
            self,
            "output_paths",
            {port: Path(path) for port, path in self.output_paths.items()},
        )
        object.__setattr__(
            self,
            "side_inputs",
            {
                name: tuple(Path(path) for path in paths)
                for name, paths in self.side_inputs.items()
            },
        )
        object.__setattr__(self, "config", dict(self.config))


@dataclass(frozen=True, slots=True)
class ShardOutcome:
    """What one shard produced, as reported back across the process boundary."""

    rows_in: int
    #: Output port -> rows written.  A ``FILTER`` stage writes fewer parent
    #: rows than it read; both numbers are kept so the funnel can be audited.
    rows_out: Mapping[str, int]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows_out", dict(self.rows_out))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class ShardedResult:
    """The merged answer ``run_sharded`` hands back to an adapter."""

    #: Output port -> staged file names, ordered by shard index.  Order is the
    #: point: ``ONE_TO_ONE`` stages and every positional join downstream depend
    #: on row order surviving parallel execution.
    file_paths: Mapping[str, tuple[str, ...]]
    rows_in: int
    rows_out: Mapping[str, int]
    shard_count: int
    reused_count: int
    devices: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    #: What each shard reported, in shard order.  Row counts come back from the
    #: Parquet footers, but anything a stage counts that the footers do not
    #: record -- warnings versus rejections, endpoints evaluated, molecules that
    #: failed to embed -- can only travel this way.  Left for the adapter to
    #: reduce, because summing is right for a counter and wrong for a set.
    shard_metadata: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "file_paths",
            {port: tuple(paths) for port, paths in self.file_paths.items()},
        )
        object.__setattr__(self, "rows_out", dict(self.rows_out))
        object.__setattr__(self, "devices", tuple(self.devices))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(
            self, "shard_metadata", tuple(dict(item) for item in self.shard_metadata)
        )

    def total(self, key: str) -> int:
        """Sum one integer counter across every shard, reused ones included."""

        return sum(int(item.get(key, 0)) for item in self.shard_metadata)

    def first(self, key: str, default: Any = None) -> Any:
        """The first shard's value for ``key``, for facts every shard agrees on.

        A backend version or a method identifier is the same in every worker by
        construction; taking it from shard zero avoids recomputing in the parent
        something only the child had the imports to determine.
        """

        for item in self.shard_metadata:
            if key in item:
                return item[key]
        return default

    def response_metadata(self) -> dict[str, Any]:
        """The parallelism facts worth recording in the stage artifact."""

        return {
            "shard_count": self.shard_count,
            "shards_reused": self.reused_count,
            "execution_lanes": list(self.devices),
            "rows_in": self.rows_in,
            **self.metadata,
        }


def cpu_lane_count(usable_cores: int | None = None) -> int:
    """Workers to use when there is no accelerator to divide the work by.

    One core is left for the parent, the writer and the operating system, which
    is the same reservation ``environment.detect._plan`` already makes.
    """

    cores = usable_cores
    if cores is None:
        try:
            cores = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
        except AttributeError:
            cores = os.cpu_count() or 1
    return max(1, min(32, cores - 1 if cores > 2 else cores))


__all__ = [
    "DEFAULT_SHARD_ROWS",
    "MAX_WORKERS",
    "ShardOutcome",
    "ShardTask",
    "ShardedResult",
    "StageResources",
    "cpu_lane_count",
]
