"""Safe YAML loading for strict MolCascade configuration."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from molcascade.config.models import PipelineConfig
from molcascade.errors import ConfigError, DuplicateKeyError


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant with duplicate-key checks and YAML 1.2 booleans."""

    # Copy the inherited lists before editing; mutating SafeLoader's global
    # resolver table would surprise other libraries in this Python process.
    yaml_implicit_resolvers: ClassVar[Any] = {
        first_character: list(resolvers)
        for first_character, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(
                None,
                None,
                f"expected a mapping node, but found {node.id}",
                node.start_mark,
            )

        # Preserve normal SafeLoader merge handling.  Duplicate explicit keys
        # and collisions introduced by merges are both rejected; implicit
        # overrides would otherwise make the hashed configuration misleading.
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable mapping key",
                    key_node.start_mark,
                ) from error
            if duplicate:
                raise DuplicateKeyError(
                    f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}",
                    context={
                        "key": str(key),
                        "line": key_node.start_mark.line + 1,
                        "column": key_node.start_mark.column + 1,
                    },
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


# PyYAML follows YAML 1.1 here and would interpret values such as ``yes`` and
# ``off`` as booleans.  Config files use the YAML 1.2 spelling true/false so a
# plugin value like ``mode: on`` remains the string the author wrote.
for _initial, _resolvers in list(_UniqueKeySafeLoader.yaml_implicit_resolvers.items()):
    _UniqueKeySafeLoader.yaml_implicit_resolvers[_initial] = [
        resolver for resolver in _resolvers if resolver[0] != "tag:yaml.org,2002:bool"
    ]
_UniqueKeySafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _validate_json_tree(value: Any, *, location: str = "$", active: set[int] | None = None) -> None:
    """Reject YAML-only and non-finite values before Pydantic validation."""

    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError(
                f"non-finite number at {location} is not allowed",
                code="CONFIG_NON_FINITE_NUMBER",
                context={"location": location},
            )
        return

    active = active if active is not None else set()
    if isinstance(value, (list, Mapping)):
        marker = id(value)
        if marker in active:
            raise ConfigError(
                f"recursive YAML alias at {location} is not allowed",
                code="CONFIG_RECURSIVE_ALIAS",
                context={"location": location},
            )
        active.add(marker)
        try:
            if isinstance(value, list):
                for index, item in enumerate(value):
                    _validate_json_tree(item, location=f"{location}[{index}]", active=active)
            else:
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise ConfigError(
                            f"mapping key at {location} must be a string",
                            code="CONFIG_NON_STRING_KEY",
                            context={
                                "location": location,
                                "key_type": type(key).__name__,
                            },
                        )
                    _validate_json_tree(item, location=f"{location}.{key}", active=active)
        finally:
            active.remove(marker)
        return

    raise ConfigError(
        f"YAML value at {location} is not JSON-compatible: {type(value).__name__}",
        code="CONFIG_NOT_JSON",
        context={"location": location, "value_type": type(value).__name__},
    )


def load_yaml(text: str, *, source: str = "<string>") -> dict[str, Any]:
    """Parse one YAML document into a finite JSON-compatible mapping."""

    try:
        value = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except DuplicateKeyError:
        raise
    except yaml.YAMLError as error:
        context: dict[str, Any] = {"source": source}
        mark = getattr(error, "problem_mark", None)
        if mark is not None:
            context.update(line=mark.line + 1, column=mark.column + 1)
        raise ConfigError(
            f"could not parse YAML configuration from {source}: {error}",
            code="CONFIG_YAML_INVALID",
            context=context,
        ) from error

    if value is None:
        raise ConfigError(
            f"configuration from {source} is empty",
            code="CONFIG_EMPTY",
            context={"source": source},
        )
    if not isinstance(value, dict):
        raise ConfigError(
            f"configuration root in {source} must be a mapping",
            code="CONFIG_ROOT_NOT_MAPPING",
            context={"source": source, "root_type": type(value).__name__},
        )
    _validate_json_tree(value)
    return value


def parse_pipeline_config(text: str, *, source: str = "<string>") -> PipelineConfig:
    """Parse and strictly validate a pipeline configuration YAML string."""

    raw = load_yaml(text, source=source)
    try:
        return PipelineConfig.model_validate(raw)
    except ValidationError as error:
        raise ConfigError(
            f"pipeline configuration from {source} is invalid: {error}",
            code="CONFIG_SCHEMA_INVALID",
            context={"source": source, "error_count": error.error_count()},
        ) from error


def load_pipeline_config(path: str | Path) -> PipelineConfig:
    """Read and validate a pipeline YAML file without rewriting runtime paths."""

    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ConfigError(
            f"could not read pipeline configuration {config_path}: {error}",
            code="CONFIG_IO_ERROR",
            context={"path": str(config_path)},
        ) from error
    return parse_pipeline_config(text, source=str(config_path))


# Concise aliases for callers that already know which configuration kind they
# are loading.
load_config = load_pipeline_config
parse_config = parse_pipeline_config


__all__ = [
    "load_config",
    "load_pipeline_config",
    "load_yaml",
    "parse_config",
    "parse_pipeline_config",
]
