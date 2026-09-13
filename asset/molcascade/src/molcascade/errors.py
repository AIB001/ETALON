"""Structured exceptions shared by MolCascade's control plane.

The exception classes in this module deliberately carry only JSON-compatible
context.  They are suitable for a concise CLI error as well as a future API
response, without leaking a traceback or arbitrary Python objects.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ErrorCategory(StrEnum):
    """Stable, coarse error categories exposed to callers."""

    CONFIG = "CONFIG"
    INPUT = "INPUT"
    CONTRACT = "CONTRACT"
    PLUGIN = "PLUGIN"
    EXECUTION = "EXECUTION"
    ARTIFACT_INTEGRITY = "ARTIFACT_INTEGRITY"
    RESOURCE = "RESOURCE"
    INTERNAL = "INTERNAL"


class ErrorInfo(BaseModel):
    """Serializable public representation of a :class:`MolCascadeError`."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        validate_default=True,
    )

    code: str = Field(min_length=1)
    category: ErrorCategory
    message: str = Field(min_length=1)
    hint: str | None = None
    retryable: bool = False
    context: dict[str, JsonValue] = Field(default_factory=dict)


class MolCascadeError(Exception):
    """Base class for expected, user-facing MolCascade failures.

    Parameters are keyword-only except for ``message`` so call sites cannot
    accidentally swap an error code and a human-readable message.
    """

    default_code: ClassVar[str] = "MOLCASCADE_ERROR"
    category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
        retryable: bool = False,
        context: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.hint = hint
        self.retryable = retryable
        # Validate at the boundary.  It prevents later JSON rendering from
        # failing because an exception captured a Path, NaN, or custom object.
        info = ErrorInfo(
            code=self.code,
            category=self.category,
            message=message,
            hint=hint,
            retryable=retryable,
            context=context or {},
        )
        self.context = info.context

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        """Keep the code, hint and context when the error crosses a process.

        The default reduction rebuilds an exception from ``args`` alone, which
        for this hierarchy means everything after ``message`` is dropped and the
        code falls back to the class default.  A worker process reporting
        ``SCSCORE_FINGERPRINT_EMPTY`` would then reach its parent as a bare
        ``PLUGIN_ERROR``, which is exactly the diagnosis the caller needed.
        Every field here is JSON-compatible by construction, so nothing
        unpicklable can be captured this way.
        """

        return _rebuild_error, (
            type(self),
            self.message,
            self.code,
            self.hint,
            self.retryable,
            self.context,
        )

    def to_info(self) -> ErrorInfo:
        """Return the stable serializable form of this exception."""

        return ErrorInfo(
            code=self.code,
            category=self.category,
            message=self.message,
            hint=self.hint,
            retryable=self.retryable,
            context=self.context,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-mode dictionary for CLI/API rendering."""

        return self.to_info().model_dump(mode="json")


def _rebuild_error(
    error_type: type[MolCascadeError],
    message: str,
    code: str,
    hint: str | None,
    retryable: bool,
    context: dict[str, JsonValue],
) -> MolCascadeError:
    """Reconstruct an unpickled error through the keyword-only constructor.

    Module-level and taking the class as an argument so every subclass reduces
    the same way; no subclass in this project adds constructor parameters, and
    one that did would have to say so here.
    """

    return error_type(
        message,
        code=code,
        hint=hint,
        retryable=retryable,
        context=dict(context),
    )


class ConfigError(MolCascadeError):
    """Configuration input could not be read, parsed, or validated."""

    default_code = "CONFIG_INVALID"
    category = ErrorCategory.CONFIG


# A descriptive spelling is convenient at public boundaries while ConfigError
# remains terse at call sites.  This is an alias (not a subclass), so either
# name catches exactly the same failures.
ConfigurationError = ConfigError


class DuplicateKeyError(ConfigError):
    """A YAML mapping contains a key more than once."""

    default_code = "CONFIG_DUPLICATE_KEY"


class PipelineError(MolCascadeError):
    """A pipeline cannot be compiled or its revision is inconsistent."""

    default_code = "PIPELINE_INVALID"
    category = ErrorCategory.CONFIG


class InputError(MolCascadeError):
    default_code = "INPUT_INVALID"
    category = ErrorCategory.INPUT


class ContractError(MolCascadeError):
    default_code = "CONTRACT_INVALID"
    category = ErrorCategory.CONTRACT


class PluginError(MolCascadeError):
    default_code = "PLUGIN_ERROR"
    category = ErrorCategory.PLUGIN


class ExecutionError(MolCascadeError):
    default_code = "EXECUTION_FAILED"
    category = ErrorCategory.EXECUTION


class ArtifactIntegrityError(MolCascadeError):
    default_code = "ARTIFACT_INTEGRITY_FAILED"
    category = ErrorCategory.ARTIFACT_INTEGRITY


class ResourceError(MolCascadeError):
    default_code = "RESOURCE_EXHAUSTED"
    category = ErrorCategory.RESOURCE


class AssetError(MolCascadeError):
    """A vendored model, weight file, or rule table is absent or does not verify.

    This is deliberately not retryable.  A screening run never downloads, so
    the only cure is an explicit ``molcascade assets fetch`` by a human who has
    decided to trust the source.
    """

    default_code = "ASSET_UNAVAILABLE"
    category = ErrorCategory.RESOURCE


__all__ = [
    "ArtifactIntegrityError",
    "AssetError",
    "ConfigError",
    "ConfigurationError",
    "ContractError",
    "DuplicateKeyError",
    "ErrorCategory",
    "ErrorInfo",
    "ExecutionError",
    "InputError",
    "MolCascadeError",
    "PipelineError",
    "PluginError",
    "ResourceError",
]
