"""What all three docking engines need before any of them can dock anything.

The three adapters differ in almost everything -- input format, scoring
function, whether the pose is searched for or predicted -- but they agree on
four things, and those four live here so they cannot drift apart: where the
receptor is and what its bytes hash to, where the box is, how an out-of-process
engine is located and launched, and how its console output is quoted back when
it fails.

The receptor digest is the load-bearing one.  ``docking_score/v1`` makes
``receptor_id`` part of the primary key precisely so a score can never be
silently compared against a pose computed on different bytes, and that
identifier is this file's ``sha256`` of the structure as it sits on disk.

These adapters read and hash the receptor; they never edit one.  That is a
statement about *this* module rather than about the pipeline: repair happens
once upstream in :mod:`molcascade.cascade.receptor`, before any engine forms an
opinion, and what arrives here is the prepared file whose bytes the digest is
taken from -- so two engines cannot be handed two preparations of one protein.
What nobody in the pipeline does is guess.  Protonation states and tautomers
are never assigned, an unresolved loop is reported rather than rebuilt from a
template, and every change that *is* made is counted and named in the run's
target notes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator, model_validator

from molcascade.config.models import StrictFrozenModel
from molcascade.errors import PluginError
from molcascade.parallel import ShardTask, read_side_input
from molcascade.plugins.builtin._machine_paths import (
    MachinePath,
    absolute_path,
    backend_root,
    conda_environment,
    engine_path,
    environment_key,
    fill_machine_paths,
    path_from_environment,
)
from molcascade.plugins.builtin.docking.pose_quality import PoseQualityConfig

#: How much of a failed engine's console output travels into the error context.
#: Enough to show what it complained about, bounded so that a progress bar
#: redrawn ten thousand times cannot become the error message.
MAX_LOG_TAIL = 4000

#: A receptor is one protein structure.  Even a large complex with waters is a
#: few tens of megabytes; anything past this is a trajectory or a mistake.
MAX_RECEPTOR_BYTES = 256 * 1024 * 1024

#: Why a missing converter is this environment's problem and not the engine's.
#: meeko is what turns a conformer into the PDBQT the Vina-family engines read,
#: so it installs *here* rather than into an engine's own environment -- which
#: is the one thing that makes its absence fixable with a pip command.
MEEKO_HINT = (
    'pip install "molcascade[docking]". meeko is what turns a prepared conformer '
    "into the PDBQT a Vina-family engine reads, so without it there is nothing "
    "to hand the engine -- unlike the engine itself, it installs into this "
    "environment."
)

_READ_CHUNK = 4 * 1024 * 1024


def resolved_executable(configured: str, *, engine: str, hint: str) -> Path:
    """Check the interpreter-side facts about an engine before using it.

    ``shutil.which`` is deliberately not consulted, for the same reason as in
    the AiZynthFinder adapter: an isolated backend is one whose dependencies
    conflict with this environment's, so its ``bin`` is not on this process's
    ``PATH`` and must not be.  Resolving the name would find either nothing or
    something worse than nothing.
    """

    path = Path(configured)
    context = {"engine": engine, "executable": configured}
    try:
        info = path.stat()
    except OSError as error:
        raise PluginError(
            f"{engine} was not found at the configured path",
            code="DOCKING_EXECUTABLE_MISSING",
            hint=hint,
            context={**context, "error_type": type(error).__name__},
        ) from error
    if not stat.S_ISREG(info.st_mode):
        raise PluginError(
            f"the configured {engine} path is not a regular file",
            code="DOCKING_EXECUTABLE_MISSING",
            hint=hint,
            context=context,
        )
    if not os.access(path, os.X_OK):
        raise PluginError(
            f"the configured {engine} path is not executable by this user",
            code="DOCKING_EXECUTABLE_MISSING",
            hint=hint,
            context=context,
        )
    return path


def structure_digest(
    configured: str,
    *,
    code: str,
    hint: str,
    limit_bytes: int = MAX_RECEPTOR_BYTES,
) -> tuple[Path, str]:
    """Hash a structure file exactly as it sits on disk, without following links.

    ``O_NOFOLLOW`` and the regular-file check are the same fail-closed rules the
    artifact store applies: a receptor reached through a symlink is a receptor
    whose bytes can change between the digest and the docking run.
    """

    path = Path(configured)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    context = {"path": configured}
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PluginError(
            f"the structure file could not be opened: {configured}",
            code=code,
            hint=hint,
            context={**context, "error_type": type(error).__name__},
        ) from error
    digest = hashlib.sha256()
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PluginError(
                f"the structure path is not a regular file: {configured}",
                code=code,
                hint=hint,
                context=context,
            )
        if info.st_size > limit_bytes:
            raise PluginError(
                f"the structure file is larger than a structure file: {configured}",
                code=code,
                hint=hint,
                context={**context, "size_bytes": info.st_size, "limit_bytes": limit_bytes},
            )
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            while chunk := stream.read(_READ_CHUNK):
                digest.update(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return path, digest.hexdigest()


def verified_receptor(config: DockingEngineConfig) -> tuple[Path, str]:
    """Digest the receptor and, when the cascade pinned one, check it matches.

    A pinned digest that no longer matches is the failure this is for: the same
    cascade re-run against a re-prepared, re-protonated or simply different
    structure would otherwise produce scores that look comparable to the old
    ones and are not.
    """

    path, digest = structure_digest(
        config.receptor_path,
        code="DOCKING_RECEPTOR_UNREADABLE",
        hint=(
            "This is the prepared receptor structure for the docking tier -- "
            "the one resolve_target repaired and hashed, not the file the "
            "operator named. Protonation, tautomers and unresolved loops are "
            "still yours to decide; what was repaired is listed in the run's "
            "target notes."
        ),
    )
    if config.receptor_sha256 is not None and config.receptor_sha256 != digest:
        raise PluginError(
            "the receptor file does not match the digest recorded in the cascade",
            code="DOCKING_RECEPTOR_DIGEST_MISMATCH",
            hint=(
                "Every docking score records the receptor it was computed against. "
                "Either restore the structure this cascade was written for, or "
                "update 'target.receptor_sha256' and accept that the new scores "
                "are not comparable with the old ones."
            ),
            context={
                "receptor_path": config.receptor_path,
                "expected_sha256": config.receptor_sha256,
                "actual_sha256": digest,
            },
        )
    return path, digest


def require_pdb_receptor(receptor_path: str, *, engine: str, hint: str) -> None:
    """Refuse a receptor in a format the engine will misread rather than reject.

    For the two engines that open the operator's structure directly, the format
    it arrives in is the format they have to cope with, and both read PDB by
    parsing lines they recognise and ignoring the rest.  Handed an mmCIF that
    means no atoms rather than an error -- a run that completes against an empty
    protein.  The suffix is the only warning available before that happens.
    """

    suffix = Path(receptor_path).suffix.lower()
    if suffix != ".pdb":
        raise PluginError(
            f"{engine} reads PDB receptors and was given {suffix or 'no'} suffix",
            code="DOCKING_RECEPTOR_FORMAT_INVALID",
            hint=hint,
            context={"engine": engine, "receptor_path": receptor_path},
        )


def isolated_environment() -> dict[str, str]:
    """This process's environment minus the parts that would leak into theirs.

    ``CUDA_VISIBLE_DEVICES`` is deliberately *kept*: the shard pool already set
    it to this lane's card before the adapter ran, and inheriting it is exactly
    how an engine with no device flag -- Uni-Dock has none -- ends up on the
    card it was assigned.  ``PYTHONPATH`` and ``PYTHONHOME`` are just as
    deliberately dropped, since an engine pinned to an incompatible RDKit is the
    whole reason it runs somewhere else.
    """

    environment = dict(os.environ)
    for leaked in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        environment.pop(leaked, None)
    return environment


def log_tail(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace")[-MAX_LOG_TAIL:]


def run_engine(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    engine: str,
    code: str,
    hint: str,
    context: Mapping[str, Any] | None = None,
) -> str:
    """Run one docking engine and hand back its console output.

    An argv list, never a shell string, so nothing a user configures is parsed
    by ``/bin/sh``.  ``stdin`` is closed rather than inherited: an engine that
    decides to prompt would otherwise block a headless run forever, and a
    prompt answered by whatever happened to be on the terminal is worse.
    """

    extra = dict(context or {})
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=isolated_environment(),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise PluginError(
            f"{engine} did not finish within the configured budget",
            code=f"{code}_TIMEOUT",
            hint=(
                "Raise 'timeout_per_molecule_seconds' if the box or the search "
                "settings are genuinely this expensive; otherwise the engine is "
                "stuck rather than slow."
            ),
            context={**extra, "timeout_seconds": timeout},
        ) from error
    except OSError as error:
        raise PluginError(
            f"{engine} could not be started",
            code=code,
            hint=hint,
            context={**extra, "error_type": type(error).__name__},
        ) from error

    if completed.returncode != 0:
        raise PluginError(
            f"{engine} exited with status {completed.returncode}",
            code=code,
            hint=(
                f"The message below comes from {engine}, not MolCascade. "
                "A receptor it cannot parse and a box that misses the pocket "
                "are the usual causes; neither is something MolCascade fixes "
                "on your behalf."
            ),
            context={
                **extra,
                "returncode": completed.returncode,
                "output_tail": log_tail(completed.stdout),
            },
        )
    return log_tail(completed.stdout)


def shard_geometry(
    task: ShardTask,
    parent_ids: Sequence[str],
    *,
    index: int,
    name: str = "conformers",
) -> dict[str, str]:
    """The molblock each of this batch's molecules will be docked as.

    Joined on ``parent_id`` rather than on row position: a gate between the
    preparation stage and a docking stage is entitled to remove molecules, and
    a positional read would then dock every survivor as its neighbour --
    producing a complete, plausible, entirely wrong score table.

    Shared by the two engines that dock a supplied conformer, so that a
    consensus between them is a consensus about the same geometry rather than
    about two joins that happen to agree today.
    """

    table = read_side_input(
        task,
        name,
        keys=list(parent_ids),
        columns=["parent_id", "conformer_index", "molblock"],
    )
    geometry: dict[str, str] = {}
    for row in table.to_pylist():
        if int(row["conformer_index"]) != index:
            continue
        molblock = row["molblock"]
        if isinstance(molblock, str) and molblock:
            geometry[str(row["parent_id"])] = molblock
    return geometry


def ranked(scores: Sequence[float], *, limit: int, descending: bool) -> list[int]:
    """Order one ligand's poses best-first, and say which block is which.

    Both engines emit their poses in score order already, so this is normally
    the identity.  It is done anyway because ``docking_score/v1`` states
    best-first ordering as an invariant, and an invariant inherited from another
    program's output format is one release away from not holding.  Ties keep
    file order, so the ranking is stable.
    """

    order = sorted(
        range(len(scores)),
        key=lambda index: (-scores[index] if descending else scores[index], index),
    )
    return order[:limit]


def finite_number(value: str | None) -> float | None:
    """A number, or ``None`` for anything that is not one.

    Engines write a NaN when a minimisation or a correction failed to converge,
    and a NaN in a score column compares false against every threshold -- a
    filter that rejects silently and leaves no trace of having done so.  Absent
    says the same thing in a way the gate downstream has to handle.
    """

    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def population_size(files: Sequence[Path]) -> int:
    """Count the rows a stage is about to dock, from the footers alone.

    Read before the first shard runs, because a cap that is only noticed once
    the work is under way is not a cap.  Parquet keeps the row count in the
    footer, so this costs one seek per file and no data pages.
    """

    return sum(pq.ParquetFile(path).metadata.num_rows for path in files)


def enforce_population_cap(count: int, *, limit: int, engine: str) -> None:
    """Refuse a run that is larger than the operator said they meant."""

    if count > limit:
        raise PluginError(
            f"{engine} was asked to dock {count} molecules against a cap of {limit}",
            code="DOCKING_POPULATION_TOO_LARGE",
            hint=(
                "Docking is the most expensive tier in the funnel. Either put a "
                "cheaper gate in front of it, or raise 'max_molecules' on this "
                "stage once you have worked out what the run will cost."
            ),
            context={"engine": engine, "molecule_count": count, "max_molecules": limit},
        )


class DockingEngineConfig(StrictFrozenModel):
    """The target every engine in the tier is pointed at.

    Lowering writes these from the cascade's single ``target`` block using the
    existing "only fields the plugin declares" rule, so three engines in one
    tier are structurally incapable of disagreeing about which protein or which
    bytes of it they were asked about.

    ``receptor_path`` is always the structure the *user* supplied, never a file
    MolCascade derived from it.  Uni-Dock reads a PDBQT and KarmaDock reads a
    PDB, and if each recorded the digest of what it happened to open, the same
    target would carry two ``receptor_id`` values and the tier's scores would
    stop being about one protein.  Derived files are digested too, but they
    belong to ``method_id`` -- they change how a number was computed, not what
    it is a number about.
    """

    #: Which engine's environment overrides apply to this config.
    engine_id: ClassVar[str] = ""

    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=2_048, ge=1, le=250_000)

    #: Absolute path to the engine's program: the compiled binary, or the
    #: interpreter inside the environment built for it.  May be left out of the
    #: cascade entirely, in which case the run host answers it -- from
    #: ``MOLCASCADE_<ENGINE>_EXECUTABLE`` or from where ``envs/bootstrap.sh``
    #: installs, resolved by :func:`~molcascade.plugins.builtin._machine_paths.
    #: fill_machine_paths` and required by
    #: :func:`~molcascade.backends.preflight.preflight_engine_paths`.
    executable: str = Field(default="", max_length=4096)

    #: Absolute path to the prepared receptor structure.
    receptor_path: str = Field(min_length=1, max_length=4096)

    #: The receptor's sha256 as the cascade recorded it, verified before the
    #: first ligand is written.  ``None`` means this run establishes it.
    receptor_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    #: Poses kept per ligand.  ``docking_score/v1`` carries one row per pose.
    num_modes: int = Field(default=1, ge=1, le=20)

    seed: int = Field(default=20_260_823, ge=0, le=2**31 - 1)

    #: Fail-closed cap, so pointing an engine at a whole library stops here
    #: rather than at three in the morning.
    max_molecules: int = Field(default=200_000, ge=1, le=10_000_000)

    #: Wall-clock budget per molecule, multiplied by a shard's size.  A guard
    #: against a hung engine, not a search-time setting.
    timeout_per_molecule_seconds: float = Field(default=60.0, gt=0.0, le=86_400.0)

    #: Where shard inputs and engine outputs are written.  Worth setting when
    #: the system temporary directory is a small tmpfs.
    scratch_dir: str | None = Field(default=None, max_length=4096)

    #: Pose repair and validation, on by default for every engine.
    #:
    #: It lives here rather than in a separate cascade stage because a pose no
    #: one checked is not a result an operator should have to opt in to
    #: distrusting.  Measured on this project's own shortlist, 82% of poses that
    #: passed the score gate were inside the protein's van der Waals surface,
    #: and the score saw none of it (r = +0.01).  Turning it off is one field:
    #: ``pose_quality: {enabled: false}``.
    pose_quality: PoseQualityConfig = Field(default_factory=PoseQualityConfig)

    @classmethod
    def installed_paths(cls) -> tuple[MachinePath, ...]:
        """Every path that describes *this machine's* installation, not the campaign.

        The receptor and the reference ligand are deliberately absent: those are
        facts about what is being screened, so they stay in the cascade where a
        reviewer can see them.  These are the ones a host can answer for itself,
        and each entry carries where to look and what installs it -- see
        :class:`~molcascade.plugins.builtin._machine_paths.MachinePath`.

        The base names the field without naming any candidate, because where an
        engine's program lives is the one thing this class cannot know.  Each
        engine overrides with its own.
        """

        return (MachinePath(field="executable", label="the engine's program"),)

    @model_validator(mode="before")
    @classmethod
    def _fill_machine_paths(cls, data: Any) -> Any:
        return fill_machine_paths(
            data,
            engine_id=cls.engine_id,
            paths=cls.installed_paths(),
        )

    @field_validator("executable")
    @classmethod
    def _executable_is_absolute(cls, value: str) -> str:
        return engine_path(value, field="executable", engine_id=cls.engine_id)

    @field_validator("receptor_path")
    @classmethod
    def _receptor_is_absolute(cls, value: str) -> str:
        return absolute_path(value, field="receptor_path")

    @field_validator("scratch_dir")
    @classmethod
    def _scratch_is_absolute(cls, value: str | None) -> str | None:
        return None if value is None else absolute_path(value, field="scratch_dir")


class BoxedDockingEngineConfig(DockingEngineConfig):
    """A target plus the search volume, for the engines that take one.

    Uni-Dock and GNINA search a box the operator chose.  KarmaDock does not --
    it locates the site from a reference ligand and predicts a pose rather than
    searching for one -- so it deliberately does not inherit these fields, and
    lowering's "write only what the plugin declares" rule keeps a box out of
    its config rather than passing one it would ignore.
    """

    center_x: float
    center_y: float
    center_z: float
    #: Box edge lengths in angstrom.  Vina-family search cost grows with the
    #: volume, and a box larger than this is a blind-docking experiment rather
    #: than a pocket.
    size_x: float = Field(gt=0.0, le=200.0)
    size_y: float = Field(gt=0.0, le=200.0)
    size_z: float = Field(gt=0.0, le=200.0)

    @property
    def box(self) -> dict[str, float]:
        return {
            "center_x": self.center_x,
            "center_y": self.center_y,
            "center_z": self.center_z,
            "size_x": self.size_x,
            "size_y": self.size_y,
            "size_z": self.size_z,
        }

    def box_arguments(self) -> list[str]:
        """The box as command-line arguments, in a fixed order.

        Fixed because the order ends up in the recorded command, and a command
        that differs only by argument order between two runs of the same
        cascade would look like two different experiments.
        """

        arguments: list[str] = []
        for name, value in self.box.items():
            arguments.extend([f"--{name}", _number(value)])
        return arguments


def _number(value: float) -> str:
    """Render a box coordinate the same way every time.

    ``repr`` of a float is shortest-round-trip, so ``10.0`` stays ``10.0`` and
    never becomes ``1e+01`` on one machine and ``10`` on another.
    """

    return repr(float(value))


def validated_config(request: Any, model: type[Any], *, engine: str, hint: str) -> Any:
    """Validate one engine's config, failing with the flag that fixes it."""

    try:
        return model.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid {engine} configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            hint=hint,
            context={"engine": engine, "error_count": error.error_count()},
        ) from error


