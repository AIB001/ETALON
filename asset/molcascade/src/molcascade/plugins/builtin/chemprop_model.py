"""Score molecules with a locally provisioned Chemprop 2.x checkpoint.

This is the second way to bring your own trained model into a cascade, and it
exists because the first one cannot express this class of model.  The ONNX
adapter next door computes a fixed-width representation with MolCascade's own
featurizer and hands that matrix to a graph that consumes vectors.  A
message-passing network has no such input: it learns over the molecular graph
itself, and the featurization is a trained part of the model rather than a
preprocessing step someone chose.  So this bundle has no ``representation``
field -- there is nothing for the user to declare and nothing for MolCascade to
compute.  Chemprop is handed SMILES and returns numbers.

That difference has a price.  A Chemprop checkpoint is a Torch checkpoint, and
:func:`chemprop.models.load_model` calls ``torch.load(..., weights_only=False)``
-- deserializing it runs code from the file.  The ONNX adapter refuses ``.pt``
outright for exactly this reason.  Here it cannot, so the refusal is replaced
with the same explicit trust acknowledgement the ADMET-AI adapter requires:
digests establish which bytes ran, the flag establishes that a person decided to
run them.  Pinning without the flag would quietly imply that a hash makes an
executable file safe.

Nothing here downloads a model, installs a package, or trains anything.  A
checkpoint is something the user trained elsewhere and copied in.
"""

from __future__ import annotations

import math
import re
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.load import load_yaml
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import PARENT_V1, PREDICTION_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.builtin._bundles import BundleCodes, bundle_files, bundle_root
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

MANIFEST_FILENAME = "molcascade_chemprop.yaml"
BUNDLE_SCHEMA_VERSION = 1

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_PREDICTION_PATH = Path("datasets/chemprop_predictions/part-00000.parquet")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ENDPOINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:/-]{0,127}$")

#: Bundle-reading failures reported under this plugin's own error vocabulary.
_CODES = BundleCodes("CHEMPROP")

#: What the parent verified and a shard cannot: which directory holds the
#: checkpoint whose digest was just checked, and the identity derived from it.
#: Popped before the strict config model sees the mapping.
_RUNTIME_KEY = "molcascade.runtime"

#: A message-passing forward pass per molecule, so a shard is minutes of GPU
#: time rather than seconds of arithmetic.  Smaller than the default for the
#: same reason ADMET's is: resume should not cost more than it saves.
_SHARD_ROWS = 20_000


