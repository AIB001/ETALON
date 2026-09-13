"""Deterministic SHA-256 helpers for cache and artifact identities.

The three public hash forms are intentionally namespaced.  A cache key identifies an
invocation, an artifact ID identifies verified output plus lineage, and a checksum
identifies a byte stream.  Giving them different prefixes makes accidental interchange
visible both to humans and to Pydantic validation.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_DEFAULT_CHUNK_SIZE = 1024 * 1024


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented as canonical JSON."""


def _normalise_json(
    value: Any,
    *,
    location: str = "$",
    active: set[int] | None = None,
) -> Any:
    """Return a JSON-compatible value with deterministic scalar representations."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", by_alias=True, exclude_none=False)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    elif isinstance(value, Enum):
        value = value.value

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(f"non-finite float at {location}")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalizationError(f"naive datetime at {location}")
        utc_value = value.astimezone(UTC)
        return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        active = active if active is not None else set()
        marker = id(value)
        if marker in active:
            raise CanonicalizationError(f"recursive value at {location}")
        active.add(marker)
        try:
            normalised: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise CanonicalizationError(
                        f"mapping key at {location} must be a string, "
                        f"got {type(key).__name__}"
                    )
                normalised[key] = _normalise_json(
                    item,
                    location=f"{location}.{key}",
                    active=active,
                )
            return normalised
        finally:
            active.remove(marker)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        active = active if active is not None else set()
        marker = id(value)
        if marker in active:
            raise CanonicalizationError(f"recursive value at {location}")
        active.add(marker)
        try:
            return [
                _normalise_json(
                    item,
                    location=f"{location}[{index}]",
                    active=active,
                )
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(marker)
    raise CanonicalizationError(
        f"unsupported canonical JSON value at {location}: {type(value).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize *value* to stable UTF-8 JSON suitable for hashing.

    Mapping keys are sorted, insignificant whitespace is omitted, non-finite numbers
    are rejected, and aware datetimes are rendered in UTC.  This is a deliberately
    small project canonical form rather than a claim of full RFC 8785 compliance.
    """

    normalised = _normalise_json(value)
    return json.dumps(
        normalised,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(content: bytes | bytearray | memoryview) -> str:
    """Return the lowercase hexadecimal SHA-256 digest of a byte sequence."""

    return hashlib.sha256(content).hexdigest()


def sha256_file(path: str | Path, *, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> str:
    """Stream a regular file and return its lowercase hexadecimal SHA-256 digest."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"not a regular file: {source}")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while block := stream.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def sha256_checksum(content: bytes | bytearray | memoryview) -> str:
    """Return a namespaced checksum for in-memory bytes."""

    return f"sha256:{sha256_bytes(content)}"


def sha256_file_checksum(path: str | Path, *, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> str:
    """Return a namespaced checksum for a file."""

    return f"sha256:{sha256_file(path, chunk_size=chunk_size)}"


def canonical_content_hash(value: Any) -> str:
    """Return a namespaced SHA-256 checksum of canonical JSON content."""

    return sha256_checksum(canonical_json_bytes(value))


def make_cache_key(invocation: Any) -> str:
    """Hash an invocation description into a cache-key namespace."""

    return f"cache:sha256:{sha256_bytes(canonical_json_bytes(invocation))}"


def make_artifact_id(identity_payload: Any) -> str:
    """Hash verified artifact content and lineage into an artifact-ID namespace."""

    return f"artifact:sha256:{sha256_bytes(canonical_json_bytes(identity_payload))}"


__all__ = [
    "CanonicalizationError",
    "canonical_content_hash",
    "canonical_json_bytes",
    "make_artifact_id",
    "make_cache_key",
    "sha256_bytes",
    "sha256_checksum",
    "sha256_file",
    "sha256_file_checksum",
]
