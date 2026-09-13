"""ADMET endpoints from OpenADMET's released models, in OpenADMET's own environment.

This is the second opinion in the cascade's ADMET tier, and it exists because
the first one is a single model.  ADMET-AI's hERG head reports an AUROC of 0.84
on its own held-out set, which is good enough to inform a decision and not good
enough to make one alone; a tier that deletes on one such number deletes on its
errors too.  So the default cascade runs this beside it and joins the two with
``any`` -- a molecule survives unless *both* models object -- and the whole
point of the arrangement is that the two objections rest on different evidence.

They genuinely do.  ADMET-AI predicts the TDC binary label, so its number is a
probability that the molecule blocks hERG.  The released model this adapter is
built around predicts pIC50 against hERG, regressed on ChEMBL, so its number is
a potency.  Neither one is a rescaling of the other, and they are recorded as
two endpoints -- ``prediction/v1`` exists to keep exactly this from being
collapsed into one column.

*Nothing here deserializes a checkpoint.*  That is the reason this is a CLI
adapter rather than an in-process one, and it is the reason it carries no trust
flag while ``prediction.admet_ai_v2`` and ``prediction.chemprop_checkpoint``
both do.  A Torch checkpoint is executable, and the two in-process predictors
therefore have to pin digests and ask the operator to accept that loading them
runs code.  This one hands a directory to another program in another
environment and reads a CSV back, so the code that runs is OpenADMET's, in
OpenADMET's interpreter, on a machine where somebody installed it deliberately.
What that does *not* buy is provenance: a swapped model directory would still
change every number.  So the directory is digested into ``model_id``, which
makes a swap a different measurement rather than a silent one.

*It cannot share this interpreter either*, and again the conflict is Torch
rather than Python.  OpenADMET's own environment pins its Lightning and brings
a conda-channel Torch; resolving it into this one would re-pin the Torch that
ADMET-AI predicts with in-process, which is the same trap Boltz-2 is kept out
of the way of.  So it lives behind its own ``openadmet`` executable and this
adapter is the process boundary.

*The uncertainty column is real only for an ensemble.*  Upstream's
``--model-dir`` may be given more than once, and the standard deviation it
writes is the spread across whatever was given; with one directory the column
is there but empty.  ``prediction_std`` is nullable and that empty column
becomes null, which is honest.  ``prediction_mean`` is not nullable, and a
molecule the run produced no finite prediction for therefore gets no row at
all rather than a sentinel -- the same decision, for the same reason, as in the
Boltz-2 adapter next door.  A fail-closed numeric gate turns absent evidence
into a rejection, which is the correct handling of "we do not know".

*Its accelerator is the one this stage was granted.*  Upstream defaults to
``gpu`` and also accepts Lightning's ``auto``, both of which pick a card
without reference to what the runner assigned.  So ``auto`` here means "follow
``StageContext.resources``", and the chosen card is fenced off with
``CUDA_VISIBLE_DEVICES`` because the CLI has no way to name a device ordinal.

One caveat belongs in the open rather than in a release note: the hERG model
OpenADMET has published as a baseline is trained on its full dataset with no
held-out split, and publishes no accuracy figure of any kind.  Its own model
card says to proceed with caution.  That is precisely why the default tier
joins it with ``any`` instead of letting it reject on its own, and why the
threshold on it is set from a measured distribution rather than from a metric
nobody has reported.
"""

from __future__ import annotations

import csv
import hashlib
import math
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Literal

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import iter_contract_batches, require_single_input
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import PARENT_V1, PREDICTION_V1
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
_PREDICTION_PATH = Path("datasets/predictions/part-00000.parquet")

_IMPLEMENTATION_VERSION = 1

#: Same shape and same reason as in the ADMET-AI adapter: an endpoint id travels
#: into an artifact and into a gate, so it has to survive a filename and a URL.
_ENDPOINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")

#: The column this adapter adds to the input CSV so the output can be joined on
#: identity rather than on position.  Prefixed because it travels through
#: someone else's dataframe, where a bare ``parent_id`` could collide.
_PARENT_ID_COLUMN = "molcascade_parent_id"

#: How much of a failed run's console output travels into the error context.
_MAX_LOG_TAIL = 4000

#: Read in blocks so that digesting a checkpoint does not hold two copies.
_READ_CHUNK = 4 * 1024 * 1024

#: A ceiling on what will be hashed for ``model_id``.  A released chemprop
#: model is tens of megabytes; this is generous enough that a real ensemble
#: passes and tight enough that a field pointed at a data lake fails while
#: reading rather than after an hour.
_MAX_MODEL_BYTES = 8 * 1024 * 1024 * 1024

