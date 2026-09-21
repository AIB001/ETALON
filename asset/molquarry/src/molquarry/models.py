from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class InputModel(BaseModel):
    # A typo must never silently turn a targeted query into an unfiltered query.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class Provenance(BaseModel):
    source: str
    url: str
    method: str
    retrieved_at: str
    request_sha256: str
    response_sha256: str
    source_version: str | None = None
    license_url: str
    cached: bool = False
    etag: str | None = None
    last_modified: str | None = None


class QueryResult(BaseModel):
    schema_version: str = "1"
    ok: Literal[True] = True
    source: str
    operation: str
    parameters: dict[str, Any]
    records: list[dict[str, Any]]
    returned: int
    total: int | None = None
    next_parameters: dict[str, Any] | None = None
    provenance: list[Provenance]
    warnings: list[str] = Field(default_factory=list)


class DownloadPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1"
    source: str
    operation: str
    parameters: dict[str, Any]
    url: str
    filename: str
    format: str
    source_version: str | None = None
    license_url: str
    expected_sha256: str | None = None
    estimated_bytes: int | None = None
    created_at: str = Field(default_factory=utcnow)
    provenance: list[Provenance] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DownloadResult(BaseModel):
    ok: Literal[True] = True
    path: str
    manifest_path: str
    sha256: str
    bytes: int
    manifest: dict[str, Any]
