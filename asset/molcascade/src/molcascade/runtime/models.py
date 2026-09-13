"""Persistent control-plane models for local pipeline execution.

The runtime state is deliberately small and JSON-only.  Artifact data never
appears in these files: a completed stage is represented by an exact,
port-qualified :class:`~molcascade.artifacts.ArtifactDatasetRef` checkpoint.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
)

from molcascade.artifacts import ArtifactDatasetRef, CacheKey
from molcascade.errors import ErrorInfo

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_REVISION_ID_RE = r"^[0-9a-f]{64}$"
_WINDOWS_RESERVED_RUN_NAMES = {
    "AUX",
    "CLOCK$",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

RunId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128),
]
RevisionId = Annotated[
    str,
    StringConstraints(strict=True, pattern=_REVISION_ID_RE),
]


def validate_run_id(value: str) -> str:
    """Validate a run identifier before it is interpolated into a path."""

    invalid_shape = not isinstance(value, str) or _RUN_ID_RE.fullmatch(value) is None
    windows_stem = value.split(".", maxsplit=1)[0].upper() if isinstance(value, str) else ""
    windows_unsafe = (
        isinstance(value, str)
        and (value.endswith(".") or windows_stem in _WINDOWS_RESERVED_RUN_NAMES)
    )
    if invalid_shape or windows_unsafe:
        raise ValueError(
            "run_id must start with a letter or digit and contain only "
            "letters, digits, '.', '_' or '-', and must be a portable filename"
        )
    return value


def _parse_utc_datetime(value: Any) -> Any:
    """Permit canonical ISO strings at the persistence boundary, then require UTC."""

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime timestamps must include a timezone")
        return value.astimezone(UTC)
    return value


class RunStatus(StrEnum):
    """Lifecycle of a complete pipeline invocation."""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class StageRunStatus(StrEnum):
    """Lifecycle of one stage within a run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    CACHED = "CACHED"
    FAILED = "FAILED"


class _RuntimeModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )


class StageRunState(_RuntimeModel):
    """Durable state and checkpoint for one compiled stage."""

    stage_id: str = Field(min_length=1, max_length=128)
    plugin_key: str = Field(min_length=1, max_length=512)
    status: StageRunStatus = StageRunStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    cache_key: CacheKey | None = None
    output_ref: ArtifactDatasetRef | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: ErrorInfo | None = None

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def _parse_timestamps(cls, value: Any) -> Any:
        return _parse_utc_datetime(value)


class RunState(_RuntimeModel):
    """Atomically persisted state for a resumable local run."""

    schema_version: Literal["molcascade.run/v1"] = "molcascade.run/v1"
    run_id: RunId
    revision_id: RevisionId
    status: RunStatus
    stages: tuple[StageRunState, ...]
    output_ref: ArtifactDatasetRef | None = None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    error: ErrorInfo | None = None

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return validate_run_id(value)

    @field_validator("stages", mode="before")
    @classmethod
    def _accept_json_array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("started_at", "updated_at", "finished_at", mode="before")
    @classmethod
    def _parse_timestamps(cls, value: Any) -> Any:
        return _parse_utc_datetime(value)


# A run result is the final durable state, not a second representation which
# could disagree with it.
RunResult = RunState


class AuditEvent(_RuntimeModel):
    """One append-only, monotonically sequenced audit record."""

    schema_version: Literal["molcascade.audit/v1"] = "molcascade.audit/v1"
    sequence: int = Field(ge=1)
    timestamp: datetime
    event_type: str = Field(min_length=1, max_length=128)
    run_id: RunId
    revision_id: RevisionId
    stage_id: str | None = Field(default=None, min_length=1, max_length=128)
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return validate_run_id(value)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: Any) -> Any:
        return _parse_utc_datetime(value)


class CacheEntry(_RuntimeModel):
    """Trusted-local index entry from invocation identity to a dataset view."""

    schema_version: Literal["molcascade.cache/v1"] = "molcascade.cache/v1"
    cache_key: CacheKey
    output_ref: ArtifactDatasetRef


__all__ = [
    "AuditEvent",
    "CacheEntry",
    "RunId",
    "RunResult",
    "RunState",
    "RunStatus",
    "StageRunState",
    "StageRunStatus",
    "validate_run_id",
]
