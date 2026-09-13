"""Typed, batch-oriented Parquet helpers for MolCascade's data plane."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from molcascade.artifacts.hashing import sha256_checksum
from molcascade.io.atomic import atomic_publish_file

if TYPE_CHECKING:
    import pyarrow as pa
    import pyarrow.dataset as ds


class ParquetValidationError(ValueError):
    """Raised when a Parquet file violates its declared data contract."""


@dataclass(frozen=True, slots=True)
class ParquetSummary:
    """Small serializable facts useful in an artifact manifest."""

    row_count: int
    row_group_count: int
    column_count: int
    schema_checksum: str
    size_bytes: int


def _modules() -> tuple[Any, Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.dataset as ds
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - exercised only in broken installs
        raise RuntimeError(
            "Parquet support requires the core dependency 'pyarrow'"
        ) from error
    return pa, ds, pq


def arrow_schema_checksum(schema: pa.Schema) -> str:
    """Hash Arrow's deterministic serialized schema representation."""

    return sha256_checksum(schema.serialize().to_pybytes())


def validate_parquet_file(
    path: str | Path,
    *,
    expected_schema: pa.Schema | None = None,
    expected_rows: int | None = None,
    check_schema_metadata: bool = True,
) -> ParquetSummary:
    """Read the footer and validate schema/row invariants without loading all rows."""

    _, _, pq = _modules()
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ParquetValidationError(f"not a regular Parquet file: {source}")
    if expected_rows is not None and expected_rows < 0:
        raise ValueError("expected_rows must be non-negative")
    try:
        parquet_file = pq.ParquetFile(source)
        metadata = parquet_file.metadata
        schema = parquet_file.schema_arrow
    except Exception as error:
        raise ParquetValidationError(f"invalid Parquet file {source}: {error}") from error

    if expected_rows is not None and metadata.num_rows != expected_rows:
        raise ParquetValidationError(
            f"row-count mismatch for {source}: expected {expected_rows}, "
            f"got {metadata.num_rows}"
        )
    if expected_schema is not None and not schema.equals(
        expected_schema,
        check_metadata=check_schema_metadata,
    ):
        raise ParquetValidationError(
            f"schema mismatch for {source}: expected {expected_schema}, got {schema}"
        )
    return ParquetSummary(
        row_count=metadata.num_rows,
        row_group_count=metadata.num_row_groups,
        column_count=metadata.num_columns,
        schema_checksum=arrow_schema_checksum(schema),
        size_bytes=source.stat().st_size,
    )


def _schema_with_metadata(
    schema: pa.Schema,
    metadata: Mapping[str, str] | None,
) -> pa.Schema:
    if metadata is None:
        return schema
    merged = dict(schema.metadata or {})
    for key, value in metadata.items():
        if not isinstance(key, str) or not key:
            raise ValueError("Parquet metadata keys must be non-empty strings")
        if not isinstance(value, str):
            raise TypeError("Parquet metadata values must be strings")
        merged[key.encode("utf-8")] = value.encode("utf-8")
    # Metadata order can affect serialized bytes; sorting makes repeat writes stable.
    return schema.with_metadata(dict(sorted(merged.items())))


def _temporary_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".parquet.tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(name)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def write_parquet_table(
    table: pa.Table,
    destination: str | Path,
    *,
    expected_schema: pa.Schema | None = None,
    metadata: Mapping[str, str] | None = None,
    compression: str = "zstd",
    row_group_size: int = 128 * 1024,
    use_dictionary: bool | Sequence[str] = True,
    overwrite: bool = False,
) -> ParquetSummary:
    """Write and validate an Arrow table before atomically publishing one file."""

    pa, _, pq = _modules()
    if not isinstance(table, pa.Table):
        raise TypeError("table must be a pyarrow.Table")
    if row_group_size <= 0:
        raise ValueError("row_group_size must be positive")
    if expected_schema is not None and not table.schema.equals(
        expected_schema,
        check_metadata=True,
    ):
        raise ParquetValidationError(
            f"table schema does not match expected schema: {table.schema}"
        )

    schema = _schema_with_metadata(table.schema, metadata)
    output_table = (
        table.replace_schema_metadata(schema.metadata) if metadata is not None else table
    )
    target = Path(destination)
    temporary = _temporary_path(target)
    try:
        pq.write_table(
            output_table,
            temporary,
            compression=compression,
            row_group_size=row_group_size,
            use_dictionary=use_dictionary,
        )
        summary = validate_parquet_file(
            temporary,
            expected_schema=schema,
            expected_rows=table.num_rows,
        )
        _fsync_file(temporary)
        atomic_publish_file(temporary, target, overwrite=overwrite)
        return summary
    finally:
        temporary.unlink(missing_ok=True)


def write_parquet_batches(
    batches: Iterable[pa.RecordBatch],
    destination: str | Path,
    *,
    schema: pa.Schema,
    metadata: Mapping[str, str] | None = None,
    compression: str = "zstd",
    row_group_size: int = 128 * 1024,
    use_dictionary: bool | Sequence[str] = True,
    overwrite: bool = False,
) -> ParquetSummary:
    """Stream validated record batches into an atomically published Parquet file."""

    pa, _, pq = _modules()
    if row_group_size <= 0:
        raise ValueError("row_group_size must be positive")
    output_schema = _schema_with_metadata(schema, metadata)
    target = Path(destination)
    temporary = _temporary_path(target)
    row_count = 0
    try:
        with pq.ParquetWriter(
            temporary,
            output_schema,
            compression=compression,
            use_dictionary=use_dictionary,
        ) as writer:
            for index, batch in enumerate(batches):
                if not isinstance(batch, pa.RecordBatch):
                    raise TypeError(f"batch {index} is not a pyarrow.RecordBatch")
                if not batch.schema.equals(schema, check_metadata=True):
                    raise ParquetValidationError(
                        f"schema mismatch in record batch {index}: {batch.schema}"
                    )
                output_batch = (
                    batch.replace_schema_metadata(output_schema.metadata)
                    if batch.schema != output_schema
                    else batch
                )
                writer.write_batch(output_batch, row_group_size=row_group_size)
                row_count += batch.num_rows
        summary = validate_parquet_file(
            temporary,
            expected_schema=output_schema,
            expected_rows=row_count,
        )
        _fsync_file(temporary)
        atomic_publish_file(temporary, target, overwrite=overwrite)
        return summary
    finally:
        temporary.unlink(missing_ok=True)


def read_parquet_table(
    source: str | Path,
    *,
    columns: Sequence[str] | None = None,
    filter_expression: ds.Expression | None = None,
) -> pa.Table:
    """Read a file or partitioned dataset with projection and predicate pushdown."""

    _, ds, _ = _modules()
    dataset = ds.dataset(Path(source), format="parquet")
    return dataset.to_table(columns=columns, filter=filter_expression)


def iter_parquet_batches(
    source: str | Path,
    *,
    columns: Sequence[str] | None = None,
    filter_expression: ds.Expression | None = None,
    batch_size: int = 64 * 1024,
) -> Iterator[pa.RecordBatch]:
    """Scan a file or dataset in bounded Arrow record batches."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    _, ds, _ = _modules()
    dataset = ds.dataset(Path(source), format="parquet")
    scanner = dataset.scanner(
        columns=columns,
        filter=filter_expression,
        batch_size=batch_size,
    )
    yield from scanner.to_batches()


__all__ = [
    "ParquetSummary",
    "ParquetValidationError",
    "arrow_schema_checksum",
    "iter_parquet_batches",
    "read_parquet_table",
    "validate_parquet_file",
    "write_parquet_batches",
    "write_parquet_table",
]
