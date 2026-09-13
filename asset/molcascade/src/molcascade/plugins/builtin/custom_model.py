"""Run a user's own trained model as one screening criterion.

This is the interface for bringing your own science.  You pick the molecular
representation and the learning algorithm elsewhere, export the fitted
estimator to ONNX, and drop it into a *model bundle*: a directory holding one
``molcascade_model.yaml`` manifest and the model file it names.  MolCascade
then treats that bundle exactly like any other evidence producer -- it emits
``prediction/v1``, and a separate threshold gate turns those numbers into
decisions, so a custom model gets no privileges a built-in one does not have.

Two deliberate constraints shape the design.

*MolCascade computes the features, not the bundle.*  A scikit-learn pipeline
whose first step is an RDKit featuriser cannot be converted to ONNX -- there is
no shape calculator for a custom transformer -- so in practice people export
the estimator alone.  Rather than pretend otherwise, the manifest declares the
representation and :mod:`molcascade.chemistry.featurizers` recomputes it here.
The declared width is checked against the graph's input width before a single
molecule is scored, so a representation that does not match the model is a
configuration error rather than a silently wrong column mapping.

*The model is data, not code.*  ONNX is loaded as a graph by ONNX Runtime; no
Python from the bundle is imported and no custom operator library is
registered.  That is why this adapter needs no equivalent of the Torch
"deserialising this can execute code" acknowledgement.  Pickle and joblib are
refused outright for the same reason -- loading them *is* code execution.  The
bundle is still pinned by digest, because provenance is a separate question
from safety: a run must be able to say which bytes produced its numbers.
"""

from __future__ import annotations

import math
import re
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.featurizers import (
    FEATURIZER_IMPLEMENTATION_VERSION,
    Featurizer,
    RepresentationSpec,
)
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

MANIFEST_FILENAME = "molcascade_model.yaml"
BUNDLE_SCHEMA_VERSION = 1

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_PREDICTION_PATH = Path("datasets/custom_predictions/part-00000.parquet")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ENDPOINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:/-]{0,127}$")
_READ_CHUNK_SIZE = 1024 * 1024
# Formats whose "loading" step runs arbitrary code.  They are named explicitly
# so the error can say what to do instead of just "unsupported".
_EXECUTABLE_MODEL_SUFFIXES = {
    ".pkl": "pickle",
    ".pickle": "pickle",
    ".joblib": "joblib",
    ".pt": "torch",
    ".pth": "torch",
    ".bin": "torch",
    ".h5": "keras",
    ".keras": "keras",
    ".pmml": "pmml",
}


class ModelFileSpec(StrictFrozenModel):
    """The estimator graph, pinned to exact bytes."""

    file: str = Field(min_length=1, max_length=255)
    sha256: str
    format: Literal["onnx"] = "onnx"
    n_features: int = Field(ge=1, le=1_000_000)
    dtype: Literal["float32", "float64"] = "float32"
    # ``None`` means "the graph has exactly one, use it".  Naming them is only
    # necessary for multi-input or multi-output graphs.
    input_name: str | None = Field(default=None, min_length=1, max_length=256)
    output_name: str | None = Field(default=None, min_length=1, max_length=256)
    # Which column to read from an ``(N, K)`` output -- for a binary
    # classifier exported with ``zipmap=False`` that is usually 1.
    output_column: int | None = Field(default=None, ge=0, le=65_535)

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


class UncertaintySpec(StrictFrozenModel):
    """An optional second graph output carrying a per-prediction spread."""

    output_name: str = Field(min_length=1, max_length=256)
    output_column: int | None = Field(default=None, ge=0, le=65_535)
    # Ensembles usually emit a standard deviation; some emit a variance.
    scale: Literal["stddev", "variance"] = "stddev"


