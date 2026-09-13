"""Deterministic sharding, GPU lane assignment, and within-stage resume.

One import boundary matters here and it is worth stating where it is enforced:
**nothing in this package imports :mod:`molcascade.plugins`.**
``plugins.registry`` imports ``plugins.builtin`` at its module bottom, so a
builtin adapter that reached back into the plugin package root through this one
would close an import cycle.  A caller resolves its ``StageInput`` to concrete
paths with ``chemistry.datasets.discover_contract_files`` and passes plain
values -- paths, ints, dicts -- across.

The pieces:

- :class:`StageResources` -- what the runner grants a stage.  Deliberately not
  part of any stage config, because ``stage_cache_key`` hashes configs and a
  worker count in there would make one computation cache differently on every
  machine.
- :func:`plan_lanes` -- hardware plus one ``--device`` request becomes a list of
  execution lanes.
- :func:`run_sharded` -- the single entry point an adapter calls.  Splits, runs,
  checkpoints, resumes, and hands back staged file names in shard order.
"""

from molcascade.parallel.devices import (
    FrameworkProbe,
    LanePlan,
    describe_lanes,
    pinned_device,
    plan_lanes,
    probe_framework_gpu,
    resolve_framework_lanes,
)
from molcascade.parallel.models import (
    DEFAULT_SHARD_ROWS,
    MAX_WORKERS,
    ShardedResult,
    ShardOutcome,
    ShardTask,
    StageResources,
    cpu_lane_count,
)
from molcascade.parallel.shards import (
    ShardError,
    count_completed_shards,
    iter_shard_batches,
    plan_shards,
    read_side_input,
    run_sharded,
)

__all__ = [
    "DEFAULT_SHARD_ROWS",
    "MAX_WORKERS",
    "FrameworkProbe",
    "LanePlan",
    "ShardError",
    "ShardOutcome",
    "ShardTask",
    "ShardedResult",
    "StageResources",
    "count_completed_shards",
    "cpu_lane_count",
    "describe_lanes",
    "iter_shard_batches",
    "pinned_device",
    "plan_lanes",
    "plan_shards",
    "probe_framework_gpu",
    "read_side_input",
    "resolve_framework_lanes",
    "run_sharded",
]
