"""Crash-releasing advisory locks for one run ID across runner instances."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from molcascade.errors import ExecutionError


def _locked_error(run_id: str) -> ExecutionError:
    return ExecutionError(
        f"run is already active in another runner: {run_id}",
        code="RUN_LOCKED",
        hint="Wait for the active runner to finish before retrying or resuming.",
        retryable=True,
        context={"run_id": run_id},
    )


@contextmanager
def acquire_run_lock(lock_root: str | Path, run_id: str) -> Iterator[None]:
    """Acquire one non-blocking OS advisory lock for a complete run lifecycle.

    The small lock file intentionally remains on disk.  The lock is attached
    to the open descriptor and is automatically released by the operating
    system after a process crash; retaining the inode avoids unlink races
    between waiters.
    """

    root = Path(lock_root)
    if root.is_symlink() or not root.is_dir():
        raise ExecutionError(
            "run lock root is not a real directory",
            code="RUN_LOCK_FAILED",
            context={"run_id": run_id},
        )
    lock_path = root / f"{run_id}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o644)
    except OSError as error:
        raise ExecutionError(
            f"cannot open run lock for {run_id}: {error}",
            code="RUN_LOCK_FAILED",
            retryable=True,
            context={
                "run_id": run_id,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        ) from error

    acquired = False
    try:
        try:
            opened = os.fstat(descriptor)
            linked = os.stat(lock_path, follow_symlinks=False)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(linked.st_mode)
                or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
            ):
                raise ExecutionError(
                    f"run lock is not a stable regular file for {run_id}",
                    code="RUN_LOCK_FAILED",
                    context={"run_id": run_id},
                )
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise _locked_error(run_id) from error
            raise ExecutionError(
                f"cannot acquire run lock for {run_id}: {error}",
                code="RUN_LOCK_FAILED",
                retryable=True,
                context={
                    "run_id": run_id,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            ) from error
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                # Closing the descriptor is the authoritative crash-safe
                # release path on both supported lock implementations.
                acquired = False
        os.close(descriptor)


__all__ = ["acquire_run_lock"]
