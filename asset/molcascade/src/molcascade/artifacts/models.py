"""Strict control-plane models for immutable MolCascade artifacts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, NoReturn, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from molcascade.artifacts.hashing import canonical_json_bytes, make_artifact_id

MANIFEST_VERSION = "molcascade.artifact/v2"
DEFAULT_OUTPUT_CONTRACT = "artifact_payload/v1"

_HEX_64 = r"[0-9a-f]{64}"
_PORT_RE = r"[A-Za-z][A-Za-z0-9_.-]{0,127}"
_CONTRACT_ID_RE = r"[a-z][a-z0-9_.-]{0,126}/v[1-9][0-9]*"
_WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CLOCK$",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

Sha256Checksum = Annotated[
    str,
    StringConstraints(strict=True, pattern=rf"^sha256:{_HEX_64}$"),
]
ArtifactId = Annotated[
    str,
    StringConstraints(strict=True, pattern=rf"^artifact:sha256:{_HEX_64}$"),
]
CacheKey = Annotated[
    str,
    StringConstraints(strict=True, pattern=rf"^cache:sha256:{_HEX_64}$"),
]
NonEmptyString = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]
PortName = Annotated[str, StringConstraints(strict=True, pattern=rf"^{_PORT_RE}$")]
ContractId = Annotated[
    str,
    StringConstraints(strict=True, pattern=rf"^{_CONTRACT_ID_RE}$"),
]


def validate_relative_artifact_path(value: str) -> str:
    """Validate a portable POSIX relative path used inside an artifact.

    In addition to ``..`` and absolute paths, Windows drive/UNC forms, alternate data
    streams, ambiguous normalisations, control characters, and Windows device names are
    rejected.  Backslashes are never separators in a manifest; producers must emit
    forward slashes on every platform.
    """

    if not value:
        raise ValueError("artifact path must not be empty")
    if "\x00" in value or any(ord(character) < 32 for character in value):
        raise ValueError("artifact path contains a control character")
    if "\\" in value:
        raise ValueError("artifact paths must use POSIX '/' separators")
    if value.startswith("/") or PurePosixPath(value).is_absolute():
        raise ValueError("artifact path must be relative")
    windows_path = PureWindowsPath(value)
    if windows_path.drive or windows_path.is_absolute():
        raise ValueError("artifact path must not contain a Windows drive or UNC root")

    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("artifact path must be normalised and may not traverse parents")
    for part in parts:
        if any(character in '<>:"|?*' for character in part):
            raise ValueError("artifact path contains a non-portable filename character")
        if part.endswith((" ", ".")):
            raise ValueError("artifact path segments may not end with a space or dot")
        device_name = part.split(".", maxsplit=1)[0].upper()
        if device_name in _WINDOWS_RESERVED_NAMES:
            raise ValueError(f"reserved Windows device name in artifact path: {part!r}")

    if PurePosixPath(value).as_posix() != value:
        raise ValueError("artifact path is not in canonical POSIX form")
    return value


RelativeArtifactPath = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=4096),
    AfterValidator(validate_relative_artifact_path),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )


class _FrozenJsonDict(dict[str, JsonValue]):
    """Serializable dictionary which rejects mutation after validation."""

    @staticmethod
    def _immutable() -> NoReturn:
        raise TypeError("artifact identity metadata is immutable")

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
    """Serializable list which rejects mutation after validation."""

    @staticmethod
    def _immutable() -> NoReturn:
        raise TypeError("artifact identity metadata is immutable")

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
    if isinstance(value, dict):
        frozen = _FrozenJsonDict()
        dict.update(frozen, {key: _freeze_json(item) for key, item in value.items()})
        return frozen
    if isinstance(value, list):
        frozen_list = _FrozenJsonList()
        list.extend(frozen_list, (_freeze_json(item) for item in value))
        return frozen_list
    return value


def _freeze_json_mapping(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    frozen = _FrozenJsonDict()
    dict.update(frozen, {key: _freeze_json(item) for key, item in value.items()})
    return frozen


class ArtifactRef(_StrictModel):
    """Store-independent reference to a complete committed artifact bundle.

    ``kind`` classifies the bundle and is deliberately not a dataset contract.  Data
    flow and lineage use :class:`ArtifactDatasetRef`, which selects an exact output port.
    """

    artifact_id: ArtifactId
    kind: NonEmptyString

    @field_validator("kind")
    @classmethod
    def _kind_must_not_have_surrounding_space(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("kind must not have surrounding whitespace")
        return value


class ArtifactOutput(_StrictModel):
    """One identity-bearing dataset port within an artifact bundle."""

    port: PortName
    contract_id: ContractId
    file_paths: tuple[RelativeArtifactPath, ...]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("file_paths", mode="before")
    @classmethod
    def _normalise_file_paths(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return tuple(sorted(value))
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _accept_metadata_mapping(cls, value: Any) -> Any:
        return dict(value) if isinstance(value, Mapping) else value

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _freeze_json_mapping(value)

    @model_validator(mode="after")
    def _validate_files(self) -> Self:
        if not self.file_paths:
            raise ValueError("artifact output must claim at least one file")
        if len(self.file_paths) != len(set(self.file_paths)):
            raise ValueError("artifact output file paths must be unique")
        if len(self.file_paths) != len({path.casefold() for path in self.file_paths}):
            raise ValueError(
                "artifact output file paths must be unique on case-insensitive filesystems"
            )
        return self


class ArtifactDatasetRef(_StrictModel):
    """Portable, port-qualified view of one dataset in an artifact bundle.

    Callers should obtain this value from :meth:`ArtifactManifest.dataset_ref` and ask
    the store to resolve it.  Duplicating the contract and paths here makes lineage and
    cache inputs self-describing; the resolver verifies the duplicate values against the
    identity-bearing manifest before exposing any files.
    """

    artifact_id: ArtifactId
    artifact_kind: NonEmptyString
    port: PortName
    contract_id: ContractId
    file_paths: tuple[RelativeArtifactPath, ...]

    @field_validator("artifact_kind")
    @classmethod
    def _kind_must_not_have_surrounding_space(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("artifact kind must not have surrounding whitespace")
        return value

    @field_validator("file_paths", mode="before")
    @classmethod
    def _normalise_file_paths(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return tuple(sorted(value))
        return value

    @model_validator(mode="after")
    def _validate_files(self) -> Self:
        if not self.file_paths:
            raise ValueError("artifact dataset reference must include at least one file")
        if len(self.file_paths) != len(set(self.file_paths)):
            raise ValueError("artifact dataset reference file paths must be unique")
        if len(self.file_paths) != len({path.casefold() for path in self.file_paths}):
            raise ValueError(
                "artifact dataset reference paths must be unique on case-insensitive filesystems"
            )
        return self


def normalize_artifact_outputs(
    outputs: Mapping[str, Any] | Sequence[ArtifactOutput] | None,
    *,
    file_paths: Sequence[str],
    default_contract_id: str = DEFAULT_OUTPUT_CONTRACT,
) -> tuple[ArtifactOutput, ...]:
    """Convert runner/plugin port declarations into a canonical immutable tuple."""

    if outputs is None:
        return (
            ArtifactOutput(
                port="primary",
                contract_id=default_contract_id,
                file_paths=tuple(file_paths),
            ),
        )

    normalized: list[ArtifactOutput] = []
    if isinstance(outputs, Mapping):
        for port, value in outputs.items():
            if isinstance(value, ArtifactOutput):
                if value.port != port:
                    raise ValueError(
                        f"artifact output mapping key {port!r} does not match port {value.port!r}"
                    )
                normalized.append(value)
                continue
            if isinstance(value, Mapping):
                payload = dict(value)
                declared_port = payload.pop("port", port)
                if declared_port != port:
                    raise ValueError(
                        f"artifact output mapping key {port!r} does not match port "
                        f"{declared_port!r}"
                    )
                payload.setdefault("metadata", {})
                normalized.append(ArtifactOutput(port=port, **payload))
                continue
            try:
                contract_id = value.contract_id
                declared_paths = value.file_paths
                metadata = value.metadata
            except AttributeError as error:
                raise TypeError(
                    "artifact output values must expose contract_id, file_paths, and metadata"
                ) from error
            normalized.append(
                ArtifactOutput(
                    port=port,
                    contract_id=contract_id,
                    file_paths=declared_paths,
                    metadata=dict(metadata),
                )
            )
    else:
        for value in outputs:
            if not isinstance(value, ArtifactOutput):
                raise TypeError("artifact output sequences must contain ArtifactOutput values")
            normalized.append(value)

    if not normalized:
        raise ValueError("artifact must declare at least one output port")
    return tuple(sorted(normalized, key=lambda output: output.port))


class ArtifactProducer(_StrictModel):
    """Versioned implementation that produced an artifact."""

    plugin_id: NonEmptyString
    plugin_version: NonEmptyString
    api_version: NonEmptyString = "molcascade.plugin/v1"
    code_checksum: Sha256Checksum | None = None

    @field_validator("plugin_id", "plugin_version", "api_version")
    @classmethod
    def _identifier_must_not_have_surrounding_space(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("producer identifiers must not have surrounding whitespace")
        return value


class ArtifactFile(_StrictModel):
    """A declared regular file whose bytes participate in artifact identity."""

    path: RelativeArtifactPath
    size_bytes: Annotated[int, Field(strict=True, ge=0)]
    checksum: Sha256Checksum
    media_type: NonEmptyString | None = None
    row_count: Annotated[int, Field(strict=True, ge=0)] | None = None
    schema_checksum: Sha256Checksum | None = None

    @property
    def record_count(self) -> int | None:
        """Compatibility spelling for non-tabular callers."""

        return self.row_count


class _ArtifactIdentity(_StrictModel):
    manifest_version: Literal["molcascade.artifact/v2"] = MANIFEST_VERSION
    kind: NonEmptyString
    producer: ArtifactProducer
    inputs: tuple[ArtifactDatasetRef, ...] = ()
    files: tuple[ArtifactFile, ...]
    outputs: tuple[ArtifactOutput, ...]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _kind_must_not_have_surrounding_space(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("kind must not have surrounding whitespace")
        return value

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _freeze_json_mapping(value)

    @field_validator("inputs", "files", "outputs", mode="before")
    @classmethod
    def _accept_json_arrays(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _validate_inventory_and_ports(self) -> Self:
        paths = [entry.path for entry in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact manifest contains duplicate file paths")
        if len(paths) != len({path.casefold() for path in paths}):
            raise ValueError("artifact paths must be unique on case-insensitive filesystems")
        if paths != sorted(paths):
            raise ValueError("artifact files must be sorted by path")

        if not self.outputs:
            raise ValueError("artifact manifest must contain at least one output port")
        ports = [output.port for output in self.outputs]
        if len(ports) != len(set(ports)):
            raise ValueError("artifact output port names must be unique")
        if ports != sorted(ports):
            raise ValueError("artifact output ports must be sorted by port name")

        claimed_by: dict[str, str] = {}
        overlapping: list[str] = []
        for output in self.outputs:
            for path in output.file_paths:
                if path in claimed_by:
                    overlapping.append(path)
                else:
                    claimed_by[path] = output.port
        if overlapping:
            raise ValueError(
                "artifact files are claimed by multiple output ports: "
                + ", ".join(sorted(set(overlapping)))
            )
        file_set = set(paths)
        claimed_set = set(claimed_by)
        unclaimed = sorted(file_set - claimed_set)
        missing = sorted(claimed_set - file_set)
        if unclaimed or missing:
            raise ValueError(
                "artifact output ports must claim the complete file inventory; "
                f"unclaimed={unclaimed}, missing={missing}"
            )
        return self

    def identity_payload(self) -> dict[str, Any]:
        """Return exactly the fields covered by the artifact ID."""

        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=False,
            include={
                "manifest_version",
                "kind",
                "producer",
                "inputs",
                "files",
                "outputs",
                "metadata",
            },
        )


class ArtifactManifest(_ArtifactIdentity):
    """Self-validating manifest for a completely committed artifact.

    ``created_at`` and ``cache_key`` are observations about publication and invocation;
    neither participates in ``artifact_id``.  Output files, producer, input lineage,
    bundle kind, typed output ports, metadata, and manifest version do participate.
    """

    artifact_id: ArtifactId
    cache_key: CacheKey | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _artifact_id_matches_identity(self) -> Self:
        expected = make_artifact_id(self.identity_payload())
        if self.artifact_id != expected:
            raise ValueError(
                f"artifact_id does not match manifest identity: expected {expected}"
            )
        return self

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        producer: ArtifactProducer | dict[str, Any],
        files: tuple[ArtifactFile, ...] | list[ArtifactFile],
        outputs: Mapping[str, Any] | Sequence[ArtifactOutput] | None = None,
        inputs: tuple[ArtifactDatasetRef, ...] | list[ArtifactDatasetRef] = (),
        metadata: dict[str, JsonValue] | None = None,
        cache_key: str | None = None,
        created_at: datetime | None = None,
    ) -> ArtifactManifest:
        """Validate identity fields, compute their ID, and create a manifest."""

        producer_model = (
            producer
            if isinstance(producer, ArtifactProducer)
            else ArtifactProducer.model_validate(producer)
        )
        sorted_files = tuple(sorted(files, key=lambda entry: entry.path))
        default_contract_id = (
            kind if re.fullmatch(rf"{_CONTRACT_ID_RE}", kind) else DEFAULT_OUTPUT_CONTRACT
        )
        normalized_outputs = normalize_artifact_outputs(
            outputs,
            file_paths=tuple(entry.path for entry in sorted_files),
            default_contract_id=default_contract_id,
        )
        identity = _ArtifactIdentity(
            kind=kind,
            producer=producer_model,
            files=sorted_files,
            outputs=normalized_outputs,
            inputs=tuple(inputs),
            metadata={} if metadata is None else metadata,
        )
        values = identity.model_dump(mode="python")
        return cls(
            **values,
            artifact_id=make_artifact_id(identity.identity_payload()),
            cache_key=cache_key,
            created_at=datetime.now(UTC) if created_at is None else created_at,
        )

    @classmethod
    def from_json_bytes(cls, content: bytes | bytearray | memoryview) -> ArtifactManifest:
        """Parse and fully validate manifest JSON."""

        return cls.model_validate_json(bytes(content))

    def to_json_bytes(self) -> bytes:
        """Serialize the complete manifest in canonical JSON form."""

        # Nested metadata can be mutated despite Pydantic's shallow frozen model.  Check
        # again at the persistence boundary so such a mutation can never be committed.
        expected = make_artifact_id(self.identity_payload())
        if self.artifact_id != expected:
            raise ValueError("manifest was mutated after artifact_id calculation")
        return canonical_json_bytes(self)

    def as_ref(self) -> ArtifactRef:
        """Return a bundle reference for discovery and administrative operations."""

        return ArtifactRef(artifact_id=self.artifact_id, kind=self.kind)

    def output(self, port: str) -> ArtifactOutput:
        """Return one exact output declaration or reject an unknown port."""

        for output in self.outputs:
            if output.port == port:
                return output
        raise ValueError(f"artifact has no output port {port!r}")

    def dataset_ref(self, port: str) -> ArtifactDatasetRef:
        """Create the canonical, identity-bound downstream reference for ``port``."""

        output = self.output(port)
        return ArtifactDatasetRef(
            artifact_id=self.artifact_id,
            artifact_kind=self.kind,
            port=output.port,
            contract_id=output.contract_id,
            file_paths=output.file_paths,
        )

    def validate_dataset_ref(self, ref: ArtifactDatasetRef) -> ArtifactOutput:
        """Verify that a supplied dataset view exactly matches this manifest."""

        if not isinstance(ref, ArtifactDatasetRef):
            raise TypeError("ref must be an ArtifactDatasetRef")
        try:
            expected = self.dataset_ref(ref.port)
        except ValueError as error:
            raise ValueError("dataset reference does not match manifest output") from error
        if ref != expected:
            raise ValueError("dataset reference does not match manifest output")
        return self.output(ref.port)


def artifact_digest(artifact_id: str) -> str:
    """Extract and validate the hexadecimal digest from an artifact ID."""

    pattern = re.compile(rf"^artifact:sha256:({_HEX_64})$")
    match = pattern.fullmatch(artifact_id)
    if match is None:
        raise ValueError(f"invalid artifact ID: {artifact_id!r}")
    return match.group(1)


__all__ = [
    "DEFAULT_OUTPUT_CONTRACT",
    "MANIFEST_VERSION",
    "ArtifactDatasetRef",
    "ArtifactFile",
    "ArtifactId",
    "ArtifactManifest",
    "ArtifactOutput",
    "ArtifactProducer",
    "ArtifactRef",
    "CacheKey",
    "ContractId",
    "PortName",
    "RelativeArtifactPath",
    "Sha256Checksum",
    "artifact_digest",
    "normalize_artifact_outputs",
    "validate_relative_artifact_path",
]
