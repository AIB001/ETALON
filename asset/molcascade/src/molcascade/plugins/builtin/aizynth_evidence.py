"""Native AiZynthFinder evidence using the existing artifact data plane."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from molcascade.config.canonical import canonical_json
from molcascade.contracts import SYNTHESIS_ROUTES_V1, SYNTHESIS_SEARCH_V1

ROUTES_PATH = Path("datasets/synthesis_routes/part-00000.parquet")
SEARCH_PATH = Path("datasets/synthesis_search/part-00000.parquet")


def file_identity(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    before = path.stat()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"dependency changed while hashing: {path}")
    return {"path": str(path), "sha256": digest.hexdigest(), "size_bytes": after.st_size}


def write_evidence(
    root: Path, scratch: Path, *, parent_ids: list[str], canonical: list[str],
    method_id: str, search_id: str, provenance: dict[str, Any],
    records: list[dict[str, Any]] | None, status: str,
) -> None:
    """Write complete native bytes once; aligned per-target records remain queryable."""
    output = scratch / "results.json"
    payload = output.read_bytes() if output.exists() else None
    log = scratch / "console.log"
    rows = []
    for index, (parent_id, target) in enumerate(zip(parent_ids, canonical, strict=True)):
        record = records[index] if records is not None else None
        row_status = status
        if record is not None:
            row_status = "SOLVED" if record.get("is_solved") else "NOT_FOUND_WITHIN_BUDGET"
        rows.append({
            "parent_id": parent_id, "method_id": method_id, "search_id": search_id,
            "target_index": index, "target_smiles": target, "status": row_status,
            # Native JSON can contain NaN statistics. Keep them exactly as JSON's
            # native serializer does; the byte-exact output is separately archived.
            "record_json": json.dumps(record, ensure_ascii=False) if record is not None else None,
        })
    archive = [{
        "search_id": search_id, "method_id": method_id, "status": status,
        "input_bytes": (scratch / "targets.smi").read_bytes(),
        "config_bytes": (scratch / "config.original.yml").read_bytes(),
        "output_bytes": payload,
        "output_sha256": hashlib.sha256(payload).hexdigest() if payload is not None else None,
        "log_bytes": log.read_bytes() if log.exists() else b"",
        "provenance_json": canonical_json(provenance),
    }]
    for relative, contract, data in (
        (ROUTES_PATH, SYNTHESIS_ROUTES_V1, rows),
        (SEARCH_PATH, SYNTHESIS_SEARCH_V1, archive),
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(data, schema=contract.schema), destination,
                       compression="zstd")


def retain_failure(root: Path, *, destination_root: str | None) -> dict[str, Any]:
    """Retain an unpublished failure bundle and return its error-context locator.

    A configured root has caller-owned retention. The fallback uses system temp
    and explicitly reports that weaker lifetime. Neither is a successful artifact.
    """
    if destination_root is not None:
        Path(destination_root).mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="aizynth-failed-", dir=destination_root))
    files = []
    for relative in (ROUTES_PATH, SEARCH_PATH):
        source = root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        item = file_identity(target)
        item["path"] = relative.as_posix()
        files.append(item)
    manifest = {
        "schema": "aizynthfinder-failure-evidence/1", "published_artifact": False,
        "retention": "caller_owned" if destination_root else "system_temporary",
        "files": files,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    return {"directory": str(destination), "manifest": file_identity(manifest_path),
            "published_artifact": False, "retention": manifest["retention"]}