class CheckpointSpec(StrictFrozenModel):
    """The trained network, pinned to exact bytes."""

    file: str = Field(min_length=1, max_length=255)
    sha256: str
    format: Literal["chemprop_pt"] = "chemprop_pt"
    #: How many tasks the saved predictor head emits.  Declared rather than
    #: discovered so a checkpoint swapped for a differently-shaped one fails
    #: before it produces a column of numbers that mean something else.
    n_tasks: int = Field(default=1, ge=1, le=4096)
    #: Which task column carries ``endpoint_id``.  A multitask model trained on
    #: twelve endpoints contributes one of them to a cascade tier; the other
    #: eleven are not silently averaged in.
    task_index: int = Field(default=0, ge=0, le=4095)

    @field_validator("file")
    @classmethod
    def _is_a_plain_child_filename(cls, value: str) -> str:
        path = PurePosixPath(value)
        if len(path.parts) != 1 or value in {".", ".."} or path.is_absolute():
            raise ValueError("file must be a plain file name inside the bundle")
        return value

    @field_validator("sha256")
    @classmethod
    def _hash_is_valid(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must contain 64 lowercase hexadecimal characters")
        return value


class ChempropBundleManifest(StrictFrozenModel):
    """``molcascade_chemprop.yaml`` -- everything needed to reproduce a score."""

    schema_version: int = Field(default=BUNDLE_SCHEMA_VERSION, ge=1, le=1)
    model_name: str = Field(min_length=1, max_length=128)
    endpoint_id: str = Field(min_length=1, max_length=256)
    task: Literal["regression", "binary_probability"]
    model: CheckpointSpec
    #: What the number means, for a regression head.  Free text, and the reason
    #: a reviewer can tell logS from logP six months later.
    units: str | None = Field(default=None, max_length=128)
    # Free text kept out of the identity hash on purpose: describing a model
    # better should not invalidate results produced with it.
    notes: str | None = Field(default=None, max_length=8192)

    @field_validator("model_name")
    @classmethod
    def _name_is_portable(cls, value: str) -> str:
        if not _NAME_RE.fullmatch(value):
            raise ValueError("model_name contains unsupported characters")
        return value

    @field_validator("endpoint_id")
    @classmethod
    def _endpoint_is_portable(cls, value: str) -> str:
        if not _ENDPOINT_ID_RE.fullmatch(value):
            raise ValueError("endpoint_id contains unsupported characters")
        return value

    @model_validator(mode="after")
    def _task_index_exists(self) -> Self:
        if self.model.task_index >= self.model.n_tasks:
            raise ValueError(
                f"task_index {self.model.task_index} does not exist in a model "
                f"declaring n_tasks={self.model.n_tasks}"
            )
        return self

    @property
    def identity_inputs(self) -> dict[str, Any]:
        """Everything that must change the model identity if it changes."""

        return {
            "schema_version": self.schema_version,
            "model_name": self.model_name,
            "endpoint_id": self.endpoint_id,
            "task": self.task,
            "model_sha256": self.model.sha256,
            "model_format": self.model.format,
            "n_tasks": self.model.n_tasks,
            "task_index": self.model.task_index,
        }


class ChempropCheckpointConfig(StrictFrozenModel):
    """Point one criterion at a locally provisioned Chemprop checkpoint."""

    schema_version: int = Field(default=1, ge=1, le=1)
    bundle_dir: str = Field(min_length=1, max_length=4096)
    expected_bundle_sha256: str = Field(
        description="Digest from `molcascade model-bundle <dir>`; binds the run to exact bytes."
    )
    allow_unsafe_model_deserialization: bool = Field(
        default=False,
        description="Explicit trust acknowledgement for the pinned Torch checkpoint.",
    )
    #: Smaller than the ONNX default: each row here is a molecular graph held in
    #: Torch tensors, not a row of a feature matrix.
    batch_size: int = Field(default=256, ge=1, le=65_536)
    num_workers: int = Field(default=0, ge=0, le=64)
    max_bundle_files: int = Field(default=64, ge=1, le=4096)
    max_bundle_bytes: int = Field(
        default=4 * 1024 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024 * 1024
    )

    @field_validator("expected_bundle_sha256")
    @classmethod
    def _hash_is_valid(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("expected_bundle_sha256 must be 64 lowercase hex characters")
        return value


# --------------------------------------------------------------------------
# Bundle inspection
#
# Reading and hashing only.  Neither ``chemprop`` nor ``torch`` is imported
# here, which is the point: the digest a user pastes into a config has to be
# computable *before* deciding whether to run the file it pins.
# --------------------------------------------------------------------------


def _chemprop_version() -> str | None:
    """The installed Chemprop release, read from distribution metadata.

    Metadata, not an import: this must stay safe to call from an inspection
    path, and importing Chemprop costs roughly half a minute besides.
    """

    try:
        return version("chemprop")
    except PackageNotFoundError:
        return None


def inspect_chemprop_bundle(
    bundle_dir: str | Path,
    *,
    maximum_files: int = 64,
    maximum_bytes: int = 4 * 1024 * 1024 * 1024,
) -> dict[str, Any]:
    """Validate a bundle and report the digests to paste into a config."""

    root = bundle_root(str(Path(bundle_dir).expanduser().resolve()), codes=_CODES)
    contents, total_bytes = bundle_files(
        root, maximum_files=maximum_files, maximum_bytes=maximum_bytes, codes=_CODES
    )
    if MANIFEST_FILENAME not in contents:
        raise PluginError(
            f"chemprop bundle has no {MANIFEST_FILENAME}",
            code="CHEMPROP_MANIFEST_MISSING",
            hint=f"Every bundle directory must contain {MANIFEST_FILENAME}.",
            context={"bundle_dir": str(root), "files": sorted(contents)},
        )
    manifest = _parse_manifest(contents[MANIFEST_FILENAME][0])
    _check_checkpoint_file(manifest, contents)
    files = [
        {"name": name, "sha256": digest, "size_bytes": len(payload)}
        for name, (payload, digest) in sorted(contents.items())
    ]
    installed = _chemprop_version()
    return {
        "bundle_dir": str(root),
        "backend": "chemprop",
        "manifest_filename": MANIFEST_FILENAME,
        "model_name": manifest.model_name,
        "endpoint_id": manifest.endpoint_id,
        "task": manifest.task,
        "units": manifest.units,
        "n_tasks": manifest.model.n_tasks,
        "task_index": manifest.model.task_index,
        "file_count": len(files),
        "size_bytes": total_bytes,
        "files": files,
        "bundle_sha256": canonical_sha256(files),
        "chemprop_version": installed,
        # Unlike the ONNX bundle, the featurizer belongs to Chemprop rather than
        # to MolCascade, so the identity cannot be settled without knowing which
        # Chemprop produced the numbers.  Absent package, absent identity --
        # ``bundle_sha256`` is still reported, because that is what a config
        # pins and it is readable from the bytes alone.
        "model_id": None if installed is None else _model_id(manifest, files, installed),
    }


def _parse_manifest(payload: bytes) -> ChempropBundleManifest:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as error:
        raise PluginError(
            f"{MANIFEST_FILENAME} is not valid UTF-8",
            code="CHEMPROP_MANIFEST_INVALID",
        ) from error
    document = load_yaml(text, source=MANIFEST_FILENAME)
    try:
        return ChempropBundleManifest.model_validate(document)
    except ValidationError as error:
        raise PluginError(
            f"invalid {MANIFEST_FILENAME}: {error}",
            code="CHEMPROP_MANIFEST_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _check_checkpoint_file(
    manifest: ChempropBundleManifest,
    contents: dict[str, tuple[bytes, str]],
) -> None:
    name = manifest.model.file
    if name not in contents:
        raise PluginError(
            "checkpoint named in the manifest is not in the bundle",
            code="CHEMPROP_FILE_MISSING",
            context={"file": name, "files": sorted(contents)},
        )
    payload, digest = contents[name]
    if digest != manifest.model.sha256:
        raise PluginError(
            "checkpoint does not match the sha256 pinned in the manifest",
            code="CHEMPROP_FILE_HASH_MISMATCH",
            context={"file": name, "expected": manifest.model.sha256, "actual": digest},
        )
    if not payload:
        raise PluginError(
            "checkpoint is empty",
            code="CHEMPROP_FILE_INVALID",
            context={"file": name},
        )


def _model_id(
    manifest: ChempropBundleManifest,
    files: list[dict[str, Any]],
    chemprop_version: str,
) -> str:
    """Bind bytes, task selection and software into one identity.

    ``prediction/v1`` requires ``model_id`` to identify the model bytes *and*
    how they were turned into numbers.  For the ONNX path that means
    MolCascade's featurizer version; here the featurizer ships inside Chemprop,
    so the release is part of the identity.  Two runs agreeing on this
    identifier computed the same numbers the same way.
    """

    digest = canonical_sha256(
        {
            "manifest": manifest.identity_inputs,
            "bundle_files": files,
            "chemprop_version": chemprop_version,
        }
    )
    return f"{manifest.model_name}:sha256:{digest}"


# --------------------------------------------------------------------------
# Inference
# --------------------------------------------------------------------------


def _load_checkpoint(checkpoint_path: Path, manifest: ChempropBundleManifest) -> Any:
    """Deserialize the checkpoint and check it is shaped as the manifest says."""

    try:
        from chemprop import models
    except ImportError as error:
        raise PluginError(
            "Chemprop is required to run Chemprop checkpoints",
            code="CHEMPROP_RUNTIME_UNAVAILABLE",
            hint=(
                "Install chemprop in this environment. MolCascade never installs "
                "packages or downloads models on your behalf."
            ),
        ) from error
    try:
        model = models.load_model(checkpoint_path)
    except Exception as error:
        raise PluginError(
            "the bundled Chemprop checkpoint could not be loaded",
            code="CHEMPROP_LOAD_FAILED",
            context={"error_type": type(error).__name__},
        ) from error

    n_tasks = int(getattr(model, "n_tasks", 0))
    if n_tasks != manifest.model.n_tasks:
        raise PluginError(
            "the checkpoint emits a different number of tasks than the manifest declares",
            code="CHEMPROP_TASK_COUNT_MISMATCH",
            hint=(
                "n_tasks in the manifest must match the head the model was trained "
                "with, otherwise task_index selects a different endpoint."
            ),
            context={"checkpoint_n_tasks": n_tasks, "manifest_n_tasks": manifest.model.n_tasks},
        )

    # A regression head returns raw values and a classification head returns
    # probabilities, so declaring the wrong one silently reinterprets every
    # number the stage emits.  The class name is what distinguishes them.
    predictor = type(getattr(model, "predictor", None)).__name__
    classifier = "Classification" in predictor
    if classifier != (manifest.task == "binary_probability"):
        raise PluginError(
            "the checkpoint's head does not match the task declared in the manifest",
            code="CHEMPROP_TASK_KIND_MISMATCH",
            context={"predictor": predictor, "manifest_task": manifest.task},
        )
    model.eval()
    return model


def _predict(
    model: Any,
    smiles: list[str],
    *,
    batch_size: int,
    num_workers: int,
) -> np.ndarray:
    """Run every molecule through the network, in order, losing none of them."""

    import torch
    from chemprop import data, featurizers

    datapoints = [data.MoleculeDatapoint.from_smi(value) for value in smiles]
    dataset = data.MoleculeDataset(
        datapoints, featurizers.SimpleMoleculeMolGraphFeaturizer()
    )
    loader = data.build_dataloader(
        dataset,
        batch_size=batch_size,
        num_workers=num_workers,
        shuffle=False,
        # Explicitly false.  Left at its default, Chemprop drops a trailing
        # batch of one to protect batch-norm statistics during *training*; in
        # prediction that silently returns fewer rows than it was given, and the
        # loss lands on whichever molecule happened to sort last.
        drop_last=False,
    )
    chunks: list[np.ndarray] = []
    with torch.no_grad():
        for batch in loader:
            chunks.append(model(batch.bmg, batch.V_d, batch.X_d).numpy())
    if not chunks:
        return np.empty((0, 0), dtype=np.float64)
    return np.vstack(chunks).astype(np.float64, copy=False)


def _score(
    model: Any,
    *,
    manifest: ChempropBundleManifest,
    model_id: str,
    parent_ids: list[str],
    smiles: list[str],
    batch_size: int,
    num_workers: int,
) -> tuple[list[dict[str, Any]], int, int]:
    # Chemprop raises on a SMILES RDKit cannot parse, which would abort the
    # screen on one bad row.  Parents are canonicalized upstream so this is
    # rare, but a hand-assembled pipeline may not have standardized, and one
    # unparseable molecule is evidence-missing, not a run failure.
    from rdkit import Chem

    kept: list[str] = []
    kept_smiles: list[str] = []
    skipped = 0
    for parent_id, value in zip(parent_ids, smiles, strict=True):
        if Chem.MolFromSmiles(value) is None:
            skipped += 1
            continue
        kept.append(parent_id)
        kept_smiles.append(value)
    if not kept:
        return [], skipped, 0

    try:
        predictions = _predict(
            model, kept_smiles, batch_size=batch_size, num_workers=num_workers
        )
    except PluginError:
        raise
    except Exception as error:
        raise PluginError(
            "chemprop inference failed",
            code="CHEMPROP_INFERENCE_FAILED",
            context={"batch_size": len(kept), "error_type": type(error).__name__},
        ) from error

    if predictions.shape[0] != len(kept):
        raise PluginError(
            "chemprop returned a different number of rows than it was given",
            code="CHEMPROP_OUTPUT_COUNT_MISMATCH",
            context={
                "input_count": len(kept),
                "output_count": int(predictions.shape[0]),
            },
        )
    if predictions.ndim != 2 or predictions.shape[1] <= manifest.model.task_index:
        raise PluginError(
            "chemprop output has no column at the manifest's task_index",
            code="CHEMPROP_TASK_INDEX_OUT_OF_RANGE",
            context={
                "shape": [int(size) for size in predictions.shape],
                "task_index": manifest.model.task_index,
            },
        )
    values = predictions[:, manifest.model.task_index]

    rows: list[dict[str, Any]] = []
    dropped = 0
    for index, parent_id in enumerate(kept):
        mean = float(values[index])
        if not math.isfinite(mean):
            # One odd molecule should not abort a two-million-row screen,
            # but it must not be scored either.  No row means no evidence,
            # which the gate turns into an explicit reject.
            dropped += 1
            continue
        if manifest.task == "binary_probability" and not 0.0 <= mean <= 1.0:
            raise PluginError(
                "a binary_probability model returned a value outside [0, 1]",
                code="CHEMPROP_PROBABILITY_OUT_OF_RANGE",
                hint=(
                    "This usually means the checkpoint has a regression head "
                    "and the manifest declares the wrong task."
                ),
                context={"value": mean},
            )
        rows.append(
            {
                "parent_id": parent_id,
                "endpoint_id": manifest.endpoint_id,
                "model_id": model_id,
                "prediction_mean": mean,
                "prediction_std": None,
                "interval_lower": None,
                "interval_upper": None,
                "calibration_id": None,
            }
        )
    return rows, skipped, dropped

@cache
def _loaded_bundle(bundle_dir: str, bundle_sha256: str) -> tuple[ChempropBundleManifest, Any]:
    """Read the manifest and deserialize the checkpoint once per worker process.

    Cached because a lane is handed shard after shard and this is a
    ``torch.load`` of a trained message-passing network.  ``bundle_sha256`` is
    part of the key rather than decoration: the parent has already proved those
    bytes, so two stages pinning different bundles can never share one model,
    and re-pinning the same directory reloads instead of returning the old one.
    """

    root = Path(bundle_dir)
    manifest = _parse_manifest((root / MANIFEST_FILENAME).read_bytes())
    return manifest, _load_checkpoint(root / manifest.model.file, manifest)


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Score one contiguous range of parents on whichever lane owns it."""

    settings = dict(task.config)
    runtime = dict(settings.pop(_RUNTIME_KEY))
    config = ChempropCheckpointConfig.model_validate(settings)
    model_id = str(runtime["model_id"])
    manifest, model = _loaded_bundle(
        str(runtime["bundle_dir"]), str(runtime["bundle_sha256"])
    )

    input_count = 0
    prediction_count = 0
    unfeaturizable = 0
    non_finite = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["predictions"], PREDICTION_V1.schema, compression="zstd"
        ) as prediction_writer,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            parent_ids = [str(value) for value in batch.column("parent_id").to_pylist()]
            smiles = [str(value) for value in batch.column("parent_smiles").to_pylist()]
            rows, skipped, dropped = _score(
                model,
                manifest=manifest,
                model_id=model_id,
                parent_ids=parent_ids,
                smiles=smiles,
                batch_size=config.batch_size,
                num_workers=config.num_workers,
            )
            parent_writer.write_batch(batch)
            if rows:
                prediction_writer.write_table(
                    pa.Table.from_pylist(rows, schema=PREDICTION_V1.schema)
                )
            input_count += batch.num_rows
            prediction_count += len(rows)
            unfeaturizable += skipped
            non_finite += dropped
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": input_count, "predictions": prediction_count},
        metadata={
            "unfeaturizable_count": unfeaturizable,
            "non_finite_prediction_count": non_finite,
        },
    )


class ChempropCheckpointPlugin:
    """Score molecules with a user-trained, hash-pinned Chemprop checkpoint."""

    descriptor = PluginDescriptor(
        id="prediction.chemprop_checkpoint",
        version="0.1.0",
        kind=PluginKind.PREDICTOR,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, PREDICTION_V1.id),
        output_ports={"primary": PARENT_V1.id, "predictions": PREDICTION_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        # Weights and featurization are pinned, but Torch floating-point kernels
        # differ between builds, CPU instruction sets and devices.
        determinism=Determinism.BEST_EFFORT,
        display_name="Chemprop checkpoint",
        description=(
            "Run a user-trained Chemprop 2.x message-passing model over parent "
            "SMILES, pinned by bundle digest and gated on explicit trust."
        ),
    )
    config_model = ChempropCheckpointConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        try:
            config = ChempropCheckpointConfig.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid chemprop checkpoint configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"error_count": error.error_count()},
            ) from error
        if not config.allow_unsafe_model_deserialization:
            raise PluginError(
                (
                    "Chemprop checkpoints are Torch checkpoints, which execute code "
                    "when deserialized; explicit trust is required"
                ),
                code="CHEMPROP_MODEL_TRUST_REQUIRED",
                hint=(
                    "Confirm the bundle digest identifies a checkpoint you trained or "
                    "obtained from a source you trust, then set "
                    "allow_unsafe_model_deserialization: true."
                ),
                context={"backend": "chemprop", "risk": "executable-model-artifacts"},
            )

        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        inspected = inspect_chemprop_bundle(
            config.bundle_dir,
            maximum_files=config.max_bundle_files,
            maximum_bytes=config.max_bundle_bytes,
        )
        if inspected["bundle_sha256"] != config.expected_bundle_sha256:
            raise PluginError(
                "chemprop bundle does not match the digest pinned in the configuration",
                code="CHEMPROP_BUNDLE_HASH_MISMATCH",
                hint="Re-run `molcascade model-bundle <dir>` and update the config.",
                context={
                    "expected": config.expected_bundle_sha256,
                    "actual": inspected["bundle_sha256"],
                },
            )
        root = Path(str(inspected["bundle_dir"]))
        # Loaded in the parent as well, so a checkpoint that Torch refuses or
        # that is shaped unlike its manifest stops the stage before a single
        # shard is scheduled -- and so the manifest below is the one the model
        # was checked against.
        manifest, _ = _loaded_bundle(str(root), str(inspected["bundle_sha256"]))
        # Settled after the import succeeded, so it names the release that
        # actually ran rather than the one metadata advertised.
        model_id = _model_id(
            manifest,
            list(inspected["files"]),
            str(inspected["chemprop_version"]),
        )
        sharded = shard_stage(
            worker=_run_shard,
            stage_input=stage_input,
            contract=PARENT_V1,
            output_paths={
                "primary": _PARENT_PATH.as_posix(),
                "predictions": _PREDICTION_PATH.as_posix(),
            },
            context=context,
            stage_id=request.stage_id,
            config={
                **config.model_dump(mode="json"),
                _RUNTIME_KEY: {
                    "bundle_dir": str(root),
                    "bundle_sha256": str(inspected["bundle_sha256"]),
                    "model_id": model_id,
                },
            },
            shard_rows=_SHARD_ROWS,
        )
        input_count = sharded.rows_in
        prediction_count = sharded.rows_out.get("predictions", 0)
        unfeaturizable = sharded.total("unfeaturizable_count")
        non_finite = sharded.total("non_finite_prediction_count")

        if input_count == 0:
            raise PluginError(
                "chemprop input contains no parents",
                code="CHEMPROP_EMPTY_INPUT",
            )

        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id, sharded.file_paths["primary"], {"row_count": input_count}
                ),
                "predictions": PendingOutput(
                    PREDICTION_V1.id,
                    sharded.file_paths["predictions"],
                    {"row_count": prediction_count, "model_id": model_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": input_count,
                "prediction_count": prediction_count,
                # Molecules with no prediction are not quietly lost: the
                # threshold gate downstream rejects missing evidence, and these
                # counts say how many that will be and why.
                "unfeaturizable_count": unfeaturizable,
                "non_finite_prediction_count": non_finite,
                "backend": "chemprop-checkpoint",
                "model_id": model_id,
                "endpoint_id": manifest.endpoint_id,
                "task": manifest.task,
                "units": manifest.units,
                "bundle_sha256": inspected["bundle_sha256"],
                "bundle_file_count": inspected["file_count"],
                "model_file_sha256": manifest.model.sha256,
                "chemprop_version": inspected["chemprop_version"],
                "n_tasks": manifest.model.n_tasks,
                "task_index": manifest.model.task_index,
                "uncertainty_available": False,
                "calibration_available": False,
                "model_deserialization_trust_acknowledged": True,
                "network_or_download_invoked_by_adapter": False,
                **sharded.response_metadata(),
            },
        )


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "MANIFEST_FILENAME",
    "CheckpointSpec",
    "ChempropBundleManifest",
    "ChempropCheckpointConfig",
    "ChempropCheckpointPlugin",
    "inspect_chemprop_bundle",
]
