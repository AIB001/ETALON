"""Deterministic stage invocation keys and a tiny atomic local cache index."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from molcascade.artifacts import (
    ArtifactDatasetRef,
    ArtifactIntegrityError,
    canonical_json_bytes,
    make_cache_key,
)
from molcascade.io.atomic import DestinationExistsError, atomic_write_bytes
from molcascade.pipeline import CompiledStage
from molcascade.runtime.models import CacheEntry

EXECUTION_SEMANTICS_VERSION = "molcascade.local-stage/v1"
_CACHE_KEY_RE = re.compile(r"^cache:sha256:([0-9a-f]{64})$")
_CACHE_ENTRY_SIZE_LIMIT = 1024 * 1024


def stage_cache_key(
    stage: CompiledStage,
    input_ref: ArtifactDatasetRef | Mapping[str, ArtifactDatasetRef] | None = None,
) -> str:
    """Return a path- and time-independent identity for one stage invocation.

    Every request-port-qualified dataset view is included, rather than only
    containing artifact IDs.  Different ports of one multi-output artifact,
    or different request-port bindings, can therefore never alias.
    """

    if not isinstance(stage, CompiledStage):
        raise TypeError("stage must be a CompiledStage")
    if input_ref is None:
        inputs: dict[str, ArtifactDatasetRef] = {}
    elif isinstance(input_ref, ArtifactDatasetRef):
        request_port = (
            stage.input_bindings[0].request_port
            if len(stage.input_bindings) == 1
            else "primary"
        )
        inputs = {request_port: input_ref}
    elif isinstance(input_ref, Mapping):
        inputs = dict(input_ref)
        if any(
            not isinstance(port, str) or not isinstance(ref, ArtifactDatasetRef)
            for port, ref in inputs.items()
        ):
            raise TypeError(
                "input mappings must contain string ports and ArtifactDatasetRef values"
            )
    else:
        raise TypeError(
            "input_ref must be an ArtifactDatasetRef, a request-port mapping, or None"
        )

    configuration = dict(stage.config)
    invocation = {
        "schema_version": "molcascade.stage-cache-key/v1",
        "execution_semantics": {
            "runner": EXECUTION_SEMANTICS_VERSION,
            "artifact_manifest": "molcascade.artifact/v2",
            "main_chain": "linear-primary-port/v1",
        },
        "stage": {
            "id": stage.stage_id,
            "slot": stage.slot,
            "plugin_key": stage.plugin_key,
            "plugin": stage.descriptor.model_dump(
                mode="json", by_alias=True, exclude_none=False
            ),
            "config": configuration,
            # Kept explicit even though it is also part of config.  It makes
            # the reproducibility condition of SEEDED plugins auditable.
            "seed": configuration.get("seed"),
            "input_contract": stage.input_contract,
            "output_contract": stage.output_contract,
            "input_port": stage.input_port,
            "output_port": stage.output_port,
            "input_bindings": [
                {
                    "request_port": binding.request_port,
                    "source_stage_id": binding.source_stage_id,
                    "source_port": binding.source_port,
                    "contract_id": binding.contract_id,
                }
                for binding in stage.input_bindings
            ],
        },
        "inputs": {
            port: ref.model_dump(mode="json", by_alias=True, exclude_none=False)
            for port, ref in sorted(inputs.items())
        },
    }
    return make_cache_key(invocation)


def _cache_digest(cache_key: str) -> str:
    match = _CACHE_KEY_RE.fullmatch(cache_key)
    if match is None:
        raise ValueError(f"invalid cache key: {cache_key!r}")
    return match.group(1)


class LocalStageCache:
    """Atomic cache-key index; artifact bytes remain owned by the store."""

    def __init__(self, root: str | Path) -> None:
        requested = Path(root).expanduser()
        if requested.is_symlink():
            raise ValueError("cache root may not be a symlink")
        requested.mkdir(parents=True, exist_ok=True)
        self.root = requested.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"cache root is not a directory: {self.root}")

    def _path(self, cache_key: str) -> Path:
        return self.root / f"{_cache_digest(cache_key)}.json"

    def get(self, cache_key: str) -> CacheEntry | None:
        path = self._path(cache_key)
        if not path.exists() and not path.is_symlink():
            return None
        if path.is_symlink() or not path.is_file():
            raise ArtifactIntegrityError(
                f"cache entry is not a regular file: {cache_key}",
                code="RUNTIME_CACHE_INVALID",
                context={"cache_key": cache_key},
            )
        try:
            if path.stat().st_size > _CACHE_ENTRY_SIZE_LIMIT:
                raise ValueError("cache entry is unreasonably large")
            content = path.read_bytes()
            entry = CacheEntry.model_validate_json(content)
        except (OSError, ValueError, ValidationError) as error:
            raise ArtifactIntegrityError(
                f"cache entry is invalid for {cache_key}: {error}",
                code="RUNTIME_CACHE_INVALID",
                context={"cache_key": cache_key},
            ) from error
        if entry.cache_key != cache_key:
            raise ArtifactIntegrityError(
                f"cache entry key does not match its filename: {cache_key}",
                code="RUNTIME_CACHE_INVALID",
                context={
                    "requested_cache_key": cache_key,
                    "stored_cache_key": entry.cache_key,
                },
            )
        if content != canonical_json_bytes(entry):
            raise ArtifactIntegrityError(
                f"cache entry is not canonical: {cache_key}",
                code="RUNTIME_CACHE_INVALID",
                context={"cache_key": cache_key},
            )
        return entry

    def put(self, entry: CacheEntry) -> None:
        if not isinstance(entry, CacheEntry):
            raise TypeError("entry must be a CacheEntry")
        path = self._path(entry.cache_key)
        existing = self.get(entry.cache_key)
        if existing is not None:
            if existing != entry:
                raise ArtifactIntegrityError(
                    "a deterministic cache key resolved to different artifacts",
                    code="RUNTIME_CACHE_CONFLICT",
                    context={
                        "cache_key": entry.cache_key,
                        "existing_artifact_id": existing.output_ref.artifact_id,
                        "new_artifact_id": entry.output_ref.artifact_id,
                    },
                )
            return
        content = canonical_json_bytes(entry)
        if len(content) > _CACHE_ENTRY_SIZE_LIMIT:
            raise ArtifactIntegrityError(
                "cache entry exceeds the local persistence limit",
                code="RUNTIME_CACHE_INVALID",
                context={
                    "cache_key": entry.cache_key,
                    "size_bytes": len(content),
                    "limit_bytes": _CACHE_ENTRY_SIZE_LIMIT,
                },
            )
        try:
            atomic_write_bytes(path, content, overwrite=False)
        except DestinationExistsError:
            # A concurrent writer won.  Accept it only when it published the
            # exact same immutable mapping.
            winner = self.get(entry.cache_key)
            if winner != entry:
                # ``from None``: losing the write race is the normal path and
                # not the fault being reported.  Chaining would print "during
                # handling of the above exception" above a DestinationExists
                # that is expected, burying the actual finding -- that two
                # writers disagree about what belongs under one cache key.
                raise ArtifactIntegrityError(
                    "concurrent cache writers published different artifacts",
                    code="RUNTIME_CACHE_CONFLICT",
                    context={"cache_key": entry.cache_key},
                ) from None


__all__ = [
    "EXECUTION_SEMANTICS_VERSION",
    "LocalStageCache",
    "stage_cache_key",
]
