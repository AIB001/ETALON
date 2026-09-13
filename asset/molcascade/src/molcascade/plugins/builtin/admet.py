"""Optional adapter for locally provisioned ADMET-AI v2 prediction assets.

This module deliberately does not import ADMET-AI, Chemprop, Torch, pandas, or
RDKit at import time.  ADMET-AI's Torch checkpoint files are executable model
artifacts rather than inert data, so execution requires an explicit trust
acknowledgement.  MolCascade snapshots and hashes the configured model tree
before loading it and verifies that both model and package code stayed stable
during inference.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import math
import os
import re
import shutil
import stat
from collections.abc import Mapping
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Self

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import PARENT_V1, PREDICTION_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_PREDICTION_PATH = Path("datasets/admet_predictions/part-00000.parquet")
_MODEL_SNAPSHOT = Path(".admet-ai-model-snapshot")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ENDPOINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
# The current official v2 loader discovers exactly ``**/*.pt`` beneath each
# ensemble directory. Accepting other generic model suffixes here would claim
# an asset was usable even though ADMET-AI would ignore it.
_CHECKPOINT_SUFFIXES = {".pt"}
#: ``models_dir`` accepts this in place of an absolute path.  See
#: :func:`_package_relative_root` for why an absolute path is the wrong default.
_PACKAGE_REFERENCE_PREFIX = "package:"
_PACKAGE_REFERENCE_NAME = "admet_ai"
DEFAULT_MODELS_REFERENCE = "package:admet_ai/resources/models"
#: The release the digests in ``cascade/catalog.py`` were measured from.
#:
#: Those digests are what makes the trust flag meaningful: the parent rehashes
#: the installed package tree and model tree and refuses to hand a shard any
#: path whose bytes differ.  That refusal is only informative while the pins
#: belong to the release actually installed, so the release number is written
#: once, here, and read by the catalogue's pins, ``molcascade pins admet-ai``
#: and the default cascade's version gate rather than being retyped in each.
PINNED_ADMET_AI_VERSION = "2.0.1"
_PACKAGE_CODE_SUFFIXES = {".dll", ".pyd", ".py", ".pyi", ".so"}
_SCIENTIFIC_DISTRIBUTIONS = (
    "chemprop",
    "lightning",
    "numpy",
    "pandas",
    "rdkit",
    "scikit-learn",
    "scipy",
    "torch",
)
_COPY_CHUNK_SIZE = 1024 * 1024

#: What the parent worked out and a shard cannot: where the verified model
#: snapshot landed, and the identity computed from its digests.  It travels
#: under one key that the worker pops before validating, because ``ADMETAIConfig``
#: is strict and these are facts about *this run*, not settings anyone authored.
#: Nothing here reaches ``stage_cache_key`` -- that hashes the stage config in
#: the pipeline, not the mapping handed to :func:`shard_stage`.
_RUNTIME_KEY = "molcascade.runtime"

#: Smaller than the 50 000-row default.  Each shard is a Chemprop ensemble
#: forward pass over every endpoint, so the useful unit of resume here is
#: minutes of GPU time rather than a fraction of a cheap descriptor sweep.
_SHARD_ROWS = 20_000


class ADMETEndpoint(StrictFrozenModel):
    """Bind one exact ADMET-AI output column to a stable endpoint identity."""

    output_column: str = Field(min_length=1, max_length=512)
    endpoint_id: str = Field(min_length=1, max_length=256)

    @field_validator("output_column")
    @classmethod
    def _column_is_unambiguous(cls, value: str) -> str:
        if value != value.strip() or any(character in value for character in "\r\n\x00"):
            raise ValueError("output_column must not contain surrounding or control whitespace")
        return value

    @field_validator("endpoint_id")
    @classmethod
    def _endpoint_is_portable(cls, value: str) -> str:
        if not _ENDPOINT_ID_RE.fullmatch(value):
            raise ValueError("endpoint_id contains unsupported characters")
        return value


class ExpectedCheckpointHash(StrictFrozenModel):
    """Optional user pin for one checkpoint within ``models_dir``."""

    relative_path: str = Field(min_length=1, max_length=4096)
    sha256: str

    @field_validator("relative_path")
    @classmethod
    def _path_is_canonical(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            value != path.as_posix()
            or path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("relative_path must be a canonical relative POSIX path")
        return value

    @field_validator("sha256")
    @classmethod
    def _hash_is_valid(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must contain 64 lowercase hexadecimal characters")
        return value


class ADMETAIConfig(StrictFrozenModel):
    """Configuration for an explicitly provisioned ADMET-AI v2 installation."""

    schema_version: int = Field(default=1, ge=1, le=1)
    models_dir: str = Field(
        default=DEFAULT_MODELS_REFERENCE,
        min_length=1,
        max_length=4096,
        description=(
            "Verified local ADMET-AI v2 ensemble directory; never downloaded. Accepts "
            "'package:admet_ai/<relative path>' so an exported cascade stays portable."
        ),
    )
    expected_model_manifest_sha256: str = Field(
        description="Required digest from inspect_admet_ai_model_assets()."
    )
    expected_package_code_sha256: str = Field(
        description="Required digest from inspect_admet_ai_installation()."
    )
    endpoints: tuple[ADMETEndpoint, ...] = Field(
        min_length=1,
        max_length=256,
        description="Exact ADMET-AI output-column to endpoint-ID mappings.",
    )
    batch_size: int = Field(default=256, ge=1, le=8192)
    num_workers: int = Field(default=0, ge=0, le=64)
    allow_unsafe_model_deserialization: bool = Field(
        default=False,
        description=(
            "Explicit trust acknowledgement for the pinned package and Torch checkpoints."
        ),
    )
    expected_checkpoints: tuple[ExpectedCheckpointHash, ...] = ()
    max_model_files: int = Field(default=10_000, ge=1, le=1_000_000)
    max_model_bytes: int = Field(
        default=20 * 1024 * 1024 * 1024,
        ge=1,
        le=1024 * 1024 * 1024 * 1024,
    )
    max_package_code_files: int = Field(default=10_000, ge=1, le=1_000_000)
    max_package_code_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=1,
        le=64 * 1024 * 1024 * 1024,
    )

    @field_validator("endpoints", "expected_checkpoints", mode="before")
    @classmethod
    def _arrays_to_tuples(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator(
        "expected_model_manifest_sha256",
        "expected_package_code_sha256",
    )
    @classmethod
    def _hash_is_valid(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("expected SHA-256 must contain 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def _identities_are_unique(self) -> Self:
        columns = [endpoint.output_column for endpoint in self.endpoints]
        endpoint_ids = [endpoint.endpoint_id for endpoint in self.endpoints]
        checkpoint_paths = [item.relative_path for item in self.expected_checkpoints]
        if len(columns) != len(set(columns)):
            raise ValueError("ADMET-AI output columns must be unique")
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("endpoint_id values must be unique")
        if len(checkpoint_paths) != len(set(checkpoint_paths)):
            raise ValueError("expected checkpoint paths must be unique")
        return self


def _validated_config(request: StageRequest) -> ADMETAIConfig:
    try:
        return ADMETAIConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid ADMET-AI configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _package_relative_root(reference: str) -> Path:
    """Resolve ``package:admet_ai/<relative path>`` against the installed wheel.

    ADMET-AI v2 ships its checkpoints inside the distribution rather than as a
    separate download, so the bytes are already on any machine that pip-installed
    it.  What is *not* portable is where they landed: a conda prefix on a laptop
    is not the conda prefix on an eight-GPU node, and an absolute site-packages
    path baked into an exported cascade would be wrong the first time the config
    travelled.  This reference means the same thing in both places.

    It is deliberately not a general-purpose loader.  Only ``admet_ai`` may be
    named, because that is the only package this adapter knows how to run, and
    the remainder must be a canonical relative path that stays inside the
    package.  Nothing here weakens the pins: the directory this returns is
    hashed and compared against ``expected_model_manifest_sha256`` exactly as a
    hand-written absolute path would be.
    """

    remainder = reference[len(_PACKAGE_REFERENCE_PREFIX) :]
    package_name, separator, relative = remainder.partition("/")
    if package_name != _PACKAGE_REFERENCE_NAME:
        raise PluginError(
            "only the admet_ai package may be named in a models_dir package reference",
            code="ADMET_AI_MODEL_PATH_INVALID",
            context={"package": package_name},
            hint=f"Use '{_PACKAGE_REFERENCE_PREFIX}{_PACKAGE_REFERENCE_NAME}/...'.",
        )
    path = PurePosixPath(relative)
    if (
        not separator
        or relative != path.as_posix()
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PluginError(
            "a models_dir package reference must name a canonical relative path",
            code="ADMET_AI_MODEL_PATH_INVALID",
            context={"reference": reference},
        )
    try:
        specification = importlib.util.find_spec(_PACKAGE_REFERENCE_NAME)
    except (ImportError, AttributeError, ValueError) as error:
        raise PluginError(
            "ADMET-AI v2 cannot be discovered in this Python environment",
            code="ADMET_AI_BACKEND_UNAVAILABLE",
            hint="Install ADMET-AI v2 in the environment that runs the screen.",
        ) from error
    locations = tuple(specification.submodule_search_locations or ()) if specification else ()
    if specification is None or len(locations) != 1:
        raise PluginError(
            "ADMET-AI v2 is unavailable or has an unsupported package layout",
            code="ADMET_AI_BACKEND_UNAVAILABLE",
            hint="Install one local ADMET-AI v2 package in this environment.",
        )
    package_root = Path(locations[0])
    resolved_package_root = package_root.resolve()
    resolved = (package_root / Path(*path.parts)).resolve()
    if resolved != resolved_package_root and resolved_package_root not in resolved.parents:
        raise PluginError(
            "a models_dir package reference resolved outside the admet_ai package",
            code="ADMET_AI_MODEL_PATH_INVALID",
            context={"reference": reference, "resolved": str(resolved)},
        )
    return resolved


def _local_model_root(value: str) -> Path:
    if "\x00" in value:
        raise PluginError(
            "ADMET-AI models_dir contains a null byte",
            code="ADMET_AI_MODEL_PATH_INVALID",
        )
    if value.startswith(_PACKAGE_REFERENCE_PREFIX):
        value = str(_package_relative_root(value))
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise PluginError(
            "ADMET-AI models_dir cannot be a symbolic link",
            code="ADMET_AI_MODEL_PATH_INVALID",
            context={"path": str(candidate)},
        )
    try:
        root = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise PluginError(
            "ADMET-AI models_dir was not found or cannot be resolved",
            code="ADMET_AI_MODEL_PATH_INVALID",
            context={"path": str(candidate)},
        ) from error
    if not root.is_dir():
        raise PluginError(
            "ADMET-AI models_dir must identify a local directory",
            code="ADMET_AI_MODEL_PATH_INVALID",
            context={"path": str(candidate)},
        )
    return root


def _walk_regular_files(
    root: Path,
    *,
    maximum_files: int | None = None,
) -> list[tuple[str, Path]]:
    """Return a sorted, symlink-free regular-file inventory."""

    inventory: list[tuple[str, Path]] = []

    def walk_error(error: OSError) -> None:
        raise PluginError(
            "could not traverse an ADMET-AI asset tree",
            code="ADMET_AI_ASSET_READ_FAILED",
            context={
                "path": str(error.filename or root),
                "error_type": type(error).__name__,
            },
        ) from error

    for directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
        onerror=walk_error,
    ):
        directory_path = Path(directory)
        directory_names.sort()
        file_names.sort()
        for name in tuple(directory_names):
            path = directory_path / name
            try:
                mode = path.lstat().st_mode
            except OSError as error:
                raise PluginError(
                    "could not inspect an ADMET-AI asset directory",
                    code="ADMET_AI_ASSET_READ_FAILED",
                    context={"path": str(path), "error_type": type(error).__name__},
                ) from error
            if stat.S_ISLNK(mode):
                raise PluginError(
                    "ADMET-AI asset trees cannot contain symbolic links",
                    code="ADMET_AI_ASSET_SYMLINK_REJECTED",
                    context={"path": str(path)},
                )
            if not stat.S_ISDIR(mode):
                raise PluginError(
                    "ADMET-AI asset tree contains a non-directory entry",
                    code="ADMET_AI_ASSET_TYPE_INVALID",
                    context={"path": str(path)},
                )
        for name in file_names:
            path = directory_path / name
            try:
                mode = path.lstat().st_mode
            except OSError as error:
                raise PluginError(
                    "could not inspect an ADMET-AI asset file",
                    code="ADMET_AI_ASSET_READ_FAILED",
                    context={"path": str(path), "error_type": type(error).__name__},
                ) from error
            if stat.S_ISLNK(mode):
                raise PluginError(
                    "ADMET-AI asset trees cannot contain symbolic links",
                    code="ADMET_AI_ASSET_SYMLINK_REJECTED",
                    context={"path": str(path)},
                )
            if not stat.S_ISREG(mode):
                raise PluginError(
                    "ADMET-AI asset tree contains a non-regular file",
                    code="ADMET_AI_ASSET_TYPE_INVALID",
                    context={"path": str(path)},
                )
            relative = path.relative_to(root).as_posix()
            inventory.append((relative, path))
            if maximum_files is not None and len(inventory) > maximum_files:
                raise PluginError(
                    "ADMET-AI asset count exceeds the configured limit",
                    code="ADMET_AI_ASSET_LIMIT_EXCEEDED",
                    context={"file_count_at_least": len(inventory), "limit": maximum_files},
                )
    inventory.sort(key=lambda item: item[0])
    return inventory


def _copy_and_hash_file(
    source: Path,
    destination: Path,
    *,
    maximum_bytes: int,
) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    digest = hashlib.sha256()
    copied = 0
    try:
        descriptor = os.open(source, flags)
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise PluginError(
                "ADMET-AI model asset is not a regular file",
                code="ADMET_AI_ASSET_TYPE_INVALID",
                context={"path": str(source)},
            )
        if initial.st_size > maximum_bytes:
            raise PluginError(
                "ADMET-AI model assets exceed the configured byte limit",
                code="ADMET_AI_MODEL_ASSET_LIMIT_EXCEEDED",
                context={
                    "path": str(source),
                    "size_bytes": initial.st_size,
                    "remaining_limit_bytes": maximum_bytes,
                },
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            with destination.open("xb") as output:
                while block := stream.read(_COPY_CHUNK_SIZE):
                    digest.update(block)
                    output.write(block)
                    copied += len(block)
        if copied != initial.st_size:
            raise PluginError(
                "ADMET-AI model asset changed size while being snapshotted",
                code="ADMET_AI_ASSET_CHANGED_DURING_READ",
                context={
                    "path": str(source),
                    "initial_size_bytes": initial.st_size,
                    "copied_size_bytes": copied,
                },
            )
    except PluginError:
        raise
    except OSError as error:
        raise PluginError(
            "could not snapshot an ADMET-AI model asset",
            code="ADMET_AI_ASSET_READ_FAILED",
            context={"path": str(source), "error_type": type(error).__name__},
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return digest.hexdigest(), copied


def _validate_v2_model_layout(
    source_root: Path,
    checkpoints: tuple[str, ...],
) -> None:
    """Mirror the v2 loader's directory/``**/*.pt`` discovery assumptions."""

    try:
        with os.scandir(source_root) as scanner:
            entries = tuple(sorted(scanner, key=lambda item: item.name))
    except OSError as error:
        raise PluginError(
            "could not inspect the ADMET-AI v2 ensemble directories",
            code="ADMET_AI_ASSET_READ_FAILED",
            context={"path": str(source_root), "error_type": type(error).__name__},
        ) from error
    if not entries:
        raise PluginError(
            "ADMET-AI models_dir contains no ensemble directories",
            code="ADMET_AI_MODEL_LAYOUT_INVALID",
        )
    checkpoint_ensembles = {
        PurePosixPath(relative).parts[0]
        for relative in checkpoints
        if len(PurePosixPath(relative).parts) >= 2
    }
    for entry in entries:
        try:
            is_directory = entry.is_dir(follow_symlinks=False)
        except OSError as error:
            raise PluginError(
                "could not inspect an ADMET-AI v2 ensemble entry",
                code="ADMET_AI_ASSET_READ_FAILED",
                context={"path": entry.path, "error_type": type(error).__name__},
            ) from error
        if not is_directory:
            raise PluginError(
                "ADMET-AI v2 models_dir may contain ensemble directories only",
                code="ADMET_AI_MODEL_LAYOUT_INVALID",
                context={"path": entry.path},
            )
        if entry.name not in checkpoint_ensembles:
            raise PluginError(
                "an ADMET-AI v2 ensemble directory contains no .pt checkpoint",
                code="ADMET_AI_MODEL_LAYOUT_INVALID",
                context={"path": entry.path},
            )


