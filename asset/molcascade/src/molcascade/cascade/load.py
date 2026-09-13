"""Load a screening configuration file of either supported kind.

A user should not have to know whether the file in front of them is a tier-first
cascade or a flat pipeline.  ``load_screening_config`` reads both and reports
which one it found, so the CLI can accept a configuration produced by any
version of the builder.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from molcascade.cascade.models import CascadeConfig
from molcascade.config.load import load_yaml
from molcascade.config.models import PipelineConfig
from molcascade.errors import ConfigError


@dataclass(frozen=True, slots=True)
class ScreeningConfig:
    """Either configuration kind, with the raw mapping retained for diagnostics."""

    cascade: CascadeConfig | None
    pipeline: PipelineConfig | None
    source: str

    @property
    def kind(self) -> str:
        return "cascade" if self.cascade is not None else "pipeline"


def _looks_like_cascade(raw: dict[str, Any]) -> bool:
    if raw.get("kind") == "cascade":
        return True
    return "tiers" in raw and "stages" not in raw


def parse_screening_config(text: str, *, source: str = "<string>") -> ScreeningConfig:
    """Parse YAML or JSON into whichever configuration kind it declares."""

    raw = load_yaml(text, source=source)
    if _looks_like_cascade(raw):
        try:
            cascade = CascadeConfig.model_validate(raw)
        except ValidationError as error:
            raise ConfigError(
                f"cascade configuration from {source} is invalid: {error}",
                code="CASCADE_SCHEMA_INVALID",
                context={"source": source, "error_count": error.error_count()},
            ) from error
        return ScreeningConfig(cascade=cascade, pipeline=None, source=source)
    try:
        pipeline = PipelineConfig.model_validate(raw)
    except ValidationError as error:
        raise ConfigError(
            f"pipeline configuration from {source} is invalid: {error}",
            code="CONFIG_SCHEMA_INVALID",
            context={"source": source, "error_count": error.error_count()},
        ) from error
    return ScreeningConfig(cascade=None, pipeline=pipeline, source=source)


def load_screening_config(path: str | Path) -> ScreeningConfig:
    """Read a cascade or pipeline configuration from disk."""

    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ConfigError(
            f"could not read screening configuration {config_path}: {error}",
            code="CONFIG_IO_ERROR",
            context={"path": str(config_path)},
        ) from error
    return parse_screening_config(text, source=str(config_path))


def parse_cascade_config(text: str, *, source: str = "<string>") -> CascadeConfig:
    """Parse a file that must be a cascade."""

    config = parse_screening_config(text, source=source)
    if config.cascade is None:
        raise ConfigError(
            f"{source} is a flat pipeline configuration, not a cascade",
            code="CASCADE_EXPECTED",
            context={"source": source},
        )
    return config.cascade


def load_cascade_config(path: str | Path) -> CascadeConfig:
    """Read a cascade configuration from disk."""

    config = load_screening_config(path)
    if config.cascade is None:
        raise ConfigError(
            f"{config.source} is a flat pipeline configuration, not a cascade",
            code="CASCADE_EXPECTED",
            context={"source": config.source},
        )
    return config.cascade


def dump_cascade_json(cascade: CascadeConfig, *, indent: int = 2) -> str:
    """Serialize a cascade as the exact JSON the builder downloads."""

    return json.dumps(
        cascade.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        indent=indent,
        sort_keys=False,
    )


__all__ = [
    "ScreeningConfig",
    "dump_cascade_json",
    "load_cascade_config",
    "load_screening_config",
    "parse_cascade_config",
    "parse_screening_config",
]
