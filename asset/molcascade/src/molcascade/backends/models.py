"""Strict control-plane values for locally executable software backends."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from molcascade.config.models import StrictFrozenModel


class Capability(StrEnum):
    SOURCE = "source"
    STANDARDIZE = "standardize"
    GATE = "gate"
    DESCRIPTORS = "descriptors"
    FINGERPRINT = "fingerprint"
    PREDICT = "predict"
    APPLICABILITY = "applicability"
    SYNTHESIS = "synthesis"
    DOCK = "dock"
    SCAFFOLD = "scaffold"
    CLUSTER = "cluster"
    SELECT = "select"
    EXPORT = "export"
    REPORT = "report"


class BackendTier(StrEnum):
    DEFAULT = "default"
    FALLBACK = "fallback"
    OPTIONAL = "optional"
    ISOLATED = "isolated"


class BackendInterface(StrEnum):
    NATIVE = "native"
    PYTHON = "python"
    CLI = "cli"
    JAVA = "java"


class LicenseClass(StrEnum):
    PERMISSIVE = "permissive"
    WEAK_COPYLEFT = "weak_copyleft"
    COPYLEFT = "copyleft"
    PROPRIETARY = "proprietary"
    UNKNOWN = "unknown"


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    DEGRADED = "degraded"


_BACKEND_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class BackendSpec(StrictFrozenModel):
    """One reviewed implementation option for a scientific capability.

    A catalogue entry does not import code and does not imply that a full stage
    adapter exists. ``plugin_ref`` is populated only when an executable
    MolCascade adapter is registered.
    """

    id: str = Field(min_length=1, max_length=128)
    capability: Capability
    display_name: str = Field(min_length=1, max_length=256)
    tier: BackendTier
    interface: BackendInterface
    license_spdx: str = Field(min_length=1, max_length=128)
    license_class: LicenseClass
    module: str | None = Field(default=None, min_length=1, max_length=256)
    distribution: str | None = Field(default=None, min_length=1, max_length=256)
    #: The MolCascade extra that installs :attr:`distribution`, if one does.
    #:
    #: ``pip install medchem`` and ``pip install "molcascade[alerts]"`` both work,
    #: but only the second carries the version floor the adapter was written
    #: against, and only the second keeps working when that floor moves.  Left
    #: unset for core dependencies, which are never missing, and for reviewed
    #: backends with no adapter behind them, which nothing can select.
    #:
    #: Declared here rather than read from installed metadata at run time:
    #: ``importlib.metadata`` reports the extras of the *installed* dist-info,
    #: which in an editable checkout can be several pyproject edits old and will
    #: happily name an extra that no longer exists.  The pairing is held to
    #: pyproject by test instead.
    extra: str | None = Field(default=None, min_length=1, max_length=64)
    command: tuple[str, ...] = ()
    #: For an :attr:`BackendTier.ISOLATED` backend, the stage-config key that
    #: names its executable.
    #:
    #: An isolated backend lives in an environment MolCascade is not running in
    #: and must not be on this one's ``PATH``: the whole reason it is isolated is
    #: that its dependency set conflicts with this one.  So ``shutil.which`` is
    #: not the authoritative probe for it -- the absolute path in the stage's own
    #: configuration is -- and the preflight needs to know which key holds it.
    #: The alternative, hard-coding the key next to the plugin id in the
    #: preflight, would put one backend's vocabulary in a module that otherwise
    #: knows nothing about any particular backend.
    executable_config_key: str | None = Field(default=None, min_length=1, max_length=64)
    #: Stage-config keys naming data files this backend reads, checked for
    #: existence before the run starts.
    #:
    #: A config validator can only check a path's *shape*, because validation
    #: has to work on a machine that is not the one the run will happen on.  So
    #: a stage that names a lead set nobody put there passes ``molcascade
    #: validate`` and dies at its first row -- after every stage above it has
    #: been paid for.  Preflight is where the filesystem is allowed to have an
    #: opinion, and it already receives each stage's config.
    #:
    #: A key that is absent or blank is *not* a failure here: several of these
    #: fields have in-config alternatives (embedded reference records, for one),
    #: and the plugin's own validator is what decides whether one was required.
    #: This checks only the claim a config actually makes.
    data_file_config_keys: tuple[str, ...] = ()
    plugin_ref: str | None = Field(default=None, min_length=1, max_length=512)
    platforms: tuple[str, ...] = ("windows", "linux", "darwin")
    offline: bool = True
    notes: str | None = Field(default=None, max_length=2048)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _BACKEND_ID.fullmatch(value):
            raise ValueError("backend id is not a portable lowercase identifier")
        return value

    @field_validator(
        "capability", "tier", "interface", "license_class", mode="before"
    )
    @classmethod
    def _parse_enums(cls, value: Any, info: Any) -> Any:
        enum_types = {
            "capability": Capability,
            "tier": BackendTier,
            "interface": BackendInterface,
            "license_class": LicenseClass,
        }
        enum_type = enum_types[info.field_name]
        return enum_type(value) if isinstance(value, str) else value

    @field_validator("command", "platforms", mode="before")
    @classmethod
    def _arrays_to_tuples(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("command")
    @classmethod
    def _valid_command(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or item != item.strip() for item in value):
            raise ValueError("command tokens must be non-empty and unambiguous")
        return value

    @field_validator("platforms")
    @classmethod
    def _valid_platforms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {"windows", "linux", "darwin"}
        if not value or set(value) - allowed or len(value) != len(set(value)):
            raise ValueError("platforms must be a unique non-empty supported-platform list")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def _probe_is_well_formed(self) -> Self:
        if self.interface is BackendInterface.NATIVE and (self.module or self.command):
            raise ValueError("native backend must not declare an external module or command")
        if self.interface is BackendInterface.PYTHON and not self.module:
            raise ValueError("Python backend requires a module probe")
        if self.interface in {BackendInterface.CLI, BackendInterface.JAVA} and not self.command:
            raise ValueError("CLI/Java backend requires a command probe")
        if self.distribution and not self.module:
            raise ValueError("distribution version probe requires a Python module")
        if self.executable_config_key and self.tier is not BackendTier.ISOLATED:
            raise ValueError(
                "only an isolated backend takes its executable from stage configuration"
            )
        return self


class ProbePolicy(StrictFrozenModel):
    """Local deployment policy applied without changing scientific config."""

    allow_weak_copyleft: bool = True
    allow_copyleft: bool = False
    allow_proprietary: bool = False
    allow_unknown_license: bool = False
    run_version_commands: bool = False
    command_timeout_seconds: float = Field(default=3.0, gt=0.0, le=30.0)


class BackendStatus(StrictFrozenModel):
    spec: BackendSpec
    availability: Availability
    version: str | None = None
    reason: str = Field(min_length=1, max_length=2048)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("availability", mode="before")
    @classmethod
    def _parse_availability(cls, value: Any) -> Any:
        return Availability(value) if isinstance(value, str) else value


__all__ = [
    "Availability",
    "BackendInterface",
    "BackendSpec",
    "BackendStatus",
    "BackendTier",
    "Capability",
    "LicenseClass",
    "ProbePolicy",
]
