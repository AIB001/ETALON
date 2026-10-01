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
    #: Devices that joined the screening pool this pass because their generators stopped.
    migrated: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def quiet(self) -> bool:
        """Whether this pass changed nothing. Most passes are quiet, and saying so is the point.

        Two things this got wrong, and they compound, because `quiet` is what decides whether a pass
        is printed at all.

        **A note is never quiet.** Notes are where a screen that raised ends up -- `tick()` puts
        `_reap`'s failures there first, with the comment that a failure only a counter records reads
        as nothing happening. But `notes` was not in this list, so a pass whose one product was a
        failure reported itself as quiet and a printer that skips quiet passes never showed it. That
        is the same shape as the defect those notes were added to fix.

        **A `running` recovery is quiet.** `Sweep.recover` returns one entry per outstanding batch
        and `running` is its documented no-op -- "left alone", the only branch of the four that
        mutates nothing. Counting it meant a campaign with batches in flight had no quiet passes at
        all: measured on ALK2, 27 consecutive ticks each printing the same 1,200-character line
        listing 16 batches as running, which is the channel a failure note would have arrived on.
        """

        moved = [r for r in self.recovered if r.get("action") != "running"]
        return not any(
            (
                self.notes,
                self.ingested_chunks,
                self.emitted,
                self.screens_started,
                self.recorded,
                moved,
                self.generation_started,
                self.generation_finished,
                self.retired,
                self.migrated,
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
            "migrated": list(self.migrated),
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
        screen_devices: Devices available for screening. Grows when ``migrate_retired_devices`` is
            set and a generator's card falls idle.
        batches_per_device: Batches allowed on one device at once. One by default, which is right
            when the screen is GPU-bound. Raise it when it is not: measured on ALK2, the docking
            tier's wall clock went to PoseBusters in Python rather than to the engine, so eight
            batches on eight cards held 1.3 cores each of a 96-core machine and 25 GB of each
            96 GB card, and the one-per-device limit was rationing the resource that was spare.
            Bound it by memory: ``batches_per_device * <engine's footprint>`` must fit a card,
            and Uni-Dock's footprint measured 22-26 GB.
        generation: Runs one chunk. Injected; see :data:`GenerationDriver`.
        screen: Screens one batch. Injected; see :data:`ScreenDriver`.
        retire: Given a generator's tag, whether to stop it. Injected because the decision is
            scientific -- :mod:`etalon.generate.productivity` computes it, and an operator may
            override. Default never retires.
        migrate_retired_devices: Whether a device whose every generator has stopped joins
            ``screen_devices``. Off by default, and the default is about the machine rather than the
            science: on a shared host an operator who gave five GPUs to generation may intend them
            returned when generation ends, and a supervisor that silently keeps them competes with
            whoever was waiting for one. When off, the first pass that finds such a device says so in
            ``notes`` once, because an idle GPU nobody mentions is the failure this exists to stop.
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
        migrate_retired_devices: bool = False,
        batches_per_device: int = 1,
    ) -> None:
        if not revision_id.strip():
            raise ValueError("a supervisor needs the compiled revision its batches belong to")
        if not screen_devices:
            raise ValueError("a supervisor needs at least one device to screen on")
        self.sweep = sweep
        self.revision_id = revision_id
        self.workspace = Path(workspace)
        self.generators = {generator.tag: generator for generator in generators}
        # Deduplicated because a device named twice would be offered two batches at once, and the
        # second claim would be refused by the sweep rather than by anything that could explain it.
        self.screen_devices = tuple(dict.fromkeys(screen_devices))
        self.generation = generation
        self.screen = screen
        self.retire = retire or (lambda _tag: False)
        self.migrate_retired_devices = bool(migrate_retired_devices)
        if int(batches_per_device) < 1:
            raise ValueError("batches_per_device must be at least 1")
        self.batches_per_device = int(batches_per_device)
        self._noted_idle: set[str] = set()
        self.jobs: list[_Job] = []
        self.barren: dict[str, int] = {}
        self.stopped: set[str] = set()
        self.chunks: dict[str, list[ChunkResult]] = {}
        self.unique: dict[str, list[int]] = {}
        # Every generator must share one output root, because only one Ingest is kept (see below)
        # and it discovers chunks by path. Refused rather than tolerated: with per-generator roots
        # the campaign runs, every loop generates, every chunk lands on disk with its manifest --
        # and the pool only ever grows by whichever generator happened to sort first. Measured on
        # ALK2: five of six loops produced nothing the sweep could see, and the symptom was a pool
        # that looked merely slow. A Generator's own directory is ``output_root / tag``, so sharing
        # the root does not make two loops collide.
        roots = {str(generator.output_root) for generator in self.generators.values()}
        if len(roots) > 1:
            raise ValueError(
                "every Generator must share one output_root; got "
                + ", ".join(sorted(roots))
                + ". Each generator already gets its own directory at output_root/tag, and only "
                "one Ingest is kept for the campaign -- so distinct roots mean every generator but "
                "one is silently never ingested."
            )
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
        before claiming means a batch a crash orphaned is re-offered rather than stranded. Migrating
        before starting screens means a generation device released on the *previous* pass screens on
        this one -- the stop decision stays in :meth:`_start_generation` alone, so a device retired
        here waits one interval rather than being decided about twice.
        """

        notes: list[str] = []
        finished, failures = self._reap()
        # Straight into notes: a failure that only a counter records reads as nothing happening.
        notes.extend(failures)
        ingested, admitted, duplicates = self._ingest_chunks()
        # Recovery is also how a finished batch gets recorded, and there is deliberately no second
        # path for the ordinary case. Sweep.recover already asks the run record of every claimed
        # batch and records the terminal ones; a supervisor with its own "record what my screen
        # finished" branch would be the same query answered twice, and the two would eventually
        # disagree about a batch whose screen outlived the process that launched it -- which is the
        # exact case recovery exists for.
        try:
            recovered = self.sweep.recover()
        except Exception as error:  # noqa: BLE001 -- one batch must not end the campaign
            # A supervisor exists so that a 44-hour campaign survives things going wrong in it. One
            # batch that cannot be recorded is a thing going wrong in it. Measured on ALK2: a
            # revision check inside ``recover`` raised on a single batch, the exception left
            # ``tick``, and the process driving 470,651 molecules through 23 batches exited --
            # leaving every GPU idle until somebody looked. Loud and fatal is not better than
            # silent; the note is the loud part, and continuing is the supervisor's whole job.
            recovered = ()
            notes.append(f"recovery raised and was contained: {error!r}")
        emitted = self._emit()
        migrated = self._migrate(notes)
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
            migrated=migrated,
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

    def _reap(self) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
        """Collect finished subprocesses, and say which ones raised. Never waits.

        A screen that raises used to be reaped in silence: this dropped the job and reported only
        finished *generation*, so the error ``_Call`` had carefully carried to the poller was read
        by nobody. Measured on ALK2: every batch failed on its first line, the supervisor ticked on
        for fourteen hours, and the campaign filled a 470,651-molecule pool that nothing ever
        screened. A failing screen and an idle one are the same picture -- batches claimed, GPUs
        quiet -- so the failure has to be said out loud or it cannot be told from a slow start.
        """

        finished: list[dict[str, Any]] = []
        failures: list[str] = []
        still_running: list[_Job] = []
        for job in self.jobs:
            status = job.process.poll()
            if status is None:
                still_running.append(job)
                continue
            if job.kind == "generation":
                finished.append({"tag": job.name, "seconds": round(time.monotonic() - job.started, 1)})
            error = getattr(job.process, "error", None)
            if error is not None:
                failures.append(f"{job.kind} {job.name} on {job.device} failed: {error!r}")
        self.jobs = still_running
        return tuple(finished), tuple(failures)

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

    def _free_slots(self) -> list[str]:
        """Screening slots available now, a device repeated once per free slot on it.

        One batch per device is the right default and was the only option until a measurement
        contradicted the assumption under it. On ALK2 the docking tier turned out to be CPU-bound --
        Uni-Dock runs in bursts and the wall clock goes to pose validation in Python -- so eight
        batches on eight cards held 1.3 cores each of a 96-core machine and 25 GB of each 96 GB
        card. The limit being enforced was the one resource that was not scarce.
        """

        from collections import Counter

        busy = Counter(job.device for job in self.jobs if job.kind == "screen")
        slots: list[str] = []
        for device in self.screen_devices:
            slots.extend([device] * max(0, self.batches_per_device - busy[device]))
        return slots

    def _start_screens(self, notes: list[str]) -> tuple[str, ...]:
        if self.screen is None:
            return ()
        started: list[str] = []
        free = self._free_slots()
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

    def _migrate(self, notes: list[str]) -> tuple[str, ...]:
        """Hand a device whose generators have all stopped to the screening pool.

        ``screen_devices`` used to be fixed for the life of the supervisor, so a campaign that
        retired four of five generation loops finished on the screeners it started with and the other
        four cards sat idle. :func:`etalon.mcp.sweep.etalon_campaign_plan` advises the move and an
        operator made it by hand on one campaign -- eighteen hours in, three devices -- and that move
        produced half of that campaign's hits. This is the same move, made when the loop ends rather
        than when somebody notices.

        A device is taken only when **every** generator configured on it has stopped, because one
        model on one card across two loops is a normal configuration and the card is not free until
        both are done. It is also refused while any job still holds the device: ``stopped`` is only
        set for a tag with no running chunk, so the two conditions agree today, and the second is
        here so that they still agree if the first ever changes.

        What it does not do: take a device back. Screening keeps it for the rest of the campaign, and
        a generator that an operator restarts on that card would contend with a screen. Restart the
        supervisor with the device lists it should have instead.
        """

        configured = {generator.device for generator in self.generators.values()}
        held = {job.device for job in self.jobs}
        idle = sorted(
            device
            for device in configured
            if device not in self.screen_devices
            and device not in held
            and all(
                tag in self.stopped
                for tag, generator in self.generators.items()
                if generator.device == device
            )
        )
        if not idle:
            return ()
        if not self.migrate_retired_devices:
            fresh = [device for device in idle if device not in self._noted_idle]
            if fresh:
                self._noted_idle.update(fresh)
                notes.append(
                    f"{', '.join(fresh)} has been idle since its generators stopped and is not "
                    "screening. Construct the supervisor with migrate_retired_devices=True, or name "
                    "the device in screen_devices, to use it."
                )
            return ()
        self.screen_devices = self.screen_devices + tuple(idle)
        notes.append(
            f"{', '.join(idle)} joined the screening pool; every generator on it has stopped."
        )
        return tuple(idle)

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
            # Reported because it grows: a reader comparing two readings needs to see that a device
            # moved from generation to screening, not infer it from a note that scrolled past.
            "screen_devices": list(self.screen_devices),
            "migrate_retired_devices": self.migrate_retired_devices,
            "batches_per_device": self.batches_per_device,
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
