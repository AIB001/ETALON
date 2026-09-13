"""Finite canonical JSON used for stable content and revision identifiers."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import PurePath
from typing import Any

from pydantic import BaseModel

from molcascade.errors import ConfigError


def _json_compatible(
    value: Any,
    *,
    location: str = "$",
    active: set[int] | None = None,
) -> Any:
    """Return a plain finite JSON tree or raise :class:`ConfigError`.

    Paths are converted with ``str`` only.  In particular, this function never
    calls ``resolve()``, ``absolute()``, ``expanduser()``, or ``normpath()``:
    runtime paths are meaningful inputs and must not silently change identity.
    """

    if isinstance(value, BaseModel):
        return _json_compatible(value.model_dump(mode="json"), location=location, active=active)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError(
                f"non-finite number at {location} cannot be represented in canonical JSON",
                code="CONFIG_NON_FINITE_NUMBER",
                context={"location": location},
            )
        return value
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, Enum):
        return _json_compatible(value.value, location=location, active=active)
    if isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    ):
        active = active if active is not None else set()
        marker = id(value)
        if marker in active:
            raise ConfigError(
                f"recursive value at {location} cannot be represented in canonical JSON",
                code="CONFIG_RECURSIVE_VALUE",
                context={"location": location},
            )
        active.add(marker)
        try:
            if isinstance(value, Mapping):
                result: dict[str, Any] = {}
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise ConfigError(
                            f"mapping key at {location} must be a string, "
                            f"got {type(key).__name__}",
                            code="CONFIG_NON_STRING_KEY",
                            context={"location": location, "key_type": type(key).__name__},
                        )
                    result[key] = _json_compatible(
                        item,
                        location=f"{location}.{key}",
                        active=active,
                    )
                return result
            return [
                _json_compatible(item, location=f"{location}[{index}]", active=active)
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(marker)
    raise ConfigError(
        f"value at {location} is not JSON-compatible: {type(value).__name__}",
        code="CONFIG_NOT_JSON",
        context={"location": location, "value_type": type(value).__name__},
    )


def canonical_json(value: Any) -> str:
    """Serialize ``value`` as deterministic compact UTF-8 JSON text.

    Mapping keys are sorted recursively by :func:`json.dumps`; array order is
    preserved.  Unicode strings and path spelling are preserved exactly.
    """

    compatible = _json_compatible(value)
    try:
        return json.dumps(
            compatible,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:  # defensive: _json_compatible should catch these
        raise ConfigError(
            "configuration cannot be encoded as canonical JSON",
            code="CONFIG_CANONICALIZATION_FAILED",
        ) from error


def canonical_json_bytes(value: Any) -> bytes:
    """Return the canonical JSON UTF-8 byte representation."""

    return canonical_json(value).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return a lowercase SHA-256 hex digest of canonical JSON bytes."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


# Public aliases which read naturally at different call sites.
canonicalize_json = canonical_json
stable_sha256 = canonical_sha256


__all__ = [
    "canonical_json",
    "canonical_json_bytes",
    "canonical_sha256",
    "canonicalize_json",
    "stable_sha256",
]
