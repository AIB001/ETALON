"""Sealed, inspectable data runs. A successful request is not a scientific verdict."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "etalon-data-snapshot/1"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".data-write-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # No replacement: publication is atomic and an existing record wins a race.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def new_run(workspace: Path, run_id: str, request: dict[str, Any]) -> Path:
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", run_id):
        raise ValueError("run_id must be one safe path component (1 to 120 characters)")
    # mkdir is the exclusive reservation. An interrupted run remains visible, never reused.
    root = Path(workspace).resolve() / "data" / run_id
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "request.json", request)
    return root


def seal(root: Path, *, kind: str, result: dict[str, Any],
         infrastructure: dict[str, Any], **metadata: Any) -> dict[str, Any]:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("data snapshots cannot contain symlinks")
        if path.is_file():
            if path.name == "snapshot.json" and path.parent == root:
                raise FileExistsError(path)
            files[path.relative_to(root).as_posix()] = {
                "sha256": file_hash(path), "bytes": path.stat().st_size}
    body = {"schema": SCHEMA, "kind": kind, "created_at": datetime.now(UTC).isoformat(),
            "result": result, "infrastructure": infrastructure, "files": files, **metadata}
    body["snapshot_id"] = digest(body)
    write_json(root / "snapshot.json", body)
    return {"snapshot": str(root), **body}


def read_snapshot(root: Path) -> dict[str, Any]:
    """Verify the seal and every referenced byte before using any data in a campaign."""
    root = Path(root).resolve()
    value = json.loads((root / "snapshot.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("data snapshot manifest must be a mapping")
    identity = value.pop("snapshot_id", None)
    if value.get("schema") != SCHEMA or digest(value) != identity:
        raise ValueError("data snapshot manifest identity differs from its seal")
    if not isinstance(value.get("files"), dict) or not value["files"]:
        raise ValueError("data snapshot has no sealed files")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("data snapshots cannot contain symlinks")
        if path.is_file() and path != root / "snapshot.json":
            actual.add(path.relative_to(root).as_posix())
    if actual != set(value["files"]):
        raise ValueError("snapshot file inventory differs from its seal")
    for relative, entry in value["files"].items():
        path = root / relative
        if (Path(relative).is_absolute() or ".." in Path(relative).parts
                or path.is_symlink() or not path.resolve().is_relative_to(root)
                or not path.is_file()):
            raise ValueError("snapshot file escapes its root or is missing")
        if path.stat().st_size != entry["bytes"] or file_hash(path) != entry["sha256"]:
            raise ValueError(f"snapshot file changed: {relative}")
    return {**value, "snapshot_id": identity, "snapshot": str(root)}


def status(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    if (root / "snapshot.json").is_file():
        return {"state": "sealed", **read_snapshot(root)}
    if (root / "failure.json").is_file():
        return {"state": "failed", "snapshot": str(root),
                "failure": json.loads((root / "failure.json").read_text())}
    if not (root / "request.json").is_file():
        raise FileNotFoundError(root)
    return {"state": "unsealed", "snapshot": str(root),
            "note": "Running or interrupted; this is not a worker heartbeat. Never admit unsealed data."}


def summary(snapshot: dict[str, Any], *, include_files: bool = False) -> dict[str, Any]:
    """Keep potentially thousands of file hashes on disk rather than flooding the agent context."""
    if "files" not in snapshot or include_files:
        return snapshot
    return {**{key: value for key, value in snapshot.items() if key != "files"},
            "file_count": len(snapshot["files"]),
            "manifest_path": str(Path(snapshot["snapshot"]) / "snapshot.json")}
