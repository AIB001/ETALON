"""Keep a sweep fed and drained, for days, without a person in the loop.

One campaign was supervised by a person reading a status script every five minutes for 44 hours:
roughly 500 readings, of which **fewer than ten needed a decision**. The other 490 were mechanical --
ingest a finished chunk, carve a batch when the watermark is reached, hand a batch to a free device,
record a terminal run, resolve a claim a crash left behind. That work is a loop, and a loop is not a
thing to ask a language model to be.

So the division here is:

**The supervisor decides nothing scientific.** It moves molecules from chunks into the pool, batches
out of the pool, batches onto devices, and outcomes into the ledger. Every judgement it could make is
one an operator or a model already made: the gate token was minted before it started, the batch size
is fixed, and which generators run is its configuration.

**The model decides what the numbers mean.** Whether a generator has exhausted its space, whether the
enrichment justifies continuing, whether a threshold should move -- and each of those requires reading
a measurement the supervisor has recorded, not watching for it.

:meth:`Supervisor.tick` does one pass and returns. It never blocks on a subprocess, so a caller can
run it on any cadence, and it reconstructs everything from the pool, the ledger and the filesystem at
every call -- which is what makes it restartable. Kill the process mid-campaign and the next
:meth:`tick` recovers: chunks are discovered by their manifests, batches by their reservations, runs
by their run records.

What it deliberately does not do:

- **Guess liveness from the process table.** A batch's fate comes from its run record. ``pgrep`` on a
  run id matched the supervisor's own shell, and a worker whose shell had died kept committing stages
  for an hour; the run record was right both times.
- **Retry an exhausted batch.** Zero survivors is a measurement, and every score it computed is
  committed.
- **Start work it cannot account for.** A chunk or a screen is launched only after the record that
  will explain it exists.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from etalon.boundary.screen import ScreenResult
from etalon.campaign.generation import BARREN_LIMIT, ChunkResult, Generator, Ingest
from etalon.campaign.sweep import Sweep, library_rows

#: Run one chunk for one generator and report what it produced.
GenerationDriver = Callable[[Generator, int], ChunkResult]

#: Screen one batch's library on one device. Returns when the screen is finished; the supervisor runs
#: it in a worker process, so this may block.
ScreenDriver = Callable[[str, Path, str], ScreenResult]


@dataclass(frozen=True, slots=True)
class TickReport:
    """What one pass did. Every field is a count of something that happened, not a status."""

    ingested_chunks: int = 0
    admitted: int = 0
    duplicates: int = 0
    emitted: tuple[str, ...] = ()
    screens_started: tuple[str, ...] = ()
    recorded: tuple[str, ...] = ()
    recovered: tuple[dict[str, str], ...] = ()
    generation_started: tuple[str, ...] = ()
    generation_finished: tuple[dict[str, Any], ...] = ()
    retired: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def quiet(self) -> bool:
        """Whether this pass changed nothing. Most passes are quiet, and saying so is the point."""

        return not any(
            (
                self.ingested_chunks,
                self.emitted,
                self.screens_started,
                self.recorded,
                self.recovered,
                self.generation_started,
                self.generation_finished,
                self.retired,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "quiet": self.quiet,
            "ingested_chunks": self.ingested_chunks,
            "admitted": self.admitted,
            "duplicates": self.duplicates,
            "emitted": list(self.emitted),
            "screens_started": list(self.screens_started),
            "recorded": list(self.recorded),
            "recovered": [dict(row) for row in self.recovered],
            "generation_started": list(self.generation_started),
            "generation_finished": [dict(row) for row in self.generation_finished],
            "retired": list(self.retired),
            "notes": list(self.notes),
        }


@dataclass
class _Job:
    """A subprocess the supervisor is waiting on, and what it is for."""

    kind: str
    name: str
    device: str
    process: subprocess.Popen[bytes] | Any
    started: float = field(default_factory=time.monotonic)


class Supervisor:
    """The mechanical half of a campaign, as a restartable pass.

    Args:
        sweep: The pool, batches and ledger. Its gate authorization is what permits emission, so a
            supervisor cannot screen a configuration nobody calibrated.
        revision_id: The compiled funnel. Batches are carved under it and outcomes are refused if they
            disagree, which is the provenance a hand-run campaign did not have.
        workspace: Where batch libraries are written.
        generators: The loops filling the pool. Empty is legitimate -- a campaign screening a library
            it already has needs no generation.
        screen_devices: Devices available for screening. One batch per device at a time.
        generation: Runs one chunk. Injected; see :data:`GenerationDriver`.
        screen: Screens one batch. Injected; see :data:`ScreenDriver`.
        retire: Given a generator's tag, whether to stop it. Injected because the decision is
            scientific -- :mod:`etalon.generate.productivity` computes it, and an operator may
            override. Default never retires.
    """

    def __init__(
        self,
        sweep: Sweep,
        *,
        revision_id: str,
        workspace: str | Path,
        generators: Sequence[Generator] = (),
        screen_devices: Sequence[str] = ("cuda:0",),
        generation: GenerationDriver | None = None,
        screen: ScreenDriver | None = None,
        retire: Callable[[str], bool] | None = None,
    ) -> None:
        if not revision_id.strip():
            raise ValueError("a supervisor needs the compiled revision its batches belong to")
        if not screen_devices:
            raise ValueError("a supervisor needs at least one device to screen on")
        self.sweep = sweep
        self.revision_id = revision_id
        self.workspace = Path(workspace)
        self.generators = {generator.tag: generator for generator in generators}
        self.screen_devices = tuple(screen_devices)
        self.generation = generation
        self.screen = screen
        self.retire = retire or (lambda _tag: False)
        self.jobs: list[_Job] = []
        self.barren: dict[str, int] = {}
        self.stopped: set[str] = set()
        self.chunks: dict[str, list[ChunkResult]] = {}
        self.unique: dict[str, list[int]] = {}
        self.ingest = {
            tag: Ingest(
                root=generator.output_root,
                state=self.sweep.pool.with_name(f"{self.sweep.pool.stem}.ingested.json"),
            )
            for tag, generator in self.generators.items()
        } or {}
        # One ingest state file for the whole campaign, not one per generator: chunks are discovered
        # by path and a per-generator file would re-read every other generator's chunks on restart.
        self._ingest = next(iter(self.ingest.values()), None) if self.ingest else None
        self.workspace.mkdir(parents=True, exist_ok=True)

    # -- one pass -----------------------------------------------------------

    def tick(self) -> TickReport:
        """Do whatever the campaign's recorded state says is next, and return.

        The order is deliberate. Reaping first means a device freed this pass is usable this pass.
        Ingesting before emitting means a chunk that just landed can complete a batch. Recovering
        before claiming means a batch a crash orphaned is re-offered rather than stranded.
        """

        notes: list[str] = []
        finished = self._reap()
        ingested, admitted, duplicates = self._ingest_chunks()
        # Recovery is also how a finished batch gets recorded, and there is deliberately no second
        # path for the ordinary case. Sweep.recover already asks the run record of every claimed
        # batch and records the terminal ones; a supervisor with its own "record what my screen
        # finished" branch would be the same query answered twice, and the two would eventually
        # disagree about a batch whose screen outlived the process that launched it -- which is the
        # exact case recovery exists for.
        recovered = self.sweep.recover()
        emitted = self._emit()
        started_screens = self._start_screens(notes)
        started_generation, retired = self._start_generation(notes)

        return TickReport(
            ingested_chunks=ingested,
            admitted=admitted,
            duplicates=duplicates,
            emitted=emitted,
            screens_started=started_screens,
            recorded=tuple(r.batch_id for r in recovered if r.action == "recorded"),
            recovered=tuple(
                {"batch_id": r.batch_id, "action": r.action}
                for r in recovered
                if r.action != "recorded"
            ),
            generation_started=started_generation,
            generation_finished=finished,
            retired=retired,
            notes=tuple(notes),
        )

    def run(
        self,
        *,
        interval: float = 60.0,
        until: Callable[[], bool] | None = None,
        on_tick: Callable[[TickReport], None] | None = None,
    ) -> TickReport:
        """Tick until ``until`` says stop, or until there is nothing left to do.

        The default stopping condition is the honest one for a sweep: generation is finished or
        retired, the pool holds no full batch, and no batch is outstanding. A campaign with a
        molecule budget or a deadline passes its own.
        """

        done = until or self.complete
        last = TickReport()
        while not done():
            last = self.tick()
            if on_tick is not None:
                on_tick(last)
            if done():
                break
            time.sleep(interval)
        return last

    def complete(self) -> bool:
        """Nothing running, nothing pending, nothing left to generate."""

        if self.jobs:
            return False
        if self.sweep.pending() or self.sweep.outstanding():
            return False
        if self.sweep.ready() >= self.sweep.batch_size:
            return False
        return all(tag in self.stopped or self._exhausted_target(tag) for tag in self.generators)

    # -- the pass, in pieces ------------------------------------------------

    def _reap(self) -> tuple[dict[str, Any], ...]:
        """Collect finished subprocesses. Never waits."""

        finished: list[dict[str, Any]] = []
        still_running: list[_Job] = []
        for job in self.jobs:
            if job.process.poll() is None:
                still_running.append(job)
                continue
            if job.kind == "generation":
                finished.append({"tag": job.name, "seconds": round(time.monotonic() - job.started, 1)})
        self.jobs = still_running
        return tuple(finished)

    def _ingest_chunks(self) -> tuple[int, int, int]:
        if self._ingest is None:
            return 0, 0, 0
        count = admitted = duplicates = 0
        for chunk in list(self._ingest.pending()):
            rows = self._ingest.consume(chunk)
            count += 1
            tag = chunk.parent.name
            if not rows:
                self.barren[tag] = self.barren.get(tag, 0) + 1
                continue
            self.barren[tag] = 0
            report = self.sweep.admit(rows)
            admitted += report.accepted
            duplicates += report.duplicates
            self.unique.setdefault(tag, []).append(report.accepted)
        return count, admitted, duplicates

    def _emit(self) -> tuple[str, ...]:
        if self.sweep.ready() < self.sweep.batch_size:
            return ()
        return tuple(batch.batch_id for batch in self.sweep.emit(revision_id=self.revision_id))

    def _busy_devices(self) -> set[str]:
        return {job.device for job in self.jobs if job.kind == "screen"}

    def _start_screens(self, notes: list[str]) -> tuple[str, ...]:
        if self.screen is None:
            return ()
        started: list[str] = []
        free = [d for d in self.screen_devices if d not in self._busy_devices()]
        for device in free:
            pending = self.sweep.pending()
            if not pending:
                break
            batch = pending[0]
            library = self.workspace / "libraries" / f"{batch.batch_id}.csv"
            try:
                self.sweep.claim(batch.batch_id, by=device)
            except Exception as error:  # noqa: BLE001 -- another supervisor took it; not an error
                notes.append(f"{batch.batch_id} was claimed by someone else ({error})")
                continue
            library_rows(self.sweep, batch.batch_id, library)
            self.jobs.append(
                _Job(
                    kind="screen",
                    name=batch.batch_id,
                    device=device,
                    process=_Call(self.screen, batch.batch_id, library, device),
                )
            )
            started.append(f"{batch.batch_id}@{device}")
        return tuple(started)

    def _exhausted_target(self, tag: str) -> bool:
        generator = self.generators[tag]
        produced = sum(result.delivered for result in self.chunks.get(tag, ()))
        return produced >= generator.total

    def _start_generation(self, notes: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if self.generation is None:
            return (), ()
        running = {job.name for job in self.jobs if job.kind == "generation"}
        started: list[str] = []
        retired: list[str] = []
        for tag, generator in self.generators.items():
            if tag in running or tag in self.stopped:
                continue
            if self.barren.get(tag, 0) >= BARREN_LIMIT:
                self.stopped.add(tag)
                retired.append(f"{tag}:barren")
                notes.append(
                    f"{tag} produced nothing in {BARREN_LIMIT} consecutive chunks; stopped. One empty "
                    "chunk is normal, this many is the model or the box."
                )
                continue
            if self._exhausted_target(tag):
                self.stopped.add(tag)
                retired.append(f"{tag}:target")
                continue
            if self.retire(tag):
                self.stopped.add(tag)
                retired.append(f"{tag}:retired")
                notes.append(f"{tag} retired by the campaign's own productivity rule")
                continue
            index = generator.next_index()
            self.jobs.append(
                _Job(
                    kind="generation",
                    name=tag,
                    device=generator.device,
                    process=_Call(self._run_chunk, generator, index),
                )
            )
            started.append(f"{tag}:chunk_{index:03d}")
        return tuple(started), tuple(retired)

    def _run_chunk(self, generator: Generator, index: int) -> ChunkResult:
        assert self.generation is not None
        result = self.generation(generator, index)
        self.chunks.setdefault(generator.tag, []).append(result)
        return result

    # -- read ---------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """One reading, for a status tool or a model deciding whether to intervene."""

        return {
            "revision_id": self.revision_id,
            "sweep": self.sweep.state(),
            "running": [
                {
                    "kind": job.kind,
                    "name": job.name,
                    "device": job.device,
                    "seconds": round(time.monotonic() - job.started, 1),
                }
                for job in self.jobs
            ],
            "generators": {
                tag: {
                    "model": generator.model,
                    "pocket": generator.pocket.label,
                    "device": generator.device,
                    "chunks": len(self.chunks.get(tag, ())),
                    "delivered": sum(r.delivered for r in self.chunks.get(tag, ())),
                    "target": generator.total,
                    "consecutive_barren": self.barren.get(tag, 0),
                    "stopped": tag in self.stopped,
                }
                for tag, generator in self.generators.items()
            },
            "complete": self.complete(),
        }

    def productivity(self) -> dict[str, Any]:
        """Per-generator viability and exhaustion, from this campaign's own chunks.

        Empty until a generator has finished chunks: a productivity verdict on no measurements is the
        thing :mod:`etalon.generate.productivity` refuses to give.
        """

        from etalon.campaign.generation import chunk_measurements
        from etalon.generate.productivity import allocate

        profiles = []
        for tag, generator in self.generators.items():
            results = self.chunks.get(tag, [])
            counts = self.unique.get(tag, [])
            if not results or len(results) != len(counts):
                continue
            profiles.append(chunk_measurements(generator, results, counts))
        return allocate(profiles) if profiles else {"loops": [], "retire": {}, "note": "no finished chunks yet"}


class _Call:
    """A callable run in a thread, shaped like a ``Popen`` so the supervisor polls one way.

    A thread rather than a process because the work is already in a subprocess -- PRISM and MolCascade
    are external programs -- and what is being waited on here is only the wrapper that launched one.
    """

    def __init__(self, function: Callable[..., Any], *args: Any) -> None:
        import threading

        self.result: Any = None
        self.error: BaseException | None = None

        def target() -> None:
            try:
                self.result = function(*args)
            except BaseException as error:  # noqa: BLE001 -- carried to the poller, not swallowed
                self.error = error

        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def poll(self) -> int | None:
        if self._thread.is_alive():
            return None
        return 1 if self.error is not None else 0


__all__ = ["GenerationDriver", "ScreenDriver", "Supervisor", "TickReport"]