class ModelBundleManifest(StrictFrozenModel):
    """``molcascade_model.yaml`` -- everything needed to reproduce a score."""

    schema_version: int = Field(default=BUNDLE_SCHEMA_VERSION, ge=1, le=1)
    model_name: str = Field(min_length=1, max_length=128)
    endpoint_id: str = Field(min_length=1, max_length=256)
    task: Literal["regression", "binary_probability"]
    representation: RepresentationSpec
    model: ModelFileSpec
    uncertainty: UncertaintySpec | None = None
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
    def _representation_matches_the_model(self) -> Self:
        width = self.representation.width
        if width != self.model.n_features:
            raise ValueError(
                f"representation produces {width} features but the model "
                f"declares n_features={self.model.n_features}; the bundle's "
                "representation does not match the exported estimator"
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
            "dtype": self.model.dtype,
            "n_features": self.model.n_features,
            "input_name": self.model.input_name,
            "output_name": self.model.output_name,
            "output_column": self.model.output_column,
            "uncertainty": (
                None if self.uncertainty is None else self.uncertainty.model_dump(mode="json")
            ),
        }


class CustomModelConfig(StrictFrozenModel):
    """Point one criterion at a locally provisioned model bundle."""

    schema_version: int = Field(default=1, ge=1, le=1)
    bundle_dir: str = Field(min_length=1, max_length=4096)
    expected_bundle_sha256: str = Field(
        description="Digest from `molcascade model-bundle <dir>`; binds the run to exact bytes."
    )
    batch_size: int = Field(default=4096, ge=1, le=250_000)
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
# The directory walk itself lives in ``_bundles`` because the Chemprop adapter
# reads an identically shaped directory and a traversal defence fixed in one
# copy but not the other is worse than none.  Everything policy-shaped stays
# here: which formats are acceptable, what the manifest may say, and the refusal
# below to load anything that executes on deserialization.
#
# All of it is deliberately independent of the ADMET-AI asset hasher next door:
# that one filters by suffix and snapshots a tree before an untrusted loader
# touches it, whereas a bundle is small, hashed whole, and never copied.
# --------------------------------------------------------------------------

#: Bundle-reading failures reported under this plugin's own error vocabulary.
_CODES = BundleCodes("CUSTOM_MODEL")

#: What the parent verified and a shard cannot: which directory holds the
#: bundle whose digest was just checked, and the identity read out of it.
#: Popped before the strict config model sees the mapping.
_RUNTIME_KEY = "molcascade.runtime"


def inspect_model_bundle(
    bundle_dir: str | Path,
    *,
    maximum_files: int = 64,
    maximum_bytes: int = 4 * 1024 * 1024 * 1024,
) -> dict[str, Any]:
    """Validate a bundle and report the digests to paste into a config.

    Nothing is executed and ONNX Runtime is not imported, so this is safe to
    run against a bundle before deciding to trust it.
    """

    root = bundle_root(str(Path(bundle_dir).expanduser().resolve()), codes=_CODES)
    contents, total_bytes = bundle_files(
        root, maximum_files=maximum_files, maximum_bytes=maximum_bytes, codes=_CODES
    )
    if MANIFEST_FILENAME not in contents:
        raise PluginError(
            f"model bundle has no {MANIFEST_FILENAME}",
            code="CUSTOM_MODEL_MANIFEST_MISSING",
            hint=f"Every bundle directory must contain {MANIFEST_FILENAME}.",
            context={"bundle_dir": str(root), "files": sorted(contents)},
        )
    manifest = _parse_manifest(contents[MANIFEST_FILENAME][0])
    _check_model_file(manifest, contents)
    files = [
        {"name": name, "sha256": digest, "size_bytes": len(payload)}
        for name, (payload, digest) in sorted(contents.items())
    ]
    return {
        "bundle_dir": str(root),
        "model_name": manifest.model_name,
        "endpoint_id": manifest.endpoint_id,
        "task": manifest.task,
        "n_features": manifest.model.n_features,
        "representation_width": manifest.representation.width,
        "file_count": len(files),
        "size_bytes": total_bytes,
        "files": files,
        "bundle_sha256": canonical_sha256(files),
        "model_id": _model_id(manifest, files),
    }


