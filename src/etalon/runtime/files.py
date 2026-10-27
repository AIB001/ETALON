"""Durable local artifacts shared by execution and optional research consumers."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from etalon.active.schema import canonical

class EvidenceError(ValueError):
    """Explicit artifact or identity failure (legacy exception name preserved)."""

def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise EvidenceError("SHA256 must be 64 lowercase hexadecimal characters")
    return value

def file_identity(path):
    h = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            h.update(chunk)
    return {"sha256": h.hexdigest(), "bytes": size}

def _read(path):
    path = Path(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise EvidenceError("publication metadata exceeds 4 MiB")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _save(path, value, *, replace=False):
    """Durable bytes; immutable by default. Only the index and entry are replaced."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (canonical(value) + "\n").encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".publication-", delete=False) as out:
        temporary = Path(out.name)
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    try:
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise EvidenceError("immutable publication metadata conflicts: " + path.name) from None
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _copy(source, target, identity):
    """A complete copy is recoverable even if the producer's original is gone."""
    target = Path(target)
    if target.exists():
        if file_identity(target) != identity:
            raise EvidenceError("saved publication result identity changed")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".publication-", delete=False) as out:
            temporary = Path(out.name)
            with Path(source).open("rb") as inp:
                shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        if file_identity(temporary) != identity:
            raise EvidenceError("producer result identity changed; no scientific retry performed")
        try:
            os.link(temporary, target)
        except FileExistsError:
            if file_identity(target) != identity:
                raise EvidenceError("saved publication result identity conflicts") from None
        _sync_directory(target.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _consumer_lock(path: Path):
    """One cursor writer; OS releases the lock even when a consumer crashes."""
    lock = Path(str(path) + ".lock").absolute()
    if any(p.is_symlink() or (getattr(p, "is_junction", lambda: False)()) for p in (lock, *lock.parents)):
        raise ValueError("lock path cannot contain symlinks or junctions")
    with lock.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
