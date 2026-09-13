"""Strict and immutable user-facing pipeline configuration models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, NoReturn, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


class StrictFrozenModel(BaseModel):
    """Base for small, versioned control-plane values.

    ``strict=True`` prevents coercions such as ``"1"`` to ``1``.  Frozen
    Pydantic models are normally only shallowly immutable; the JSON mappings in
    this module are additionally frozen recursively below.
    """

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        validate_default=True,
        revalidate_instances="always",
    )


class _FrozenJsonDict(dict[str, JsonValue]):
    """A JSON object which remains serializable but rejects mutation."""

    @staticmethod
    def _immutable() -> NoReturn:
        raise TypeError("configuration values are immutable")

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        memo[id(self)] = self
        return self

    def __setitem__(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def __delitem__(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def __ior__(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def clear(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def pop(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def popitem(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def setdefault(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def update(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()


class _FrozenJsonList(list[JsonValue]):
    """A JSON array which remains serializable but rejects mutation."""

    @staticmethod
    def _immutable() -> NoReturn:
        raise TypeError("configuration values are immutable")

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        memo[id(self)] = self
        return self

    def __setitem__(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def __delitem__(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def __iadd__(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def __imul__(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def append(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def clear(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def extend(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def insert(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def pop(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def remove(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def reverse(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()

    def sort(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()


def _freeze_json(value: JsonValue) -> JsonValue:
    """Make a validated JSON value deeply immutable without changing shape."""

    if isinstance(value, dict):
        # Bypass our mutation overrides while constructing the snapshot.
        frozen = _FrozenJsonDict()
        dict.update(frozen, {key: _freeze_json(item) for key, item in value.items()})
        return frozen
    if isinstance(value, list):
        frozen_list = _FrozenJsonList()
        list.extend(frozen_list, (_freeze_json(item) for item in value))
        return frozen_list
    return value


def _freeze_json_mapping(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    frozen = _FrozenJsonDict()
    dict.update(frozen, {key: _freeze_json(item) for key, item in value.items()})
    return frozen


_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class StageInputBinding(StrictFrozenModel):
    """Bind one plugin request port to an output port of an earlier stage.

    Ordered pipelines remain easy to read, while selectors and reports can
    retain side datasets such as properties, predictions, scaffolds and cluster
    assignments without copying them into every subsequent artifact.
    """

    request_port: str = Field(min_length=1, max_length=128)
    stage: str = Field(min_length=1, max_length=128)
    port: str = Field(default="primary", min_length=1, max_length=128)

    @field_validator("request_port", "stage", "port")
    @classmethod
    def _validate_binding_identifier(cls, value: str) -> str:
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError(
                "must start with a letter and contain only letters, digits, '.', '_' or '-'"
            )
        return value


class StageConfig(StrictFrozenModel):
    """One ordered, replaceable stage in a pipeline.

    The generic ``config`` object is validated again against the selected
    plugin's own Pydantic model by the pipeline compiler.  At this boundary it
    is intentionally restricted to finite JSON so it can be hashed and passed
    to an isolated worker without Python-specific serialization.
    """

    id: str = Field(min_length=1, max_length=128)
    slot: str = Field(min_length=1, max_length=128)
    plugin: str = Field(min_length=1, max_length=512)
    inputs: tuple[StageInputBinding, ...] = ()
    config: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("id", "slot")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError(
                "must start with a letter and contain only letters, digits, '.', '_' or '-'"
            )
        return value

    @field_validator("plugin")
    @classmethod
    def _validate_plugin(cls, value: str) -> str:
        # Do not strip or otherwise normalize this value: it participates in
        # the revision identity.  Reject ambiguous whitespace instead.
        if not value or any(character.isspace() for character in value):
            raise ValueError("plugin must be a non-empty identifier without whitespace")
        return value

    @field_validator("inputs", mode="before")
    @classmethod
    def _accept_input_binding_array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _input_request_ports_are_unique(self) -> StageConfig:
        request_ports = [binding.request_port for binding in self.inputs]
        if len(request_ports) != len(set(request_ports)):
            raise ValueError("stage input request_port values must be unique")
        return self

    @field_validator("config")
    @classmethod
    def _freeze_config(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _freeze_json_mapping(value)


class PipelineConfig(StrictFrozenModel):
    """Version 1 ordered pipeline configuration."""

    schema_version: int = Field(default=1, ge=1, le=1)
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    stages: tuple[StageConfig, ...]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        # Preserve the exact user value.  Silent trimming would make displayed
        # configuration differ from the bytes used for its revision ID.
        return value

    @field_validator("stages", mode="before")
    @classmethod
    def _accept_json_array_as_ordered_tuple(cls, value: Any) -> Any:
        # YAML and JSON have arrays rather than tuples.  This one explicit
        # structural conversion retains strict scalar validation while making
        # the final model immutable and preserving author order.
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _freeze_json_mapping(value)

    @model_validator(mode="after")
    def _validate_stage_set(self) -> PipelineConfig:
        seen: set[str] = set()
        duplicate_ids: list[str] = []
        for stage in self.stages:
            if stage.id in seen and stage.id not in duplicate_ids:
                duplicate_ids.append(stage.id)
            seen.add(stage.id)
        if duplicate_ids:
            duplicates = ", ".join(duplicate_ids)
            raise ValueError(f"stage ids must be unique; duplicate(s): {duplicates}")
        if not any(stage.enabled for stage in self.stages):
            raise ValueError("pipeline must contain at least one enabled stage")
        positions = {stage.id: index for index, stage in enumerate(self.stages)}
        enabled = {stage.id for stage in self.stages if stage.enabled}
        for index, stage in enumerate(self.stages):
            for binding in stage.inputs:
                source_index = positions.get(binding.stage)
                if source_index is None:
                    raise ValueError(
                        f"stage {stage.id!r} input {binding.request_port!r} references "
                        f"unknown stage {binding.stage!r}"
                    )
                if source_index >= index:
                    raise ValueError(
                        f"stage {stage.id!r} input {binding.request_port!r} must reference "
                        "an earlier stage"
                    )
                if binding.stage not in enabled:
                    raise ValueError(
                        f"stage {stage.id!r} input {binding.request_port!r} references "
                        f"disabled stage {binding.stage!r}"
                    )
        return self


__all__ = ["PipelineConfig", "StageConfig", "StageInputBinding", "StrictFrozenModel"]
