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
import re
import sqlite3
import time
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from etalon.authority.gate import GateAuthorization, require_gate
from etalon.boundary.infra import etalon_revision
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


def _MATCHES(prefix: str, batch_id: str) -> bool:
    """Whether ``batch_id`` is one this prefix would have carved.

    Anchored at both ends on purpose. ``startswith`` would accept the dangerous direction silently:
    every id starts with the empty prefix, so a sweep that *lost* its prefix passes a prefix check
    written that way while carving ``batch_0028`` beside ``v7_batch_0027``.
    """

    return re.fullmatch(re.escape(prefix) + r"batch_\d{4,}", batch_id) is not None


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
    recorded_at  TEXT,
    gate         TEXT,               -- the calibration digest this batch was *emitted* under
    etalon       TEXT                -- the ETALON revision that claimed it, i.e. that screened it
);
"""

# Pools written before the `gate` column existed. `CREATE TABLE IF NOT EXISTS` is a no-op on an
# existing table, so a column added to the schema above never reaches a campaign already on disk --
# and the campaign already on disk is the one with batches in it.
_MIGRATIONS = (
    ("batch", "gate", "ALTER TABLE batch ADD COLUMN gate TEXT"),
    ("batch", "etalon", "ALTER TABLE batch ADD COLUMN etalon TEXT"),
)

# One SMILES, one row. Not in `_SCHEMA` because this one can legitimately fail to be created, and
# the schema script runs as one `executescript` that must not be left half-applied.
_SMILES_INDEX = "molecule_smiles"


@dataclass(frozen=True, slots=True)
class AdmitReport:
    """What one ingest added to the pool.

    ``duplicates`` is ``offered - accepted``, so it counts every row the pool refused for any
    reason -- a key it already held, and now also a SMILES it already held. The second case used to
    be invisible: a molecule offered with a second key for a SMILES already in the pool was counted
    as newly accepted, and on the v7 pool thirty-one of them were carved into a second batch and
    docked again while this field reported nothing.
    """

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
    gate: str | None = None
    """The calibration digest this batch was *emitted* under.

    Carried on the row rather than re-read at record time, because the sweep that records a batch
    is not always the sweep that carved it. Measured: a supervisor restarted mid-campaign
    re-authorizes its gate, and ``record`` filed that new digest against batches carved under the
    old one -- so a ledger read back afterwards attributed every batch to whichever authorization
    happened to be live when the recorder ran. ``None`` on a row written before this column existed.
    """
    etalon: str | None = None
    """The ETALON revision of the process that *claimed* this batch, from
    :func:`etalon.boundary.infra.etalon_revision`.

    The claimer rather than the emitter or the recorder, because the claimer is the process that
    runs the screen: it writes the library, dispatches the funnel and reads the result back. A
    batch requeued and re-claimed by a newer ETALON correctly carries the newer revision. ``None``
    where the commit was unevaluable, on a row written before this column existed, or on a batch
    recorded without ever being claimed -- which is why ``record`` falls back to the live revision
    rather than filing a null.

    Not recorded for the emit side. The funnel's ``revision_id`` identifies the screen and the gate
    digest identifies the admission rule, so what a comparison is missing is the code that drove
    the screening, and that is this.
    """

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
            "gate": self.gate,
            "etalon": self.etalon,
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
            for table, column, statement in _MIGRATIONS:
                present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if column not in present:
                    db.execute(statement)
            self._ensure_one_row_per_smiles(db)
            db.commit()

    @staticmethod
    def _ensure_one_row_per_smiles(db: sqlite3.Connection) -> None:
        """Make a second row for one SMILES an ignored insert rather than a second docking.

        The pool deduplicates on ``key``, and ``key`` is the caller's -- ``etalon_sweep_admit`` takes
        it as an argument and says an InChIKey is the usual choice. Nothing checked that it was a
        function of the SMILES, and on the ALK2 v7 pool it was not: thirty-two SMILES carried two
        keys each, thirty-one of those pairs were carved into two different batches, and each was
        docked twice and counted as two molecules. The ingest path is fixed at its source in
        :func:`etalon.campaign.generation.read_chunk`, but the pool is a public boundary and the
        invariant belongs on the boundary.

        With the index in place ``INSERT OR IGNORE`` drops the second row, so ``accepted`` does not
        rise and ``AdmitReport.duplicates`` becomes true for the first time.

        A pool that already violates the invariant cannot have it imposed -- SQLite raises
        ``IntegrityError`` and the index is not created -- and a finished campaign's pool must not
        be rewritten to make a new rule fit. So the fallback is a plain index: the invariant is not
        enforced there, and :meth:`state` says so out loud under ``one_row_per_smiles`` rather than
        letting a reader assume it holds everywhere.
        """

        existing = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (_SMILES_INDEX,)
        ).fetchone()
        if existing is not None:
            return
        try:
            db.execute(f"CREATE UNIQUE INDEX {_SMILES_INDEX} ON molecule(smiles)")
        except sqlite3.IntegrityError:
            db.execute(f"CREATE INDEX {_SMILES_INDEX} ON molecule(smiles)")

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
        concurrently would both find a key absent. It also does the second half of the work: with
        the unique index on ``smiles`` in place, a row whose SMILES the pool already holds under a
        different key is ignored too, so the deduplication no longer depends on the caller having
        derived ``key`` from ``smiles``. See :meth:`_ensure_one_row_per_smiles` for the pool this
        was measured on and for the one case where the index cannot be imposed.
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

    def _check_prefix(self, db: sqlite3.Connection) -> None:
        """Refuse a prefix that disagrees with the one this pool was carved under.

        The prefix is a campaign's scope for batch ids, and it is passed to the constructor rather
        than stored -- so a supervisor restarted from a script that lost the argument carves into a
        *different* id family in the same pool. Nothing downstream notices: the run-collision guard
        below only fires on a name an earlier run already took, and ``batch_0028`` after
        ``v7_batch_0001..0027`` is free. The campaign then holds two id families, and every reader
        that selects a campaign's runs by id prefix -- which is how a workspace of several
        campaigns' run records is read at all -- silently sees half of it.

        Measured on ALK2: a harvest globbing ``runs/batch_*.json`` against a workspace holding both
        families collected the *previous* campaign's 27 batches and reported 198,511 hits under the
        new campaign's name. The numbers were real; they described a different cascade revision.

        The pool already knows the answer, so this costs one query.
        """

        ids = [str(row[0]) for row in db.execute("SELECT batch_id FROM batch")]
        stray = [b for b in ids if not _MATCHES(self.prefix, b)]
        if stray:
            found = sorted(
                {m.group(1) for m in (re.fullmatch(r"(.*)batch_\d+", b) for b in stray) if m}
            )
            fix = (
                f"Pass prefix={found[0]!r} to use this pool"
                if len(found) == 1
                else "Point this sweep at a pool of its own"
            )
            raise SweepError(
                f"this pool was carved under batch ids like {stray[0]!r}, but this sweep has "
                f"prefix {self.prefix!r}. Carving now would put two id families in one campaign, "
                f"and a reader selecting runs by prefix would see only one of them. {fix}, or "
                "point this sweep at a pool of its own."
            )

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
        # Read once, outside the carving loop, so every batch of one emit carries the same digest.
        # ``require_gate`` has already refused a missing authorization, so this is not None in
        # practice; the fallback keeps the column honest rather than inventing a digest.
        gate_digest = None if self.gate is None else self.gate.calibration_sha256
        carved: list[Batch] = []
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = int(db.execute("SELECT COUNT(*) FROM batch").fetchone()[0])
                self._check_prefix(db)
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
                        "INSERT INTO batch(batch_id, size, emitted_at, revision_id, gate) "
                        "VALUES (?,?,?,?,?)",
                        (batch_id, take, at, revision_id, gate_digest),
                    )
                    carved.append(
                        Batch(
                            batch_id=batch_id,
                            size=take,
                            emitted_at=at,
                            revision_id=revision_id,
                            gate=gate_digest,
                        )
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
                "UPDATE batch SET claimed_by = ?, claimed_at = ?, etalon = ? "
                "WHERE batch_id = ? AND claimed_by IS NULL AND outcome IS NULL",
                # Stamped here because this process is the one that will screen it. See
                # ``Batch.etalon``; ``or None`` so an unevaluable commit stays distinguishable from
                # a recorded one.
                (by, _now(), etalon_revision() or None, batch_id),
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
        # A run that has not finished has no outcome to record, and the thing that makes this worth
        # a guard rather than a comment is which way ``ScreenResult.outcome`` errs on one:
        # ``outcome`` is derived from the *stages*, so a RUNNING run whose stages have not failed
        # yet classifies as ``"committed"`` -- the same string a finished screen produces. A batch
        # killed at stage 6 of 43 therefore files as a successful measurement of 20,000 molecules,
        # with 37 stages that never ran, and nothing downstream can tell it from a real one: the
        # ledger entry carries ``committed_stages`` but no reader compares it against the funnel's
        # length. ``recover`` (:525) and the MCP record tool (mcp/sweep.py:344) each already
        # refuse this; the invariant belongs here, where the row is actually written, so that a
        # driver calling the campaign API directly cannot bypass it.
        if result.status not in ("SUCCEEDED", "FAILED"):
            raise SweepError(
                f"{batch_id}'s run {result.run_id} is {result.status}, which is not a terminal "
                "state. An unfinished run has committed stages and no outcome, and recording one "
                "would file a partial screen as a complete measurement -- "
                "ScreenResult.outcome reads 'committed' for a RUNNING run whose stages have not "
                "failed. Poll Screen.progress, or call recover(abandoned=...) once you have "
                "confirmed nothing is still screening it."
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
            # ``AND outcome IS NULL`` is what makes the append-only guard above actually hold. The
            # guard is a read, the write is a separate statement, and between them is a window two
            # recorders fit through: a supervisor's ``recover`` and an operator's MCP call both see
            # ``outcome IS NULL``, both pass, and the second silently overwrites the first's
            # measurement -- including overwriting ``committed`` with ``failed`` from a retry that
            # should never have been dispatched. ``claim`` (:407-418) already had this pattern; the
            # terminal write did not.
            cursor = db.execute(
                "UPDATE batch SET run_id = ?, revision_id = ?, outcome = ?, recorded_at = ? "
                "WHERE batch_id = ? AND outcome IS NULL",
                (result.run_id, revision, result.outcome, at, batch_id),
            )
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                raise SweepError(
                    f"{batch_id} was recorded by someone else between this call's check and its "
                    "write. An outcome is append-only; read batch() for the one that landed."
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
            # The gate the batch was *emitted* under, read off its row, not this sweep's. They are
            # the same digest for a campaign that ran in one process and different ones for a
            # campaign that was restarted -- and the restarted campaign is the one whose ledger
            # anybody reads. ``or`` rather than a plain read so a pool carved before the column
            # existed still files the live digest instead of a null.
            gate=recorded.gate or (None if self.gate is None else self.gate.calibration_sha256),
            # ETALON's own revision, which every provenance block in this project omitted while
            # naming the three packages ETALON drives. Read off the row -- the claimer screened it --
            # with the same fallback the gate uses for a batch with no row value to read.
            etalon=recorded.etalon or (etalon_revision() or None),
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
                "outcome, recorded_at, gate, etalon FROM batch WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        return None if row is None else Batch(*row)

    def _select(self, where: str, *args: Any) -> tuple[Batch, ...]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT batch_id, size, emitted_at, claimed_by, claimed_at, run_id, revision_id, "
                f"outcome, recorded_at, gate, etalon FROM batch WHERE {where} ORDER BY batch_id",
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
            # Whether one SMILES means one row is a property of *this* pool, not of the code: a
            # pool filled before the invariant existed keeps its violations, because rewriting a
            # finished campaign's pool to fit a new rule would destroy the record of what was
            # actually screened. So the reading asks the database rather than assuming.
            index_sql = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (_SMILES_INDEX,)
            ).fetchone()
            one_per_smiles = index_sql is not None and "UNIQUE" in str(index_sql[0]).upper()
            smiles_conflicts = (
                0
                if one_per_smiles
                else int(
                    db.execute(
                        "SELECT COUNT(*) FROM "
                        "(SELECT smiles FROM molecule GROUP BY smiles HAVING COUNT(*) > 1)"
                    ).fetchone()[0]
                )
            )
        revisions = sorted({b.revision_id for b in done if b.revision_id})
        etalon_revisions = sorted({b.etalon for b in done if b.etalon})
        return {
            "pool": {
                "unique": int(total),
                "awaiting_batch": int(pooled),
                "by_source": by_source,
                "one_row_per_smiles": one_per_smiles,
                # How many SMILES this pool holds more than once. Zero by construction where the
                # invariant is enforced; a count of molecules screened twice where it is not.
                "smiles_conflicts": smiles_conflicts,
            },
            "batches": {
                "emitted": len(done) + len(self.pending()) + len(self.outstanding()),
                "pending": len(self.pending()),
                "outstanding": len(self.outstanding()),
                "recorded": len(done),
                "by_outcome": by_outcome,
            },
            "batch_size": self.batch_size,
            "revisions": revisions,
            # The ETALON revisions that screened these batches, a different question from the funnel
            # revision and one that used to be unanswerable: six commits separate the v6 and v7
            # campaigns' screening windows, five of them touching sweep.py, calibrate.py or
            # supervisor.py, and neither ledger records which one ran.
            "etalon_revisions": etalon_revisions,
            "etalon": etalon_revision(),
            "gate": None if self.gate is None else self.gate.as_dict(),
            # More than one revision across recorded batches means the funnel changed mid-campaign.
            # Not an error -- a campaign may legitimately retune -- but enrichment computed across
            # the boundary is not attributable to anything, so the reading says so out loud. Two
            # ETALON revisions break comparability for the same reason and were previously
            # invisible: the funnel can be byte-identical while the code choosing the molecules and
            # reading the result back is not. A batch carved before the column existed carries no
            # revision and counts as neither agreeing nor disagreeing, the only honest reading of a
            # null.
            "comparable": len(revisions) <= 1 and len(etalon_revisions) <= 1,
        }


def library_rows(
    sweep: Sweep, batch_id: str, path: str | Path, *, content_addressed: bool = False
) -> Path:
    """Write a batch as the CSV MolCascade reads, with the id column it needs.

    The id column is not optional in practice. Without it a run records no molecule names, and three
    later readers -- per-tier recall, per-molecule explanation, a named shortlist -- have nothing to
    key on. Measured: MolCascade's own recall measurement refuses such a run outright rather than
    reporting a funnel that lost everything.

    Args:
        content_addressed: Treat ``path`` as a directory and name the file after a digest of its
            own bytes. **This is what lets the screen's cache work at all**, and the reason is not
            tidiness.

            MolCascade's source stage carries the library's absolute path in its stage config, and
            ``stage_cache_key`` hashes that config -- so a name chosen by the caller is folded into
            the entry stage's key, its output artifact is republished under a new digest, and every
            downstream stage's input digest changes with it. The whole funnel re-runs, docking
            included, to produce identical numbers.

            Measured on ALK2: a second campaign over the same pool wrote the same 20,000 molecules
            to ``batch_0001.csv`` and ``v7_batch_0001.csv``. The files were byte-identical, same
            md5. Of the 43 stages, the 10 whose config had actually changed were the gates that were
            meant to change; ``docking_score``'s own config was identical. All 43 re-ran, because
            the 11th differing config was the source stage's and the only thing in it that differed
            was the file name.
    """

    import csv
    import hashlib
    import io

    rows = sweep.molecules(batch_id)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["id", "smiles", "source"])
    writer.writeheader()
    writer.writerows(rows)
    payload = buffer.getvalue().encode("utf-8")

    if not content_addressed:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target

    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    existing = _library_index(root).get(digest)
    if existing is not None and _holds(root / existing, digest):
        # Reuse the name this exact library already has, whatever it is. The goal is one path per
        # content, not a particular spelling of it -- and an earlier campaign that wrote these bytes
        # under its own naming scheme has cache entries keyed on *that* path. Measured on ALK2: 27
        # batches of 20,000 molecules each, every one byte-identical to the previous campaign's
        # batch of the same number, 3.9 GB of artifacts and 1,633 cache entries already on disk, and
        # a batch costing ~250 minutes. Insisting on a fresh `lib-<digest>.csv` here would be
        # content-addressing that still recomputes everything.
        return root / existing

    target = root / f"lib-{digest}.csv"
    if not _holds(target, digest):
        _write_atomically(target, payload)
    _remember_library(root, digest, target.name)
    return target


def _holds(path: Path, digest: str) -> bool:
    """Whether ``path`` right now contains the bytes that ``digest`` names.

    The index is a cache of a claim about file contents, and nothing keeps the contents from
    changing after it was made: a batch library is a plain CSV in a campaign directory that
    operators and harvest scripts also write to. So the digest is the question, and the file has
    to be asked it rather than the index.

    Checking the length was the measured bug. Replacing an indexed ``lib-<digest>.csv`` with a
    31-byte wrong-content file -- wrong length, so a size guard would have caught it -- still
    returned that file untouched, because the index hit returned before any guard ran. In the
    original observation a request for one batch's library returned a path whose first data row
    read ``J0,N0,flowr``: another batch's molecules, under this batch's id, about to be docked and
    recorded as this batch's measurement.

    Re-hashing costs reading a couple of megabytes against a batch costing hours, so it is paid on
    every lookup instead of trusted.
    """

    import hashlib

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == digest
    except OSError:
        return False


def _write_atomically(target: Path, payload: bytes) -> None:
    """Write ``payload`` to ``target`` so that no reader ever sees a partial library.

    A library file is named after its own content, so a half-written one is a file whose name is a
    lie. With the digest check above, the next lookup would reject it, write it again, and hand the
    screen a path whose bytes changed underneath an already-running source stage. Replacing within
    the same directory keeps the rename atomic on one filesystem.
    """

    import os
    import tempfile

    descriptor, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".lib-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _library_index(root: Path) -> dict[str, str]:
    """Map content digest to the file name holding it, building the index on first use.

    Built by hashing whatever CSVs are already in the directory, so a campaign started before
    content-addressing existed still has its libraries found. At campaign scale this is tens of
    files of a couple of megabytes -- paid once, then read from the index.
    """

    import hashlib

    index_path = root / "by-content.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(index, dict):
            return {str(k): str(v) for k, v in index.items()}
    except (OSError, ValueError):
        pass

    index: dict[str, str] = {}
    # Oldest name wins. When one content sits under several names -- which is exactly the situation
    # this index exists to repair -- the cache entries belong to whichever was written first, so
    # picking any other name indexes the content to a path the screen has never seen. Measured on
    # ALK2: 54 library files, 27 distinct contents, every v6 name shadowed by a v7 twin; keying on
    # the later name would have produced a tidy index and not one cache hit.
    for candidate in _oldest_first(root.glob("*.csv")):
        try:
            index.setdefault(hashlib.sha256(candidate.read_bytes()).hexdigest(), candidate.name)
        except OSError:
            continue
    _write_index(index_path, index)
    return index


def _oldest_first(candidates: Iterable[Path]) -> list[Path]:
    """Order files oldest-first, dropping any that cannot be stat'd while being ordered.

    ``sorted(..., key=lambda p: (p.stat().st_mtime, p.name))`` reads the filesystem from inside the
    sort key, which is the one place the loop body's own ``OSError`` guard cannot reach. Measured: a
    single dangling CSV in the library directory -- a broken symlink, or a file removed between the
    glob and the stat -- raised out of index construction and therefore out of ``library_rows``,
    which the supervisor calls *after* the batch is already claimed. The batch stayed claimed with
    no screen running and nothing retried it.
    """

    aged: list[tuple[float, str, Path]] = []
    for candidate in candidates:
        try:
            aged.append((candidate.stat().st_mtime, candidate.name, candidate))
        except OSError:
            continue
    return [candidate for _, _, candidate in sorted(aged, key=lambda item: item[:2])]


def _write_index(index_path: Path, index: dict[str, str]) -> None:
    """Replace the content index in one step, or leave the old one in place.

    A truncated ``by-content.json`` is worse than a missing one: missing is rebuilt from the
    directory, truncated is a ``ValueError`` that is also rebuilt but only after the half-written
    file is read -- and a *valid* half of the index is neither. Two supervisors sharing a library
    directory then race, and the loser's entries vanish rather than the file becoming unreadable.
    Last writer wins, which costs a rebuild on the next miss, not a wrong path.
    """

    import os
    import tempfile

    payload = json.dumps(index, indent=1, sort_keys=True).encode("utf-8")
    try:
        descriptor, temporary = tempfile.mkstemp(
            dir=str(index_path.parent), prefix=".by-content-", suffix=".tmp"
        )
    except OSError:
        return
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, index_path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)


def _remember_library(root: Path, digest: str, name: str) -> None:
    index = _library_index(root)
    if index.get(digest) == name:
        return
    index[digest] = name
    _write_index(root / "by-content.json", index)


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
