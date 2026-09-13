"""SCScore: synthetic complexity learned from reaction data.

SA Score asks how unusual a molecule's fragments are.  SCScore asks a different
question -- it was trained on twelve million Reaxys reactions under the single
assumption that a product is more complex than its reactants, so it scores
*reaction* complexity rather than fragment rarity.  The two disagree often
enough to be worth running together, and the disagreement is informative: a
natural product scores high on SA and moderately on SC, while a heavily
decorated but routine amide scores the other way round.

The published model is a plain feed-forward network over a Morgan fingerprint,
and its weights are distributed as a gzipped JSON list of matrices.  That means
this adapter needs no new dependency at all: the forward pass is four lines of
numpy, and numpy is already required.  What it does need is the weight file,
which MolCascade will not download.  The user fetches it once from
connorcoley/scscore, points a criterion at it, and pins its digest.

Three things here differ deliberately from the reference implementation.

*The fingerprint width is read from the weights, not configured.*  The reference
takes ``FP_len`` as an argument and will happily build a 1024-bit fingerprint
for a 2048-input model, which multiplies silently into a wrong-but-plausible
score.  The first weight matrix already states the input width, so this adapter
uses that and treats a mismatch as impossible rather than as a setting.

*An unscoreable molecule stops the stage instead of scoring zero.*  The
reference returns ``0.0`` when a fingerprint comes back empty.  Zero is outside
the model's 1-5 range, and on a HIGHER_HARDER axis it reads as "trivially easy
to make" -- precisely the wrong direction for a value that means "no answer".

*Counts and bits are named, not inferred from the file name.*  The reference
switches fingerprint style on whether the path contains ``uint8``.  A renamed
file would then be scored by the wrong featurization with no error at all.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator

from molcascade.assets import is_asset_reference, resolve_reference
from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_json, canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import PARENT_V1, SYNTHESIS_SCORE_V1
from molcascade.errors import AssetError, PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_PARENT_PATH = Path("datasets/parents/part-00000.parquet")
_SCORE_PATH = Path("datasets/synthesis_scores/part-00000.parquet")

_IMPLEMENTATION_VERSION = 1

#: The published models are ~10-40 MB compressed.  The cap is a decompression
#: bomb guard, not a capability limit: a gzip member declares nothing about its
#: expanded size, so the only safe way to read one is to stop counting bytes.
_MAX_COMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_DECOMPRESSED_BYTES = 1024 * 1024 * 1024
_READ_CHUNK = 4 * 1024 * 1024

#: Both published architectures use radius 2 with chirality, and both map onto
#: 1024 or 2048 inputs.  These are properties of the trained weights rather than
#: choices, so they are constants and the width is read from the matrices.
_MORGAN_RADIUS = 2
_USE_CHIRALITY = True
_SCORE_SCALE = 5.0
_MIN_FINGERPRINT_BITS = 16
_MAX_FINGERPRINT_BITS = 65_536
_MAX_LAYERS = 32


class ScScoreConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)

    #: Absolute path to the gzipped JSON weight file from connorcoley/scscore,
    #: for example ``model.ckpt-10654.as_numpy.json.gz``.
    weights_path: str = Field(min_length=1, max_length=4096)

    #: The digest of that exact file.  Run ``sha256sum`` on it, or read the
    #: value this stage reports after a first run against a copy you trust.
    expected_weights_sha256: str = Field(min_length=64, max_length=64)

    #: ``bits`` for the ``*_1024bool`` and ``*_2048bool`` models, ``counts``
    #: for ``*_1024uint8``.  Named rather than guessed from the file name.
    fingerprint: str = Field(default="bits")

    @field_validator("expected_weights_sha256")
    @classmethod
    def _hash_is_valid(cls, value: str) -> str:
        lowered = value.strip().lower()
        if len(lowered) != 64 or any(character not in "0123456789abcdef" for character in lowered):
            raise ValueError("expected_weights_sha256 must be 64 lowercase hex characters")
        return lowered

    @field_validator("fingerprint")
    @classmethod
    def _known_fingerprint(cls, value: str) -> str:
        lowered = value.strip().lower()
        if lowered not in {"bits", "counts"}:
            raise ValueError("fingerprint must be 'bits' or 'counts'")
        return lowered


def _validated_config(request: StageRequest) -> ScScoreConfig:
    try:
        return ScScoreConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid SCScore configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _read_weight_bytes(configured: str) -> tuple[Path, bytes, str]:
    """Read the weight file as a regular file, hashing the bytes on disk.

    The digest covers the compressed file exactly as distributed, so the value
    a user pins is the value ``sha256sum`` prints for the download.
    """

    if is_asset_reference(configured):
        # A vendored asset resolves the same way on every machine, and
        # resolution has already proved the bytes against the pinned digest.
        try:
            path = resolve_reference(configured)
        except AssetError as error:
            raise PluginError(
                str(error),
                code="SCSCORE_WEIGHTS_PATH_INVALID",
                hint=error.hint,
                context={"weights_path": configured},
            ) from error
    else:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise PluginError(
                "weights_path must be an absolute path or an 'asset:' reference",
                code="SCSCORE_WEIGHTS_PATH_INVALID",
                hint=(
                    "Give the full path to the model.ckpt-*.as_numpy.json.gz file, or "
                    "run 'molcascade assets fetch scscore' and use "
                    "'asset:scscore/models/.../model.ckpt-10654.as_numpy.json.gz'."
                ),
                context={"weights_path": configured},
            )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PluginError(
            "SCScore weight file could not be opened as a regular file",
            code="SCSCORE_WEIGHTS_UNREADABLE",
            hint=(
                "MolCascade never downloads anything. Fetch the weights yourself from "
                "github.com/connorcoley/scscore (MIT) and copy the real file into place."
            ),
            context={"weights_path": str(path), "error_type": type(error).__name__},
        ) from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PluginError(
                "weights_path is not a regular file",
                code="SCSCORE_WEIGHTS_UNREADABLE",
                context={"weights_path": str(path)},
            )
        if info.st_size > _MAX_COMPRESSED_BYTES:
            raise PluginError(
                "SCScore weight file is larger than this adapter will read",
                code="SCSCORE_WEIGHTS_TOO_LARGE",
                context={"size_bytes": info.st_size, "limit_bytes": _MAX_COMPRESSED_BYTES},
            )
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return path, payload, hashlib.sha256(payload).hexdigest()


def _decompress(payload: bytes) -> bytes:
    """Expand the gzip member, refusing to grow without bound."""

    chunks: list[bytes] = []
    total = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
            while chunk := stream.read(_READ_CHUNK):
                total += len(chunk)
                if total > _MAX_DECOMPRESSED_BYTES:
                    raise PluginError(
                        "SCScore weight file expands beyond the size this adapter will read",
                        code="SCSCORE_WEIGHTS_TOO_LARGE",
                        context={"limit_bytes": _MAX_DECOMPRESSED_BYTES},
                    )
                chunks.append(chunk)
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise PluginError(
            "SCScore weight file is not readable gzip",
            code="SCSCORE_WEIGHTS_MALFORMED",
            hint="Expected the model.ckpt-*.as_numpy.json.gz file, not the pickle variant.",
            context={"error_type": type(error).__name__},
        ) from error
    return b"".join(chunks)


def _parse_layers(raw: bytes) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    """Turn the flat ``[W0, b0, W1, b1, ...]`` list into validated layers.

    The reference implementation trusts the file completely.  Every shape
    relation it assumes is checked here instead, because a weight file that is
    subtly the wrong shape produces numbers rather than errors.
    """

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PluginError(
            "SCScore weight file does not contain JSON",
            code="SCSCORE_WEIGHTS_MALFORMED",
            context={"error_type": type(error).__name__},
        ) from error
    if not isinstance(decoded, list) or not decoded or len(decoded) % 2 != 0:
        raise PluginError(
            "SCScore weights must be an even-length list of weight and bias arrays",
            code="SCSCORE_WEIGHTS_MALFORMED",
            context={"entries": len(decoded) if isinstance(decoded, list) else None},
        )
    if len(decoded) // 2 > _MAX_LAYERS:
        raise PluginError(
            "SCScore weight file declares more layers than this adapter will build",
            code="SCSCORE_WEIGHTS_MALFORMED",
            context={"layers": len(decoded) // 2, "limit": _MAX_LAYERS},
        )

    try:
        arrays = [np.asarray(entry, dtype=np.float64) for entry in decoded]
    except (TypeError, ValueError) as error:
        raise PluginError(
            "SCScore weights contain a value that is not a number",
            code="SCSCORE_WEIGHTS_MALFORMED",
            context={"error_type": type(error).__name__},
        ) from error
    if not all(np.all(np.isfinite(array)) for array in arrays):
        raise PluginError(
            "SCScore weights contain a non-finite value",
            code="SCSCORE_WEIGHTS_MALFORMED",
        )

    layers: list[tuple[np.ndarray, np.ndarray]] = []
    width: int | None = None
    for index in range(0, len(arrays), 2):
        weight, bias = arrays[index], arrays[index + 1]
        if weight.ndim != 2 or bias.ndim != 1:
            raise PluginError(
                "SCScore layer is not a 2-D weight paired with a 1-D bias",
                code="SCSCORE_WEIGHTS_MALFORMED",
                context={"layer": index // 2, "weight_ndim": weight.ndim, "bias_ndim": bias.ndim},
            )
        if weight.shape[1] != bias.shape[0]:
            raise PluginError(
                "SCScore layer weight and bias disagree on the output width",
                code="SCSCORE_WEIGHTS_MALFORMED",
                context={"layer": index // 2, "weight": list(weight.shape), "bias": bias.shape[0]},
            )
        if width is not None and weight.shape[0] != width:
            raise PluginError(
                "SCScore layers do not chain: one layer's input does not match the previous "
                "layer's output",
                code="SCSCORE_WEIGHTS_MALFORMED",
                context={"layer": index // 2, "expected": width, "received": weight.shape[0]},
            )
        width = weight.shape[1]
        layers.append((weight, bias))

    if width != 1:
        raise PluginError(
            "SCScore weights must end in a single output unit",
            code="SCSCORE_WEIGHTS_MALFORMED",
            context={"final_width": width},
        )
    fingerprint_bits = int(layers[0][0].shape[0])
    if not _MIN_FINGERPRINT_BITS <= fingerprint_bits <= _MAX_FINGERPRINT_BITS:
        raise PluginError(
            "SCScore weights declare an implausible fingerprint width",
            code="SCSCORE_WEIGHTS_MALFORMED",
            context={"fingerprint_bits": fingerprint_bits},
        )
    return tuple(layers)


def _fingerprints(molecules: Sequence[Any], *, bits: int, counts: bool) -> np.ndarray:
    """Featurize a batch exactly as the published model was trained to see it.

    The counts variant folds the *unfolded* sparse fingerprint with ``k % bits``
    and accumulates in ``uint8``.  That is not the same as asking RDKit for a
    folded count vector of the same width, so it is reproduced rather than
    approximated -- including the wraparound past 255, which the reference gets
    from its dtype.  Anything else would not be an SCScore.
    """

    from rdkit.Chem import rdFingerprintGenerator

    generator = rdFingerprintGenerator.GetMorganGenerator(
        radius=_MORGAN_RADIUS,
        fpSize=bits,
        includeChirality=_USE_CHIRALITY,
    )
    matrix = np.zeros((len(molecules), bits), dtype=np.float64)
    if counts:
        for row, molecule in enumerate(molecules):
            folded = [0] * bits
            sparse = generator.GetSparseCountFingerprint(molecule).GetNonzeroElements()
            for key, value in sparse.items():
                index = key % bits
                folded[index] = (folded[index] + int(value)) % 256
            matrix[row] = folded
    else:
        for row, molecule in enumerate(molecules):
            matrix[row] = generator.GetFingerprintAsNumPy(molecule)
    return matrix


def _forward(features: np.ndarray, layers: tuple[tuple[np.ndarray, np.ndarray], ...]) -> np.ndarray:
    """Run the network over a whole batch and map it onto the 1-5 scale."""

    activations = features
    for index, (weight, bias) in enumerate(layers):
        activations = activations @ weight + bias
        if index != len(layers) - 1:
            activations = np.maximum(activations, 0.0)
    logits = activations.reshape(-1)
    # 1 + 4 * sigmoid(x), written so a large negative logit cannot overflow.
    return 1.0 + (_SCORE_SCALE - 1.0) / (1.0 + np.exp(-np.clip(logits, -500.0, 500.0)))


@cache
def _load_model(
    weights_path: str,
    expected_sha256: str,
) -> tuple[Path, str, tuple[tuple[np.ndarray, np.ndarray], ...], int]:
    """Read, verify and parse the published weights once per process.

    Cached because the file is a 10-40 MB gzipped JSON list of matrices and a
    worker is handed many shards in a row; parsing it per shard would cost more
    than the forward pass over the whole shard.  The digest is part of the key,
    so a differently-pinned criterion never sees another one's matrices.
    """

    path, payload, digest = _read_weight_bytes(weights_path)
    if digest != expected_sha256:
        raise PluginError(
            "SCScore weight file does not match the pinned digest",
            code="SCSCORE_WEIGHTS_DIGEST_MISMATCH",
            hint=(
                "Set expected_weights_sha256 to the value sha256sum prints for the "
                "file you intend to use, or restore the file you pinned."
            ),
            context={
                "weights_path": str(path),
                "expected": expected_sha256,
                "actual": digest,
            },
        )
    layers = _parse_layers(_decompress(payload))
    del payload
    return path, digest, layers, int(layers[0][0].shape[0])


def _method_id(
    config: ScScoreConfig,
    *,
    rdkit_version: str,
    fingerprint_bits: int,
    layers: tuple[tuple[np.ndarray, np.ndarray], ...],
    digest: str,
) -> str:
    return "synthesis-sc:sha256:" + canonical_sha256(
        {
            "backend": "molcascade-native-numpy",
            "direction": "HIGHER_HARDER",
            "doi": "10.1021/acs.jcim.7b00622",
            "fingerprint": config.fingerprint,
            "fingerprint_bits": fingerprint_bits,
            "implementation_version": _IMPLEMENTATION_VERSION,
            "layer_shapes": [[int(size) for size in weight.shape] for weight, _ in layers],
            "method": "SCScore (Coley 2018)",
            "morgan_radius": _MORGAN_RADIUS,
            "rdkit_version": rdkit_version,
            "score_scale": _SCORE_SCALE,
            "use_chirality": _USE_CHIRALITY,
            "weights_sha256": digest,
        }
    )


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Score one contiguous range of parents in whichever process owns it."""

    from rdkit import Chem, rdBase

    config = ScScoreConfig.model_validate(dict(task.config))
    _, digest, layers, fingerprint_bits = _load_model(
        config.weights_path, config.expected_weights_sha256
    )
    use_counts = config.fingerprint == "counts"
    method_id = _method_id(
        config,
        rdkit_version=rdBase.rdkitVersion,
        fingerprint_bits=fingerprint_bits,
        layers=layers,
        digest=digest,
    )
    domain = (
        f"SCScore reaction-complexity model over a {fingerprint_bits}-bit Morgan "
        f"fingerprint ({config.fingerprint}); nominal range 1-5"
    )
    warning_codes = canonical_json(["SYNTHESIS_PROXY_NOT_ROUTE"])
    input_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["synthesis_scores"],
            SYNTHESIS_SCORE_V1.schema,
            compression="zstd",
        ) as score_writer,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            rows = batch.select(["parent_id", "parent_smiles"]).to_pylist()
            molecules = []
            for row in rows:
                smiles = row.get("parent_smiles")
                molecule = Chem.MolFromSmiles(smiles if isinstance(smiles, str) else "")
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed for SCScore",
                        code="SCSCORE_PARENT_INVALID",
                        context={"parent_id": str(row.get("parent_id"))},
                    )
                molecules.append(molecule)
            input_count += len(rows)

            features = _fingerprints(molecules, bits=fingerprint_bits, counts=use_counts)
            empty = np.flatnonzero(features.sum(axis=1) == 0.0)
            if empty.size:
                # The reference scores these 0.0, which on a HIGHER_HARDER axis
                # reads as "trivially easy" -- the opposite of "no answer".
                # Stop instead.
                raise PluginError(
                    "a parent produced an empty fingerprint, so SCScore has no input "
                    "to score",
                    code="SCSCORE_FINGERPRINT_EMPTY",
                    context={"parent_id": str(rows[int(empty[0])].get("parent_id"))},
                )
            scores = _forward(features, layers)
            if not np.all(np.isfinite(scores)):
                raise PluginError(
                    "SCScore produced a non-finite value",
                    code="SCSCORE_SCORE_NON_FINITE",
                )

            parent_writer.write_batch(batch)
            score_writer.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "parent_id": row.get("parent_id"),
                            "method_id": method_id,
                            "score": float(score),
                            "direction": "HIGHER_HARDER",
                            "domain": domain,
                            "warning_codes_json": warning_codes,
                        }
                        for row, score in zip(rows, scores.tolist(), strict=True)
                    ],
                    schema=SYNTHESIS_SCORE_V1.schema,
                )
            )
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": input_count, "synthesis_scores": input_count},
    )


