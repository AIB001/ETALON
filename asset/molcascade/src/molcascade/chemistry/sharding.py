"""The two lines every sharded adapter would otherwise repeat.

:mod:`molcascade.parallel.shards` deliberately knows nothing about stage inputs
or contracts -- it takes paths, because anything it imported from
``molcascade.plugins`` would close an import cycle through
``plugins.registry``.  That leaves each adapter to resolve its ``StageInput``
into files and hand them over, which is the same six lines sixteen times.

This module is the seam between the two: it lives on the chemistry side, where
``StageInput`` and ``DataContract`` are already at hand, and calls into the
parallel side with plain paths.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from molcascade.chemistry.datasets import discover_contract_files
from molcascade.contracts import DataContract
from molcascade.parallel.models import ShardedResult, ShardOutcome, ShardTask
from molcascade.parallel.shards import run_sharded

if TYPE_CHECKING:
    from molcascade.plugins.api import StageContext, StageInput


def shard_stage(
    *,
    worker: Callable[[ShardTask], ShardOutcome],
    stage_input: StageInput,
    contract: DataContract,
    output_paths: Mapping[str, str],
    context: StageContext,
    stage_id: str = "",
    config: Mapping[str, Any] | None = None,
    shard_rows: int | None = None,
    side_inputs: Mapping[str, tuple[StageInput, DataContract]] | None = None,
) -> ShardedResult:
    """Run ``worker`` over every shard of one contract-typed stage input.

    ``worker`` must be a module-level function; ``spawn`` re-imports it by
    qualified name in a fresh interpreter, so a closure or a bound method
    cannot cross the boundary.

    Schema validation happens here, once per file, rather than once per batch
    inside the worker: ``discover_contract_files`` reads each footer and refuses
    anything that does not satisfy the contract, and the files cannot change
    underneath a run because artifacts are immutable.

    ``side_inputs`` maps a name to the second input a worker needs to *join*
    against -- shared conformers, a reference fingerprint table -- and each one
    is validated here exactly like the sliced input.  Every shard receives all
    of its files; slicing a side input by the same row range would assume the
    two datasets line up row for row, which any gate between the producer and
    this stage is entitled to break.
    """

    return run_sharded(
        worker=worker,
        input_files=discover_contract_files(stage_input, contract),
        output_paths=output_paths,
        staging_root=context.staging_root,
        resources=context.resources,
        stage_id=stage_id,
        config=config,
        shard_rows=shard_rows,
        side_inputs={
            name: discover_contract_files(side_input, side_contract)
            for name, (side_input, side_contract) in (side_inputs or {}).items()
        },
    )


__all__ = ["shard_stage"]