def _parse_manifest(payload: bytes) -> ModelBundleManifest:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as error:
        raise PluginError(
            f"{MANIFEST_FILENAME} is not valid UTF-8",
            code="CUSTOM_MODEL_MANIFEST_INVALID",
        ) from error
    document = load_yaml(text, source=MANIFEST_FILENAME)
    try:
        return ModelBundleManifest.model_validate(document)
    except ValidationError as error:
        raise PluginError(
            f"invalid {MANIFEST_FILENAME}: {error}",
            code="CUSTOM_MODEL_MANIFEST_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _check_model_file(
    manifest: ModelBundleManifest,
    contents: dict[str, tuple[bytes, str]],
) -> bytes:
    name = manifest.model.file
    suffix = Path(name).suffix.casefold()
    if suffix in _EXECUTABLE_MODEL_SUFFIXES:
        raise PluginError(
            f"model bundles cannot load {_EXECUTABLE_MODEL_SUFFIXES[suffix]} files",
            code="CUSTOM_MODEL_FORMAT_EXECUTABLE",
            hint=(
                "Loading these formats runs code from the file. Export the fitted "
                "estimator to ONNX instead, e.g. with skl2onnx, and convert "
                "classifiers with the zipmap option disabled."
            ),
            context={"file": name},
        )
    if name not in contents:
        raise PluginError(
            "model file named in the manifest is not in the bundle",
            code="CUSTOM_MODEL_FILE_MISSING",
            context={"file": name, "files": sorted(contents)},
        )
    payload, digest = contents[name]
    if digest != manifest.model.sha256:
        raise PluginError(
            "model file does not match the sha256 pinned in the manifest",
            code="CUSTOM_MODEL_FILE_HASH_MISMATCH",
            context={"file": name, "expected": manifest.model.sha256, "actual": digest},
        )
    if not payload:
        raise PluginError(
            "model file is empty",
            code="CUSTOM_MODEL_FILE_INVALID",
            context={"file": name},
        )
    return payload


def _model_id(manifest: ModelBundleManifest, files: list[dict[str, Any]]) -> str:
    """Bind bytes, features and software into one identity.

    ``prediction/v1`` requires ``model_id`` to identify the model bytes *and*
    the feature specification: two runs that agree on the identifier must have
    computed the same numbers the same way.
    """

    featurizer_identity = Featurizer(manifest.representation).identity
    digest = canonical_sha256(
        {
            "manifest": manifest.identity_inputs,
            "bundle_files": files,
            "featurizer_identity": featurizer_identity,
            "featurizer_implementation_version": FEATURIZER_IMPLEMENTATION_VERSION,
        }
    )
    return f"{manifest.model_name}:sha256:{digest}"


# --------------------------------------------------------------------------
# Inference
# --------------------------------------------------------------------------


def _load_onnx_session(model_path: Path, *, device: str = "cpu") -> Any:
    """Open an ONNX Runtime session on the lane this process was given.

    No custom operator library is registered, so a graph that needs one fails
    to load rather than pulling native code in from the bundle's directory.

    A GPU lane asks for ``CUDAExecutionProvider`` with ``CPUExecutionProvider``
    behind it, which is ONNX Runtime's own fallback: an operator the CUDA
    provider does not implement runs on the CPU instead of failing the graph.
    The device *index* is not passed, because the shard runner has already
    pinned ``CUDA_VISIBLE_DEVICES`` in this process -- the assigned card is the
    only one visible, and it is numbered zero.

    Asking for CUDA on a build that has no such provider is a configuration
    error rather than something to work around silently: an eight-hour screen
    that quietly fell back to one CPU core is the failure worth refusing, and
    ``--device auto`` never reaches this branch on a CPU-only install.
    """

    try:
        import onnxruntime
    except ImportError as error:
        raise PluginError(
            "ONNX Runtime is required to run custom model bundles",
            code="CUSTOM_MODEL_RUNTIME_UNAVAILABLE",
            hint=(
                "Install onnxruntime in this environment. MolCascade never "
                "installs packages or downloads models on your behalf."
            ),
        ) from error
    options = onnxruntime.SessionOptions()
    # One shard already owns a whole lane, and single-threaded execution is the
    # only setting that reproduces run to run.  Parallelism comes from the
    # shards, which is why it can stay off here.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.graph_optimization_level = (
        onnxruntime.GraphOptimizationLevel.ORT_ENABLE_BASIC
    )
    providers = ["CPUExecutionProvider"]
    if device.startswith("cuda"):
        available = set(onnxruntime.get_available_providers())
        if "CUDAExecutionProvider" not in available:
            raise PluginError(
                "this ONNX Runtime build has no CUDA execution provider",
                code="CUSTOM_MODEL_CUDA_UNAVAILABLE",
                hint=(
                    "Install onnxruntime-gpu, or run this stage on CPU lanes with "
                    "--device cpu."
                ),
                context={"device": device, "providers": sorted(available)},
            )
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    try:
        return onnxruntime.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=providers,
        )
    except Exception as error:
        raise PluginError(
            "the bundled ONNX graph could not be loaded",
            code="CUSTOM_MODEL_LOAD_FAILED",
            context={"error_type": type(error).__name__, "device": device},
        ) from error


