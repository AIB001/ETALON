"""Artifact-in/pending-artifact-out public protocol for stage plugins."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from molcascade.artifacts.models import (
    ArtifactDatasetRef,
    ArtifactOutput,
    ArtifactRef,
    validate_relative_artifact_path,
)
from molcascade.parallel.models import StageResources
from molcascade.plugins.manifest import PluginDescriptor

_PORT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class StageInput:
    """One immutable, port-qualified dataset view resolved by the runner.

    New runner code passes an :class:`ArtifactDatasetRef`; port, contract, and paths
    are then derived from that single identity-bearing value and cannot disagree.  A
    bundle ``ArtifactRef`` plus explicit ``contract_id`` remains accepted for legacy
    single-output/import-adapter tests, but cannot address a multi-output manifest.
    """

    ref: ArtifactDatasetRef | ArtifactRef
    root: Path
    contract_id: str | None = None
    port: str | None = None
    file_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        if isinstance(self.ref, ArtifactDatasetRef):
            if self.contract_id is not None and self.contract_id != self.ref.contract_id:
                raise ValueError("stage input contract_id does not match dataset reference")
            if self.port is not None and self.port != self.ref.port:
                raise ValueError("stage input port does not match dataset reference")
            supplied_paths = tuple(self.file_paths)
            if supplied_paths and tuple(sorted(supplied_paths)) != self.ref.file_paths:
                raise ValueError("stage input file_paths do not match dataset reference")
            object.__setattr__(self, "contract_id", self.ref.contract_id)
            object.__setattr__(self, "port", self.ref.port)
            object.__setattr__(self, "file_paths", self.ref.file_paths)
            return

        if self.contract_id is None or self.contract_id != self.ref.kind:
            raise ValueError(
                "stage input contract_id must exactly match the artifact reference kind"
            )
        selected_port = "primary" if self.port is None else self.port
        if not _PORT_RE.fullmatch(selected_port):
            raise ValueError("stage input port is not a valid identifier")
        paths = tuple(sorted(self.file_paths))
        if len(paths) != len(set(paths)):
            raise ValueError("stage input file_paths must be unique")
        for path in paths:
            validate_relative_artifact_path(path)
        object.__setattr__(self, "port", selected_port)
        object.__setattr__(self, "file_paths", paths)

    @property
    def artifact_ref(self) -> ArtifactDatasetRef | ArtifactRef:
        return self.ref


@dataclass(frozen=True, slots=True)
class StageRequest:
    """Immutable invocation data supplied to a plugin implementation."""

    stage_id: str
    inputs: Mapping[str, StageInput] = field(default_factory=dict)
    config: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _PORT_RE.fullmatch(self.stage_id):
            raise ValueError("stage_id is not a valid identifier")
        if any(not _PORT_RE.fullmatch(port) for port in self.inputs):
            raise ValueError("input port names must be valid identifiers")
        object.__setattr__(self, "inputs", MappingProxyType(dict(self.inputs)))
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))


@dataclass(frozen=True, slots=True)
class StageContext:
    """Runner-owned filesystem and machine context for one isolated stage attempt.

    Every output file is written beneath the same ``staging_root``.  Plugins do
    not publish, rename, or calculate final artifact identities.

    ``resources`` says how much of this machine the stage may use -- worker
    count, GPU lanes, and where it may leave completed shards so an interrupted
    run can resume.  It travels here rather than in the stage's config because
    ``stage_cache_key`` hashes the config: a worker count in there would give
    the same science a different cache key on every machine, and a cache that
    never hits is not a cache.  The default is the historical single-worker,
    CPU-only, no-checkpoint behaviour, so every plugin and test that predates
    parallel execution keeps working and keeps producing the same bytes.
    """

    staging_root: Path
    resources: StageResources = field(default_factory=StageResources)

    def __post_init__(self) -> None:
        object.__setattr__(self, "staging_root", Path(self.staging_root))


@dataclass(frozen=True, slots=True)
class PendingOutput:
    """One dataset declaration whose paths are relative to a shared staging root."""

    contract_id: str
    file_paths: tuple[str, ...]
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        paths = tuple(self.file_paths)
        if not paths:
            raise ValueError("pending output must declare at least one file")
        if len(paths) != len(set(paths)):
            raise ValueError("pending output file paths must be unique")
        for path in paths:
            validate_relative_artifact_path(path)
        object.__setattr__(self, "file_paths", tuple(sorted(paths)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


# Kept as a descriptive compatibility spelling for early plugin prototypes.
StageOutput = PendingOutput


@dataclass(frozen=True, slots=True)
class StageResponse:
    """All pending datasets to commit together as one multi-dataset artifact.

    ``file_paths`` from every output port share :class:`StageContext`'s single
    staging root and may not overlap.  The runner validates the complete union,
    records each port as an identity-bearing manifest output, and performs one atomic
    artifact commit.  This response never contains an ``ArtifactManifest`` or
    output ``ArtifactDatasetRef``.
    """

    outputs: Mapping[str, PendingOutput]
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        outputs = dict(self.outputs)
        if not outputs:
            raise ValueError("stage response must declare at least one output port")
        if any(not _PORT_RE.fullmatch(port) for port in outputs):
            raise ValueError("output port names must be valid identifiers")
        claimed_paths: dict[str, str] = {}
        for port, output in outputs.items():
            if not isinstance(output, PendingOutput):
                raise TypeError("stage response outputs must be PendingOutput values")
            for path in output.file_paths:
                previous = claimed_paths.get(path)
                if previous is not None:
                    raise ValueError(
                        f"staged file {path!r} is declared by both {previous!r} and {port!r}"
                    )
                claimed_paths[path] = port
        object.__setattr__(self, "outputs", MappingProxyType(outputs))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def file_paths(self) -> tuple[str, ...]:
        """Return the complete, sorted file inventory for atomic commit."""

        return tuple(
            sorted(path for output in self.outputs.values() for path in output.file_paths)
        )

    @property
    def artifact_outputs(self) -> tuple[ArtifactOutput, ...]:
        """Return canonical artifact-layer declarations for one atomic commit."""

        return tuple(
            ArtifactOutput(
                port=port,
                contract_id=output.contract_id,
                file_paths=output.file_paths,
                metadata=dict(output.metadata),
            )
            for port, output in sorted(self.outputs.items())
        )


@runtime_checkable
class StagePlugin(Protocol):
    """Minimal synchronous plugin boundary used by isolated workers."""

    descriptor: PluginDescriptor

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        """Write pending files beneath ``context.staging_root`` and declare them."""

        ...


__all__ = [
    "PendingOutput",
    "StageContext",
    "StageInput",
    "StageOutput",
    "StagePlugin",
    "StageRequest",
    "StageResources",
    "StageResponse",
]