#: Loading the model and its featurizer is a fixed cost that dominates a small
#: batch, so the per-molecule budget below is floored rather than trusted at
#: face value on a handful of molecules.
_MIN_TIMEOUT_SECONDS = 600.0

_UNPINNED_WARNING = "OPENADMET_BACKEND_VERSION_UNPINNED"

#: Recorded in the stage metadata when the CLI did not return the id column
#: this adapter wrote, so predictions had to be matched by row order.  Not an
#: error -- the row counts agreed -- but a weaker claim than a join, and one a
#: reader of the artifact is entitled to see.
_POSITIONAL_JOIN_WARNING = "OPENADMET_JOINED_ON_POSITION"


class OpenADMETEndpoint(StrictFrozenModel):
    """Bind one exact output column to a stable endpoint identity.

    ``std_column`` is separate and optional rather than derived from the mean
    column by rewriting its prefix.  Deriving it would be one line and would
    guess: the naming convention belongs to OpenADMET, not to this adapter, and
    a convention that changes would silently start reading a column that is not
    the uncertainty.  Left unset, no uncertainty is recorded, which is the right
    answer for a single-model run where the column is empty anyway.
    """

    output_column: str = Field(min_length=1, max_length=512)
    endpoint_id: str = Field(min_length=1, max_length=256)
    std_column: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("output_column", "std_column")
    @classmethod
    def _column_is_unambiguous(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip() or any(character in value for character in "\r\n\x00"):
            raise ValueError("column names must not carry surrounding or control whitespace")
        return value

    @field_validator("endpoint_id")
    @classmethod
    def _endpoint_is_portable(cls, value: str) -> str:
        if not _ENDPOINT_ID_RE.fullmatch(value):
            raise ValueError("endpoint_id contains unsupported characters")
        return value


class OpenADMETConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)

    #: Which engine's environment overrides apply, giving the two paths below
    #: ``MOLCASCADE_OPENADMET_EXECUTABLE`` and ``MOLCASCADE_OPENADMET_MODEL_DIR``.
    #: See :mod:`molcascade.plugins.builtin._machine_paths`.
    engine_id: ClassVar[str] = "openadmet"

    @classmethod
    def installed_paths(cls) -> tuple[MachinePath, ...]:
        """An executable in an environment of its own, and at least one model.

        The models are the interesting one.  They are not in the distribution:
        each is a separate release on Hugging Face, where the checkpoint is
        LFS-tracked, so a clone whose LFS objects were never pulled is a
        directory of pointer files that looks complete and loads nothing.  So
        the directory is a machine path with ``contents``, and the preflight
        refuses it before the first molecule rather than letting the child fail
        on a 130-byte checkpoint.  ``envs/bootstrap.sh openadmet`` avoids the
        shape entirely by fetching the files over a pinned revision and
        checking a digest, but a hand clone is the likelier way this directory
        arrives, and that is the one that needs catching.

        What ``--model-dir`` wants is the ``anvil_training`` directory *inside*
        a released clone, which is what the model card's own inference script
        passes -- not the clone's root.  The candidates say so, so that a host
        which ran the bootstrap step gets the right level without knowing this.

        A clone is a checkout rather than an installation, so it lands in
        :func:`backend_root` beside KarmaDock's, and surviving ``conda env
        remove`` is the point: re-creating the environment after an upstream
        change should not re-download the weights.  The environment's own
        prefix is offered second for a host that put them there anyway.
        """

        return (
            MachinePath(
                field="executable",
                label="the openadmet CLI inside OpenADMET's own environment",
                candidates=conda_environment("openadmet", "bin/openadmet"),
                remedy="bash envs/bootstrap.sh openadmet",
            ),
            MachinePath(
                field="model_dir",
                label="a released OpenADMET model directory",
                kind="directory",
                candidates=(
                    backend_root()
                    / "openadmet-models"
                    / "herg-chemeleon-baseline"
                    / "anvil_training",
                    *conda_environment(
                        "openadmet", "models/herg-chemeleon-baseline/anvil_training"
                    ),
                ),
                # The checkpoint and the recipe that says how to load it. Both
                # are inside the directory the CLI is given, so a clone that
                # was interrupted or pointed one level too high is caught here
                # rather than by a traceback from somebody else's loader.
                contents=("model.pth", "model.json"),
                remedy="bash envs/bootstrap.sh openadmet",
                note=(
                    "That step downloads the released model over a pinned revision "
                    "and verifies the checkpoint's sha256, so it needs no git-lfs. Give "
                    "this field once per ensemble member: with a single directory "
                    "OpenADMET writes no uncertainty."
                ),
            ),
        )

    #: Absolute path to ``openadmet`` inside the environment created for it. Not
    #: a bare name: that environment is deliberately not on this process's PATH.
    executable: str = Field(default="", max_length=4096)

    #: One directory per model.  Repeatable because that is how upstream forms
    #: an ensemble and therefore the only way it produces a standard deviation;
    #: a single entry predicts perfectly well and leaves that column empty.
    model_dir: tuple[str, ...] = Field(default=(), max_length=32)

    #: The column the CLI reads SMILES from.  Upstream's own default, written
    #: here as well as passed as ``--input-col`` so that the command records
    #: what it read rather than relying on a default that could move.
    input_column: str = Field(default="OPENADMET_SMILES", min_length=1, max_length=256)

    #: ``auto`` means the device this stage was granted, not Lightning's own
    #: ``auto``.  See the module docstring: a child that picks its own card can
    #: pick one another lane is already using.
    accelerator: Literal["auto", "cpu", "gpu"] = "auto"

    #: Which output columns become which endpoints.  No default: a model
    #: directory decides what the columns are called, and guessing them is how
    #: a run ends up gating on a column that happens to exist.
    endpoints: tuple[OpenADMETEndpoint, ...] = Field(min_length=1, max_length=64)

    #: Wall-clock budget per molecule, multiplied by the batch and floored at
    #: :data:`_MIN_TIMEOUT_SECONDS`.  A deadlock guard around one child, not a
    #: per-molecule limit the CLI would honour.
    timeout_per_molecule_seconds: float = Field(default=1.0, gt=0.0, le=3_600.0)

    #: The OpenADMET release, as the operator knows it to be.  Optional and
    #: hashed into ``model_id`` when given; when it is not, the stage reports
    #: ``OPENADMET_BACKEND_VERSION_UNPINNED`` so the gap is visible rather than
    #: implied.  ``openadmet --version`` is not consulted: a version read at run
    #: time describes the machine, not the prediction that was archived.
    backend_version: str | None = Field(default=None, min_length=1, max_length=64)

    #: Where the input CSV and the prediction CSV are written.
    scratch_dir: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="before")
    @classmethod
    def _fill_machine_paths(cls, data: Any) -> Any:
        return fill_machine_paths(data, engine_id=cls.engine_id, paths=cls.installed_paths())

    @field_validator("endpoints", mode="before")
    @classmethod
    def _arrays_to_tuples(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("model_dir", mode="before")
    @classmethod
    def _one_directory_is_a_list_of_one(cls, value: Any) -> Any:
        """Accept a bare string, and a JSON array, where a tuple is declared.

        The array is the same coercion every sequence field in these configs
        needs, because strict mode does not read a list as a tuple and a
        ``cascade.json`` has only lists.  The bare string is this field's own:
        discovery and ``MOLCASCADE_OPENADMET_MODEL_DIR`` can only ever answer
        with one path, because one path is what a layout on disk is, and
        wrapping it here keeps that machinery type-agnostic instead of teaching
        it which fields happen to be repeatable.
        """

        if isinstance(value, str):
            return (value,) if value.strip() else ()
        return tuple(value) if isinstance(value, list) else value

    @field_validator("executable")
    @classmethod
    def _executable_is_absolute(cls, value: str) -> str:
        return engine_path(value, field="executable", engine_id=cls.engine_id)

    @field_validator("model_dir")
    @classmethod
    def _models_are_absolute(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        resolved = tuple(
            engine_path(entry, field="model_dir", engine_id=cls.engine_id)
            for entry in value
            if entry.strip()
        )
        if len(set(resolved)) != len(resolved):
            raise ValueError(
                "'model_dir' names the same directory twice; an ensemble of one model "
                "repeated is one model with a standard deviation of zero"
            )
        return resolved

    @field_validator("endpoints")
    @classmethod
    def _endpoints_are_distinct(
        cls, value: tuple[OpenADMETEndpoint, ...]
    ) -> tuple[OpenADMETEndpoint, ...]:
        columns = [endpoint.output_column for endpoint in value]
        if len(columns) != len(set(columns)):
            raise ValueError("output_column values must be unique")
        endpoint_ids = [endpoint.endpoint_id for endpoint in value]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("endpoint_id values must be unique")
        return value

    @field_validator("scratch_dir")
    @classmethod
    def _scratch_is_absolute(cls, value: str | None) -> str | None:
        return None if value is None else absolute_path(value, field="scratch_dir")


def _validated_config(request: StageRequest) -> OpenADMETConfig:
    try:
        return OpenADMETConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid OpenADMET configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            hint=(
                "'executable' is the absolute path to the openadmet CLI inside the "
                "environment you created for it, 'model_dir' is a released model "
                "directory -- give it once per ensemble member -- and 'endpoints' "
                "binds that model's output columns to endpoint ids. Run "
                "'bash envs/bootstrap.sh openadmet' to create the environment and "
                "fetch the baseline model."
            ),
            context={"error_count": error.error_count()},
        ) from error


def _prepare_outputs(context: StageContext) -> tuple[Path, Path]:
    context.staging_root.mkdir(parents=True, exist_ok=True)
    relatives = (_PARENT_PATH, _PREDICTION_PATH)
    destinations = tuple(context.staging_root / relative for relative in relatives)
    for relative, destination in zip(relatives, destinations, strict=True):
        if destination.exists() or destination.is_symlink():
            raise PluginError(
                f"OpenADMET output already exists: {relative.as_posix()}",
                code="PLUGIN_STAGING_NOT_EMPTY",
                context={"path": relative.as_posix()},
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
    return destinations[0], destinations[1]


def _resolved_executable(configured: str) -> Path:
    """Check the interpreter-side facts about ``openadmet`` before using it.

    ``shutil.which`` is deliberately not consulted, for the reason given in the
    module docstring: the environment this adapter calls into must not be on
    this process's PATH, because keeping its Torch away from this one's is why
    it exists elsewhere.
    """

    path = Path(configured)
    try:
        info = path.stat()
    except OSError as error:
        raise PluginError(
            "the openadmet CLI was not found at the configured path",
            code="OPENADMET_EXECUTABLE_MISSING",
            hint=(
                "Create the environment once, outside MolCascade: "
                "'bash envs/bootstrap.sh openadmet'. Then point this stage at the "
                "absolute path 'conda run -n openadmet which openadmet' prints. "
                "MolCascade never installs anything itself."
            ),
            context={"executable": configured, "error_type": type(error).__name__},
        ) from error
    if not stat.S_ISREG(info.st_mode):
        raise PluginError(
            "the configured openadmet path is not a regular file",
            code="OPENADMET_EXECUTABLE_MISSING",
            context={"executable": configured},
        )
    if not os.access(path, os.X_OK):
        raise PluginError(
            "the configured openadmet path is not executable by this user",
            code="OPENADMET_EXECUTABLE_MISSING",
            context={"executable": configured},
        )
    return path


def _resolved_models(configured: tuple[str, ...]) -> tuple[Path, ...]:
    """Refuse an absent or unpulled model here rather than inside the child.

    A Hugging Face clone without its LFS objects is the failure worth naming:
    every file is present, the checkpoint is a 130-byte text pointer, and the
    error it eventually produces is about a corrupt archive rather than about a
    download that never happened.
    """

    if not configured:
        raise PluginError(
            "no OpenADMET model directory is configured",
            code="OPENADMET_MODEL_MISSING",
            hint=(
                "Set 'model_dir' to a released model directory, or export "
                "MOLCASCADE_OPENADMET_MODEL_DIR once for this machine. "
                "'bash envs/bootstrap.sh openadmet' fetches the hERG baseline."
            ),
        )
    resolved: list[Path] = []
    for entry in configured:
        path = Path(entry)
        if not path.is_dir():
            raise PluginError(
                "a configured OpenADMET model directory does not exist",
                code="OPENADMET_MODEL_MISSING",
                hint=(
                    "Run 'bash envs/bootstrap.sh openadmet', which downloads the "
                    "released model over a pinned revision and verifies its checkpoint "
                    "against a recorded sha256."
                ),
                context={"model_dir": entry},
            )
        pointers = _unpulled_pointers(path)
        if pointers:
            raise PluginError(
                "a configured OpenADMET model directory holds git-lfs pointers "
                "rather than the files they stand for",
                code="OPENADMET_MODEL_NOT_PULLED",
                hint=(
                    "The clone is there but its large files are not. Run "
                    "'git lfs install && git lfs pull' inside it, or use "
                    "'bash envs/bootstrap.sh openadmet' instead, which fetches the "
                    "files directly over a pinned revision -- no git-lfs -- and "
                    "verifies the checkpoint's digest."
                ),
                context={"model_dir": entry, "pointers": pointers},
            )
        resolved.append(path)
    return tuple(resolved)


def _unpulled_pointers(model_dir: Path) -> list[JsonValue]:
    """Name the files that are git-lfs stand-ins instead of the real thing.

    The released hERG checkpoint is 49 MB and LFS-tracked, so a plain ``git
    clone`` without the filter leaves a 130-byte text file of exactly the right
    name in exactly the right place.  Existence cannot tell the difference, and
    the error it eventually produces comes from a deserializer complaining
    about a corrupt archive -- which sends the reader after the wrong problem.
    A pointer announces itself on its first line, so this is one cheap read per
    small file and nothing at all for a real one.
    """

    found: list[JsonValue] = []
    for path in sorted(model_dir.rglob("*")):
        if ".git" in path.parts or not path.is_file():
            continue
        try:
            if path.stat().st_size > 1024:
                continue
            with path.open("rb") as handle:
                head = handle.read(42)
        except OSError:
            # Unreadable is a different fault, reported where the tree is read.
            continue
        if head == b"version https://git-lfs.github.com/spec/v1":
            found.append(str(path.relative_to(model_dir)))
    return found


def _model_digest(models: tuple[Path, ...]) -> str:
    """Digest every byte of every model directory, in a stable order.

    This is what makes ``model_id`` a claim about the model rather than about
    the path it was read from.  Nothing here interprets the files: a manifest of
    relative path, size and content hash is enough to make a swapped checkpoint
    a different measurement, and interpreting someone else's serialization
    format is exactly the work this adapter avoids doing in-process.
    """

    manifest: list[dict[str, JsonValue]] = []
    total = 0
    for index, root in enumerate(models):
        try:
            files = sorted(item for item in root.rglob("*") if item.is_file())
        except OSError as error:
            raise PluginError(
                "an OpenADMET model directory could not be read",
                code="OPENADMET_MODEL_UNREADABLE",
                context={"model_dir": str(root), "error_type": type(error).__name__},
            ) from error
        for item in files:
            # A .git directory is the transport, not the model: its pack files
            # are large, and they change when a clone is refetched without the
            # checkpoint changing at all.
            if ".git" in item.relative_to(root).parts:
                continue
            digest = hashlib.sha256()
            size = 0
            try:
                with item.open("rb") as handle:
                    while chunk := handle.read(_READ_CHUNK):
                        digest.update(chunk)
                        size += len(chunk)
                        total += len(chunk)
                        if total > _MAX_MODEL_BYTES:
                            raise PluginError(
                                "the configured OpenADMET models are larger than this "
                                "stage will digest",
                                code="OPENADMET_MODEL_TOO_LARGE",
                                hint=(
                                    "'model_dir' wants a released model directory, not a "
                                    "tree that contains one. The identity of a prediction "
                                    "includes the bytes that produced it, so this is "
                                    "hashed before the run starts."
                                ),
                                context={
                                    "model_dir": str(root),
                                    "limit_bytes": _MAX_MODEL_BYTES,
                                },
                            )
            except OSError as error:
                raise PluginError(
                    "an OpenADMET model file could not be read",
                    code="OPENADMET_MODEL_UNREADABLE",
                    context={"path": str(item), "error_type": type(error).__name__},
                ) from error
            manifest.append(
                {
                    "member": index,
                    "path": item.relative_to(root).as_posix(),
                    "size": size,
                    "sha256": digest.hexdigest(),
                }
            )
    if not manifest:
        raise PluginError(
            "the configured OpenADMET model directories hold no files",
            code="OPENADMET_MODEL_MISSING",
            context={"model_dirs": [str(root) for root in models]},
        )
    return canonical_sha256({"models": manifest})


def _isolated_environment(device: str | None) -> dict[str, str]:
    """This process's environment minus the parts that would leak into theirs.

    ``PYTHONPATH`` and ``PYTHONHOME`` are how one environment's site-packages
    ends up in front of another's, and keeping OpenADMET's Torch away from this
    one's is the entire reason it runs out of process.

    ``CUDA_VISIBLE_DEVICES`` is set rather than passed as a flag: the CLI takes
    an accelerator *kind*, not a device ordinal, so leaving one card visible is
    the only way to say which one.
    """

    environment = dict(os.environ)
    for leaked in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        environment.pop(leaked, None)
    if device and device.startswith("cuda:"):
        environment["CUDA_VISIBLE_DEVICES"] = device.split(":", 1)[1]
    return environment


def _selected_accelerator(configured: str, device: str) -> str:
    """Turn the granted device into the word the CLI understands.

    An explicit ``cpu`` or ``gpu`` is honoured as written, because an operator
    who names one is making a statement about the run rather than asking for a
    guess.  ``auto`` follows the lane the runner assigned.
    """

    if configured != "auto":
        return configured
    return "gpu" if device.startswith("cuda:") else "cpu"


def _parseable(smiles: str) -> bool:
    """Can RDKit read this SMILES at all?

    Asked here so that one unreadable row costs one row rather than the whole
    child: the CLI is handed a single CSV, and a molecule its featurizer cannot
    build is the kind of thing that ends a batch instead of skipping a line.
    """

    from rdkit import Chem

    return Chem.MolFromSmiles(smiles) is not None


class OpenADMETPredictorPlugin:
    """Predict named ADMET endpoints with released OpenADMET models, out of process."""

    descriptor = PluginDescriptor(
        id="prediction.openadmet",
        version="0.1.0",
        kind=PluginKind.PREDICTOR,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, PREDICTION_V1.id),
        output_ports={"primary": PARENT_V1.id, "predictions": PREDICTION_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        # A Torch forward pass in someone else's build: the model bytes are
        # digested and recorded, and bit-for-bit equality across cards is not
        # something this adapter can promise on its behalf.
        determinism=Determinism.BEST_EFFORT,
        display_name="OpenADMET released model",
        description=(
            "Runs a released OpenADMET model through its own CLI in a separate "
            "environment, binding named output columns to endpoint ids. Gives a "
            "standard deviation only when several model directories are supplied, "
            "and deserializes nothing in this process."
        ),
    )
    config_model = OpenADMETConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)

        # Every check that can be made without reading a molecule is made
        # before one is read, so a missing environment or an unpulled
        # checkpoint costs nothing rather than costing a pass over the
        # population.
        executable = _resolved_executable(config.executable)
        models = _resolved_models(config.model_dir)
        model_digest = _model_digest(models)

        parent_destination, prediction_destination = _prepare_outputs(context)
        model_id = "openadmet:sha256:" + canonical_sha256(
            {
                "backend": "openadmet",
                "backend_version": config.backend_version,
                "implementation_version": _IMPLEMENTATION_VERSION,
                "method": "OpenADMET released model, predicted through the openadmet CLI",
                "models_sha256": model_digest,
                "ensemble_size": len(models),
                "endpoints": [
                    {
                        "output_column": endpoint.output_column,
                        "endpoint_id": endpoint.endpoint_id,
                        "std_column": endpoint.std_column,
                    }
                    for endpoint in config.endpoints
                ],
            }
        )

        try:
            accepted, unparseable = self._write_parents(
                stage_input,
                parent_destination,
                config=config,
            )
            records, positional = self._predict(
                accepted,
                config=config,
                context=context,
                executable=executable,
                models=models,
            )
            written, missing = self._write_predictions(
                prediction_destination,
                accepted=accepted,
                records=records,
                config=config,
                model_id=model_id,
            )
        except BaseException:
            parent_destination.unlink(missing_ok=True)
            prediction_destination.unlink(missing_ok=True)
            raise

        input_count = len(accepted) + len(unparseable)
        endpoints: list[JsonValue] = [endpoint.endpoint_id for endpoint in config.endpoints]
        unparseable_examples: list[JsonValue] = list(unparseable[:8])
        warnings: list[JsonValue] = [] if config.backend_version else [_UNPINNED_WARNING]
        if positional:
            warnings.append(_POSITIONAL_JOIN_WARNING)
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    (_PARENT_PATH.as_posix(),),
                    {"row_count": input_count},
                ),
                "predictions": PendingOutput(
                    PREDICTION_V1.id,
                    (_PREDICTION_PATH.as_posix(),),
                    {"row_count": written, "model_id": model_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": input_count,
                "model_id": model_id,
                "endpoints": endpoints,
                "calibrated": False,
                "predicted_count": len(records),
                "prediction_row_count": written,
                "ensemble_size": len(models),
                "models_sha256": model_digest,
                # Counted rather than recorded as rows: prediction/v1 cannot
                # hold "unknown", so these molecules carry no evidence and a
                # fail-closed gate is what turns that into a rejection.
                "skipped_unparseable_count": len(unparseable),
                "skipped_unparseable_examples": unparseable_examples,
                "missing_prediction_count": missing,
                "accelerator": config.accelerator,
                "backend": "openadmet",
                "backend_version": config.backend_version,
                "warnings": warnings,
            },
        )

    def _write_parents(
        self,
        stage_input: Any,
        destination: Path,
        *,
        config: OpenADMETConfig,
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Pass the parents through and decide which of them can be predicted."""

        accepted: list[tuple[str, str]] = []
        unparseable: list[str] = []
        seen = 0
        with pq.ParquetWriter(destination, PARENT_V1.schema, compression="zstd") as writer:
            for batch in iter_contract_batches(
                stage_input,
                PARENT_V1,
                batch_size=config.batch_size,
            ):
                for row in batch.select(["parent_id", "parent_smiles"]).to_pylist():
                    seen += 1
                    parent_id = str(row.get("parent_id"))
                    smiles = row.get("parent_smiles")
                    if isinstance(smiles, str) and smiles and _parseable(smiles):
                        accepted.append((parent_id, smiles))
                    else:
                        unparseable.append(parent_id)
                writer.write_batch(batch)
        if not seen:
            raise PluginError(
                "OpenADMET input contains no parents",
                code="OPENADMET_EMPTY_INPUT",
            )
        if not accepted:
            raise PluginError(
                "no molecule in this population could be read as a SMILES",
                code="OPENADMET_NOTHING_TO_PREDICT",
                context={"unparseable": len(unparseable)},
            )
        return accepted, unparseable

    def _predict(
        self,
        accepted: list[tuple[str, str]],
        *,
        config: OpenADMETConfig,
        context: StageContext,
        executable: Path,
        models: tuple[Path, ...],
    ) -> tuple[dict[str, dict[str, float | None]], bool]:
        """Write one CSV, run ``openadmet predict`` over it, and read the answer back.

        One invocation rather than one per molecule: the model and its
        featurizer load once, which is the dominant cost, and the CLI's own
        input is a table.
        """

        devices = context.resources.devices or ("cpu",)
        device = devices[0]
        timeout = max(
            _MIN_TIMEOUT_SECONDS,
            len(accepted) * config.timeout_per_molecule_seconds,
        )
        with tempfile.TemporaryDirectory(
            prefix="molcascade-openadmet-",
            dir=config.scratch_dir,
        ) as scratch_name:
            scratch = Path(scratch_name)
            input_csv = scratch / "molecules.csv"
            output_csv = scratch / "predictions.csv"

            # Written with the csv module rather than by formatting: a SMILES is
            # full of characters a comma-separated line cares about, and quoting
            # them by hand works until a molecule with a comma in a bracket
            # arrives.
            with input_csv.open("w", encoding="utf-8", newline="") as handle:
                columns = [_PARENT_ID_COLUMN, config.input_column]
                stream = csv.DictWriter(handle, fieldnames=columns)
                stream.writeheader()
                for parent_id, smiles in accepted:
                    stream.writerow({_PARENT_ID_COLUMN: parent_id, config.input_column: smiles})

            command = [
                str(executable),
                "predict",
                "--input-path",
                str(input_csv),
                "--input-col",
                config.input_column,
                "--output-csv",
                str(output_csv),
                "--accelerator",
                _selected_accelerator(config.accelerator, device),
            ]
            for model in models:
                command += ["--model-dir", str(model)]

            try:
                # An argv list, never a shell string: nothing an operator
                # configures is parsed by /bin/sh, and every path in it was
                # validated as absolute before we got here.
                completed = subprocess.run(
                    command,
                    cwd=scratch,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=_isolated_environment(device),
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise PluginError(
                    "openadmet did not finish within the configured budget",
                    code="OPENADMET_TIMEOUT",
                    hint=(
                        "A forward pass is milliseconds per molecule, so a timeout here "
                        "is usually a model that is still loading or a CPU run of a "
                        "population sized for a card. Raise "
                        "'timeout_per_molecule_seconds' or give this stage a GPU."
                    ),
                    context={
                        "timeout_seconds": timeout,
                        "molecules": len(accepted),
                        "device": device,
                    },
                ) from error
            except OSError as error:
                raise PluginError(
                    "openadmet could not be started",
                    code="OPENADMET_RUN_FAILED",
                    context={"executable": str(executable), "error_type": type(error).__name__},
                ) from error

            log = completed.stdout.decode("utf-8", errors="replace")
            if completed.returncode != 0:
                raise PluginError(
                    f"openadmet exited with status {completed.returncode}",
                    code="OPENADMET_RUN_FAILED",
                    hint=(
                        "The message below comes from OpenADMET, not MolCascade. A clone "
                        "whose git-lfs objects were never pulled and a GPU without "
                        "enough memory are the usual causes."
                    ),
                    context={
                        "returncode": completed.returncode,
                        "output_tail": log[-_MAX_LOG_TAIL:],
                    },
                )
            return self._collect(
                output_csv,
                accepted=accepted,
                config=config,
                log=log,
            )

    def _collect(
        self,
        output_csv: Path,
        *,
        accepted: list[tuple[str, str]],
        config: OpenADMETConfig,
        log: str,
    ) -> tuple[dict[str, dict[str, float | None]], bool]:
        """Key the predicted columns by parent, joining on identity where possible.

        The id column this adapter wrote is the join key, and it is the join
        that is wanted: a future release that deduplicated or reordered its
        output would break a positional match silently.  It is not guaranteed to
        survive somebody else's dataframe, though, so position is accepted as a
        fallback -- and only when the row counts agree exactly, with the weaker
        claim recorded in the stage metadata rather than smoothed over.
        """

        try:
            with output_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except FileNotFoundError as error:
            raise PluginError(
                "openadmet produced no prediction file",
                code="OPENADMET_OUTPUT_MISSING",
                hint=(
                    "The run exited cleanly, so this is an input or a layout problem "
                    "rather than a crash. The console tail below is OpenADMET's own."
                ),
                context={"expected": len(accepted), "output_tail": log[-_MAX_LOG_TAIL:]},
            ) from error
        except (OSError, UnicodeDecodeError, csv.Error) as error:
            raise PluginError(
                "the OpenADMET prediction file could not be read",
                code="OPENADMET_OUTPUT_UNREADABLE",
                context={"path": output_csv.name, "error_type": type(error).__name__},
            ) from error

        if not rows:
            raise PluginError(
                "the OpenADMET prediction file has no rows",
                code="OPENADMET_OUTPUT_MISSING",
                context={"expected": len(accepted), "output_tail": log[-_MAX_LOG_TAIL:]},
            )

        wanted: set[str] = set()
        for endpoint in config.endpoints:
            wanted.add(endpoint.output_column)
            if endpoint.std_column:
                wanted.add(endpoint.std_column)
        present = set(rows[0].keys())
        absent: list[JsonValue] = [column for column in sorted(wanted) if column not in present]
        if absent:
            offered: list[JsonValue] = [str(column) for column in sorted(present)[:24]]
            raise PluginError(
                "the OpenADMET prediction file does not carry every configured column",
                code="OPENADMET_OUTPUT_COLUMN_MISSING",
                hint=(
                    "Column names belong to the released model. Run the CLI once by "
                    "hand on two molecules and copy the header into 'endpoints'."
                ),
                context={"missing": absent, "columns": offered},
            )

        positional = _PARENT_ID_COLUMN not in present
        if positional and len(rows) != len(accepted):
            raise PluginError(
                "openadmet returned a different number of rows than it was given, and "
                "no column to join them on",
                code="OPENADMET_OUTPUT_COUNT_MISMATCH",
                hint=(
                    f"This adapter writes a '{_PARENT_ID_COLUMN}' column so predictions "
                    "can be matched by identity. It did not come back, so row order was "
                    "the only remaining key -- and the counts disagree, so it is not a "
                    "safe one."
                ),
                context={"input_count": len(accepted), "output_count": len(rows)},
            )

        collected: dict[str, dict[str, float | None]] = {}
        for index, row in enumerate(rows):
            if positional:
                parent_id = accepted[index][0]
            else:
                parent_id = str(row.get(_PARENT_ID_COLUMN) or "")
                if not parent_id:
                    continue
            values: dict[str, float | None] = {}
            for column in sorted(wanted):
                values[column] = _finite(row.get(column))
            collected[parent_id] = values
        return collected, positional

    def _write_predictions(
        self,
        destination: Path,
        *,
        accepted: list[tuple[str, str]],
        records: dict[str, dict[str, float | None]],
        config: OpenADMETConfig,
        model_id: str,
    ) -> tuple[int, int]:
        """One row per endpoint per molecule that got a finite number, and none otherwise.

        A molecule the model returned a blank or a NaN for gets no row, for the
        reason the module docstring gives: ``prediction_mean`` is non-nullable,
        so the only way to record "no answer" would be to invent one, and an
        invented number ranks rather than rejects.  An empty *uncertainty* is
        different -- ``prediction_std`` is nullable, so a single-model run
        writes a mean and a null beside it.
        """

        parent_ids: list[str] = []
        endpoint_ids: list[str] = []
        model_ids: list[str] = []
        means: list[float] = []
        stds: list[float | None] = []
        missing = 0
        for parent_id, _smiles in accepted:
            values = records.get(parent_id)
            if values is None:
                missing += 1
                continue
            wrote = False
            for endpoint in config.endpoints:
                mean = values.get(endpoint.output_column)
                if mean is None:
                    continue
                parent_ids.append(parent_id)
                endpoint_ids.append(endpoint.endpoint_id)
                model_ids.append(model_id)
                means.append(mean)
                stds.append(values.get(endpoint.std_column) if endpoint.std_column else None)
                wrote = True
            if not wrote:
                missing += 1

        table = pa.table(
            {
                "parent_id": pa.array(parent_ids, pa.string()),
                "endpoint_id": pa.array(endpoint_ids, pa.string()),
                "model_id": pa.array(model_ids, pa.string()),
                "prediction_mean": pa.array(means, pa.float64()),
                "prediction_std": pa.array(stds, pa.float64()),
                "interval_lower": pa.array([None] * len(means), pa.float64()),
                "interval_upper": pa.array([None] * len(means), pa.float64()),
                "calibration_id": pa.array([None] * len(means), pa.string()),
            },
            schema=PREDICTION_V1.schema,
        )
        PREDICTION_V1.validate(table)
        with pq.ParquetWriter(destination, PREDICTION_V1.schema, compression="zstd") as writer:
            writer.write_table(table)
        return int(table.num_rows), missing


def _finite(value: object) -> float | None:
    """Read one CSV cell as a number, or decide it is not one.

    Blank, ``NA`` and ``NaN`` all mean the same thing here and all become
    ``None``: OpenADMET writes an empty standard deviation for a single model,
    and chemprop writes a NaN for a molecule its featurizer could not build.
    Neither is an error in a batch of thousands, and neither is a number.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None