def _snapshot_model_tree(
    source_root: Path,
    snapshot_root: Path,
    *,
    maximum_files: int,
    maximum_bytes: int,
) -> tuple[str, tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    if snapshot_root.exists() or snapshot_root.is_symlink():
        raise PluginError(
            "ADMET-AI model snapshot already exists in staging",
            code="PLUGIN_STAGING_NOT_EMPTY",
            context={"path": str(snapshot_root)},
        )
    inventory = _walk_regular_files(source_root, maximum_files=maximum_files)
    if not inventory:
        raise PluginError(
            "ADMET-AI models_dir contains no files",
            code="ADMET_AI_MODEL_ASSETS_EMPTY",
        )
    if len(inventory) > maximum_files:
        raise PluginError(
            "ADMET-AI model asset count exceeds the configured limit",
            code="ADMET_AI_MODEL_ASSET_LIMIT_EXCEEDED",
            context={"file_count": len(inventory), "limit": maximum_files},
        )
    discovered_checkpoints = tuple(
        relative
        for relative, _ in inventory
        if PurePosixPath(relative).suffix.casefold() in _CHECKPOINT_SUFFIXES
    )
    if not discovered_checkpoints:
        raise PluginError(
            "ADMET-AI models_dir contains no recognized local checkpoint files",
            code="ADMET_AI_CHECKPOINTS_MISSING",
            hint="Provision ADMET-AI v2 .pt checkpoints locally; MolCascade never downloads them.",
        )
    _validate_v2_model_layout(source_root, discovered_checkpoints)
    snapshot_root.mkdir(parents=True, exist_ok=False)
    total_bytes = 0
    manifest: list[dict[str, object]] = []
    checkpoints: list[dict[str, object]] = []
    try:
        for relative, source in inventory:
            sha256, size = _copy_and_hash_file(
                source,
                snapshot_root.joinpath(*PurePosixPath(relative).parts),
                maximum_bytes=maximum_bytes - total_bytes,
            )
            total_bytes += size
            if total_bytes > maximum_bytes:
                raise PluginError(
                    "ADMET-AI model assets exceed the configured byte limit",
                    code="ADMET_AI_MODEL_ASSET_LIMIT_EXCEEDED",
                    context={"size_bytes": total_bytes, "limit_bytes": maximum_bytes},
                )
            item: dict[str, object] = {
                "relative_path": relative,
                "sha256": sha256,
                "size_bytes": size,
            }
            manifest.append(item)
            if PurePosixPath(relative).suffix.casefold() in _CHECKPOINT_SUFFIXES:
                checkpoints.append(dict(item))
    except BaseException:
        shutil.rmtree(snapshot_root, ignore_errors=True)
        raise
    if not checkpoints:
        shutil.rmtree(snapshot_root, ignore_errors=True)
        raise PluginError(
            "ADMET-AI models_dir contains no recognized local checkpoint files",
            code="ADMET_AI_CHECKPOINTS_MISSING",
            hint="Provision ADMET-AI v2 .pt checkpoints locally; MolCascade never downloads them.",
        )
    identity = canonical_sha256(manifest)
    return identity, tuple(manifest), tuple(checkpoints)


def _hash_existing_tree(
    root: Path,
    *,
    maximum_files: int,
    maximum_bytes: int,
    suffixes: set[str] | None = None,
) -> tuple[str, tuple[dict[str, object], ...]]:
    inventory = [
        item
        for item in _walk_regular_files(root, maximum_files=maximum_files)
        if suffixes is None or item[1].suffix.casefold() in suffixes
    ]
    if not inventory:
        raise PluginError(
            "ADMET-AI package/model inventory contains no hashable files",
            code="ADMET_AI_ASSET_INVENTORY_EMPTY",
            context={"path": str(root)},
        )
    if len(inventory) > maximum_files:
        raise PluginError(
            "ADMET-AI package/model file count exceeds the configured limit",
            code="ADMET_AI_ASSET_LIMIT_EXCEEDED",
            context={"file_count": len(inventory), "limit": maximum_files},
        )
    total_bytes = 0
    manifest: list[dict[str, object]] = []
    for relative, path in inventory:
        digest = hashlib.sha256()
        size = 0
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            initial = os.fstat(descriptor)
            if not stat.S_ISREG(initial.st_mode):
                raise OSError("not a regular file")
            if initial.st_size > maximum_bytes - total_bytes:
                raise PluginError(
                    "ADMET-AI package/model bytes exceed the configured limit",
                    code="ADMET_AI_ASSET_LIMIT_EXCEEDED",
                    context={
                        "path": str(path),
                        "size_bytes": initial.st_size,
                        "remaining_limit_bytes": maximum_bytes - total_bytes,
                    },
                )
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = None
                while block := stream.read(_COPY_CHUNK_SIZE):
                    digest.update(block)
                    size += len(block)
            if size != initial.st_size:
                raise PluginError(
                    "ADMET-AI package/model file changed size while being hashed",
                    code="ADMET_AI_ASSET_CHANGED_DURING_READ",
                    context={"path": str(path)},
                )
        except PluginError:
            raise
        except OSError as error:
            raise PluginError(
                "could not hash an ADMET-AI package/model file",
                code="ADMET_AI_ASSET_READ_FAILED",
                context={"path": str(path), "error_type": type(error).__name__},
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        total_bytes += size
        if total_bytes > maximum_bytes:
            raise PluginError(
                "ADMET-AI package/model bytes exceed the configured limit",
                code="ADMET_AI_ASSET_LIMIT_EXCEEDED",
                context={"size_bytes": total_bytes, "limit_bytes": maximum_bytes},
            )
        manifest.append(
            {"relative_path": relative, "sha256": digest.hexdigest(), "size_bytes": size}
        )
    return canonical_sha256(manifest), tuple(manifest)


def _discover_package_provenance(
    *,
    maximum_files: int,
    maximum_bytes: int,
) -> dict[str, object]:
    try:
        specification = importlib.util.find_spec("admet_ai")
    except (ImportError, AttributeError, ValueError) as error:
        raise PluginError(
            "ADMET-AI v2 cannot be discovered in this Python environment",
            code="ADMET_AI_BACKEND_UNAVAILABLE",
            hint="Install ADMET-AI v2 yourself and provision its model assets locally.",
        ) from error
    locations = tuple(specification.submodule_search_locations or ()) if specification else ()
    if specification is None or len(locations) != 1:
        raise PluginError(
            "ADMET-AI v2 is unavailable or has an unsupported package layout",
            code="ADMET_AI_BACKEND_UNAVAILABLE",
            hint="Install one local ADMET-AI v2 package in this environment.",
        )
    unresolved_package_root = Path(locations[0])
    if unresolved_package_root.is_symlink():
        raise PluginError(
            "ADMET-AI package root cannot be a symbolic link",
            code="ADMET_AI_PACKAGE_INVALID",
            context={"path": str(unresolved_package_root)},
        )
    try:
        package_root = unresolved_package_root.resolve(strict=True)
    except OSError as error:
        raise PluginError(
            "ADMET-AI package root cannot be resolved",
            code="ADMET_AI_PACKAGE_INVALID",
            context={"path": str(unresolved_package_root)},
        ) from error
    if not package_root.is_dir():
        raise PluginError(
            "ADMET-AI package root is not a regular local directory",
            code="ADMET_AI_PACKAGE_INVALID",
            context={"path": str(package_root)},
        )
    try:
        distribution = importlib.metadata.distribution("admet-ai")
        version = str(distribution.version)
    except importlib.metadata.PackageNotFoundError as error:
        raise PluginError(
            "ADMET-AI distribution metadata is unavailable",
            code="ADMET_AI_BACKEND_UNAVAILABLE",
        ) from error
    if re.fullmatch(r"2(?:\.[0-9A-Za-z+-]+)+", version) is None:
        raise PluginError(
            "this adapter supports ADMET-AI major version 2 only",
            code="ADMET_AI_VERSION_UNSUPPORTED",
            context={"version": version},
        )
    package_hash, _ = _hash_existing_tree(
        package_root,
        maximum_files=maximum_files,
        maximum_bytes=maximum_bytes,
        suffixes=_PACKAGE_CODE_SUFFIXES,
    )
    try:
        record_text = distribution.read_text("RECORD")
    except (OSError, UnicodeError) as error:
        raise PluginError(
            "ADMET-AI distribution metadata could not be read",
            code="ADMET_AI_PACKAGE_INVALID",
            context={"error_type": type(error).__name__},
        ) from error
    record_sha256 = (
        hashlib.sha256(record_text.encode("utf-8")).hexdigest()
        if record_text is not None
        else None
    )
    dependency_versions: dict[str, str | None] = {}
    for name in _SCIENTIFIC_DISTRIBUTIONS:
        try:
            dependency_versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependency_versions[name] = None
    return {
        "package_root": package_root,
        "version": version,
        "package_code_sha256": package_hash,
        "distribution_record_sha256": record_sha256,
        "dependency_versions": dependency_versions,
    }


def _package_provenance(config: ADMETAIConfig) -> dict[str, object]:
    return _discover_package_provenance(
        maximum_files=config.max_package_code_files,
        maximum_bytes=config.max_package_code_bytes,
    )


def installed_admet_ai_version() -> str | None:
    """Return the installed admet-ai version, or ``None`` when it is absent.

    Deliberately the cheapest possible question about this backend: it reads
    distribution metadata and nothing else.  :func:`inspect_admet_ai_installation`
    answers a stronger one but hashes the whole package tree to do it, and
    :func:`inspect_admet_ai_model_assets` hashes gigabytes of checkpoints, which
    is far too much work for a caller that only wants to decide whether to
    offer the backend at all.
    """

    try:
        return str(importlib.metadata.version("admet-ai"))
    except importlib.metadata.PackageNotFoundError:
        return None


def inspect_admet_ai_installation(
    *,
    max_package_code_files: int = 10_000,
    max_package_code_bytes: int = 2 * 1024 * 1024 * 1024,
) -> dict[str, object]:
    """Inspect installed ADMET-AI v2 code without importing the package."""

    if not 1 <= max_package_code_files <= 1_000_000:
        raise ValueError("max_package_code_files must be between 1 and 1,000,000")
    if not 1 <= max_package_code_bytes <= 64 * 1024 * 1024 * 1024:
        raise ValueError("max_package_code_bytes must be between 1 byte and 64 GiB")
    provenance = _discover_package_provenance(
        maximum_files=max_package_code_files,
        maximum_bytes=max_package_code_bytes,
    )
    return {
        **provenance,
        "package_root": str(provenance["package_root"]),
    }


def inspect_admet_ai_model_assets(
    models_dir: str | Path,
    *,
    max_model_files: int = 10_000,
    max_model_bytes: int = 20 * 1024 * 1024 * 1024,
) -> dict[str, object]:
    """Hash a local model tree without importing or executing ADMET-AI.

    The returned manifest identity is the value users pin in
    ``expected_model_manifest_sha256``.  This inspection reads only regular,
    symlink-free files and never deserializes a checkpoint.
    """

    if not 1 <= max_model_files <= 1_000_000:
        raise ValueError("max_model_files must be between 1 and 1,000,000")
    if not 1 <= max_model_bytes <= 1024 * 1024 * 1024 * 1024:
        raise ValueError("max_model_bytes must be between 1 byte and 1 TiB")
    root = _local_model_root(str(models_dir))
    manifest_sha256, manifest = _hash_existing_tree(
        root,
        maximum_files=max_model_files,
        maximum_bytes=max_model_bytes,
    )
    checkpoints = tuple(
        dict(item)
        for item in manifest
        if PurePosixPath(str(item["relative_path"])).suffix.casefold()
        in _CHECKPOINT_SUFFIXES
    )
    if not checkpoints:
        raise PluginError(
            "ADMET-AI models_dir contains no recognized local checkpoint files",
            code="ADMET_AI_CHECKPOINTS_MISSING",
            hint="Provision ADMET-AI v2 .pt checkpoints locally; MolCascade never downloads them.",
        )
    _validate_v2_model_layout(
        root,
        tuple(str(item["relative_path"]) for item in checkpoints),
    )
    return {
        "models_dir": str(root),
        "model_manifest_sha256": manifest_sha256,
        "model_file_count": len(manifest),
        "model_size_bytes": sum(int(item["size_bytes"]) for item in manifest),
        "checkpoint_manifest_sha256": canonical_sha256(checkpoints),
        "checkpoint_count": len(checkpoints),
        "checkpoints": [dict(item) for item in checkpoints],
    }


def _verify_expected_hashes(
    config: ADMETAIConfig,
    *,
    package_hash: str,
    model_hash: str,
    checkpoints: tuple[dict[str, object], ...],
) -> None:
    if config.expected_package_code_sha256 != package_hash:
        raise PluginError(
            "ADMET-AI package code SHA-256 does not match the configured pin",
            code="ADMET_AI_PACKAGE_HASH_MISMATCH",
            context={
                "expected_sha256": config.expected_package_code_sha256,
                "actual_sha256": package_hash,
            },
        )
    if config.expected_model_manifest_sha256 != model_hash:
        raise PluginError(
            "ADMET-AI model manifest SHA-256 does not match the configured pin",
            code="ADMET_AI_MODEL_HASH_MISMATCH",
            context={
                "expected_sha256": config.expected_model_manifest_sha256,
                "actual_sha256": model_hash,
            },
        )
    actual = {str(item["relative_path"]): str(item["sha256"]) for item in checkpoints}
    for expected in config.expected_checkpoints:
        digest = actual.get(expected.relative_path)
        if digest is None:
            raise PluginError(
                "a pinned ADMET-AI checkpoint is missing",
                code="ADMET_AI_CHECKPOINT_MISSING",
                context={"relative_path": expected.relative_path},
            )
        if digest != expected.sha256:
            raise PluginError(
                "an ADMET-AI checkpoint SHA-256 does not match the configured pin",
                code="ADMET_AI_CHECKPOINT_HASH_MISMATCH",
                context={
                    "relative_path": expected.relative_path,
                    "expected_sha256": expected.sha256,
                    "actual_sha256": digest,
                },
            )


def _prediction_rows(
    result: object,
    *,
    parent_ids: list[str],
    input_smiles: list[str],
    endpoints: tuple[ADMETEndpoint, ...],
    model_id: str,
) -> list[dict[str, object]]:
    try:
        columns = [str(column) for column in result.columns]  # type: ignore[attr-defined]
    except Exception as error:
        raise PluginError(
            "ADMET-AI predict() did not return a DataFrame-like object",
            code="ADMET_AI_OUTPUT_INVALID",
        ) from error
    if len(columns) != len(set(columns)):
        raise PluginError(
            "ADMET-AI prediction output contains duplicate columns",
            code="ADMET_AI_OUTPUT_INVALID",
        )
    missing = [
        endpoint.output_column
        for endpoint in endpoints
        if endpoint.output_column not in columns
    ]
    if missing:
        raise PluginError(
            "configured ADMET-AI endpoint columns are missing from prediction output",
            code="ADMET_AI_ENDPOINT_MISSING",
            context={"missing_columns": missing, "available_columns": columns},
        )
    try:
        result_index = result.index  # type: ignore[attr-defined]
    except AttributeError:
        result_index = None
    except Exception as error:
        raise PluginError(
            "ADMET-AI prediction output index cannot be inspected",
            code="ADMET_AI_OUTPUT_INVALID",
        ) from error
    if result_index is not None:
        try:
            indexed_smiles = list(result_index)
        except Exception as error:
            raise PluginError(
                "ADMET-AI prediction output index cannot be converted to a sequence",
                code="ADMET_AI_OUTPUT_INVALID",
            ) from error
        if len(indexed_smiles) != len(input_smiles):
            raise PluginError(
                "ADMET-AI changed prediction-index cardinality",
                code="ADMET_AI_OUTPUT_COUNT_MISMATCH",
                context={
                    "input_count": len(input_smiles),
                    "output_index_count": len(indexed_smiles),
                },
            )
        mismatch = next(
            (
                (index, expected, actual)
                for index, (expected, actual) in enumerate(
                    zip(input_smiles, indexed_smiles, strict=True)
                )
                if not isinstance(actual, str) or actual != expected
            ),
            None,
        )
        if mismatch is not None:
            position, expected, actual = mismatch
            raise PluginError(
                "ADMET-AI reordered or relabeled prediction rows",
                code="ADMET_AI_OUTPUT_INDEX_MISMATCH",
                hint="Prediction rows must retain the exact input-SMILES order.",
                context={
                    "position": position,
                    "expected_smiles": expected,
                    "actual_index": str(actual),
                },
            )
    try:
        records = result.to_dict(orient="records")  # type: ignore[attr-defined]
    except Exception as error:
        raise PluginError(
            "ADMET-AI prediction output cannot be converted to records",
            code="ADMET_AI_OUTPUT_INVALID",
        ) from error
    if not isinstance(records, list) or len(records) != len(parent_ids):
        raise PluginError(
            "ADMET-AI changed prediction row cardinality",
            code="ADMET_AI_OUTPUT_COUNT_MISMATCH",
            context={
                "input_count": len(parent_ids),
                "output_count": len(records) if isinstance(records, list) else -1,
            },
        )
    output: list[dict[str, object]] = []
    for parent_id, record in zip(parent_ids, records, strict=True):
        if not isinstance(record, Mapping):
            raise PluginError(
                "ADMET-AI prediction output contains a non-record row",
                code="ADMET_AI_OUTPUT_INVALID",
                context={"parent_id": parent_id},
            )
        for endpoint in endpoints:
            value = record.get(endpoint.output_column)
            if isinstance(value, bool):
                raise PluginError(
                    "ADMET-AI returned a Boolean instead of a numeric prediction",
                    code="ADMET_AI_PREDICTION_INVALID",
                    context={"parent_id": parent_id, "endpoint_id": endpoint.endpoint_id},
                )
            try:
                prediction = float(value)
            except (TypeError, ValueError, OverflowError) as error:
                raise PluginError(
                    "ADMET-AI returned a non-numeric prediction",
                    code="ADMET_AI_PREDICTION_INVALID",
                    context={"parent_id": parent_id, "endpoint_id": endpoint.endpoint_id},
                ) from error
            if not math.isfinite(prediction):
                raise PluginError(
                    "ADMET-AI returned a non-finite prediction",
                    code="ADMET_AI_PREDICTION_INVALID",
                    context={"parent_id": parent_id, "endpoint_id": endpoint.endpoint_id},
                )
            output.append(
                {
                    "parent_id": parent_id,
                    "endpoint_id": endpoint.endpoint_id,
                    "model_id": model_id,
                    "prediction_mean": prediction,
                    "prediction_std": None,
                    "interval_lower": None,
                    "interval_upper": None,
                    "calibration_id": None,
                }
            )
    return output


@cache
def _loaded_model(snapshot_root: str, num_workers: int) -> Any:
    """Build the ADMET-AI model once per worker process.

    Cached because a lane is handed shard after shard and this loads a Chemprop
    ensemble per endpoint group -- hundreds of megabytes off disk and onto the
    card.  Paying that per shard would cost more than the inference it enables.

    The snapshot path is the key rather than the configuration, because the
    parent has already verified those bytes against the pinned digests: two
    stages that snapshot different trees get different models, and two shards of
    one stage get the same one.

    ``ADMETModel`` takes no device argument, which is exactly why the shard
    runner pins ``CUDA_VISIBLE_DEVICES`` in the child before this is reached --
    it is the only way this library can be aimed at a card that is not the first.
    """

    try:
        module = importlib.import_module("admet_ai")
        model_class = module.ADMETModel
    except (ImportError, AttributeError) as error:
        raise PluginError(
            "ADMET-AI v2 could not be imported or does not expose ADMETModel",
            code="ADMET_AI_BACKEND_UNAVAILABLE",
            context={"error_type": type(error).__name__},
        ) from error
    if not callable(model_class):
        raise PluginError(
            "ADMET-AI ADMETModel is not callable",
            code="ADMET_AI_API_INCOMPATIBLE",
        )
    try:
        return model_class(
            models_dir=Path(snapshot_root),
            include_physchem=False,
            drugbank_path=None,
            num_workers=num_workers,
        )
    except Exception as error:
        raise PluginError(
            "ADMET-AI v2 could not load the local model snapshot",
            code="ADMET_AI_MODEL_LOAD_FAILED",
            context={"error_type": type(error).__name__},
        ) from error


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Predict one contiguous range of parents on whichever lane owns it."""

    settings = dict(task.config)
    runtime = dict(settings.pop(_RUNTIME_KEY))
    config = ADMETAIConfig.model_validate(settings)
    model_id = str(runtime["model_id"])
    model = _loaded_model(str(runtime["snapshot_root"]), config.num_workers)

    input_count = 0
    prediction_count = 0
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
            try:
                result = model.predict(smiles)
            except Exception as error:
                raise PluginError(
                    "ADMET-AI v2 prediction failed",
                    code="ADMET_AI_PREDICTION_FAILED",
                    context={
                        "batch_size": len(smiles),
                        "error_type": type(error).__name__,
                    },
                ) from error
            predictions = _prediction_rows(
                result,
                parent_ids=parent_ids,
                input_smiles=smiles,
                endpoints=config.endpoints,
                model_id=model_id,
            )
            parent_writer.write_batch(batch)
            prediction_writer.write_table(
                pa.Table.from_pylist(predictions, schema=PREDICTION_V1.schema)
            )
            input_count += batch.num_rows
            prediction_count += len(predictions)
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": input_count, "predictions": prediction_count},
    )


class ADMETAIV2PredictorPlugin:
    """Run explicitly trusted, locally provisioned ADMET-AI v2 checkpoints."""

    descriptor = PluginDescriptor(
        id="prediction.admet_ai_v2",
        version="0.1.0",
        kind=PluginKind.PREDICTOR,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, PREDICTION_V1.id),
        output_ports={"primary": PARENT_V1.id, "predictions": PREDICTION_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        # The model bytes are hashed and checked, but Torch/GPU kernels do not
        # promise bit-for-bit reproducibility across every supported platform.
        determinism=Determinism.BEST_EFFORT,
        display_name="ADMET-AI v2 local predictor",
        description=(
            "Optional multi-endpoint ADMET prediction from user-provisioned local "
            "checkpoints; requires exact package/model pins and explicit "
            "model-deserialization trust."
        ),
    )
    config_model = ADMETAIConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        if not config.allow_unsafe_model_deserialization:
            raise PluginError(
                (
                    "ADMET-AI uses Torch model deserialization, which can execute "
                    "code; explicit trust is required"
                ),
                code="ADMET_AI_MODEL_TRUST_REQUIRED",
                hint=(
                    "Verify the local package code, model tree, and checkpoint hashes, then set "
                    "allow_unsafe_model_deserialization: true only for trusted assets."
                ),
                context={"backend": "admet-ai", "risk": "executable-model-artifacts"},
            )
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        model_root = _local_model_root(config.models_dir)
        context.staging_root.mkdir(parents=True, exist_ok=True)
        snapshot_root = context.staging_root / _MODEL_SNAPSHOT

        package = _package_provenance(config)
        package_root = package["package_root"]
        assert isinstance(package_root, Path)
        package_hash = str(package["package_code_sha256"])
        model_hash = ""
        model_manifest: tuple[dict[str, object], ...] = ()
        checkpoints: tuple[dict[str, object], ...] = ()
        input_count = 0
        prediction_count = 0
        model_id = ""
        staged: list[Path] = []
        try:
            model_hash, model_manifest, checkpoints = _snapshot_model_tree(
                model_root,
                snapshot_root,
                maximum_files=config.max_model_files,
                maximum_bytes=config.max_model_bytes,
            )
            _verify_expected_hashes(
                config,
                package_hash=package_hash,
                model_hash=model_hash,
                checkpoints=checkpoints,
            )
            model_id = "admet-ai-v2:sha256:" + canonical_sha256(
                {
                    "backend_version": package["version"],
                    "package_code_sha256": package_hash,
                    "distribution_record_sha256": package["distribution_record_sha256"],
                    "dependency_versions": package.get("dependency_versions", {}),
                    "model_manifest_sha256": model_hash,
                    "checkpoint_sha256": [
                        (item["relative_path"], item["sha256"]) for item in checkpoints
                    ],
                    "include_physchem": False,
                    "drugbank_reference": None,
                }
            )
            # The lanes read the verified snapshot, not ``models_dir``: the
            # bytes under a shard's model directory have to be the bytes that
            # were hashed a moment ago, and the tree the user points at is
            # outside this run's control.
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
                        "snapshot_root": str(snapshot_root),
                        "model_id": model_id,
                    },
                },
                shard_rows=_SHARD_ROWS,
            )
            staged = [
                context.staging_root / name
                for names in sharded.file_paths.values()
                for name in names
            ]
            input_count = sharded.rows_in
            prediction_count = sharded.rows_out.get("predictions", 0)
            if input_count == 0:
                raise PluginError(
                    "ADMET-AI input contains no parents",
                    code="ADMET_AI_EMPTY_INPUT",
                )

            post_model_hash, _ = _hash_existing_tree(
                snapshot_root,
                maximum_files=config.max_model_files,
                maximum_bytes=config.max_model_bytes,
            )
            post_package_hash, _ = _hash_existing_tree(
                package_root,
                maximum_files=config.max_package_code_files,
                maximum_bytes=config.max_package_code_bytes,
                suffixes=_PACKAGE_CODE_SUFFIXES,
            )
            if post_model_hash != model_hash:
                raise PluginError(
                    "ADMET-AI model snapshot changed during inference",
                    code="ADMET_AI_MODEL_MUTATED",
                )
            if post_package_hash != package_hash:
                raise PluginError(
                    "ADMET-AI package code changed during inference",
                    code="ADMET_AI_PACKAGE_MUTATED",
                )
        except BaseException:
            # Staging is discarded by the runner when a stage fails, but a
            # prediction written by a model that turned out to have moved
            # underneath the run should not survive even that long.
            for path in staged:
                path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(snapshot_root, ignore_errors=True)

        checkpoint_metadata = [dict(item) for item in checkpoints]
        checkpoint_manifest_sha256 = canonical_sha256(checkpoints)
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    sharded.file_paths["primary"],
                    {"row_count": input_count},
                ),
                "predictions": PendingOutput(
                    PREDICTION_V1.id,
                    sharded.file_paths["predictions"],
                    {
                        "row_count": prediction_count,
                        "model_id": model_id,
                        "endpoint_count": len(config.endpoints),
                    },
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": input_count,
                "prediction_count": prediction_count,
                "backend": "admet-ai",
                "backend_version": str(package["version"]),
                "model_id": model_id,
                "package_code_sha256": package_hash,
                "distribution_record_sha256": package["distribution_record_sha256"],
                "dependency_versions": package.get("dependency_versions", {}),
                "model_manifest_sha256": model_hash,
                "model_file_count": len(model_manifest),
                "model_size_bytes": sum(
                    int(item["size_bytes"]) for item in model_manifest
                ),
                "checkpoint_manifest_sha256": checkpoint_manifest_sha256,
                "checkpoint_count": len(checkpoints),
                "checkpoint_hashes": checkpoint_metadata,
                "endpoint_bindings": [
                    endpoint.model_dump(mode="json") for endpoint in config.endpoints
                ],
                "model_deserialization_trust_acknowledged": True,
                "model_assets_snapshotted": True,
                "uncertainty_available": False,
                "calibration_available": False,
                "network_or_download_invoked_by_adapter": False,
                **sharded.response_metadata(),
            },
        )


__all__ = [
    "DEFAULT_MODELS_REFERENCE",
    "ADMETAIConfig",
    "ADMETAIV2PredictorPlugin",
    "ADMETEndpoint",
    "ExpectedCheckpointHash",
    "inspect_admet_ai_installation",
    "inspect_admet_ai_model_assets",
]
