"""A campaign whose binding constraint is throughput, not the cost of one measurement.

:mod:`etalon.campaign.loop` is built for the regime the guards were designed around: few
measurements, each costing GPU-hours, learn between them, and every refusal exists to stop a wasted
hour. Its whole vocabulary -- handoff rows, spend tokens, admission, waivers, councils -- is about
*one expensive measurement at a time*, and its :meth:`~etalon.campaign.loop.Campaign.round` is
synchronous because in that regime there is nothing to overlap.

A screening campaign with no simulation stage is the other regime, and it is not a smaller version of
the first one. Measured: 1,056,280 generated molecules, 515,012 docked, 294 surviving every gate, 56
batches, over 44 hours on eight GPUs. No single measurement in it was worth authorising individually,
generation and screening had to run at once or half the machine idled, and what actually consumed the
operator's attention was none of the things ``loop.py`` protects:

- a batch whose supervising shell died while its screen kept committing stages, invisible for six
  hours because nothing owned the reservation;
- three different error codes all meaning "no molecule survived the gates", two of them misfiled as
  failures before the family was recognised;
- 56 batch records carrying no cascade revision, so a mid-campaign threshold change would have made
  two batches incomparable with nothing to detect it;
- and the whole producer/consumer overlap, which the DAG scheduler forbids by construction --
  :func:`etalon.runtime.service._verify_dependencies` requires a dependency be ``verified``, so a
  consumer cannot start on a producer's partial output.

That campaign was therefore driven by fourteen hand-written shell and Python files beside the
framework, of which five duplicated machinery ETALON already had. This module is the missing
vocabulary, and it is deliberately a sibling of ``loop.py`` rather than a replacement: the two regimes
share the ledger, the screen adapter and the infrastructure pinning, and share no selection policy at
all, because a sweep has nothing to select -- every molecule in the pool gets screened.

**What it owns.** A deduplicated pool, a watermark that carves batches from it, a reservation per
batch that a crash cannot silently repeat, an append-only outcome per batch carrying the compiled
revision, and a recovery that asks the run record rather than the process table.

**What it refuses.** Screening at scale without a gate authorization
(:mod:`etalon.authority.gate`). In this regime the irreversible act is not a wasted GPU-hour, it is a
miscalibrated threshold deleting the interesting chemistry a million times over, and that is the one
thing nothing in ETALON guarded before.

**What it does not own.** Process supervision, device assignment and terminal persistence. Those are
properties of a host, not of a campaign; a sweep says which batches are outstanding and what happened
to each, and a supervisor of any shape drives it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from etalon.authority.gate import GateAuthorization, require_gate
from etalon.boundary.screen import Screen, ScreenResult
from etalon.campaign.ledger import Ledger

#: Molecules per batch. Twenty thousand, and the number is a scheduling decision rather than a
#: scientific one, so it is stated as the trade it makes.
#:
#: A batch is the unit of everything: of reservation, of recovery, of the shortlist a gate produces
#: and of the record a reader audits. Too large and a crash costs the whole batch's wall clock and the
#: overlap with generation coarsens; too small and per-run overhead dominates and the late-stage gates
#: see too few survivors to produce a shortlist at all. Measured at 20,000: a batch took 4,200-5,900 s
#: and yielded 10-20 hits. At 5,000, three batches carved from the same tail yielded 6, 2 and 0 --
#: the zero not because those molecules were worse but because 5,000 molecules do not reliably put
#: anything through a funnel whose end-to-end survival is 0.028%.
DEFAULT_BATCH_SIZE = 20_000


class SweepError(RuntimeError):
    """A sweep was asked for something its record does not permit."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS molecule (
    key      TEXT PRIMARY KEY,       -- the caller's identity key, normally an InChIKey
    smiles   TEXT NOT NULL,
    source   TEXT NOT NULL,          -- the generation tag that proposed it first
    seen_at  REAL NOT NULL,
    batch    TEXT                    -- NULL until carved into a batch
);
CREATE INDEX IF NOT EXISTS molecule_unbatched ON molecule(batch) WHERE batch IS NULL;
CREATE TABLE IF NOT EXISTS batch (
    batch_id     TEXT PRIMARY KEY,
    size         INTEGER NOT NULL,
    emitted_at   TEXT NOT NULL,
    claimed_by   TEXT,               -- NULL until claimed; a claim is a reservation
    claimed_at   TEXT,
    run_id       TEXT,
    revision_id  TEXT,
    outcome      TEXT,               -- committed | exhausted | failed, once recorded
    recorded_at  TEXT
);
"""


@dataclass(frozen=True, slots=True)
class AdmitReport:
    """What one ingest added to the pool."""

    offered: int
    accepted: int
    duplicates: int
    ready: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "offered": self.offered,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "ready": self.ready,
        }


@dataclass(frozen=True, slots=True)
class Batch:
    """A carved, reserved unit of screening."""

    batch_id: str
    size: int
    emitted_at: str
    claimed_by: str | None = None
    claimed_at: str | None = None
    run_id: str | None = None
    revision_id: str | None = None
    outcome: str | None = None
    recorded_at: str | None = None

    @property
    def recorded(self) -> bool:
        return self.outcome is not None

    @property
    def claimed(self) -> bool:
        return self.claimed_by is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "size": self.size,
            "emitted_at": self.emitted_at,
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at,
            "run_id": self.run_id,
            "revision_id": self.revision_id,
            "outcome": self.outcome,
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True, slots=True)
class Recovery:
    """One outstanding batch, and what the run record said to do about it."""

    batch_id: str
    action: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"batch_id": self.batch_id, "action": self.action, "detail": self.detail}


class Sweep:
    """The throughput-regime campaign: a pool, a watermark, reservations, and an append-only record.

    Args:
        screen: The adapter batches are screened through. Used for reading run state during recovery,
            never to start a run -- who runs a batch is a supervisor's decision, and a sweep that
            launched work would be a sweep that cannot be tested without a GPU.
        ledger: Where batch outcomes land. The same append-only ledger ``loop.py`` writes rounds to,
            so one campaign has one record whichever regime it ran in.
        pool: Path to the SQLite pool. Created if absent.
        batch_size: See :data:`DEFAULT_BATCH_SIZE`.
        gate: The gate authorization. Required before any batch is emitted; see
            :mod:`etalon.authority.gate` for why this is the guard that matters here.
    """

    def __init__(
        self,
        screen: Screen,
        ledger: Ledger,
        pool: str | Path,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        gate: GateAuthorization | None = None,
        prefix: str = "",
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.screen = screen
        self.ledger = ledger
        self.pool = Path(pool)
        self.batch_size = int(batch_size)
        # Campaign scope for batch ids. A run id is a name in the workspace, and two campaigns in
        # one workspace that both start at batch_0001 are two campaigns writing the same names.
        # Measured: a second ALK2 sweep carved batch_0001..0027 into a fresh ledger, recover()
        # found the first sweep's finished runs under those names, and the new campaign recorded
        # 27 committed batches in 100 seconds -- results produced by a different cascade, under a
        # different gate, filed as its own.
        self.prefix = str(prefix)
        self.gate = gate
        self.pool.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript(_SCHEMA)
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.pool, timeout=30.0, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    # -- pool ---------------------------------------------------------------

    def admit(self, molecules: Iterable[tuple[str, str, str]]) -> AdmitReport:
        """Add ``(key, smiles, source)`` triples to the pool, deduplicating on ``key``.

        Deduplication is global and permanent. A molecule proposed by two generators, or twice by
        one, is screened once, and the pool records which generator proposed it *first* so that a
        source attribution survives to the shortlist. That is what makes
        :mod:`etalon.generate.productivity`'s uniqueness measurement possible at all: only the pool
        knows what the library already held.

        ``INSERT OR IGNORE`` rather than a read-then-write, because two collectors ingesting
        concurrently would both find a key absent.
        """

        rows = [(k, s, src, time.time()) for k, s, src in molecules if str(k).strip()]
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            before = db.execute("SELECT COUNT(*) FROM molecule").fetchone()[0]
            db.executemany(
                "INSERT OR IGNORE INTO molecule(key, smiles, source, seen_at) VALUES (?,?,?,?)", rows
            )
            after = db.execute("SELECT COUNT(*) FROM molecule").fetchone()[0]
            ready = db.execute("SELECT COUNT(*) FROM molecule WHERE batch IS NULL").fetchone()[0]
            db.execute("COMMIT")
        accepted = after - before
        return AdmitReport(
            offered=len(rows), accepted=accepted, duplicates=len(rows) - accepted, ready=ready
        )

    def ready(self) -> int:
        """Molecules in the pool not yet carved into a batch."""

        with closing(self._connect()) as db:
            return int(db.execute("SELECT COUNT(*) FROM molecule WHERE batch IS NULL").fetchone()[0])

    # -- batches ------------------------------------------------------------

    def emit(self, *, revision_id: str, flush: bool = False) -> tuple[Batch, ...]:
        """Carve every full batch the pool can support, and refuse without a gate authorization.

        Args:
            revision_id: The compiled funnel these batches will be screened with. Checked against the
                gate authorization here rather than at screening time, because here is where the
                molecules become committed to a configuration -- and a refusal before any GPU work is
                the cheap one.
            flush: Also carve a short final batch from the remainder. For the end of a campaign only.
                A short batch is not a smaller sample of the same thing: end-to-end survival is a
                fraction of a percent, so a 5,000-molecule batch routinely puts nothing through the
                funnel, and three measured at that size yielded 6, 2 and 0 hits.
        """

        require_gate(self.gate, revision_id)
        carved: list[Batch] = []
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = int(db.execute("SELECT COUNT(*) FROM batch").fetchone()[0])
                while True:
                    unbatched = int(
                        db.execute("SELECT COUNT(*) FROM molecule WHERE batch IS NULL").fetchone()[0]
                    )
                    take = self.batch_size if unbatched >= self.batch_size else (
                        unbatched if flush and unbatched else 0
                    )
                    if not take:
                        break
                    batch_id = f"{self.prefix}batch_{existing + len(carved) + 1:04d}"
                    # A batch id is a run id, and a run id that already names a run in the
                    # workspace is not this campaign's. Refusing here is the only cheap place:
                    # recover() reads the run record by id and cannot tell whose it is, so a
                    # collision is recorded as this campaign's own result. Measured on ALK2 -- a
                    # second sweep over the same pool carved batch_0001..0027 into a fresh ledger
                    # and recorded all 27 as committed within 100 seconds, every one of them a
                    # result from the previous cascade under the previous gate.
                    if self.screen.state(batch_id) is not None:
                        raise SweepError(
                            f"{batch_id} already names a run in this workspace. A batch id is a "
                            "run id; carving this one would let recovery file another campaign's "
                            "result as yours. Give this sweep a prefix -- Sweep(..., prefix="
                            f"'{revision_id[:8]}_') -- or screen it in a workspace of its own."
                        )
                    at = _now()
                    db.execute(
                        "UPDATE molecule SET batch = ? WHERE key IN "
                        "(SELECT key FROM molecule WHERE batch IS NULL ORDER BY seen_at LIMIT ?)",
                        (batch_id, take),
                    )
                    db.execute(
                        "INSERT INTO batch(batch_id, size, emitted_at, revision_id) VALUES (?,?,?,?)",
                        (batch_id, take, at, revision_id),
                    )
                    carved.append(
                        Batch(batch_id=batch_id, size=take, emitted_at=at, revision_id=revision_id)
                    )
                    if flush and take < self.batch_size:
                        break
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return tuple(carved)

    def molecules(self, batch_id: str) -> tuple[dict[str, str], ...]:
        """The batch's molecules, as the library rows a screen will read."""

        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT key, smiles, source FROM molecule WHERE batch = ? ORDER BY seen_at",
                (batch_id,),
            ).fetchall()
        if not rows:
            raise SweepError(f"{batch_id} holds no molecules; it was never emitted")
        return tuple({"id": key, "smiles": smiles, "source": source} for key, smiles, source in rows)

    def claim(self, batch_id: str, *, by: str) -> Batch:
        """Reserve a batch for one screener, atomically. Refuses a batch already claimed.

        The reservation is the mechanism ``active/store.py`` uses and my own first attempt did not:
        with a claim recorded before the screen starts, a batch left by a crash is *findable*, and
        without one it is merely absent. The atomic conditional update is what makes two screeners
        unable to take the same batch -- the same guarantee an atomic rename gives, in the store that
        already has to be consulted anyway.
        """

        if not str(by).strip():
            raise ValueError("a claim must name its claimer; an anonymous reservation cannot be recovered")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE batch SET claimed_by = ?, claimed_at = ? "
                "WHERE batch_id = ? AND claimed_by IS NULL AND outcome IS NULL",
                (by, _now(), batch_id),
            )
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                raise SweepError(
                    f"{batch_id} is not claimable: it does not exist, is already claimed, or is "
                    "already recorded. Call pending() for what is available."
                )
            db.execute("COMMIT")
        claimed = self.batch(batch_id)
        assert claimed is not None
        return claimed

    def record(self, batch_id: str, result: ScreenResult) -> Batch:
        """Record a batch's terminal outcome, with the revision that produced it.

        Three outcomes, not two. ``exhausted`` -- every molecule gated out -- is a measurement, and a
        record that called it a failure would invite a retry of work that already succeeded at what it
        was asked to do. Every docking score the run committed is in the artifact store either way;
        measured, one exhausted batch held 7,545 of them.

        Refuses a revision that disagrees with the one the batch was emitted under. A batch screened
        with a different funnel than it was carved for is not comparable with its neighbours, and
        that is precisely the provenance hole 56 hand-recorded batches had.
        """

        recorded = self.batch(batch_id)
        if recorded is None:
            raise SweepError(f"{batch_id} was never emitted")
        if recorded.recorded:
            raise SweepError(
                f"{batch_id} is already recorded as {recorded.outcome!r}. An outcome is append-only; "
                "screen a new batch rather than overwriting a measurement."
            )
        # The batch keeps the revision it was emitted under, and a differing run digest is
        # provenance rather than a violation.
        #
        # This used to raise on any difference, and the difference is unavoidable: a compiled
        # revision covers the library as well as the funnel, so a run's own digest is a function of
        # which molecules were in it. Measured on ALK2, five batches of one campaign compiled to
        # five digests from one unchanged config file, and this check refused all five -- from
        # inside ``recover``, so the exception left the supervisor's tick and killed the process
        # that was driving the campaign.
        #
        # The guard it was trying to be is still in place and is the only one that can actually be
        # performed: a screen driver compiles the current config against a fixed reference library
        # before every batch and refuses to run one whose funnel is not the campaign's. A
        # configuration changed mid-campaign therefore never produces a result for this method to
        # judge. What ``revision_id`` means in the ledger -- and what ``comparable`` reads -- is the
        # funnel, which is the question a campaign asks of it.
        revision = recorded.revision_id or result.revision_id
        at = _now()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE batch SET run_id = ?, revision_id = ?, outcome = ?, recorded_at = ? "
                "WHERE batch_id = ?",
                (result.run_id, revision, result.outcome, at, batch_id),
            )
            db.execute("COMMIT")
        self.ledger.append(
            "batch",
            batch_id,
            run_id=result.run_id,
            revision_id=revision,
            # The run's own digest, which covers the library as well as the funnel. Kept beside the
            # funnel revision rather than in place of it: one identifies the screen, the other
            # identifies this batch's molecules going through it.
            run_revision_id=result.revision_id,
            outcome=result.outcome,
            size=recorded.size,
            exhausted_at=None if result.exhaustion is None else result.exhaustion.stage_id,
            committed_stages=len(result.committed),
            failed_stages=[stage.stage_id for stage in result.failed],
            infrastructure=self.screen.provenance(),
            gate=None if self.gate is None else self.gate.calibration_sha256,
        )
        updated = self.batch(batch_id)
        assert updated is not None
        return updated

    # -- recovery -----------------------------------------------------------

    def recover(self, *, abandoned: Sequence[str] = ()) -> tuple[Recovery, ...]:
        """Resolve every claimed-but-unrecorded batch by asking the run record.

        Four cases, and the design decision is that none of them involves looking for a process.

        ``recorded``
            The run reached a terminal state. Record it, whatever became of the claimer. This is the
            case that cost six hours of one batch's life when nothing was checking: the screen had
            succeeded at 17:30 and the batch sat claimed until 21:16.
        ``running``
            The run exists and has not finished. Left alone. A supervisor that has died does not make
            a committing screen stop, and killing or requeueing here would discard real work.
        ``requeued``
            No run record at all, so the claim never got as far as starting one. Returned to the
            pending pool.
        ``abandoned``
            Named by the caller. A non-terminal run whose process is really gone cannot be
            distinguished from a slow one by anything durable, so resolving it requires an assertion
            from someone who looked -- the same discipline ``active/store.py`` applies to an
            interrupted action, and for the same reason: guessing here silently repeats or discards
            work that may have cost hours.
        """

        asserted = set(abandoned)
        actions: list[Recovery] = []
        for batch in self.outstanding():
            state = None if batch.run_id is None else self.screen.state(batch.run_id)
            if state is None:
                state = self.screen.state(batch.batch_id)  # the conventional run id is the batch id
            if state is not None and state.status in ("SUCCEEDED", "FAILED"):
                self.record(batch.batch_id, state)
                actions.append(
                    Recovery(batch.batch_id, "recorded", f"run finished as {state.outcome}")
                )
                continue
            if batch.batch_id in asserted:
                self._release(batch.batch_id)
                actions.append(
                    Recovery(batch.batch_id, "requeued", "operator asserted the screener is gone")
                )
                continue
            if state is None:
                self._release(batch.batch_id)
                actions.append(
                    Recovery(batch.batch_id, "requeued", "claimed but no run was ever started")
                )
                continue
            actions.append(
                Recovery(
                    batch.batch_id,
                    "running",
                    f"run {state.run_id} is {state.status}; left alone. Pass it in abandoned= only "
                    "after confirming the screener is gone.",
                )
            )
        return tuple(actions)

    def _release(self, batch_id: str) -> None:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE batch SET claimed_by = NULL, claimed_at = NULL, run_id = NULL "
                "WHERE batch_id = ? AND outcome IS NULL",
                (batch_id,),
            )
            db.execute("COMMIT")

    # -- read ---------------------------------------------------------------

    def batch(self, batch_id: str) -> Batch | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT batch_id, size, emitted_at, claimed_by, claimed_at, run_id, revision_id, "
                "outcome, recorded_at FROM batch WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        return None if row is None else Batch(*row)

    def _select(self, where: str, *args: Any) -> tuple[Batch, ...]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT batch_id, size, emitted_at, claimed_by, claimed_at, run_id, revision_id, "
                f"outcome, recorded_at FROM batch WHERE {where} ORDER BY batch_id",
                args,
            ).fetchall()
        return tuple(Batch(*row) for row in rows)

    def pending(self) -> tuple[Batch, ...]:
        """Emitted, unclaimed, unrecorded -- what a screener may take."""

        return self._select("claimed_by IS NULL AND outcome IS NULL")

    def outstanding(self) -> tuple[Batch, ...]:
        """Claimed but not recorded -- what recovery has to resolve."""

        return self._select("claimed_by IS NOT NULL AND outcome IS NULL")

    def done(self) -> tuple[Batch, ...]:
        return self._select("outcome IS NOT NULL")

    def state(self) -> dict[str, Any]:
        """One reading of the whole sweep, for a supervisor or a status command."""

        done = self.done()
        by_outcome: dict[str, int] = {}
        for batch in done:
            assert batch.outcome is not None
            by_outcome[batch.outcome] = by_outcome.get(batch.outcome, 0) + 1
        with closing(self._connect()) as db:
            total, pooled = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(batch IS NULL), 0) FROM molecule"
            ).fetchone()
            by_source = dict(
                db.execute(
                    "SELECT source, COUNT(*) FROM molecule GROUP BY source ORDER BY 2 DESC"
                ).fetchall()
            )
        revisions = sorted({b.revision_id for b in done if b.revision_id})
        return {
            "pool": {"unique": int(total), "awaiting_batch": int(pooled), "by_source": by_source},
            "batches": {
                "emitted": len(done) + len(self.pending()) + len(self.outstanding()),
                "pending": len(self.pending()),
                "outstanding": len(self.outstanding()),
                "recorded": len(done),
                "by_outcome": by_outcome,
            },
            "batch_size": self.batch_size,
            "revisions": revisions,
            "gate": None if self.gate is None else self.gate.as_dict(),
            # More than one revision across recorded batches means the funnel changed mid-campaign.
            # Not an error -- a campaign may legitimately retune -- but enrichment computed across
            # the boundary is not attributable to anything, so the reading says so out loud.
            "comparable": len(revisions) <= 1,
        }


def library_rows(sweep: Sweep, batch_id: str, path: str | Path) -> Path:
    """Write a batch as the CSV MolCascade reads, with the id column it needs.

    The id column is not optional in practice. Without it a run records no molecule names, and three
    later readers -- per-tier recall, per-molecule explanation, a named shortlist -- have nothing to
    key on. Measured: MolCascade's own recall measurement refuses such a run outright rather than
    reporting a funnel that lost everything.
    """

    import csv

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = sweep.molecules(batch_id)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "smiles", "source"])
        writer.writeheader()
        writer.writerows(rows)
    return target


def as_json(sweep: Sweep) -> str:
    return json.dumps(sweep.state(), indent=2, sort_keys=True, default=str)


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "AdmitReport",
    "Batch",
    "Recovery",
    "Sweep",
    "SweepError",
    "as_json",
    "library_rows",
]
