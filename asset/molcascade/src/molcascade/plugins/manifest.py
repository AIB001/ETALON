"""Strict control-plane descriptors for replaceable stage plugins."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal, NoReturn, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PLUGIN_API_VERSION = "molcascade.plugin/v1"

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CONTRACT_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,126}/v[1-9][0-9]*$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_PORT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class _FrozenPortMap(dict[str, str]):
    """JSON-serializable descriptor mapping which cannot be mutated."""

    @staticmethod
    def _immutable(*args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("plugin output port mapping is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable

    def __ior__(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._immutable()


class PluginKind(StrEnum):
    """Scientific capability slots understood by the pipeline compiler."""

    SOURCE = "source"
    STANDARDIZER = "standardizer"
    GATE = "gate"
    FEATURIZER = "featurizer"
    TRAINER = "trainer"
    PREDICTOR = "predictor"
    APPLICABILITY = "applicability"
    SYNTHESIS = "synthesis"
    DOCK = "dock"
    SCAFFOLDER = "scaffolder"
    CLUSTERER = "clusterer"
    SELECTOR = "selector"
    ENUMERATOR = "enumerator"
    EXPORTER = "exporter"


class Cardinality(StrEnum):
    """Row/entity cardinality change performed by a plugin."""

    ONE_TO_ONE = "one_to_one"
    FILTER = "filter"
    MANY_TO_ONE = "many_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_MANY = "many_to_many"


class Determinism(StrEnum):
    """Recomputation guarantee declared by a plugin."""

    DETERMINISTIC = "deterministic"
    SEEDED = "seeded"
    BEST_EFFORT = "best_effort"
    NON_DETERMINISTIC = "non_deterministic"


class PluginDescriptor(BaseModel):
    """Serializable identity and compatibility declaration for one plugin."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        validate_default=True,
    )

    id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=5, max_length=128)
    api_version: Literal["molcascade.plugin/v1"] = PLUGIN_API_VERSION
    kind: PluginKind
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...]
    output_ports: dict[str, str] = Field(default_factory=dict)
    cardinality: Cardinality
    determinism: Determinism
    tier_neutral_policy: bool = False
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _PLUGIN_ID_RE.fullmatch(value):
            raise ValueError(
                "plugin id must be lowercase and contain only letters, digits, '.', '_' or '-'"
            )
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if not _SEMVER_RE.fullmatch(value):
            raise ValueError("plugin version must be a complete semantic version")
        return value

    @field_validator("inputs", "outputs", mode="before")
    @classmethod
    def _accept_json_arrays(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("output_ports", mode="before")
    @classmethod
    def _accept_output_port_mapping(cls, value: Any) -> Any:
        return dict(value) if isinstance(value, dict) else value

    @field_validator("output_ports")
    @classmethod
    def _freeze_output_ports(cls, value: dict[str, str]) -> dict[str, str]:
        frozen = _FrozenPortMap()
        dict.update(frozen, value)
        return frozen

    @field_validator("kind", mode="before")
    @classmethod
    def _parse_kind(cls, value: Any) -> Any:
        return PluginKind(value) if isinstance(value, str) else value

    @field_validator("cardinality", mode="before")
    @classmethod
    def _parse_cardinality(cls, value: Any) -> Any:
        return Cardinality(value) if isinstance(value, str) else value

    @field_validator("determinism", mode="before")
    @classmethod
    def _parse_determinism(cls, value: Any) -> Any:
        return Determinism(value) if isinstance(value, str) else value

    @field_validator("inputs", "outputs")
    @classmethod
    def _validate_contract_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("contract identifiers must be unique")
        invalid = [value for value in values if not _CONTRACT_ID_RE.fullmatch(value)]
        if invalid:
            raise ValueError(f"invalid versioned contract identifier(s): {', '.join(invalid)}")
        return values

    @model_validator(mode="after")
    def _validate_ports(self) -> Self:
        if not self.outputs:
            raise ValueError("plugin must declare at least one output contract")
        if self.kind is PluginKind.SOURCE and self.inputs:
            raise ValueError("source plugins cannot declare input contracts")
        if self.kind is not PluginKind.SOURCE and not self.inputs:
            raise ValueError("non-source plugins must declare at least one input contract")
        if not self.output_ports:
            if len(self.outputs) != 1:
                raise ValueError(
                    "plugins with multiple output contracts must declare output_ports"
                )
            inferred = _FrozenPortMap()
            dict.update(inferred, {"primary": self.outputs[0]})
            object.__setattr__(self, "output_ports", inferred)
        invalid_names = [name for name in self.output_ports if not _PORT_RE.fullmatch(name)]
        if invalid_names:
            raise ValueError(f"invalid output port name(s): {', '.join(invalid_names)}")
        if "primary" not in self.output_ports:
            raise ValueError("plugin output_ports must declare a primary port")
        mapped_contracts = tuple(self.output_ports.values())
        if len(mapped_contracts) != len(set(mapped_contracts)):
            raise ValueError("each output contract must map to exactly one output port")
        if set(mapped_contracts) != set(self.outputs):
            raise ValueError("output_ports values must exactly cover outputs")
        if self.tier_neutral_policy:
            if self.kind is not PluginKind.GATE:
                raise ValueError("a tier-neutral policy plugin must have gate kind")
            if not {"parent/v1", "decision/v1"}.issubset(self.inputs):
                raise ValueError(
                    "a tier-neutral policy plugin must consume parent/v1 and decision/v1"
                )
            if not {"parent/v1", "decision/v1"}.issubset(self.outputs):
                raise ValueError(
                    "a tier-neutral policy plugin must emit parent/v1 and decision/v1"
                )
            if self.primary_contract != "parent/v1":
                raise ValueError(
                    "a tier-neutral policy plugin must expose parent/v1 as its primary output"
                )
            if self.cardinality is not Cardinality.FILTER:
                raise ValueError("a tier-neutral policy plugin must have filter cardinality")
        return self

    @property
    def key(self) -> str:
        """Return the exact identifier accepted by pipeline configurations."""

        return f"{self.id}@{self.version}"

    @property
    def input_contracts(self) -> tuple[str, ...]:
        """Descriptive compatibility alias for :attr:`inputs`."""

        return self.inputs

    @property
    def output_contracts(self) -> tuple[str, ...]:
        """Descriptive compatibility alias for :attr:`outputs`."""

        return self.outputs

    @property
    def primary_contract(self) -> str:
        return self.output_ports["primary"]


__all__ = [
    "PLUGIN_API_VERSION",
    "Cardinality",
    "Determinism",
    "PluginDescriptor",
    "PluginKind",
]