def require_gpu_lane(device: str, *, engine: str) -> str:
    """Refuse to pretend a CUDA-only engine can run on a CPU lane.

    Uni-Dock has no CPU code path at all -- it is a CUDA kernel with a command
    line around it -- so a CPU lane here is not slow, it is a crash several
    minutes in.  Saying so before the first ligand is written costs nothing.
    """

    if not device.startswith("cuda:"):
        raise PluginError(
            f"{engine} requires a CUDA device and was assigned lane {device!r}",
            code="DOCKING_GPU_REQUIRED",
            hint=(
                "Run with '--device cuda' on a machine with an NVIDIA card, or "
                "drop this engine from the tier. GNINA is the one engine here "
                "with a CPU path, and it is thousands of times slower on one."
            ),
            context={"engine": engine, "device": device},
        )
    return device


def require_meeko(*, engine: str) -> None:
    """Ask for the ligand converter before the shard rather than during it.

    The conversion happens once per molecule, deep inside a batch loop, so a
    plain ``ImportError`` from there surfaces as a traceback out of a worker
    process after the run has already started -- for the one dependency the
    ``docking`` extra exists to install.  ``find_spec`` answers the question
    without importing anything, which matters because this is called from
    ``execute`` on every stage that will later need it.
    """

    if importlib.util.find_spec("meeko") is None:
        raise PluginError(
            f"{engine} prepares its ligands with meeko, which is not installed",
            code="DOCKING_LIGAND_PREP_UNAVAILABLE",
            hint=MEEKO_HINT,
            context={"engine": engine, "module": "meeko"},
        )


