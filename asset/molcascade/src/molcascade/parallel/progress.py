"""Small replaceable status snapshots beside existing shard checkpoints."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from molcascade.io.atomic import atomic_write_bytes

_activity: dict[str, Any] = {}


def _io_path(path: Path) -> Path:
    # Checkpoint paths can already be near Windows' legacy path-length limit.
    if os.name == 'nt' and not str(path).startswith('\\\\?\\'):
        absolute = str(path.absolute())
        return Path('\\\\?\\UNC\\' + absolute[2:] if absolute.startswith('\\\\') else '\\\\?\\' + absolute)
    return path


def report_activity(task: Any, phase: str, **values: Any) -> None:
    """One bounded snapshot per process, never a per-pose event log."""
    if task.progress_dir is None:
        return
    now = time.monotonic()
    identity = (str(task.progress_dir), task.index)
    changed = _activity.get('identity') != identity or _activity.get('phase') != phase
    if not changed and now - _activity.get('written', 0) < 1:
        return
    if _activity.get('identity') != identity:
        _activity.clear()
    _activity.update(identity=identity, phase=phase, written=now)
    directory = _io_path(Path(task.progress_dir) / str(os.getpid()))
    payload = _activity.setdefault('payload', {})
    payload.update(shard_index=task.index, rows_total=task.row_count,
                   device=task.device, phase=phase, **values,
                   updated_at=datetime.now(timezone.utc).isoformat())
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(directory / 'progress.json', json.dumps(payload, sort_keys=True).encode())


def read_progress(directory: Path | None) -> dict[str, Any]:
    if directory is None:
        return {}
    path = _io_path(directory / 'progress.json')
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_progress(directory: Path | None, **values: Any) -> None:
    if directory is None:
        return
    directory = _io_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {**read_progress(directory), **values,
               'updated_at': datetime.now(timezone.utc).isoformat()}
    atomic_write_bytes(directory / 'progress.json', json.dumps(payload, sort_keys=True).encode())


def stage_progress(checkpoints_root: Path, stage: Any) -> dict[str, Any]:
    if not stage.cache_key or str(stage.status) in {'SUCCEEDED', 'CACHED'}:
        return {}
    from molcascade.runtime.cache import checkpoint_directory_name
    checkpoint_dir = checkpoints_root / checkpoint_directory_name(stage.cache_key)
    value = read_progress(checkpoint_dir)
    if checkpoint_dir.is_dir():
        from molcascade.parallel.shards import shard_progress_counts
        value = {**value, 'shard_counts': shard_progress_counts(checkpoint_dir)}
    activity_dir = value.get('activity_dir')
    if activity_dir:
        # At most two pools' live slots for this attempt, not a scan of all shards.
        root = Path(activity_dir)
        expected = checkpoints_root / checkpoint_directory_name(stage.cache_key) / 'activity'
        if root.parent == expected and _io_path(root).is_dir():
            workers = [read_progress(p) for p in _io_path(root).iterdir() if p.is_dir()]
            counts = Counter(w.get('phase', 'unknown') for w in workers)
            value = {**value, 'worker_phases': dict(counts),
                     'worker_sample': [w for w in workers if w.get('phase') != 'idle'][:16],
                     'worker_count': len(workers)}
    if str(stage.status) == 'FAILED' and value:
        value = {**value, 'phase': 'interrupted', 'effective_workers': 0}
    return value
