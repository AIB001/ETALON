"""Retrosynthetic route search, run in a separate environment on purpose.

Every other synthesis backend in this project is a proxy.  SA Score asks how
unusual a molecule's fragments are, SCScore asks how much reaction complexity
it carries; neither one searches for a route, and both say so in their own
``domain`` string.  AiZynthFinder actually searches, so it is the only backend
here whose score is a step count rather than a number correlated with one.

It cannot run in this process, and the reason is measured rather than assumed.
Resolving ``aizynthfinder`` 4.4.1 into this environment downgrades RDKit from
2026.03 to 2023.09 and NumPy from 2.x to 1.26.  That changes every descriptor,
every fingerprint, every policy digest and every ADMET checkpoint upstream of
this stage -- a whole cascade quietly re-scored so that its last tier can run.
So route search lives in its own environment, behind ``aizynthcli``, and this
adapter is the process boundary rather than a wrapper around an import.

MolCascade does not create that environment.  It does not run pip, it does not
download policy or stock files, and it cannot pin the digest of a model tree it
has never seen.  The user builds the environment once, and the stage carries
two absolute paths: the ``aizynthcli`` inside it, and the AiZynthFinder
configuration file that names the policy and the stock.  Both are checked
before a single molecule is written out.

Three decisions in here are worth stating rather than leaving to be discovered.

*The score is a step count, and unsolved is not zero.*  ``synthesis_score/v1``
makes ``score`` non-nullable, so an unsolved target still needs a number.  On a
HIGHER_HARDER axis the honest number is a large one, not a zero -- zero reads
as "trivially easy", which is the opposite of what "no route was found" means.
The sentinel is configurable, defaults to something far outside any real step
count, and carries a ``ROUTE_NOT_FOUND`` warning code on the row so that a
downstream gate rejects it rather than ranking it.

*Alignment is checked, not trusted.*  ``aizynthcli`` returns one record per
input line in input order, which makes position the natural join.  Position is
also exactly the thing that fails silently if a future version reorders,
deduplicates or drops a target, and a silently misaligned step count is a
scientific error rather than a crash.  So both sides are canonicalized by *this*
process's RDKit -- one canonicalizer, so no version drift between the two
environments can manufacture a disagreement -- and any mismatch stops the stage.

*The backend version is pinned by the user or not at all.*  ``aizynthcli`` has
no ``--version``, and inferring one from the shebang of a console script is a
guess that would be wrong on Windows and in any vendored layout.  Rather than
fabricate provenance, ``backend_version`` is an optional setting: when it is
given it is hashed into the method identity, and when it is not, every row
carries ``ROUTE_BACKEND_VERSION_UNPINNED`` so the gap is visible in the data
instead of implied by its absence.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, ClassVar

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import iter_contract_batches, require_single_input
from molcascade.config.canonical import canonical_json, canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import PARENT_V1, SYNTHESIS_SCORE_V1
from molcascade.errors import PluginError
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.builtin._machine_paths import (
    MachinePath,
    absolute_path,
    backend_root,
    conda_environment,
    engine_path,
    fill_machine_paths,
)
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/parents/part-00000.parquet")
_SCORE_PATH = Path("datasets/synthesis_scores/part-00000.parquet")

_IMPLEMENTATION_VERSION = 1

#: The configuration file names policies and stocks; it is not a data file.  A
#: megabyte is already several orders of magnitude more YAML than one needs.
_MAX_CONFIG_BYTES = 4 * 1024 * 1024

#: ``aizynthcli`` writes the full search trees into the same file as the
#: statistics, so the output grows with ``post_processing.max_routes`` rather
#: than with the number of targets.  It has to be parsed whole -- pandas'
#: ``orient="table"`` JSON has no record framing to stream against -- so the cap
#: is a memory guard with a hint attached, not a capability limit.
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024 * 1024
_READ_CHUNK = 4 * 1024 * 1024

#: How much of a failed run's console output travels into the error context.
#: Enough to show the traceback AiZynthFinder ended on, bounded so that a
#: progress bar redrawn ten thousand times cannot become the error message.
_MAX_LOG_TAIL = 4000

_DOMAIN_SOLVED = (
    "AiZynthFinder retrosynthetic tree search; score is the number of reactions "
    "in the top-scored route, every precursor of which was found in the "
    "configured stock"
)
_DOMAIN_UNSOLVED = (
    "AiZynthFinder retrosynthetic tree search; no route to the configured stock "
    "was found within the configured search budget, and the score is the "
    "stage's unsolved sentinel rather than a step count"
)

_SOLVED_WARNINGS: tuple[str, ...] = ()
_UNSOLVED_WARNINGS: tuple[str, ...] = ("ROUTE_NOT_FOUND",)
_UNPINNED_WARNING = "ROUTE_BACKEND_VERSION_UNPINNED"


def _valid_names(value: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    for name in value:
        if not name or name != name.strip() or name.startswith("-") or " " in name:
            raise ValueError(f"{field} entries must be bare names, not command-line flags")
    return value


class AiZynthFinderConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)

    #: Which engine's environment overrides apply, giving the two paths below
    #: ``MOLCASCADE_AIZYNTHFINDER_EXECUTABLE`` and
    #: ``MOLCASCADE_AIZYNTHFINDER_CONFIG_PATH``.  See
    #: :mod:`molcascade.plugins.builtin._machine_paths`.
    engine_id: ClassVar[str] = "aizynthfinder"

    @classmethod
    def installed_paths(cls) -> tuple[MachinePath, ...]:
        """An interpreter's neighbour and a 750 MB download, found where they land.

        The public data is the interesting one: it is a genuine weight-and-stock
        download rather than an installation, and it is not a MolCascade asset --
        AiZynthFinder resolves the policy and stock files itself, relative to
        this YAML, so a copy fetched into the asset store would never be read.
        Which makes the remedy below the download instruction, not a path hint.
        """

        data = os.environ.get("MOLCASCADE_AIZYNTH_DATA", "").strip()
        data_root = Path(data).expanduser() if data else Path.home() / "aizynth-data"
        return (
            MachinePath(
                field="executable",
                label="the aizynthcli inside AiZynthFinder's own environment",
                candidates=conda_environment("aizynth", "bin/aizynthcli"),
                remedy="bash envs/bootstrap.sh aizynth",
            ),
            MachinePath(
                field="config_path",
                label="the AiZynthFinder configuration naming its policy and stock",
                kind="file",
                candidates=(
                    data_root / "config.yml",
                    backend_root() / "aizynth-data" / "config.yml",
                ),
                remedy="bash envs/bootstrap.sh aizynth",
                note=(
                    "That step downloads about 750 MB of expansion policy and stock; "
                    "the configuration is written beside it and points at it by "
                    "relative path, so the two move together."
                ),
            ),
        )

    #: Absolute path to ``aizynthcli`` inside the isolated environment, for
    #: example ``/home/you/miniforge3/envs/aizynth/bin/aizynthcli``.  Not a bare
    #: name: the environment this adapter calls into must not be on this
    #: process's ``PATH``, because the dependency conflict is the whole reason
    #: it exists somewhere else.
    #:
    #: Defaulted to empty rather than left required so that the environment or
    #: the host layout can answer it; blank with nothing found is still refused,
    #: by ``preflight_engine_paths``, at the start of the run.
    executable: str = Field(default="", max_length=4096)

    #: Absolute path to the AiZynthFinder YAML that names the expansion policy
    #: and the stock.  MolCascade never reads its contents, only its bytes, and
    #: hashes them into the method identity.  A machine path like the one above:
    #: the policy and stock files it points at were installed with the engine.
    config_path: str = Field(default="", max_length=4096)

    #: Names defined in that YAML, passed through to ``aizynthcli`` when the
    #: file defines more than one and the stage wants a specific selection.
    policy: tuple[str, ...] = ()
    filter: tuple[str, ...] = ()
    stocks: tuple[str, ...] = ()

    #: The AiZynthFinder release, as the user knows it to be.  Optional, hashed
    #: into ``method_id`` when supplied; see the module docstring.
    backend_version: str | None = Field(default=None, min_length=1, max_length=64)

    #: Tree search is seconds to minutes per molecule.  The cap exists so that
    #: pointing this stage at a whole library fails in the preflight instead of
    #: at three in the morning, and raising it has to be a deliberate act.
    max_molecules: int = Field(default=1_000, ge=1, le=100_000)

    #: Wall-clock budget per molecule, multiplied by the number of molecules
    #: each worker will see.  This is a deadlock guard on top of whatever
    #: ``time_limit`` the AiZynthFinder configuration already sets, not a
    #: replacement for it.
    timeout_per_molecule_seconds: float = Field(default=600.0, gt=0.0, le=86_400.0)

    #: Passed to ``aizynthcli --nproc``, which splits the input file across that
    #: many processes.  Left unset, AiZynthFinder runs single-process.
    nproc: int | None = Field(default=None, ge=1, le=256)

    #: The score recorded for a target no route was found for.  Large, because
    #: the axis is HIGHER_HARDER and the row also carries ``ROUTE_NOT_FOUND``.
    unsolved_score: float = Field(default=99.0, ge=0.0, le=1_000_000.0)

    #: Where the SMILES input and the search output are written.  Defaults to
    #: the system temporary directory; worth setting when that is a small
    #: tmpfs, because the output carries every route it found.
    scratch_dir: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="before")
    @classmethod
    def _fill_machine_paths(cls, data: Any) -> Any:
        return fill_machine_paths(data, engine_id=cls.engine_id, paths=cls.installed_paths())

    @field_validator("executable")
    @classmethod
    def _executable_is_absolute(cls, value: str) -> str:
        return engine_path(value, field="executable", engine_id=cls.engine_id)

    @field_validator("config_path")
    @classmethod
    def _config_is_absolute(cls, value: str) -> str:
        return engine_path(value, field="config_path", engine_id=cls.engine_id)

    @field_validator("scratch_dir")
    @classmethod
    def _scratch_is_absolute(cls, value: str | None) -> str | None:
        return None if value is None else absolute_path(value, field="scratch_dir")

    @field_validator("policy", "filter", "stocks", mode="before")
    @classmethod
    def _arrays_to_tuples(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("policy", "filter", "stocks")
    @classmethod
    def _names_are_bare(cls, value: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _valid_names(value, field=str(info.field_name))


def _validated_config(request: StageRequest) -> AiZynthFinderConfig:
    try:
        return AiZynthFinderConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid AiZynthFinder configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            hint=(
                "'executable' is the absolute path to aizynthcli inside the "
                "environment you created for it, and 'config_path' is the absolute "
                "path to that environment's AiZynthFinder YAML. Both are required "
                "and neither may be a bare command name."
            ),
            context={"error_count": error.error_count()},
        ) from error


def _prepare_outputs(context: StageContext) -> tuple[Path, Path]:
    context.staging_root.mkdir(parents=True, exist_ok=True)
    destinations = (
        context.staging_root / _PARENT_PATH,
        context.staging_root / _SCORE_PATH,
    )
    for relative, destination in zip((_PARENT_PATH, _SCORE_PATH), destinations, strict=True):
        if destination.exists() or destination.is_symlink():
            raise PluginError(
                f"AiZynthFinder output already exists: {relative.as_posix()}",
                code="PLUGIN_STAGING_NOT_EMPTY",
                context={"path": relative.as_posix()},
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
    return destinations


def _resolved_executable(configured: str) -> Path:
    """Check the interpreter-side facts about ``aizynthcli`` before using it.

    ``shutil.which`` is deliberately not consulted.  An isolated backend is one
    whose dependencies conflict with this environment's, so its ``bin`` is not
    on this process's ``PATH`` and must not be: resolving the name would find
    either nothing, or something worse than nothing.
    """

    path = Path(configured)
    try:
        info = path.stat()
    except OSError as error:
        raise PluginError(
            "aizynthcli was not found at the configured path",
            code="AIZYNTHFINDER_EXECUTABLE_MISSING",
            hint=(
                "Create the environment once, outside MolCascade, and point this "
                "stage at the aizynthcli inside it -- for example "
                "'conda create -n aizynth \"python>=3.10,<3.13\"' followed by "
                "'conda run -n aizynth python -m pip install aizynthfinder', then "
                "use the absolute path that 'conda run -n aizynth which aizynthcli' "
                "prints. MolCascade never installs anything itself."
            ),
            context={"executable": configured, "error_type": type(error).__name__},
        ) from error
    if not stat.S_ISREG(info.st_mode):
        raise PluginError(
            "the configured aizynthcli path is not a regular file",
            code="AIZYNTHFINDER_EXECUTABLE_MISSING",
            context={"executable": configured},
        )
    if not os.access(path, os.X_OK):
        raise PluginError(
            "the configured aizynthcli path is not executable by this user",
            code="AIZYNTHFINDER_EXECUTABLE_MISSING",
            context={"executable": configured},
        )
    return path


def _config_digest(configured: str) -> tuple[Path, str]:
    """Hash the AiZynthFinder configuration exactly as it sits on disk.

    Its contents are never interpreted here.  The file names the policy network
    and the stock, which is to say it decides what the step counts mean, so it
    belongs in the method identity -- but its schema belongs to AiZynthFinder
    and parsing it here would be this project asserting a version compatibility
    it has no way to keep.
    """

    path = Path(configured)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PluginError(
            "the AiZynthFinder configuration file could not be opened",
            code="AIZYNTHFINDER_CONFIG_UNREADABLE",
            hint=(
                "This is the YAML that names your expansion policy and stock files. "
                "MolCascade does not download them; see the AiZynthFinder "
                "documentation for 'download_public_data'."
            ),
            context={"config_path": configured, "error_type": type(error).__name__},
        ) from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PluginError(
                "the AiZynthFinder configuration path is not a regular file",
                code="AIZYNTHFINDER_CONFIG_UNREADABLE",
                context={"config_path": configured},
            )
        if info.st_size > _MAX_CONFIG_BYTES:
            raise PluginError(
                "the AiZynthFinder configuration file is larger than a configuration file",
                code="AIZYNTHFINDER_CONFIG_UNREADABLE",
                context={"size_bytes": info.st_size, "limit_bytes": _MAX_CONFIG_BYTES},
            )
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return path, hashlib.sha256(payload).hexdigest()


def _isolated_environment() -> dict[str, str]:
    """This process's environment minus the parts that would leak into theirs.

    ``PYTHONPATH`` and ``PYTHONHOME`` are inherited by the child interpreter and
    are exactly how one environment's site-packages ends up in front of
    another's.  Since the entire point of running AiZynthFinder out of process
    is that its RDKit and NumPy must not be this one's, letting either variable
    through would reintroduce the conflict at the worst possible moment: after
    the run has started.
    """

    environment = dict(os.environ)
    for leaked in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        environment.pop(leaked, None)
    return environment


def _read_output(path: Path) -> list[dict[str, Any]]:
    """Parse what ``aizynthcli --output`` wrote into a list of records.

    AiZynthFinder saves a pandas frame with ``orient="table"`` unless the name
    ends in ``.hdf5``, so the document is ``{"schema": ..., "data": [...]}``.
    The name this adapter passes is always ``.json``; the gzip sniff is here
    because pandas infers compression from the extension and a future default
    that appends ``.gz`` would otherwise turn into an unreadable-output error
    rather than a working run.
    """

    try:
        size = path.stat().st_size
    except OSError as error:
        raise PluginError(
            "aizynthcli exited successfully but wrote no output file",
            code="AIZYNTHFINDER_OUTPUT_MISSING",
            context={"output_path": str(path), "error_type": type(error).__name__},
        ) from error
    if size > _MAX_OUTPUT_BYTES:
        raise PluginError(
            "the aizynthcli output is larger than this adapter will read",
            code="AIZYNTHFINDER_OUTPUT_TOO_LARGE",
            hint=(
                "The search trees are stored alongside the statistics, so the file "
                "grows with 'post_processing.max_routes' in your AiZynthFinder "
                "configuration rather than with the number of molecules. Lower it, "
                "or split this tier across several runs."
            ),
            context={"size_bytes": size, "limit_bytes": _MAX_OUTPUT_BYTES},
        )
    payload = path.read_bytes()
    if payload[:2] == b"\x1f\x8b":
        expanded = bytearray()
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
                while chunk := stream.read(_READ_CHUNK):
                    expanded.extend(chunk)
                    if len(expanded) > _MAX_OUTPUT_BYTES:
                        raise PluginError(
                            "the aizynthcli output expands beyond what this adapter will read",
                            code="AIZYNTHFINDER_OUTPUT_TOO_LARGE",
                            context={"limit_bytes": _MAX_OUTPUT_BYTES},
                        )
        except (OSError, EOFError, gzip.BadGzipFile) as error:
            raise PluginError(
                "the aizynthcli output is a truncated or corrupt gzip file",
                code="AIZYNTHFINDER_OUTPUT_UNREADABLE",
                context={"output_path": str(path)},
            ) from error
        payload = bytes(expanded)
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as error:
        raise PluginError(
            "the aizynthcli output is not the JSON table this adapter expects",
            code="AIZYNTHFINDER_OUTPUT_UNREADABLE",
            hint=(
                "Give --output a '.json' name rather than '.hdf5'; MolCascade does "
                "so itself, which means this points at a version of aizynthcli "
                "whose output format has changed."
            ),
            context={"output_path": str(path)},
        ) from error
    records = document.get("data") if isinstance(document, dict) else document
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise PluginError(
            "the aizynthcli output does not contain a row per target",
            code="AIZYNTHFINDER_OUTPUT_UNREADABLE",
            context={"output_path": str(path)},
        )
    return records


def _canonical_smiles(smiles: object, *, parent_id: object) -> str:
    from rdkit import Chem

    molecule = Chem.MolFromSmiles(smiles if isinstance(smiles, str) else "")
    if molecule is None:
        raise PluginError(
            "registered parent cannot be parsed for route search",
            code="SYNTHESIS_PARENT_INVALID",
            context={"parent_id": str(parent_id)},
        )
    return str(Chem.MolToSmiles(molecule))


def _returned_canonical_smiles(value: object) -> str | None:
    from rdkit import Chem

    if not isinstance(value, str):
        return None
    molecule = Chem.MolFromSmiles(value)
    return None if molecule is None else str(Chem.MolToSmiles(molecule))


class AiZynthFinderRoutePlugin:
    """Search for a retrosynthetic route with AiZynthFinder, out of process."""

    descriptor = PluginDescriptor(
        id="synthesis.aizynthfinder",
        version="0.1.0",
        kind=PluginKind.SYNTHESIS,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, SYNTHESIS_SCORE_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "synthesis_scores": SYNTHESIS_SCORE_V1.id,
        },
        cardinality=Cardinality.ONE_TO_ONE,
        # Monte-Carlo tree search under a wall-clock budget: two runs of the
        # same molecule against the same policy can return different routes,
        # and the step count can differ with them.
        determinism=Determinism.NON_DETERMINISTIC,
        display_name="AiZynthFinder retrosynthetic route search",
        description=(
            "Route step count from a Monte-Carlo tree search against a local "
            "policy and stock, executed in a separate environment through "
            "aizynthcli (higher is harder; unsolved targets are flagged)."
        ),
    )
    config_model = AiZynthFinderConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)

        # Both checks run before any molecule is read, so a missing environment
        # costs nothing rather than costing a pass over the shortlist.
        executable = _resolved_executable(config.executable)
        config_file, config_sha256 = _config_digest(config.config_path)

        parent_destination, score_destination = _prepare_outputs(context)
        method_id = "synthesis-route:sha256:" + canonical_sha256(
            {
                "backend": "aizynthfinder",
                "backend_version": config.backend_version,
                "implementation_version": _IMPLEMENTATION_VERSION,
                "method": "AiZynthFinder Monte-Carlo tree search",
                "doi": "10.1186/s13321-020-00472-1",
                "config_sha256": config_sha256,
                "policy": list(config.policy),
                "filter": list(config.filter),
                "stocks": list(config.stocks),
                "unsolved_score": config.unsolved_score,
                "direction": "HIGHER_HARDER",
            }
        )

        try:
            parent_ids, canonical = self._write_parents(
                stage_input,
                parent_destination,
                config=config,
            )
            records = self._search(
                canonical,
                config=config,
                executable=executable,
                config_file=config_file,
            )
            solved_count = self._write_scores(
                score_destination,
                parent_ids=parent_ids,
                canonical=canonical,
                records=records,
                config=config,
                method_id=method_id,
            )
        except BaseException:
            parent_destination.unlink(missing_ok=True)
            score_destination.unlink(missing_ok=True)
            raise

        input_count = len(parent_ids)
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    (_PARENT_PATH.as_posix(),),
                    {"row_count": input_count},
                ),
                "synthesis_scores": PendingOutput(
                    SYNTHESIS_SCORE_V1.id,
                    (_SCORE_PATH.as_posix(),),
                    {"row_count": input_count, "method_id": method_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": input_count,
                "method_id": method_id,
                "direction": "HIGHER_HARDER",
                "proxy_not_route": False,
                "solved_count": solved_count,
                "unsolved_count": input_count - solved_count,
                "unsolved_score": config.unsolved_score,
                "backend": "aizynthfinder",
                "backend_version": config.backend_version,
                "config_sha256": config_sha256,
                "nproc": config.nproc,
            },
        )

    def _write_parents(
        self,
        stage_input: Any,
        destination: Path,
        *,
        config: AiZynthFinderConfig,
    ) -> tuple[list[Any], list[str]]:
        """Pass the parents through and collect the targets to search for.

        The whole shortlist is held in memory, which is affordable precisely
        because ``max_molecules`` refuses the case where it would not be.
        """

        parent_ids: list[Any] = []
        canonical: list[str] = []
        with pq.ParquetWriter(destination, PARENT_V1.schema, compression="zstd") as writer:
            for batch in iter_contract_batches(
                stage_input,
                PARENT_V1,
                batch_size=config.batch_size,
            ):
                for row in batch.select(["parent_id", "parent_smiles"]).to_pylist():
                    parent_id = row.get("parent_id")
                    if len(parent_ids) >= config.max_molecules:
                        raise PluginError(
                            "route search was given more molecules than this stage allows",
                            code="AIZYNTHFINDER_INPUT_TOO_LARGE",
                            hint=(
                                "Tree search costs seconds to minutes per molecule, so it "
                                "belongs after the cheap tiers have reduced the library. "
                                "Move this tier later, or raise 'max_molecules' knowing "
                                f"the run will take roughly {config.max_molecules} times "
                                "one molecule's search time."
                            ),
                            context={"max_molecules": config.max_molecules},
                        )
                    parent_ids.append(parent_id)
                    canonical.append(
                        _canonical_smiles(row.get("parent_smiles"), parent_id=parent_id)
                    )
                writer.write_batch(batch)
        if not parent_ids:
            raise PluginError(
                "route-search input contains no parents",
                code="SYNTHESIS_EMPTY_INPUT",
            )
        return parent_ids, canonical

    def _search(
        self,
        canonical: list[str],
        *,
        config: AiZynthFinderConfig,
        executable: Path,
        config_file: Path,
    ) -> list[dict[str, Any]]:
        """Run ``aizynthcli`` once over the whole shortlist."""

        lanes = config.nproc or 1
        timeout = math.ceil(len(canonical) / lanes) * config.timeout_per_molecule_seconds
        with tempfile.TemporaryDirectory(
            prefix="molcascade-aizynth-",
            dir=config.scratch_dir,
        ) as scratch_name:
            scratch = Path(scratch_name)
            smiles_file = scratch / "targets.smi"
            output_file = scratch / "results.json"
            smiles_file.write_text("\n".join(canonical) + "\n", encoding="utf-8")

            command = [
                str(executable),
                "--config",
                str(config_file),
                "--smiles",
                str(smiles_file),
                "--output",
                str(output_file),
            ]
            for flag, names in (
                ("--policy", config.policy),
                ("--filter", config.filter),
                ("--stocks", config.stocks),
            ):
                if names:
                    command.append(flag)
                    command.extend(names)
            if config.nproc is not None:
                command.extend(["--nproc", str(config.nproc)])

            try:
                # An argv list, never a shell string: nothing the user configures
                # is ever parsed by /bin/sh, and the flag values are validated as
                # bare names so no setting can smuggle in an extra argument.
                completed = subprocess.run(
                    command,
                    cwd=scratch,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=_isolated_environment(),
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise PluginError(
                    "aizynthcli did not finish within the configured budget",
                    code="AIZYNTHFINDER_TIMEOUT",
                    hint=(
                        "Either the search budget in your AiZynthFinder configuration "
                        "is larger than 'timeout_per_molecule_seconds' here, or the "
                        "policy model is loading from cold storage on every worker."
                    ),
                    context={
                        "timeout_seconds": timeout,
                        "molecules": len(canonical),
                        "nproc": config.nproc,
                    },
                ) from error
            except OSError as error:
                raise PluginError(
                    "aizynthcli could not be started",
                    code="AIZYNTHFINDER_RUN_FAILED",
                    context={"executable": str(executable), "error_type": type(error).__name__},
                ) from error

            if completed.returncode != 0:
                log = completed.stdout.decode("utf-8", errors="replace")
                raise PluginError(
                    f"aizynthcli exited with status {completed.returncode}",
                    code="AIZYNTHFINDER_RUN_FAILED",
                    hint=(
                        "The message below comes from AiZynthFinder, not MolCascade. "
                        "A missing policy or stock file named in the configuration is "
                        "the usual cause; MolCascade does not fetch either."
                    ),
                    context={
                        "returncode": completed.returncode,
                        "output_tail": log[-_MAX_LOG_TAIL:],
                    },
                )
            return _read_output(output_file)

    def _write_scores(
        self,
        destination: Path,
        *,
        parent_ids: list[Any],
        canonical: list[str],
        records: list[dict[str, Any]],
        config: AiZynthFinderConfig,
        method_id: str,
    ) -> int:
        """Join the search results back onto the parents and write them out."""

        if len(records) != len(parent_ids):
            raise PluginError(
                "aizynthcli returned a different number of results than targets",
                code="AIZYNTHFINDER_OUTPUT_MISALIGNED",
                context={"targets": len(parent_ids), "results": len(records)},
            )

        unpinned: tuple[str, ...] = () if config.backend_version else (_UNPINNED_WARNING,)
        solved_count = 0
        rows: list[dict[str, Any]] = []
        for index, (parent_id, target, record) in enumerate(
            zip(parent_ids, canonical, records, strict=True)
        ):
            returned = _returned_canonical_smiles(record.get("target"))
            if returned != target:
                raise PluginError(
                    "aizynthcli returned a result for a molecule that was not asked about",
                    code="AIZYNTHFINDER_OUTPUT_MISALIGNED",
                    hint=(
                        "Results are joined back to parents by position, which every "
                        "released aizynthcli preserves. A mismatch means the join is "
                        "no longer safe, so the step counts are not attributed rather "
                        "than attributed to the wrong molecules."
                    ),
                    context={
                        "row": index,
                        "parent_id": str(parent_id),
                        "expected_smiles": target,
                        "returned_smiles": str(record.get("target")),
                    },
                )
            solved = bool(record.get("is_solved"))
            if solved:
                steps = record.get("number_of_steps")
                if not isinstance(steps, int | float) or isinstance(steps, bool):
                    raise PluginError(
                        "aizynthcli reported a solved target without a step count",
                        code="AIZYNTHFINDER_OUTPUT_UNREADABLE",
                        context={"parent_id": str(parent_id), "number_of_steps": str(steps)},
                    )
                score = float(steps)
                if not math.isfinite(score) or score < 0.0:
                    raise PluginError(
                        "aizynthcli reported a step count that is not a count",
                        code="AIZYNTHFINDER_OUTPUT_UNREADABLE",
                        context={"parent_id": str(parent_id), "number_of_steps": str(steps)},
                    )
                solved_count += 1
                warnings = _SOLVED_WARNINGS + unpinned
                domain = _DOMAIN_SOLVED
            else:
                score = config.unsolved_score
                warnings = _UNSOLVED_WARNINGS + unpinned
                domain = _DOMAIN_UNSOLVED
            rows.append(
                {
                    "parent_id": parent_id,
                    "method_id": method_id,
                    "score": score,
                    "direction": "HIGHER_HARDER",
                    "domain": domain,
                    "warning_codes_json": canonical_json(list(warnings)),
                }
            )

        with pq.ParquetWriter(destination, SYNTHESIS_SCORE_V1.schema, compression="zstd") as writer:
            for start in range(0, len(rows), config.batch_size):
                writer.write_table(
                    pa.Table.from_pylist(
                        rows[start : start + config.batch_size],
                        schema=SYNTHESIS_SCORE_V1.schema,
                    )
                )
        return solved_count


__all__ = ["AiZynthFinderConfig", "AiZynthFinderRoutePlugin"]
