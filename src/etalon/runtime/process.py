"""Local worker ownership: OS locks plus Linux process-start identities.

A missed heartbeat is diagnostic, not permission to rerun scientific work. No
caller-supplied PID is signalled; cancellation checks our persisted start identity.
"""

from __future__ import annotations

import os
import signal
import stat
from contextlib import contextmanager
from pathlib import Path

from etalon.active.store import StateError


def identity(pid: int) -> dict | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        if fields[0] == "Z":
            return None
        return {"pid": pid, "start_ticks": fields[19],
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "group": int(fields[2]), "session": int(fields[3])}
    except (OSError, IndexError, ValueError):
        return None


def alive(record: dict | None) -> bool:
    return bool(record and identity(record["pid"]) == record)


def group_alive(record: dict) -> bool:
    try:
        if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != record["boot_id"]:
            return False
    except OSError:
        return True  # Unknown ownership cannot authorize a replay.
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            current = identity(int(path.name))
            if current and current["group"] == record["group"] and current["session"] == record["session"]:
                return True
    return False


def signal_cancel(record: dict | None) -> bool:
    if not alive(record):
        return False
    if record["pid"] <= 1 or record["pid"] == os.getpid():
        raise StateError("refusing to signal the calling process")
    # A pidfd pins the target before rechecking its start identity. Without it,
    # exit/PID reuse between /proc inspection and kill could target a neighbour.
    # Older kernels still receive cancellation through the durable heartbeat flag.
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return False
    try:
        descriptor = os.pidfd_open(record["pid"])
    except OSError:
        return False
    try:
        if not alive(record):
            return False
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False
    finally:
        os.close(descriptor)


@contextmanager
def job_lock(root: Path):
    with file_lock(root / "owner.lock"):
        yield


@contextmanager
def file_lock(path: Path):
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise StateError("durable workers require POSIX file locks")
    import fcntl

    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise StateError("workflow lock is not a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise StateError("workflow still has a live owner; inspect or request cancellation") from error
        yield
    finally:
        os.close(descriptor)
