"""Declarations for the model weights, rule tables and reference data MolCascade uses.

An *asset* is something a backend needs that is not code we ship: a published
weight file, a curated SMARTS table, a reference library.  Three properties are
non-negotiable for every one of them.

**It is pinned.**  Each file carries an expected SHA-256.  A file whose digest
does not match is not "probably fine"; it is refused.  Where the upstream is a
git host, the Git object id is recorded too, which is an independent check
computed by a different party with a different algorithm.

**It is cited.**  Someone did the work that produced these numbers.  Every
asset names the paper, and :mod:`molcascade.assets.fetch` writes that citation
into the asset directory as ``CITATION.md`` at install time, so the provenance
travels with the bytes instead of living only in this source file.

**It is never fetched implicitly.**  Downloading is an explicit command a human
runs.  A screening run that reaches a missing asset stops and says which
command would provide it.  The alternative -- a pipeline that quietly reaches
the network mid-run -- makes results depend on what a remote host served that
afternoon.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ASSET_SCHEMA_VERSION = 1

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_ASSET_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
# A member path inside an asset directory.  Forward slashes only, no traversal,
# no leading slash, no Windows drive letters, no dot components at all.
_MEMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")


class _AssetModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class AssetKind(StrEnum):
    """What sort of thing the asset is, which drives how it is presented."""

    WEIGHTS = "weights"
    RULES = "rules"
    REFERENCE = "reference"
    SOURCE = "source"


class PayloadTrust(StrEnum):
    """Whether loading the payload executes code supplied by someone else.

    ``DATA`` means the file is parsed into numbers by our own reader: a gzipped
    JSON array of floats cannot do anything except be a bad array of floats.
    ``EXECUTABLE`` means deserialising it runs code chosen by whoever produced
    it -- Python pickles and TensorFlow SavedModel graphs both qualify.  A
    digest proves such a file is the one the author published.  It does not
    make running it safe, and this enum exists so that nothing in MolCascade can
    quietly pretend otherwise.
    """

    DATA = "data"
    EXECUTABLE = "executable"


class AssetState(StrEnum):
    """Result of comparing what is on disk against what was declared."""

    READY = "ready"
    MISSING = "missing"
    INCOMPLETE = "incomplete"
    CORRUPT = "corrupt"


class Citation(_AssetModel):
    """The paper behind an asset, in enough detail to cite it properly."""

    title: str = Field(min_length=1, max_length=512)
    authors: str = Field(min_length=1, max_length=1024)
    venue: str = Field(min_length=1, max_length=256)
    year: int = Field(ge=1950, le=2100)
    doi: str | None = Field(default=None, max_length=256)
    url: str | None = Field(default=None, max_length=1024)
    note: str | None = Field(default=None, max_length=1024)

    @property
    def reference(self) -> str:
        # Author lists conventionally end in a period after the final initial,
        # so adding the separator unconditionally would yield "K. F.." here.
        authors = self.authors.rstrip(".")
        parts = [f"{authors}. {self.title}. {self.venue} {self.year}."]
        if self.doi:
            parts.append(f"DOI: {self.doi}")
        return " ".join(parts)


class AssetFile(_AssetModel):
    """One file inside an asset directory, pinned by digest and by size."""

    name: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0, le=64 * 1024 * 1024 * 1024)
    url: str = Field(min_length=1, max_length=2048)
    git_blob_sha1: str | None = Field(default=None, min_length=40, max_length=40)
    summary: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not _MEMBER_RE.fullmatch(self.name):
            raise ValueError(
                "asset file name must be a relative slash-separated path without "
                f"'.' or '..' components: {self.name!r}"
            )
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        if self.git_blob_sha1 is not None and not _SHA1_RE.fullmatch(self.git_blob_sha1):
            raise ValueError("git_blob_sha1 must be 40 lowercase hexadecimal characters")
        if not self.url.startswith("https://"):
            raise ValueError("asset URLs must be https")
        return self


class AssetSpec(_AssetModel):
    """A complete, citable, digest-pinned unit of downloadable content."""

    id: str = Field(min_length=1, max_length=64)
    kind: AssetKind
    display_name: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1024)
    version: str = Field(min_length=1, max_length=128)
    homepage: str = Field(min_length=1, max_length=1024)
    license_spdx: str = Field(min_length=1, max_length=64)
    trust: PayloadTrust = PayloadTrust.DATA
    files: tuple[AssetFile, ...] = Field(min_length=1)
    citations: tuple[Citation, ...] = ()
    used_by: tuple[str, ...] = ()
    notes: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not _ASSET_ID_RE.fullmatch(self.id):
            raise ValueError(
                "asset id must be lowercase alphanumeric with '-' or '_' separators"
            )
        seen: set[str] = set()
        for entry in self.files:
            lowered = entry.name.lower()
            if lowered in seen:
                # Case-insensitive because macOS and Windows filesystems are, and
                # two members differing only in case would silently overwrite.
                raise ValueError(f"duplicate file name in asset {self.id!r}: {entry.name}")
            seen.add(lowered)
        return self

    @property
    def total_bytes(self) -> int:
        return sum(entry.size_bytes for entry in self.files)

    def file(self, name: str) -> AssetFile:
        for entry in self.files:
            if entry.name == name:
                return entry
        raise KeyError(f"asset {self.id!r} declares no file named {name!r}")


class AssetFileStatus(_AssetModel):
    """What is actually on disk for one declared file."""

    name: str = Field(min_length=1, max_length=512)
    present: bool
    verified: bool
    observed_size_bytes: int | None = Field(default=None, ge=0)
    detail: str = Field(default="", max_length=512)


class AssetStatus(_AssetModel):
    """The verdict for one asset on this machine."""

    asset_id: str = Field(min_length=1, max_length=64)
    state: AssetState
    root: str = Field(min_length=1, max_length=4096)
    files: tuple[AssetFileStatus, ...] = ()

    @property
    def ready(self) -> bool:
        return self.state is AssetState.READY


__all__ = [
    "ASSET_SCHEMA_VERSION",
    "AssetFile",
    "AssetFileStatus",
    "AssetKind",
    "AssetSpec",
    "AssetState",
    "AssetStatus",
    "Citation",
    "PayloadTrust",
]