class ScScorePlugin:
    """Score reaction-derived synthetic complexity from published weights."""

    descriptor = PluginDescriptor(
        id="synthesis.scscore",
        version="0.1.0",
        kind=PluginKind.SYNTHESIS,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, SYNTHESIS_SCORE_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "synthesis_scores": SYNTHESIS_SCORE_V1.id,
        },
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="SCScore synthetic complexity",
        description=(
            "Reaction-trained complexity score on a 1-5 scale (higher is more "
            "complex), evaluated locally from the published numpy weights. "
            "Complementary to SA Score; still not a route or a step count."
        ),
    )
    config_model = ScScoreConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        # Loaded in the parent as well, so a missing or re-pinned weight file
        # stops the stage before any shard is scheduled -- and the digest that
        # goes into the method identity is read from bytes, not from config.
        path, digest, layers, fingerprint_bits = _load_model(
            config.weights_path, config.expected_weights_sha256
        )
        method_id = _method_id(
            config,
            rdkit_version=rdBase.rdkitVersion,
            fingerprint_bits=fingerprint_bits,
            layers=layers,
            digest=digest,
        )
        result = shard_stage(
            worker=_run_shard,
            stage_input=stage_input,
            contract=PARENT_V1,
            output_paths={
                "primary": _PARENT_PATH.as_posix(),
                "synthesis_scores": _SCORE_PATH.as_posix(),
            },
            context=context,
            stage_id=request.stage_id,
            config=config.model_dump(mode="json"),
        )
        if result.rows_in == 0:
            raise PluginError(
                "SCScore input contains no parents",
                code="SCSCORE_EMPTY_INPUT",
            )
        input_count = result.rows_in
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": input_count},
                ),
                "synthesis_scores": PendingOutput(
                    SYNTHESIS_SCORE_V1.id,
                    result.file_paths["synthesis_scores"],
                    {"row_count": input_count, "method_id": method_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": input_count,
                "method_id": method_id,
                "direction": "HIGHER_HARDER",
                "proxy_not_route": True,
                "backend": "molcascade-native-numpy",
                "backend_version": f"numpy {np.__version__}",
                "weights_path": str(path),
                "weights_sha256": digest,
                "fingerprint": config.fingerprint,
                "fingerprint_bits": fingerprint_bits,
                "layer_count": len(layers),
                "network_or_download_invoked_by_adapter": False,
                **result.response_metadata(),
            },
        )


__all__ = ["ScScoreConfig", "ScScorePlugin"]
