"""Bounded local CSV/TSV/SDF snapshots for licensed or manually exported catalogs."""

import csv
import gzip
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from pydantic import Field

from .errors import MolQuarryError
from .http import fingerprint
from .models import InputModel, Provenance, QueryResult, utcnow


class ImportOptions(InputModel):
    source_version: str = Field(min_length=1, max_length=200)
    max_bytes: int = Field(default=104857600, ge=1)
    max_records: int = Field(default=100000, ge=1, le=10000000)


class LocalSearch(InputModel):
    snapshot_id: str = Field(pattern=r"^[a-z0-9_]+-[a-f0-9]{24}$")
    query: str = Field(default="", max_length=1000)
    field: str | None = Field(default=None, min_length=1, max_length=200)
    value: str | None = Field(default=None, max_length=2000)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


def connect(home):
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / "catalogs.sqlite", timeout=30)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, manifest TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records (
            snapshot TEXT REFERENCES snapshots(id), row_no INTEGER,
            data TEXT NOT NULL, search_text TEXT NOT NULL, PRIMARY KEY(snapshot,row_no));
        CREATE TABLE IF NOT EXISTS cells (
            snapshot TEXT, row_no INTEGER, field TEXT, value TEXT,
            FOREIGN KEY(snapshot,row_no) REFERENCES records(snapshot,row_no));
        CREATE INDEX IF NOT EXISTS cells_lookup ON cells(snapshot,field,value,row_no);
    """)
    return db


def read_records(path, fmt, *, max_bytes):
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as stream:
        used = 0

        def lines():
            nonlocal used
            while True:
                line = stream.readline(4 * 1024 * 1024 + 1)
                if not line:
                    return
                used += len(line.encode("utf-8"))
                if used > max_bytes or len(line) > 4 * 1024 * 1024:
                    raise MolQuarryError(
                        "import_too_large", "Expanded catalog exceeds byte/line budget"
                    )
                yield line

        if fmt in {"csv", "tsv"}:
            reader = csv.DictReader(lines(), delimiter="," if fmt == "csv" else "\t")
            fields = reader.fieldnames
            if (
                not fields
                or len(fields) > 256
                or len(set(fields)) != len(fields)
                or any(not f or len(f) > 200 for f in fields)
            ):
                raise MolQuarryError(
                    "invalid_catalog",
                    "Catalog requires unique, nonempty column names (maximum 256)",
                )
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise MolQuarryError("invalid_catalog", "Row width does not match the header")
                yield row
        else:
            block, size = [], 0
            for line in lines():
                if line.strip() == "$$$$":
                    if block:
                        yield parse_sdf("".join(block))
                    block, size = [], 0
                else:
                    block.append(line)
                    size += len(line)
                    if size > 4 * 1024 * 1024:
                        raise MolQuarryError("import_too_large", "SDF record exceeds 4 MiB")
            if block and "".join(block).strip():
                yield parse_sdf("".join(block))


def parse_sdf(block):
    if not re.search(r"V(?:2000|3000)\b", block) or "M  END" not in block:
        raise MolQuarryError("invalid_catalog", "Malformed SDF/MOL record")
    rows = {"_sdf": block, "_title": block.splitlines()[0] if block.splitlines() else ""}
    for match in re.finditer(r">[^\n]*<([^>]+)>[^\n]*\n(.*?)(?=\n\s*\n|\Z)", block, re.S):
        key = match.group(1)
        if key in rows:
            raise MolQuarryError(
                "invalid_catalog", "SDF property names must be unique and not reserved"
            )
        rows[key] = match.group(2).strip()
    return rows


def import_catalog(home: Path, spec, path: Path, options: ImportOptions):
    filename = path.name.lower()
    base = filename.removesuffix(".gz")
    fmt = base.rsplit(".", 1)[-1]
    if fmt not in {"csv", "tsv", "sdf"}:
        raise MolQuarryError(
            "unsupported_format", "Local import supports CSV/TSV/SDF, optionally gzip"
        )
    if not path.is_file():
        raise MolQuarryError("not_found", "Local catalog file not found")
    directory = home / "catalogs"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".import-", dir=directory) as tmp:
        suffix = "." + fmt + (".gz" if filename.endswith(".gz") else "")
        frozen = Path(tmp) / ("source" + suffix)
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as source, frozen.open("xb") as dest:
            while chunk := source.read(65536):
                size += len(chunk)
                if size > options.max_bytes:
                    raise MolQuarryError("import_too_large", "Catalog file exceeds max_bytes")
                digest.update(chunk)
                dest.write(chunk)
        sha256 = digest.hexdigest()
        identity = fingerprint(
            {"source": spec.id, "version": options.source_version, "sha256": sha256}
        )[:24]
        snapshot = f"{spec.id}-{identity}"
        destination = directory / (snapshot + suffix)
        manifest = {
            "snapshot_id": snapshot,
            "source": spec.id,
            "source_version": options.source_version,
            "version_basis": "user-supplied export label",
            "imported_at": utcnow(),
            "source_path": str(path.resolve()),
            "path": str(destination),
            "sha256": sha256,
            "bytes": size,
            "format": fmt,
            "license_url": spec.license_url,
            "license_notes": spec.license_notes,
            "transformation": "Raw file retained; fields indexed without chemical standardization.",
        }
        db = connect(home)
        published = False
        try:
            with db:
                previous = db.execute(
                    "SELECT manifest FROM snapshots WHERE id=?", (snapshot,)
                ).fetchone()
                if previous:
                    return {"ok": True, "already_imported": True, **json.loads(previous[0])}
                db.execute("INSERT INTO snapshots VALUES (?, ?)", (snapshot, "{}"))
                count = 0
                columns = set()
                for count, row in enumerate(
                    read_records(frozen, fmt, max_bytes=options.max_bytes), 1
                ):
                    if count > options.max_records:
                        raise MolQuarryError(
                            "import_too_large",
                            "Catalog exceeds max_records; no partial snapshot was published",
                        )
                    raw = json.dumps(row, ensure_ascii=False)
                    columns.update(k for k in row if k != "_sdf")
                    db.execute(
                        "INSERT INTO records VALUES (?, ?, ?, ?)",
                        (snapshot, count, raw, raw.casefold()),
                    )
                    db.executemany(
                        "INSERT INTO cells VALUES (?, ?, ?, ?)",
                        [(snapshot, count, k, v) for k, v in row.items() if not k.startswith("_")],
                    )
                if not count:
                    raise MolQuarryError("invalid_catalog", "Catalog contains no records")
                manifest["records"] = count
                manifest["fields"] = sorted(columns)
                db.execute(
                    "UPDATE snapshots SET manifest=? WHERE id=?", (json.dumps(manifest), snapshot)
                )
                os.link(frozen, destination)
                published = True
        except (csv.Error, UnicodeError, EOFError, gzip.BadGzipFile) as exc:
            raise MolQuarryError(
                "invalid_catalog", "Cannot parse catalog as UTF-8 CSV/TSV/SDF"
            ) from exc
        except BaseException:
            if published:
                destination.unlink(missing_ok=True)
            raise
        finally:
            db.close()
        return {"ok": True, "already_imported": False, **manifest}


def local_search(home: Path, params: LocalSearch):
    if (params.field is None) != (params.value is None):
        raise MolQuarryError("invalid_parameters", "field and value must be supplied together")
    db = connect(home)
    try:
        row = db.execute(
            "SELECT manifest FROM snapshots WHERE id=?", (params.snapshot_id,)
        ).fetchone()
        if not row:
            raise MolQuarryError("not_found", "Local snapshot not found")
        manifest = json.loads(row[0])
        where = "r.snapshot=? AND instr(r.search_text, ?) > 0"
        values = [params.snapshot_id, params.query.casefold()]
        if params.field is not None:
            where += (
                " AND r.row_no IN (SELECT row_no FROM cells WHERE snapshot=? AND field=? AND "
                "value=?)"
            )
            values.extend([params.snapshot_id, params.field, params.value])
        total = db.execute(f"SELECT count(*) FROM records r WHERE {where}", values).fetchone()[0]
        rows = db.execute(
            f"SELECT r.row_no,r.data FROM records r WHERE {where} "
            "ORDER BY r.row_no LIMIT ? OFFSET ?",
            [*values, params.limit, params.offset],
        ).fetchall()
    finally:
        db.close()
    records = [{"row_number": n, "fields": json.loads(data)} for n, data in rows]
    return QueryResult(
        source=manifest["source"],
        operation="local_search",
        parameters=params.model_dump(exclude_none=True),
        records=records,
        returned=len(records),
        total=total,
        next_parameters={**params.model_dump(), "offset": params.offset + params.limit}
        if params.offset + len(rows) < total
        else None,
        provenance=[
            Provenance(
                source=manifest["source"],
                url=Path(manifest["path"]).as_uri(),
                method="LOCAL_IMPORT",
                retrieved_at=manifest["imported_at"],
                request_sha256=fingerprint(params.model_dump()),
                response_sha256=fingerprint(records),
                source_version=manifest["source_version"],
                license_url=manifest["license_url"],
            )
        ],
        warnings=[
            f"Local export snapshot SHA256: {manifest['sha256']}. "
            "No live stock/price lookup or chemical equivalence matching."
        ],
    )


def list_catalogs(home):
    db = connect(home)
    try:
        return [
            json.loads(row[0]) for row in db.execute("SELECT manifest FROM snapshots ORDER BY id")
        ]
    finally:
        db.close()