def _resolve_io(session: Any, manifest: ModelBundleManifest) -> tuple[str, list[str]]:
    inputs = list(session.get_inputs())
    outputs = list(session.get_outputs())
    if manifest.model.input_name is None:
        if len(inputs) != 1:
            raise PluginError(
                "the ONNX graph has several inputs, so model.input_name is required",
                code="CUSTOM_MODEL_IO_AMBIGUOUS",
                context={"inputs": [item.name for item in inputs]},
            )
        input_name = inputs[0].name
    else:
        input_name = manifest.model.input_name
        if input_name not in {item.name for item in inputs}:
            raise PluginError(
                "model.input_name is not an input of the ONNX graph",
                code="CUSTOM_MODEL_IO_UNKNOWN",
                context={"requested": input_name, "inputs": [i.name for i in inputs]},
            )

    declared = next(item for item in inputs if item.name == input_name)
    shape = list(getattr(declared, "shape", []) or [])
    width = shape[-1] if len(shape) == 2 and isinstance(shape[-1], int) else None
    if width is not None and width != manifest.model.n_features:
        raise PluginError(
            "the ONNX graph expects a different feature width than the manifest declares",
            code="CUSTOM_MODEL_FEATURE_WIDTH_MISMATCH",
            hint=(
                "The representation in the manifest must be the one the model "
                "was trained on, in the same column order."
            ),
            context={"graph_width": width, "manifest_n_features": manifest.model.n_features},
        )

    names = {item.name for item in outputs}
    if manifest.model.output_name is None:
        if not outputs:
            raise PluginError(
                "the ONNX graph has no outputs",
                code="CUSTOM_MODEL_IO_UNKNOWN",
            )
        wanted = [outputs[0].name]
    else:
        wanted = [manifest.model.output_name]
    if manifest.uncertainty is not None:
        wanted.append(manifest.uncertainty.output_name)
    for name in wanted:
        if name not in names:
            raise PluginError(
                "a requested output is not produced by the ONNX graph",
                code="CUSTOM_MODEL_IO_UNKNOWN",
                context={"requested": name, "outputs": sorted(names)},
            )
    return input_name, wanted


