"""Read a flat model-bundle directory safely, hashing every byte on the way.

A *model bundle* is how a user hands MolCascade a trained model: one directory
holding a manifest and the model file it names.  Two adapters take one --
:mod:`molcascade.plugins.builtin.custom_model` for an ONNX graph and
:mod:`molcascade.plugins.builtin.chemprop_model` for a Chemprop checkpoint --
and they disagree about almost everything downstream of this point: what a
manifest may say, which file formats are acceptable, whether loading the model
executes code.  What they cannot afford to disagree about is *reading the
directory*, because that is where the traversal, symlink and size defences live.
A defence fixed in one copy and not the other is worse than no defence, so the
reading is here and the policy stays with each adapter.

Bundles are flat on purpose.  A nested directory would make "which bytes are the
model" a question you answer by walking a tree, and would make the bundle digest
depend on directory-walk order.  Flat keeps both answerable by looking.

This module executes nothing and imports no inference runtime, so it is safe to
run against a bundle you have not yet decided to trust.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from molcascade.errors import PluginError

#: Read in one shot rather than in chunks: a bundle file is bounded by
#: ``maximum_bytes`` and has to be hashed whole anyway, and a single read is
#: what makes the size-changed-underneath check below meaningful.
__all__ = ["BundleCodes", "bundle_files", "bundle_root", "read_and_hash"]


class BundleCodes:
    """The error codes one adapter uses for bundle-reading failures.

    Error codes are part of a plugin's observable contract -- they end up in
    artifacts and in user-facing messages -- so a shared reader must raise the
    *caller's* codes rather than inventing a third vocabulary that neither
    adapter documents.
    """

    __slots__ = ("changed_during_read", "limit_exceeded", "missing", "path_invalid", "unreadable")

    def __init__(self, prefix: str) -> None:
        self.path_invalid = f"{prefix}_BUNDLE_PATH_INVALID"
        self.missing = f"{prefix}_BUNDLE_MISSING"
        self.unreadable = f"{prefix}_BUNDLE_UNREADABLE"
        self.limit_exceeded = f"{prefix}_BUNDLE_LIMIT_EXCEEDED"
        self.changed_during_read = f"{prefix}_BUNDLE_CHANGED_DURING_READ"


def bundle_root(bundle_dir: str, *, codes: BundleCodes) -> Path:
    """Resolve ``bundle_dir`` to a real directory, or say why it is not one."""

    root = Path(bundle_dir).expanduser()
    if not root.is_absolute():
        raise PluginError(
            "model bundle_dir must be an absolute path",
            code=codes.path_invalid,
            context={"bundle_dir": bundle_dir},
        )
    if root.is_symlink() or not root.is_dir():
        raise PluginError(
            "model bundle_dir must be an existing directory and not a symlink",
            code=codes.missing,
            context={"bundle_dir": str(root)},
        )
    return root


def read_and_hash(
    path: Path,
    *,
    remaining_bytes: int,
    codes: BundleCodes,
) -> tuple[bytes, str]:
    """Read one regular file without following symlinks, hashing as we go."""

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PluginError(
            "model bundle file could not be opened as a regular file",
            code=codes.unreadable,
            context={"path": path.name, "error_type": type(error).__name__},
        ) from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PluginError(
                "model bundle contains a non-regular file",
                code=codes.unreadable,
                context={"path": path.name},
            )
        if info.st_size > remaining_bytes:
            raise PluginError(
                "model bundle exceeds the configured byte limit",
                code=codes.limit_exceeded,
                context={"path": path.name, "size_bytes": info.st_size},
            )
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) != info.st_size:
        raise PluginError(
            "model bundle file changed size while being read",
            code=codes.changed_during_read,
            context={"path": path.name},
        )
    return payload, hashlib.sha256(payload).hexdigest()


def bundle_files(
    root: Path,
    *,
    maximum_files: int,
    maximum_bytes: int,
    codes: BundleCodes,
) -> tuple[dict[str, tuple[bytes, str]], int]:
    """Every file in ``root``, as ``name -> (contents, sha256)``, plus total size."""

    entries = sorted(root.iterdir(), key=lambda item: item.name)
    if len(entries) > maximum_files:
        raise PluginError(
            "model bundle contains more files than the configured limit",
            code=codes.limit_exceeded,
            context={"file_count": len(entries), "limit": maximum_files},
        )
    contents: dict[str, tuple[bytes, str]] = {}
    total = 0
    for entry in entries:
        if entry.is_symlink():
            raise PluginError(
                "model bundle contains a symlink",
                code=codes.unreadable,
                hint="Copy the real file into the bundle directory.",
                context={"path": entry.name},
            )
        if entry.is_dir():
            raise PluginError(
                "model bundle must be a flat directory",
                code=codes.unreadable,
                context={"path": entry.name},
            )
        payload, digest = read_and_hash(
            entry, remaining_bytes=maximum_bytes - total, codes=codes
        )
        total += len(payload)
        contents[entry.name] = (payload, digest)
    return contents, total