def ligand_pdbqt(molblock: str) -> str | None:
    """Convert one prepared conformer into the format a Vina-family engine reads.

    ``None`` rather than an exception: meeko refuses molecules it has no atom
    types for, and one of those in a batch of two thousand is a molecule to
    count and move past, not a reason to lose the batch.
    """

    from meeko import MoleculePreparation, PDBQTWriterLegacy
    from rdkit import Chem

    molecule = Chem.MolFromMolBlock(molblock, removeHs=False)
    if molecule is None:
        return None
    try:
        setups = MoleculePreparation().prepare(molecule)
    except (RuntimeError, ValueError, KeyError, TypeError):
        return None
    if not setups:
        return None
    pdbqt, ok, _message = PDBQTWriterLegacy.write_string(setups[0])
    if not ok or not pdbqt:
        return None
    return str(pdbqt)


def parse_vina_poses(text: str) -> list[float]:
    """Pull one score per ``MODEL`` out of an output PDBQT, in file order.

    Vina writes its result as a ``REMARK`` inside each ``MODEL`` block.  A file
    with no ``MODEL`` framing at all is read as a single pose, which is what
    some builds emit when only one mode was requested.
    """

    scores: list[float] = []
    score: float | None = None
    in_model = False
    for line in text.splitlines():
        if line.startswith("MODEL"):
            in_model, score = True, None
            continue
        if line.startswith("ENDMDL"):
            if score is not None:
                scores.append(score)
            in_model, score = False, None
            continue
        if line.startswith("REMARK VINA RESULT:"):
            fields = line.removeprefix("REMARK VINA RESULT:").split()
            try:
                score = float(fields[0]) if fields else None
            except ValueError:
                score = None
    if not scores and not in_model and score is not None:
        scores.append(score)
    return scores


__all__ = [
    "MAX_LOG_TAIL",
    "MAX_RECEPTOR_BYTES",
    "MEEKO_HINT",
    "BoxedDockingEngineConfig",
    "DockingEngineConfig",
    "MachinePath",
    "absolute_path",
    "backend_root",
    "conda_environment",
    "enforce_population_cap",
    "engine_path",
    "environment_key",
    "fill_machine_paths",
    "finite_number",
    "isolated_environment",
    "ligand_pdbqt",
    "log_tail",
    "parse_vina_poses",
    "path_from_environment",
    "population_size",
    "ranked",
    "require_gpu_lane",
    "require_meeko",
    "require_pdb_receptor",
    "resolved_executable",
    "run_engine",
    "shard_geometry",
    "structure_digest",
    "validated_config",
    "verified_receptor",
]