def _as_column(value: Any, *, column: int | None, label: str) -> np.ndarray:
    """Reduce one graph output to a 1-D float column."""

    if isinstance(value, list):
        # scikit-learn classifiers converted with the default settings emit a
        # ZipMap: a sequence of {class: probability} dicts, which is not a
        # tensor and cannot be indexed by column.
        raise PluginError(
            f"the {label} output is a sequence of maps rather than a tensor",
            code="CUSTOM_MODEL_OUTPUT_NOT_TENSOR",
            hint=(
                "Re-export the classifier with ZipMap disabled, e.g. "
                "convert_sklearn(..., options={id(model): {'zipmap': False}})."
            ),
        )
    array = np.asarray(value)
    if array.ndim == 1:
        if column not in (None, 0):
            raise PluginError(
                f"the {label} output has one column, so output_column {column} does not exist",
                code="CUSTOM_MODEL_OUTPUT_COLUMN_INVALID",
            )
        selected = array
    elif array.ndim == 2:
        if column is None:
            if array.shape[1] != 1:
                raise PluginError(
                    f"the {label} output has {array.shape[1]} columns; "
                    "declare output_column to choose one",
                    code="CUSTOM_MODEL_OUTPUT_COLUMN_INVALID",
                    hint="For a binary classifier the positive class is usually column 1.",
                )
            selected = array[:, 0]
        elif column >= array.shape[1]:
            raise PluginError(
                f"output_column {column} is out of range for the {label} output",
                code="CUSTOM_MODEL_OUTPUT_COLUMN_INVALID",
                context={"columns": int(array.shape[1])},
            )
        else:
            selected = array[:, column]
    else:
        raise PluginError(
            f"the {label} output has an unsupported rank",
            code="CUSTOM_MODEL_OUTPUT_NOT_TENSOR",
            context={"ndim": int(array.ndim)},
        )
    try:
        return selected.astype(np.float64, copy=False)
    except (TypeError, ValueError) as error:
        raise PluginError(
            f"the {label} output is not numeric",
            code="CUSTOM_MODEL_OUTPUT_NOT_NUMERIC",
            context={"dtype": str(array.dtype)},
        ) from error


def _score(
    session: Any,
    *,
    featurizer: Featurizer,
    manifest: ModelBundleManifest,
    model_id: str,
    input_name: str,
    output_names: list[str],
    parent_ids: list[str],
    smiles: list[str],
) -> tuple[list[dict[str, Any]], int, int]:
    matrix, failed = featurizer.transform(smiles, dtype=manifest.model.dtype)
    skipped = set(failed)
    kept = [pid for index, pid in enumerate(parent_ids) if index not in skipped]
    if not kept:
        return [], len(failed), 0
    try:
        raw = session.run(output_names, {input_name: matrix})
    except PluginError:
        raise
    except Exception as error:
        raise PluginError(
            "custom model inference failed",
            code="CUSTOM_MODEL_INFERENCE_FAILED",
            context={"batch_size": len(kept), "error_type": type(error).__name__},
        ) from error

    values = _as_column(raw[0], column=manifest.model.output_column, label="prediction")
    if values.shape[0] != len(kept):
        raise PluginError(
            "the custom model returned a different number of rows than it was given",
            code="CUSTOM_MODEL_OUTPUT_COUNT_MISMATCH",
            context={"input_count": len(kept), "output_count": int(values.shape[0])},
        )
    spread: np.ndarray | None = None
    if manifest.uncertainty is not None:
        spread = _as_column(
            raw[1], column=manifest.uncertainty.output_column, label="uncertainty"
        )
        if spread.shape[0] != len(kept):
            raise PluginError(
                "the custom model uncertainty output has a different row count",
                code="CUSTOM_MODEL_OUTPUT_COUNT_MISMATCH",
                context={"input_count": len(kept), "output_count": int(spread.shape[0])},
            )
        if manifest.uncertainty.scale == "variance":
            spread = np.sqrt(np.clip(spread, 0.0, None))

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
                code="CUSTOM_MODEL_PROBABILITY_OUT_OF_RANGE",
                hint=(
                    "This usually means output_name or output_column points at "
                    "the predicted label instead of the probability tensor."
                ),
                context={"value": mean},
            )
        deviation: float | None = None
        if spread is not None:
            candidate = float(spread[index])
            deviation = candidate if math.isfinite(candidate) and candidate >= 0 else None
        rows.append(
            {
                "parent_id": parent_id,
                "endpoint_id": manifest.endpoint_id,
                "model_id": model_id,
                "prediction_mean": mean,
                "prediction_std": deviation,
                "interval_lower": None,
                "interval_upper": None,
                "calibration_id": None,
            }
        )
    return rows, len(failed), dropped


