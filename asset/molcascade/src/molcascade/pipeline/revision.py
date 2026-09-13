"""Creation and verification of immutable pipeline revisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from molcascade.config.canonical import canonical_json, canonical_sha256
from molcascade.config.models import PipelineConfig
from molcascade.errors import PipelineError
from molcascade.pipeline.models import PipelineRevision


def revision_id_for(config: PipelineConfig) -> str:
    """Compute the stable revision ID for a validated pipeline config."""

    return canonical_sha256(config)


def freeze_pipeline(config: PipelineConfig | Mapping[str, Any]) -> PipelineRevision:
    """Validate and snapshot ``config`` as a content-addressed revision."""

    try:
        validated = (
            config
            if isinstance(config, PipelineConfig)
            else PipelineConfig.model_validate(dict(config))
        )
        # Re-validate through canonical JSON.  Besides detaching the revision
        # from caller-owned containers, this guarantees that its retained
        # config is exactly the representation which was hashed.
        normalized = PipelineConfig.model_validate_json(canonical_json(validated))
        return PipelineRevision(
            revision_id=revision_id_for(normalized),
            config=normalized,
        )
    except ValidationError as error:
        raise PipelineError(
            f"cannot freeze invalid pipeline configuration: {error}",
            code="PIPELINE_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def create_revision(config: PipelineConfig | Mapping[str, Any]) -> PipelineRevision:
    """Alias for :func:`freeze_pipeline`."""

    return freeze_pipeline(config)


def verify_revision(revision: PipelineRevision) -> bool:
    """Return whether the retained config still matches its revision ID."""

    return revision.revision_id == revision_id_for(revision.config)


__all__ = ["create_revision", "freeze_pipeline", "revision_id_for", "verify_revision"]
