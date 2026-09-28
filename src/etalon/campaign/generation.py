"""Run PRISM's generators in chunks, and turn finished chunks into pool molecules.

A sweep needs a pool and does not care where it came from. This is the half that fills it, and it
exists because the campaign that motivated :mod:`etalon.campaign.sweep` filled its pool with a shell
loop and a collector daemon written beside the framework -- about 9 KB of the fourteen hand-written
files, and the source of three of the incident reports.

Chunks rather than one long run, for a reason that is operational rather than aesthetic. A generator
asked for 200,000 molecules in one invocation produces nothing consumable until it finishes, which on
the slowest viable model is two days; asked for 5,000 at a time it produces a consumable unit every
hour, and a crash costs one of them. The chunk is also the unit
:mod:`etalon.generate.productivity` measures viability and exhaustion on.

Three things here were learned by getting them wrong:

**A chunk is finished when its manifest exists, not when its process exits.** PRISM writes
per-molecule SDFs, then runs QC, then consolidates -- and the consolidation is a separate CPU-side
phase during which the worker has exited, the GPU is released and the log is empty. A collector
reading the directory at that moment sees a chunk that looks complete and is not.

**A failed chunk still writes a manifest, beside a zero-byte candidates file.** RDKit raises ``OSError``
on such a file rather than yielding nothing, which took one collector down for twelve hours.

**One empty chunk is normal.** A sampler discarding its own reconstruction failures produces one
routinely; a loop that aborts on the first one retires a working model.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Molecules requested per chunk. Five thousand: about an hour on the models that matter, which is
#: short enough that a crash is cheap and long enough that per-invocation setup does not dominate.
DEFAULT_CHUNK = 5_000

#: Consecutive chunks that may produce nothing before a loop is abandoned. Five, and the number is a
#: correction: the first version stopped on one, which killed a model whose sampler had merely
#: discarded a batch of reconstruction failures.
BARREN_LIMIT = 5


@dataclass(frozen=True, slots=True)
class Pocket:
    """How a generator is told where to build.

    One object rather than free-form arguments because the *pair* matters: viability is a property of
    the model and the box together, and a campaign screening two pockets needs the label to tell its
    productivity profiles apart. TargetDiff delivered 2 of 100 on a 28 A box and 92 of 100 on a 22 A
    one -- the same model, the same protein.
    """

    label: str
    #: ``reference`` (a ligand file conditions the site), ``center`` (a YAML naming a centre and box)
    #: or ``residues``. Passed through to PRISM's ``--pocket-kind``.
    kind: str
    path: Path
    reference_ligand: Path | None = None

    def arguments(self) -> list[str]:
        args = ["--pocket", str(self.path)]
        if self.kind == "reference":
            args += ["--reference-ligand", str(self.reference_ligand or self.path)]
        else:
            args += ["--pocket-kind", self.kind]
        return args


@dataclass(frozen=True, slots=True)
class ChunkResult:
    """One chunk, after it finished or failed to."""

    tag: str
    index: int
    path: Path
    requested: int
    delivered: int
    seconds: float
    returncode: int
    #: Set when the chunk produced no consumable output, with the reason. Not an exception: a barren
    #: chunk is data the loop reasons about.
    barren: str | None = None

    @property
    def usable(self) -> bool:
        return self.barren is None and self.delivered > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "index": self.index,
            "path": str(self.path),
            "requested": self.requested,
            "delivered": self.delivered,
            "seconds": round(self.seconds, 1),
            "returncode": self.returncode,
            "barren": self.barren,
            "usable": self.usable,
        }


def finished_chunks(root: Path) -> list[Path]:
    """Every chunk directory under ``root`` that PRISM has finished writing.

    The manifest is the completion marker, and nothing else is. A directory holding per-molecule SDFs
    and no manifest is a chunk whose consolidation and QC have not run -- observed on a real chunk
    that had 19,958 molecules on disk, no ``candidates.sdf``, and wrote both a minute later.
    """

    return sorted(p.parent for p in root.glob("*/chunk_*/manifest.json"))


def read_chunk(path: Path, *, tag: str | None = None) -> list[tuple[str, str, str]]:
    """A finished chunk as ``(inchikey, smiles, source)`` triples, ready for ``Sweep.admit``.

    Returns an empty list for a chunk that produced nothing, including the failed-chunk shape: a
    manifest beside a zero-byte ``candidates.sdf``. RDKit raises ``OSError`` reading that file rather
    than yielding no molecules, and an ingest loop that did not expect an exception there stopped for
    twelve hours.

    The identity key is the InChIKey. Whatever key a campaign chooses has to be chosen *before* the
    pool is filled -- two standardisation policies give one molecule two keys, and a pool cannot be
    un-deduplicated afterwards.
    """

    from rdkit import Chem, RDLogger

    RDLogger.DisableLog("rdApp.*")
    source = tag or path.parent.name
    sdf = path / "candidates.sdf"
    if not sdf.is_file() or sdf.stat().st_size == 0:
        return []
    try:
        molecules = list(Chem.SDMolSupplier(str(sdf), removeHs=True, sanitize=True))
    except OSError:
        return []

    rows: list[tuple[str, str, str]] = []
    for molecule in molecules:
        if molecule is None:
            continue
        try:
            key = Chem.MolToInchiKey(molecule)
            smiles = Chem.MolToSmiles(molecule)
        except Exception:  # noqa: BLE001 -- one unconvertible molecule must not lose the chunk
            continue
        if key and smiles:
            rows.append((key, smiles, source))
    return rows


@dataclass(frozen=True, slots=True)
class Generator:
    """One generation loop: a model, a pocket, a device, and a target.

    ``tag`` rather than the model name is the identity, because a campaign may run the same model on
    several devices. Keying on the model merges them, which hides both the death of one loop and the
    fact that their uniqueness rates are independent evidence about the space being sampled.
    """

    tag: str
    model: str
    pocket: Pocket
    device: str
    protein: Path
    output_root: Path
    generation_config: Path
    chunk: int = DEFAULT_CHUNK
    total: int = 200_000
    qc: str = "standard"
    seed_base: int = 20_260_101

    def __post_init__(self) -> None:
        if not self.tag.strip() or not self.model.strip():
            raise ValueError("a generator needs a tag and a model")
        if self.chunk < 1 or self.total < 1:
            raise ValueError("chunk and total must be positive")

    @property
    def directory(self) -> Path:
        return self.output_root / self.tag

    def chunk_path(self, index: int) -> Path:
        return self.directory / f"chunk_{index:03d}"

    def next_index(self) -> int:
        """The first chunk index with no directory. Resuming is therefore free."""

        existing = sorted(p.name for p in self.directory.glob("chunk_*")) if self.directory.is_dir() else []
        return len(existing) + 1

    def command(self, index: int) -> list[str]:
        """The exact ``prism generate`` invocation for one chunk.

        The device is passed as a physical index and no ``CUDA_VISIBLE_DEVICES`` is set, because
        PRISM's wrappers disagree about which convention they follow: ``flowr``, ``molcraft`` and
        ``diffsbdd`` override an external mask and treat ``cuda:N`` as physical, while ``targetdiff``,
        ``pocket2mol`` and ``pocketxmol`` respect it. Setting neither is the only way to be right for
        both.
        """

        return [
            "prism", "generate",
            "--model", self.model,
            "--protein", str(self.protein),
            *self.pocket.arguments(),
            "-o", str(self.chunk_path(index)),
            "--generation-config", str(self.generation_config),
            "--num-samples", str(self.chunk),
            "--seed", str(self.seed_base + index * 1000 + abs(hash(self.tag)) % 997),
            "--device", self.device,
            "--qc", self.qc,
        ]


@dataclass
class PrismGeneration:
    """Runs one chunk at a time for one generator, as a subprocess.

    Injected into :class:`~etalon.campaign.supervisor.Supervisor` rather than imported by it, for the
    reason :data:`~etalon.campaign.loop.ExpensiveStage` gives: the part that needs a GPU should not be
    the part that cannot be tested.

    Not a loop. One call runs one chunk and returns what it produced; deciding whether to run another
    is the supervisor's, because that decision needs the pool's uniqueness numbers and this object
    does not have them.
    """

    timeout: int = 7_200
    #: Set to capture output somewhere other than the chunk's own log.
    log_suffix: str = ".log"

    def __call__(self, generator: Generator, index: int) -> ChunkResult:
        path = generator.chunk_path(index)
        path.parent.mkdir(parents=True, exist_ok=True)
        log = path.with_suffix(self.log_suffix)
        started = time.monotonic()
        try:
            with log.open("w", encoding="utf-8") as handle:
                completed = subprocess.run(  # noqa: S603 -- argv built from a validated Generator
                    generator.command(index),
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    timeout=self.timeout,
                    check=False,
                )
            returncode = completed.returncode
            barren = None
        except subprocess.TimeoutExpired:
            returncode, barren = -1, f"timed out after {self.timeout}s"
        except FileNotFoundError as error:
            returncode, barren = -1, f"prism is not on PATH ({error})"

        seconds = time.monotonic() - started
        delivered = 0
        if (path / "manifest.json").is_file():
            try:
                delivered = int(json.loads((path / "manifest.json").read_text())["candidate_count"])
            except Exception:  # noqa: BLE001 -- a manifest that will not parse is a barren chunk
                delivered = 0
        if barren is None and delivered == 0:
            barren = "no manifest, or a manifest reporting no candidates"
        return ChunkResult(
            tag=generator.tag,
            index=index,
            path=path,
            requested=generator.chunk,
            delivered=delivered,
            seconds=seconds,
            returncode=returncode,
            barren=barren,
        )


@dataclass
class Ingest:
    """Turns finished chunks into pool admissions, once each.

    Which chunks have been consumed is kept in a JSON file beside the pool rather than inferred, so a
    restarted supervisor does not re-read 150 chunk directories -- and so that a chunk whose molecules
    were admitted is not admitted again after the pool's deduplication has already forgotten why they
    were duplicates.
    """

    root: Path
    state: Path
    consumed: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.state.is_file():
            self.consumed = set(json.loads(self.state.read_text(encoding="utf-8")))

    def pending(self) -> Iterator[Path]:
        for chunk in finished_chunks(self.root):
            if str(chunk) not in self.consumed:
                yield chunk

    def consume(self, chunk: Path) -> list[tuple[str, str, str]]:
        rows = read_chunk(chunk)
        self.consumed.add(str(chunk))
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(json.dumps(sorted(self.consumed)), encoding="utf-8")
        return rows


def chunk_measurements(generator: Generator, results: Sequence[ChunkResult], unique: Sequence[int]):
    """Pair chunk results with the pool's uniqueness counts, for :mod:`etalon.generate.productivity`.

    ``unique`` has to come from the pool rather than from the generator: a chunk's novelty is measured
    against what the library already held, which only the pool knows.
    """

    from etalon.generate.productivity import Chunk, Productivity

    if len(results) != len(unique):
        raise ValueError("one uniqueness count per chunk result")
    return Productivity(
        tag=generator.tag,
        model=generator.model,
        pocket=generator.pocket.label,
        chunks=tuple(
            Chunk(
                requested=result.requested,
                delivered=max(result.delivered, 0),
                unique=max(min(count, result.delivered), 0),
                seconds=max(result.seconds, 1e-6),
            )
            for result, count in zip(results, unique, strict=True)
            if result.delivered > 0
        )
        or (Chunk(requested=generator.chunk, delivered=0, unique=0, seconds=1.0),),
    )


__all__ = [
    "BARREN_LIMIT",
    "DEFAULT_CHUNK",
    "ChunkResult",
    "Generator",
    "Ingest",
    "Pocket",
    "PrismGeneration",
    "chunk_measurements",
    "finished_chunks",
    "read_chunk",
]
