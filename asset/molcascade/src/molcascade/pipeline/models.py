"""Immutable compiled pipeline values."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from molcascade.config.canonical import canonical_json, canonical_sha256
from molcascade.config.models import PipelineConfig, StrictFrozenModel

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PipelineRevision(StrictFrozenModel):
    """A complete frozen pipeline configuration and its content identity.

    The identifier hashes only ``config``.  It never includes timestamps,
    filesystem location, or this envelope's serialization formatting.
    """

    revision_schema_version: Literal[1] = 1
    revision_id: str = Field(min_length=64, max_length=64)
    config: PipelineConfig

    @model_validator(mode="after")
    def _validate_revision_id(self) -> PipelineRevision:
        if not _SHA256_RE.fullmatch(self.revision_id):
            raise ValueError("revision_id must be a lowercase SHA-256 hexadecimal digest")
        expected = canonical_sha256(self.config)
        if self.revision_id != expected:
            raise ValueError(
                f"revision_id does not match canonical config (expected {expected})"
            )
        return self

    @property
    def canonical_config(self) -> str:
        """Canonical config JSON represented by this revision."""

        return canonical_json(self.config)

    @property
    def canonical_json(self) -> str:
        """Compatibility spelling for :attr:`canonical_config`."""

        return self.canonical_config

    @property
    def pipeline(self) -> PipelineConfig:
        """Compatibility spelling for the complete pipeline configuration."""

        return self.config


__all__ = ["PipelineRevision"]
