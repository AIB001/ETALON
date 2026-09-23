"""Bounded inspection of recorded results and immutable screening configurations.

The runtime service supplies the trusted node result. Callers can select a sealed
member or a recorded stage artifact, never an arbitrary host path.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from etalon.boundary.infra import load
from etalon.boundary.screen import Screen
from etalon.data.artifacts import digest, read_snapshot, write_json

DESIGN_SCHEMA = "etalon-cascade-design/1"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
_RESPONSE_ALLOWANCE = MAX_RESPONSE_BYTES - 4096  # Room for the CLI/MCP envelope.
_TEXT_SUFFIXES = {".txt", ".md", ".log", ".yaml", ".yml", ".smi", ".sdf", ".pdb", ".cif", ".fa", ".fasta"}


def _json(value: Any, *, binary: bool = False) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if binary and isinstance(value, bytes):
        return {"$bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, list):
        return [_json(item, binary=binary) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json(item, binary=binary) for key, item in value.items()}
    raise ValueError("values must be JSON with string object keys")


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8"))


def _directory(value: str | Path, *, create: bool = False) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError("workspace must be an absolute directory path")
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("workspace must be an absolute directory path without '..'")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError("workspace directories must not be symlinks")
        if create:
            current.mkdir(exist_ok=True)
        if not current.is_dir():
            raise FileNotFoundError(f"workspace directory does not exist: {current}")
    return path


def _relative(member: str) -> tuple[str, ...]:
    if (not isinstance(member, str) or not member or "\\" in member or "\x00" in member
            or PurePosixPath(member).is_absolute() or PureWindowsPath(member).drive
            or any(part in {"", ".", ".."} for part in member.split("/"))):
        raise ValueError("member must be a normalized relative path within the snapshot")
    return tuple(member.split("/"))


def _read_bytes(root: Path, member: str, *, maximum: int = MAX_MEMBER_BYTES) -> bytes:
    """Open each path component without following a symlink; reject special files."""
    parts = _relative(member)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    with ExitStack() as stack:
        try:
            descriptor = os.open(root, directory_flags)
            stack.callback(os.close, descriptor)
            for part in parts[:-1]:
                descriptor = os.open(part, directory_flags, dir_fd=descriptor)
                stack.callback(os.close, descriptor)
            descriptor = os.open(parts[-1], os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0), dir_fd=descriptor)
            handle = stack.enter_context(os.fdopen(descriptor, "rb"))
        except OSError as error:
            raise ValueError("snapshot/configuration member is missing or contains a symlink") from error
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("snapshot/configuration member must be a regular file")
        if metadata.st_size > maximum:
            raise ValueError(f"member exceeds the bounded reader limit of {maximum} bytes")
        contents = handle.read(maximum + 1)
        if len(contents) > maximum:
            raise ValueError(f"member exceeds the bounded reader limit of {maximum} bytes")
        return contents


def configure(workspace: str | Path, configuration: dict) -> dict:
    """Validate and exclusively publish a caller-authored MolCascade configuration.

    Accept either a full ``kind=cascade`` configuration or the explicit design
    envelope ``{schema: etalon-cascade-design/1, name, tiers, ...}``. The latter
    accepts only compose's target, standardize_settings and finalize options.
    No example scientific hierarchy is inserted. Compilation/preflight still
    happens when the caller makes a screening plan with actual inputs.
    """
    if not isinstance(configuration, dict):
        raise ValueError("configuration must be a JSON object")
    raw = _json(configuration)
    if _size(raw) > _RESPONSE_ALLOWANCE:
        raise ValueError("configuration exceeds the 1 MiB response limit")
    load("molcascade")
    from molcascade.cascade.models import CascadeConfig

    if raw.get("schema") == DESIGN_SCHEMA:
        from etalon.campaign.design import compose

        required = {"schema", "name", "tiers"}
        optional = {"target", "standardize_settings", "finalize"}
        if required - raw.keys() or raw.keys() - required - optional or not isinstance(raw["tiers"], list):
            raise ValueError("cascade design requires schema, name and tiers; only compose options are allowed")
        for key in optional:
            if key in raw and raw[key] is not None and not isinstance(raw[key], dict):
                raise ValueError(f"design {key} must be an object or null")
        if any(not isinstance(tier, dict) for tier in raw["tiers"]):
            raise ValueError("design tiers must be JSON objects")
        normalized = compose(raw["name"], raw["tiers"], **{key: raw[key] for key in optional if key in raw})
    else:
        if raw.get("kind") != "cascade":
            raise ValueError(f"configuration requires kind=cascade or schema={DESIGN_SCHEMA}")
        normalized = CascadeConfig.model_validate(raw).model_dump(mode="json")
    identity = digest(normalized)
    root = _directory(workspace, create=True)
    directory = root / "configurations"
    path = directory / f"{identity}.json"
    result = {"config_path": str(path), "config_id": identity, "configuration": normalized}
    if _size(result) > _RESPONSE_ALLOWANCE:
        raise ValueError("normalized configuration exceeds the 1 MiB response limit")
    _directory(directory, create=True)
    if path.is_symlink():
        raise ValueError("configuration file must not be a symlink")
    try:
        write_json(path, normalized)
    except FileExistsError:
        existing = json.loads(_read_bytes(directory, path.name, maximum=MAX_RESPONSE_BYTES))
        if existing != normalized or digest(existing) != identity:
            raise ValueError("existing configuration differs from its immutable identity") from None
    return result


def _select(value: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("result member must be an empty string or a JSON Pointer such as /run/stages")
    if not pointer:
        return value
    for raw in pointer[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            raise ValueError("invalid JSON Pointer escape")
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(value):
            value = value[int(key)]
        else:
            raise KeyError(f"result member does not exist: {pointer}")
    return value


def _page(value: Any, *, offset: int, limit: int, **metadata: Any) -> dict:
    if isinstance(value, dict):
        keys = list(value)
        total, unit = len(keys), "fields"

        def selection(end):
            return {key: value[key] for key in keys[offset:end]}
    elif isinstance(value, (list, str)):
        total, unit = len(value), "items" if isinstance(value, list) else "characters"

        def selection(end):
            return value[offset:end]
    else:
        total, unit = 1, "values"

        def selection(end):
            return value if end > offset else None

    if offset > total:
        raise ValueError(f"offset {offset} exceeds total {total}")
    requested = min(limit, total - offset)

    def build(count):
        end = offset + count
        return {**metadata, "data": _json(selection(end), binary=True), "unit": unit,
                "total": total, "offset": offset, "limit": limit, "returned": count,
                "next_offset": end if end < total else None, "size_limited": count < requested}

    page = build(requested)
    if _size(page) <= _RESPONSE_ALLOWANCE:
        return page
    low, high = 0, requested
    while low < high:
        middle = (low + high + 1) // 2
        if _size(build(middle)) <= _RESPONSE_ALLOWANCE:
            low = middle
        else:
            high = middle - 1
    if not low:
        raise ValueError("one result item exceeds the 1 MiB response limit; select a smaller JSON Pointer if available")
    return build(low)


def read_result(result: dict, *, kind: str = "result", member: str = "",
                artifact_id: str = "", contract_id: str = "", offset: int = 0,
                limit: int = 100) -> dict:
    """Read a bounded page from a trusted node result or its verified artifacts.

    ``result`` accepts an optional JSON Pointer in ``member``. For ``snapshot``,
    an empty member lists sealed files, otherwise member is exactly one listed
    relative file. JSON objects page by fields, arrays/CSV by rows, text by Unicode
    characters. ``screen`` only accepts a stage artifact recorded in result.run.
    Binary Arrow cells are represented explicitly as ``{$bytes_base64: ...}``.
    Each response stays below 1 MiB; snapshot text/JSON members are at most 64 MiB.
    """
    if not isinstance(result, dict):
        raise ValueError("node result must be an object")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("offset must be a nonnegative integer and limit an integer between 1 and 1000")
    if not all(isinstance(value, str) for value in (kind, member, artifact_id, contract_id)):
        raise ValueError("kind, member, artifact_id and contract_id must be strings")
    if kind == "result":
        if artifact_id or contract_id:
            raise ValueError("artifact_id and contract_id apply only to screen results")
        return _page(_select(result, member), offset=offset, limit=limit, kind=kind, format="json", member=member)
    if kind == "snapshot":
        if artifact_id or contract_id:
            raise ValueError("artifact_id and contract_id apply only to screen results")
        if member:
            _relative(member)
        root = _directory(result.get("snapshot"))
        if (root / "snapshot.json").is_symlink():
            raise ValueError("snapshot manifest must not be a symlink")
        snapshot = read_snapshot(root)
        metadata = {"kind": kind, "snapshot_id": snapshot["snapshot_id"], "member": member}
        if not member:
            inventory = [{"member": path, **entry} for path, entry in sorted(snapshot["files"].items())]
            return _page(inventory, offset=offset, limit=limit, format="manifest", **metadata)
        if member not in snapshot["files"]:
            raise KeyError("member is not listed in the sealed snapshot")
        suffix = PurePosixPath(member).suffix.lower()
        if suffix not in {".json", ".csv", ".tsv", ".jsonl", *_TEXT_SUFFIXES}:
            raise ValueError("snapshot member is not a supported JSON, delimited or text file")
        contents = _read_bytes(root, member)
        entry = snapshot["files"][member]
        if len(contents) != entry["bytes"] or hashlib.sha256(contents).hexdigest() != entry["sha256"]:
            raise ValueError("snapshot member changed while reading")
        text = contents.decode("utf-8-sig")
        if suffix == ".json":
            value, format_name = json.loads(text), "json"
        elif suffix == ".jsonl":
            value, format_name = [json.loads(line) for line in text.splitlines() if line.strip()], "jsonl"
        elif suffix in {".csv", ".tsv"}:
            reader = csv.DictReader(io.StringIO(text, newline=""), delimiter="\t" if suffix == ".tsv" else ",")
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError("delimited member needs unique column names")
            value = list(reader)
            if any(None in row for row in value):
                raise ValueError("delimited member has rows wider than its header")
            format_name = "csv" if suffix == ".csv" else "tsv"
        else:
            value, format_name = text, "text"
        return _page(value, offset=offset, limit=limit, format=format_name, **metadata)
    if kind == "screen":
        if member:
            raise ValueError("screen results use artifact_id, not a member path")
        run = result.get("run")
        stages = run.get("stages") if isinstance(run, dict) else None
        if (not artifact_id or not isinstance(stages, list)
                or not any(isinstance(stage, dict) and stage.get("artifact_id") == artifact_id for stage in stages)):
            raise ValueError("artifact_id must belong to a recorded stage in this node result")
        workspace = _directory(result.get("workspace"))
        rows = Screen(workspace).read(artifact_id, contract_id=contract_id or None)
        return _page(rows, offset=offset, limit=limit, kind=kind, format="rows",
                     artifact_id=artifact_id, contract_id=contract_id or None)
    raise ValueError("kind must be result, snapshot or screen")
