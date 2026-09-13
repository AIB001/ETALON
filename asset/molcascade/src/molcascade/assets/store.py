"""Locate, verify, and hand out paths to vendored assets.  Never downloads.

This module is the boundary that a screening run is allowed to cross.  It can
answer "where is the SCScore weight file, and is it the one that was declared",
and it can answer "no, and here is the command that would fix that".  It cannot
answer by going and getting it, which is what keeps a run reproducible.

Configurations refer to assets by reference rather than by absolute path::

    asset:scscore/models/full_reaxys_model_1024bool/model.ckpt-10654.as_numpy.json.gz

A path like that means the same thing on a laptop and on the eight-GPU node,
which an absolute path does not.  :func:`resolve_reference` turns it into a
real path, having first proved the bytes are the declared ones.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

from molcascade.assets.catalog import asset_spec, iter_assets
from molcascade.assets.models import (
    AssetFile,
    AssetFileStatus,
    AssetSpec,
    AssetState,
    AssetStatus,
)
from molcascade.errors import AssetError

ASSET_REFERENCE_PREFIX = "asset:"
_STAMP_NAME = ".molcascade-verified.json"
_HASH_CHUNK = 4 * 1024 * 1024
_ENV_ROOT = "MOLCASCADE_ASSET_ROOT"
_VENDOR_DIRNAME = "vendor"


def _repository_vendor_dir() -> Path | None:
    """Return ``<repo>/vendor`` when running from a source checkout.

    Development and deployment want different homes for several gigabytes of
    weights.  In a checkout the obvious place is beside the code; from an
    installed wheel there is no checkout, and the user data directory is right.
    """

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src").is_dir():
            return parent / _VENDOR_DIRNAME
    return None


def default_asset_root() -> Path:
    """Where assets live, in decreasing order of explicitness."""

    override = os.environ.get(_ENV_ROOT, "").strip()
    if override:
        return Path(override).expanduser()
    vendor = _repository_vendor_dir()
    if vendor is not None:
        return vendor
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "molcascade" / "assets"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "molcascade" / "assets"


def asset_directory(spec: AssetSpec, *, root: Path | None = None) -> Path:
    return (root or default_asset_root()) / spec.id


def _digest(path: Path) -> tuple[str, int]:
    """Hash a regular file, refusing to follow a symlink to somewhere else."""

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise AssetError(
                f"asset member is not a regular file: {path}",
                code="ASSET_MEMBER_NOT_REGULAR",
            )
        hasher = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            while chunk := handle.read(_HASH_CHUNK):
                hasher.update(chunk)
        return hasher.hexdigest(), info.st_size
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_stamp(directory: Path) -> dict[str, dict[str, int | str]]:
    """Read the verification cache, treating any problem as a cold cache."""

    try:
        raw = (directory / _STAMP_NAME).read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    entries = parsed.get("files")
    return entries if isinstance(entries, dict) else {}


def write_stamp(directory: Path, entries: dict[str, dict[str, int | str]]) -> None:
    """Record what was verified, so a repeat check need not re-hash gigabytes.

    This is a cache and nothing more.  It is keyed on size and modification
    time; if either differs, or the file is absent, the bytes are hashed again.
    A tampered stamp can only cause extra work, never a skipped check of a file
    that changed.
    """

    payload = json.dumps({"version": 1, "files": entries}, indent=2, sort_keys=True)
    # Suppressed rather than reported: the stamp is a cache, and a read-only or
    # full asset directory should cost re-hashing, not fail the fetch that just
    # succeeded.
    with contextlib.suppress(OSError):
        (directory / _STAMP_NAME).write_text(payload + "\n", encoding="utf-8")


def _check_file(
    directory: Path,
    entry: AssetFile,
    stamp: dict[str, dict[str, int | str]],
    fresh: dict[str, dict[str, int | str]],
    *,
    deep: bool,
) -> AssetFileStatus:
    path = directory / entry.name
    try:
        info = path.lstat()
    except OSError:
        return AssetFileStatus(
            name=entry.name, present=False, verified=False, detail="file is not present"
        )
    if stat.S_ISLNK(info.st_mode):
        return AssetFileStatus(
            name=entry.name,
            present=True,
            verified=False,
            detail="asset members must be regular files, not symbolic links",
        )
    if info.st_size != entry.size_bytes:
        return AssetFileStatus(
            name=entry.name,
            present=True,
            verified=False,
            observed_size_bytes=info.st_size,
            detail=f"expected {entry.size_bytes} bytes, found {info.st_size}",
        )
    cached = stamp.get(entry.name)
    if (
        not deep
        and isinstance(cached, dict)
        and cached.get("sha256") == entry.sha256
        and cached.get("size_bytes") == info.st_size
        and cached.get("mtime_ns") == info.st_mtime_ns
    ):
        fresh[entry.name] = dict(cached)
        return AssetFileStatus(
            name=entry.name,
            present=True,
            verified=True,
            observed_size_bytes=info.st_size,
            detail="verified earlier; size and modification time are unchanged",
        )
    try:
        observed, size = _digest(path)
    except (AssetError, OSError) as error:
        return AssetFileStatus(
            name=entry.name,
            present=True,
            verified=False,
            observed_size_bytes=info.st_size,
            detail=f"could not be read: {error}",
        )
    if observed != entry.sha256:
        return AssetFileStatus(
            name=entry.name,
            present=True,
            verified=False,
            observed_size_bytes=size,
            detail=f"digest mismatch: expected {entry.sha256}, computed {observed}",
        )
    fresh[entry.name] = {
        "sha256": observed,
        "size_bytes": size,
        "mtime_ns": info.st_mtime_ns,
    }
    return AssetFileStatus(
        name=entry.name,
        present=True,
        verified=True,
        observed_size_bytes=size,
        detail="digest matches the declared value",
    )


def asset_status(
    spec: AssetSpec,
    *,
    root: Path | None = None,
    deep: bool = False,
) -> AssetStatus:
    """Compare disk against the declaration.  Reports; never repairs."""

    directory = asset_directory(spec, root=root)
    stamp = _read_stamp(directory)
    fresh: dict[str, dict[str, int | str]] = {}
    statuses = tuple(
        _check_file(directory, entry, stamp, fresh, deep=deep) for entry in spec.files
    )
    if all(status.verified for status in statuses):
        state = AssetState.READY
        if fresh != stamp:
            write_stamp(directory, fresh)
    elif not any(status.present for status in statuses):
        state = AssetState.MISSING
    elif all(status.present or status.verified for status in statuses):
        state = AssetState.CORRUPT
    else:
        state = AssetState.INCOMPLETE
    return AssetStatus(
        asset_id=spec.id, state=state, root=str(directory), files=statuses
    )


def is_asset_reference(value: str) -> bool:
    return value.startswith(ASSET_REFERENCE_PREFIX)


def split_reference(value: str) -> tuple[str, str]:
    """Split ``asset:<id>/<member>`` without accepting anything else."""

    if not is_asset_reference(value):
        raise AssetError(
            f"not an asset reference: {value!r}",
            code="ASSET_REFERENCE_INVALID",
            hint=f"asset references start with {ASSET_REFERENCE_PREFIX!r}",
        )
    body = value[len(ASSET_REFERENCE_PREFIX) :]
    asset_id, separator, member = body.partition("/")
    if not asset_id or not separator or not member:
        raise AssetError(
            f"asset reference must be 'asset:<asset-id>/<file>': {value!r}",
            code="ASSET_REFERENCE_INVALID",
        )
    return asset_id, member


def resolve_reference(value: str, *, root: Path | None = None) -> Path:
    """Turn ``asset:<id>/<member>`` into a verified absolute path.

    Fails closed in every direction: unknown asset, undeclared member, missing
    file, wrong size, wrong digest.  The error always names the command that
    would resolve it, because "asset missing" with no next step is a dead end
    for whoever is running the screen.
    """

    asset_id, member = split_reference(value)
    try:
        spec = asset_spec(asset_id)
    except KeyError as error:
        known = ", ".join(spec.id for spec in iter_assets())
        raise AssetError(
            f"unknown asset {asset_id!r}",
            code="ASSET_UNKNOWN",
            hint=f"known assets: {known}",
        ) from error
    try:
        entry = spec.file(member)
    except KeyError as error:
        available = ", ".join(item.name for item in spec.files)
        raise AssetError(
            f"asset {asset_id!r} declares no file {member!r}",
            code="ASSET_MEMBER_UNKNOWN",
            hint=f"declared files: {available}",
        ) from error
    directory = asset_directory(spec, root=root)
    stamp = _read_stamp(directory)
    fresh: dict[str, dict[str, int | str]] = {}
    status = _check_file(directory, entry, stamp, fresh, deep=False)
    if not status.verified:
        raise AssetError(
            f"asset {asset_id!r} file {member!r} is not usable: {status.detail}",
            code="ASSET_NOT_READY",
            hint=(
                f"run 'molcascade assets fetch {asset_id}' to download and verify it; "
                "MolCascade never downloads during a screening run"
            ),
            context={
                "asset_id": asset_id,
                "file": member,
                "expected_path": str(directory / member),
                "expected_sha256": entry.sha256,
            },
        )
    if fresh:
        merged = dict(stamp)
        merged.update(fresh)
        if merged != stamp:
            write_stamp(directory, merged)
    return (directory / member).resolve()


def resolve_path_or_reference(value: str, *, root: Path | None = None) -> Path:
    """Accept either an ``asset:`` reference or an absolute local path.

    Relative paths are rejected on purpose.  A configuration that says
    ``models/weights.gz`` means something different depending on which
    directory the run was launched from, and a screening result that depends on
    the caller's shell history is not reproducible.
    """

    if is_asset_reference(value):
        return resolve_reference(value, root=root)
    path = Path(value)
    if not path.is_absolute():
        raise AssetError(
            f"path must be absolute or an 'asset:' reference: {value!r}",
            code="ASSET_PATH_NOT_ABSOLUTE",
            hint="use an absolute path, or 'asset:<asset-id>/<file>' for a vendored asset",
        )
    return path


__all__ = [
    "ASSET_REFERENCE_PREFIX",
    "asset_directory",
    "asset_status",
    "default_asset_root",
    "is_asset_reference",
    "resolve_path_or_reference",
    "resolve_reference",
    "split_reference",
    "write_stamp",
]