@cache
def _loaded_session(
    bundle_dir: str, bundle_sha256: str, device: str
) -> tuple[ModelBundleManifest, Any, str, tuple[str, ...]]:
    """Read the manifest and open the ONNX session once per worker process.

    ``device`` is part of the key because it decides which execution provider
    the session was built on; ``bundle_sha256`` is, because the parent has
    already proved those bytes and two stages pinning different bundles must
    never share one session.
    """

    root = Path(bundle_dir)
    manifest = _parse_manifest((root / MANIFEST_FILENAME).read_bytes())
    session = _load_onnx_session(root / manifest.model.file, device=device)
    input_name, output_names = _resolve_io(session, manifest)
    return manifest, session, input_name, tuple(output_names)


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Score one contiguous range of parents on whichever lane owns it."""

    settings = dict(task.config)
    runtime = dict(settings.pop(_RUNTIME_KEY))
    config = CustomModelConfig.model_validate(settings)
    model_id = str(runtime["model_id"])
    manifest, session, input_name, output_names = _loaded_session(
        str(runtime["bundle_dir"]), str(runtime["bundle_sha256"]), task.device or "cpu"
    )
    featurizer = Featurizer(manifest.representation)

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
                session,
                featurizer=featurizer,
                manifest=manifest,
                model_id=model_id,
                input_name=input_name,
                output_names=list(output_names),
                parent_ids=parent_ids,
                smiles=smiles,
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


class CustomModelPredictorPlugin:
    """Score molecules with a user-supplied, hash-pinned ONNX model bundle."""

    descriptor = PluginDescriptor(
        id="prediction.custom_model",
        version="0.1.0",
        kind=PluginKind.PREDICTOR,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, PREDICTION_V1.id),
        output_ports={"primary": PARENT_V1.id, "predictions": PREDICTION_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        # The graph and the features are pinned, but floating-point kernels
        # differ between ONNX Runtime builds and CPU instruction sets.
        determinism=Determinism.BEST_EFFORT,
        display_name="Custom model bundle",
        description=(
            "Run a user-trained ONNX model over a MolCascade-computed molecular "
            "representation, pinned by bundle digest."
        ),
    )
    config_model = CustomModelConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        try:
            config = CustomModelConfig.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid custom model configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"error_count": error.error_count()},
            ) from error

        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        inspected = inspect_model_bundle(
            config.bundle_dir,
            maximum_files=config.max_bundle_files,
            maximum_bytes=config.max_bundle_bytes,
        )
        if inspected["bundle_sha256"] != config.expected_bundle_sha256:
            raise PluginError(
                "model bundle does not match the digest pinned in the configuration",
                code="CUSTOM_MODEL_BUNDLE_HASH_MISMATCH",
                hint="Re-run `molcascade model-bundle <dir>` and update the config.",
                context={
                    "expected": config.expected_bundle_sha256,
                    "actual": inspected["bundle_sha256"],
                },
            )
        root = Path(str(inspected["bundle_dir"]))
        manifest = _parse_manifest((root / MANIFEST_FILENAME).read_bytes())
        model_id = str(inspected["model_id"])
        featurizer = Featurizer(manifest.representation)

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
        )
        input_count = sharded.rows_in
        prediction_count = sharded.rows_out.get("predictions", 0)
        unfeaturizable = sharded.total("unfeaturizable_count")
        non_finite = sharded.total("non_finite_prediction_count")

        if input_count == 0:
            raise PluginError(
                "custom model input contains no parents",
                code="CUSTOM_MODEL_EMPTY_INPUT",
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
                "backend": "custom-model-bundle",
                "model_id": model_id,
                "endpoint_id": manifest.endpoint_id,
                "task": manifest.task,
                "bundle_sha256": inspected["bundle_sha256"],
                "bundle_file_count": inspected["file_count"],
                "model_file_sha256": manifest.model.sha256,
                "feature_width": featurizer.width,
                "featurizer_identity": featurizer.identity,
                "featurizer_implementation_version": FEATURIZER_IMPLEMENTATION_VERSION,
                "uncertainty_available": manifest.uncertainty is not None,
                "calibration_available": False,
                "model_deserialization_trust_acknowledged": False,
                "network_or_download_invoked_by_adapter": False,
                **sharded.response_metadata(),
            },
        )


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "MANIFEST_FILENAME",
    "CustomModelConfig",
    "CustomModelPredictorPlugin",
    "ModelBundleManifest",
    "ModelFileSpec",
    "UncertaintySpec",
    "inspect_model_bundle",
]
