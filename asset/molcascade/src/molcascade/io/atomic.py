"""Small, local-filesystem atomic publication primitives."""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path


class AtomicIOError(RuntimeError):
    """Base class for atomic I/O failures with a user-actionable meaning."""


class DestinationExistsError(AtomicIOError):
    """Raised when a no-overwrite publication target already exists."""


class CrossDeviceCommitError(AtomicIOError):
    """Raised when a rename would cross filesystem boundaries."""


def fsync_directory(path: str | Path) -> None:
    """Best-effort fsync of a directory after a rename.

    Some platforms do not support opening or syncing directories.  Atomic visibility
    still holds there, while crash durability is limited to the platform guarantee.
    """

    directory = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except (OSError, PermissionError):
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in {
                errno.EBADF,
                errno.EINVAL,
                getattr(errno, "ENOTSUP", errno.EINVAL),
            }:
                raise
    finally:
        os.close(descriptor)


def ensure_same_filesystem(source: str | Path, destination_parent: str | Path) -> None:
    """Fail early unless source and destination parent are on the same filesystem."""

    source_path = Path(source)
    parent_path = Path(destination_parent)
    if source_path.stat().st_dev != parent_path.stat().st_dev:
        raise CrossDeviceCommitError(
            f"atomic rename requires one filesystem: {source_path} -> {parent_path}"
        )


def atomic_publish_file(
    temporary_path: str | Path,
    destination: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Publish a completed temporary file in the same directory atomically."""

    temporary = Path(temporary_path)
    target = Path(destination)
    if not temporary.is_file():
        raise AtomicIOError(f"temporary path is not a regular file: {temporary}")
    target.parent.mkdir(parents=True, exist_ok=True)
    ensure_same_filesystem(temporary, target.parent)

    try:
        if overwrite:
            os.replace(temporary, target)
        else:
            # A hard link gives POSIX an atomic create-if-absent primitive.  It also
            # works on normal Windows local volumes and cannot replace an existing file.
            os.link(temporary, target)
            temporary.unlink()
    except FileExistsError as error:
        raise DestinationExistsError(f"destination already exists: {target}") from error
    except OSError as error:
        if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise DestinationExistsError(f"destination already exists: {target}") from error
        raise
    fsync_directory(target.parent)
    return target


def atomic_write_bytes(
    destination: str | Path,
    content: bytes | bytearray | memoryview,
    *,
    overwrite: bool = True,
    mode: int = 0o644,
) -> Path:
    """Write bytes completely, fsync them, then atomically publish the file."""

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        return atomic_publish_file(temporary, target, overwrite=overwrite)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_commit_directory(staging: str | Path, destination: str | Path) -> Path:
    """Atomically rename a staging directory without replacing committed content.

    Callers should serialize cooperative writers for the same destination.  The helper
    checks again immediately before ``rename`` and translates the platform-specific
    existing-directory failures into :class:`DestinationExistsError`.
    """

    source = Path(staging)
    target = Path(destination)
    if not source.is_dir() or source.is_symlink():
        raise AtomicIOError(f"staging path is not a real directory: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    ensure_same_filesystem(source, target.parent)
    if target.exists() or target.is_symlink():
        raise DestinationExistsError(f"destination already exists: {target}")
    try:
        os.rename(source, target)
    except FileExistsError as error:
        raise DestinationExistsError(f"destination already exists: {target}") from error
    except OSError as error:
        if error.errno == errno.EXDEV:
            raise CrossDeviceCommitError(
                f"atomic rename crossed filesystems: {source} -> {target}"
            ) from error
        if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise DestinationExistsError(f"destination already exists: {target}") from error
        raise
    fsync_directory(target.parent)
    return target


__all__ = [
    "AtomicIOError",
    "CrossDeviceCommitError",
    "DestinationExistsError",
    "atomic_commit_directory",
    "atomic_publish_file",
    "atomic_write_bytes",
    "ensure_same_filesystem",
    "fsync_directory",
]
