"""Read back the reasons a run already recorded for every molecule it removed.

Every gate in this project writes ``decision/v1``.  The standardizer writes
``FRAGMENTS_REMOVED`` when it picks one parent out of a multi-component record
and ``UNDEFINED_STEREO`` when a molecule has a stereocentre nobody assigned; the
hard gate writes a row per finding; every threshold gate writes its own
``reason_code``.  All of it is committed, content-addressed and immutable.

None of it was ever read.  ``molcascade export`` answers *what survived*,
``molcascade trace`` answers *what each stage removed*, and the run report
answers *how long each stage took* -- but "why did molecules leave, and for
which reasons" was a question whose answer sat in the artifact store with no
reader pointed at it.  This module is that reader.

Three judgements are worth stating rather than leaving to be discovered,
because each one is a way this kind of summary quietly reports a wrong number.

**It counts rows, not molecules.**  ``hard_gate`` writes one row per finding and
``property_gate`` writes one per out-of-range property, so a molecule rejected
for both its weight and its heavy-atom count contributes two rows to one stage.
Turning rows back into molecules means holding every ``entity_id`` seen -- the
one thing a reader over a multi-million-row dataset must not do.  Molecule
counts already exist elsewhere and are exact there: survivor counts come from
manifest metadata via :mod:`molcascade.cascade.funnel`, and per-stage
``reject_count`` is in each manifest's ``response_metadata``.  So every field
here is named ``rows`` and the docstring says what it is.

**It keys on ``entity_kind`` as well.**  ``decision/v1`` admits
``SOURCE_RECORD``, ``PARENT`` and ``STATE``, and they are different populations:
a source adapter rejecting a malformed record and a gate rejecting a molecule
are not the same event, and summing them produces a number that means nothing.
The enum has three values, so carrying it costs nothing and buys a total that
can be trusted.

**Reason codes are not low-cardinality, so the bucket table is capped.**
``rd_filters_alerts`` mixes a hash of the matched rule into its code, so a run
against its full catalogue can emit over a thousand distinct codes; the medchem
adapters hash rule *combinations*, which has no bound at all.  An uncapped
grouping is therefore an unbounded allocation driven by input data.  The cap is
on the number of distinct buckets retained; rows whose bucket did not fit are
counted into ``untracked_rows`` so the totals stay exact, and ``outcome_totals``
is aggregated independently of the cap so the headline numbers are exact no
matter what the tail looks like.

Nothing here runs inside the pipeline.  It reads committed artifacts after the
fact, so it changes no contract, no stage configuration and no cache key, and it
works on a run that failed partway -- which is precisely when the question is
worth asking.  Integrity and lookup failures fold into one stage being marked
unavailable rather than the whole digest refusing, the same bargain
:func:`molcascade.trace._manifest_for` makes and for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from molcascade.artifacts import (
    ArtifactDatasetRef,
    ArtifactIntegrityError,
    ArtifactManifest,
    ArtifactNotFoundError,
)
from molcascade.contracts import DECISION_V1
from molcascade.io.parquet import iter_parquet_batches
from molcascade.runtime import LocalRunner, RunState

#: Columns the scan materialises.  ``detail`` is deliberately absent: it is a
#: ``large_string`` holding a free-text explanation per row and is by far the
#: widest column in the contract, while nothing in an aggregate needs it.
_SCAN_COLUMNS = ("entity_id", "entity_kind", "outcome", "reason_code")

#: Rows per Arrow batch.  Four narrow string columns, so this is a small buffer.
_BATCH_SIZE = 65_536

#: How many distinct ``(entity_kind, outcome, reason_code)`` buckets one stage
#: may retain.  Large enough to hold ``rd_filters``'s full rule catalogue with
#: room to spare; small enough that a pathological code generator cannot exhaust
#: memory.  Overflow is counted, never dropped -- see ``untracked_rows``.
_MAX_BUCKETS = 2_048

#: Example entity ids kept per bucket, so a reader can go look one up with
#: ``molcascade explain``.  Deliberately small: this is a signpost, not a list.
_SAMPLE_SIZE = 5


@dataclass(frozen=True, slots=True)
class DecisionBucket:
    """One ``(entity_kind, outcome, reason_code)`` group within a stage."""

    entity_kind: str
    outcome: str
    reason_code: str
    #: Decision rows in this bucket.  Not molecules -- see the module docstring.
    rows: int
    sample_entity_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_kind": self.entity_kind,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "rows": self.rows,
            "sample_entity_ids": list(self.sample_entity_ids),
        }


@dataclass(frozen=True, slots=True)
class StageDecisions:
    """What one stage recorded, or why it could not be read."""

    stage_id: str
    #: ``None`` when the stage published no ``decision/v1`` port at all, which is
    #: the ordinary state for a source reader or a featuriser.
    port: str | None
    #: Exact per-``(entity_kind, outcome)`` totals, unaffected by the bucket cap.
    outcome_totals: tuple[tuple[str, str, int], ...]
    buckets: tuple[DecisionBucket, ...]
    rows_total: int
    #: Rows whose bucket did not fit under the cap.  Counted so ``rows_total``
    #: stays exact; the sum of ``buckets`` alone would understate it.
    untracked_rows: int
    #: Set when the artifact could not be read.  The stage still appears, so a
    #: missing stage and an unreadable one are distinguishable.
    unavailable: str | None = None

    @property
    def rejected_rows(self) -> int:
        return sum(rows for _, outcome, rows in self.outcome_totals if outcome == "REJECT")

    @property
    def warned_rows(self) -> int:
        return sum(rows for _, outcome, rows in self.outcome_totals if outcome == "WARN")

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "port": self.port,
            "outcome_totals": [
                {"entity_kind": kind, "outcome": outcome, "rows": rows}
                for kind, outcome, rows in self.outcome_totals
            ],
            "buckets": [bucket.as_dict() for bucket in self.buckets],
            "rows_total": self.rows_total,
            "untracked_rows": self.untracked_rows,
            "unavailable": self.unavailable,
        }


@dataclass(frozen=True, slots=True)
class DecisionDigest:
    """Every reason one run recorded, grouped by the stage that recorded it."""

    run_id: str
    status: str
    stages: tuple[StageDecisions, ...]

    @property
    def stages_with_decisions(self) -> tuple[StageDecisions, ...]:
        return tuple(stage for stage in self.stages if stage.port is not None)

    @property
    def rejected_rows(self) -> int:
        return sum(stage.rejected_rows for stage in self.stages)

    @property
    def warned_rows(self) -> int:
        return sum(stage.warned_rows for stage in self.stages)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "rejected_rows": self.rejected_rows,
            "warned_rows": self.warned_rows,
            "stages": [stage.as_dict() for stage in self.stages_with_decisions],
        }

    def render(self) -> str:
        return "\n".join(_render_lines(self))


def build_decision_digest(
    runner: LocalRunner,
    run_id: str,
    *,
    max_buckets: int = _MAX_BUCKETS,
    sample_size: int = _SAMPLE_SIZE,
) -> DecisionDigest:
    """Aggregate every ``decision/v1`` dataset a run committed.

    Reads only committed artifacts, never re-hashes them, and never fails the
    whole digest over one unreadable stage.  Accepts a run in any terminal or
    non-terminal state: a run that died in its docking tier is exactly when the
    reasons recorded above it are worth reading.
    """

    if max_buckets <= 0:
        raise ValueError("max_buckets must be positive")
    if sample_size < 0:
        raise ValueError("sample_size must not be negative")

    state = runner.load_run(run_id)
    stages: list[StageDecisions] = []
    for stage in state.stages:
        stages.append(
            _stage_decisions(
                runner,
                stage.stage_id,
                _manifest_for(runner, state, stage.stage_id),
                max_buckets=max_buckets,
                sample_size=sample_size,
            )
        )
    return DecisionDigest(run_id=run_id, status=str(state.status), stages=tuple(stages))


def _manifest_for(
    runner: LocalRunner, state: RunState, stage_id: str
) -> ArtifactManifest | None:
    """The manifest a stage published, or ``None`` if it published nothing.

    Deliberately duplicated from :mod:`molcascade.trace` rather than imported:
    importing it would make this module depend on the trace writer, and the
    tolerant behaviour -- fold integrity and lookup failures into ``None`` -- is
    three lines and is the point of the function rather than an implementation
    detail worth sharing.
    """

    for stage in state.stages:
        if stage.stage_id != stage_id:
            continue
        if stage.output_ref is None:
            return None
        try:
            return runner.store.get_manifest(stage.output_ref.artifact_id)
        except (ArtifactIntegrityError, ArtifactNotFoundError, OSError):
            return None
    return None


def _decision_ref(manifest: ArtifactManifest) -> ArtifactDatasetRef | None:
    """The ``decision/v1`` view a manifest declares, matched by contract.

    By contract rather than by port name, for the same reason
    :func:`molcascade.trace._population_ref` does it: a port is named by whoever
    wrote the adapter, and ``decisions`` is a convention rather than a rule.
    """

    for output in manifest.outputs:
        if output.contract_id == DECISION_V1.id:
            return manifest.dataset_ref(output.port)
    return None


def _dataset_paths(runner: LocalRunner, ref: ArtifactDatasetRef) -> tuple[Path, ...]:
    """Files behind one dataset view, resolved without re-hashing them.

    ``verify=False`` on purpose.  ``resolve_dataset`` still checks the reference
    against the manifest and still refuses a path that escapes the artifact
    directory; what it skips is recomputing the sha256 of every file, which for
    a run that kept its poses is gigabytes of hashing to answer a question about
    a few narrow string columns.  :mod:`molcascade.handoff` verifies because it
    publishes a product; this is a diagnostic.
    """

    root = runner.store.resolve_dataset(ref, verify=False)
    return tuple(
        root.joinpath(*PurePosixPath(relative).parts) for relative in ref.file_paths
    )


def _stage_decisions(
    runner: LocalRunner,
    stage_id: str,
    manifest: ArtifactManifest | None,
    *,
    max_buckets: int,
    sample_size: int,
) -> StageDecisions:
    """Scan one stage's decision dataset, or record why it could not be."""

    empty = StageDecisions(
        stage_id=stage_id,
        port=None,
        outcome_totals=(),
        buckets=(),
        rows_total=0,
        untracked_rows=0,
    )
    if manifest is None:
        return empty
    ref = _decision_ref(manifest)
    if ref is None:
        return empty
    try:
        paths = _dataset_paths(runner, ref)
    except (ArtifactIntegrityError, ArtifactNotFoundError, OSError) as error:
        return StageDecisions(
            stage_id=stage_id,
            port=ref.port,
            outcome_totals=(),
            buckets=(),
            rows_total=0,
            untracked_rows=0,
            unavailable=f"{type(error).__name__}: {error}",
        )

    totals: dict[tuple[str, str], int] = {}
    buckets: dict[tuple[str, str, str], int] = {}
    samples: dict[tuple[str, str, str], list[str]] = {}
    rows_total = 0
    untracked = 0
    try:
        for path in paths:
            if not path.exists():
                continue
            for batch in iter_parquet_batches(
                path, columns=list(_SCAN_COLUMNS), batch_size=_BATCH_SIZE
            ):
                columns = {name: batch.column(name).to_pylist() for name in _SCAN_COLUMNS}
                for index in range(batch.num_rows):
                    kind = str(columns["entity_kind"][index])
                    outcome = str(columns["outcome"][index])
                    reason = str(columns["reason_code"][index])
                    rows_total += 1
                    totals[(kind, outcome)] = totals.get((kind, outcome), 0) + 1
                    key = (kind, outcome, reason)
                    if key in buckets:
                        buckets[key] += 1
                    elif len(buckets) < max_buckets:
                        buckets[key] = 1
                    else:
                        untracked += 1
                        continue
                    sample = samples.setdefault(key, [])
                    if len(sample) < sample_size:
                        sample.append(str(columns["entity_id"][index]))
    except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError) as error:
        return StageDecisions(
            stage_id=stage_id,
            port=ref.port,
            outcome_totals=(),
            buckets=(),
            rows_total=0,
            untracked_rows=0,
            unavailable=f"{type(error).__name__}: {error}",
        )

    ordered_totals = tuple(
        (kind, outcome, count)
        for (kind, outcome), count in sorted(totals.items(), key=lambda item: -item[1])
    )
    ordered_buckets = tuple(
        DecisionBucket(
            entity_kind=kind,
            outcome=outcome,
            reason_code=reason,
            rows=count,
            sample_entity_ids=tuple(samples.get((kind, outcome, reason), ())),
        )
        for (kind, outcome, reason), count in sorted(
            buckets.items(), key=lambda item: (-item[1], item[0])
        )
    )
    return StageDecisions(
        stage_id=stage_id,
        port=ref.port,
        outcome_totals=ordered_totals,
        buckets=ordered_buckets,
        rows_total=rows_total,
        untracked_rows=untracked,
    )


#: Reason buckets rendered per stage.  ``rd_filters_alerts`` can produce over a
#: thousand distinct codes, and a terminal summary that prints all of them is
#: not a summary.  The JSON payload carries every retained bucket.
_RENDER_BUCKETS = 8

#: Characters of an entity id kept in the terminal rendering.  Ids are
#: ``parent:sha256:<64 hex>``; five of those per line is 350 characters of
#: digest, which buries the number the line exists to show.  The full ids stay
#: in :meth:`DecisionDigest.as_dict` because that is what gets fed back to
#: ``molcascade explain``.
_ID_PREFIX = 14


def _count(value: int) -> str:
    return f"{value:,}"


def _short_id(entity_id: str) -> str:
    """``parent:sha256:341b3326…`` -- enough to recognise, short enough to read."""

    _, separator, digest = entity_id.rpartition(":")
    if not separator or len(digest) <= _ID_PREFIX:
        return entity_id
    kind = entity_id[: len(entity_id) - len(digest)]
    return f"{kind}{digest[:_ID_PREFIX]}…"


def _render_lines(digest: DecisionDigest) -> list[str]:
    """Terminal rendering, in the shape :mod:`molcascade.cascade.funnel` uses.

    Only ``REJECT`` and ``WARN`` buckets are itemised.  ``PASS`` rows are the
    overwhelming majority in any healthy run and their totals are already on the
    stage line, so printing one bucket line per stage saying "everything that
    passed, passed" would push the two outcomes anybody opened this for off the
    screen.
    """

    lines = [f"Decisions recorded by run {digest.run_id} ({digest.status})"]
    recorded = digest.stages_with_decisions
    if not recorded:
        lines.append("  No stage in this run published a decision/v1 dataset.")
        return lines
    for stage in recorded:
        lines.append("")
        lines.append(f"  {stage.stage_id}")
        if stage.unavailable is not None:
            lines.append(f"    unreadable: {stage.unavailable}")
            continue
        if not stage.rows_total:
            lines.append("    no decision rows")
            continue
        totals = "  ".join(
            f"{kind} {outcome}: {_count(rows)}" for kind, outcome, rows in stage.outcome_totals
        )
        lines.append(f"    {totals}")
        removals = tuple(
            bucket for bucket in stage.buckets if bucket.outcome in ("REJECT", "WARN")
        )
        for bucket in removals[:_RENDER_BUCKETS]:
            sample = ", ".join(_short_id(entity) for entity in bucket.sample_entity_ids[:3])
            suffix = f"  e.g. {sample}" if sample else ""
            lines.append(
                f"      {bucket.outcome:<7}{bucket.reason_code:<38}"
                f"{_count(bucket.rows):>9}{suffix}"
            )
        if len(removals) > _RENDER_BUCKETS:
            hidden = len(removals) - _RENDER_BUCKETS
            lines.append(f"      … and {hidden} more reason code(s); use --json for all")
        if stage.untracked_rows:
            lines.append(
                f"      (+{_count(stage.untracked_rows)} row(s) in reason codes beyond "
                "the bucket cap; the totals above are still exact)"
            )
    lines.append("")
    lines.append(
        f"  {_count(digest.rejected_rows)} reject row(s), "
        f"{_count(digest.warned_rows)} warning row(s). "
        "Rows, not molecules: one molecule can fail two rules in one stage."
    )
    return lines


__all__ = [
    "DecisionBucket",
    "DecisionDigest",
    "StageDecisions",
    "build_decision_digest",
]
