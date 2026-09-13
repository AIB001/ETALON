"""Internal streaming helpers for chemistry plugin Parquet datasets."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.parquet as pq

from molcascade.artifacts.models import validate_relative_artifact_path
from molcascade.contracts import DataContract, validate_schema
from molcascade.errors import ContractError, PluginError
from molcascade.io.parquet import iter_parquet_batches, write_parquet_batches

if TYPE_CHECKING:
    from molcascade.plugins.api import StageInput


def _resolve_claimed_file(root: Path, relative_path: str) -> Path:
    """Resolve one manifest-claimed file without following artifact symlinks."""

    try:
        validate_relative_artifact_path(relative_path)
    except ValueError as error:
        raise PluginError(
            f"claimed input path is not canonical: {relative_path}",
            code="PLUGIN_INPUT_FILE_INVALID",
            context={"path": relative_path, "reason": "noncanonical"},
        ) from error
    if PurePosixPath(relative_path).suffix.lower() != ".parquet":
        raise PluginError(
            f"claimed input is not a .parquet file: {relative_path}",
            code="PLUGIN_INPUT_FILE_INVALID",
            context={"path": relative_path, "reason": "extension"},
        )
    candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    current = root
    for part in PurePosixPath(relative_path).parts:
        current /= part
        if current.is_symlink():
            raise PluginError(
                f"claimed input path contains a symlink: {relative_path}",
                code="PLUGIN_INPUT_FILE_INVALID",
                context={"path": relative_path, "reason": "symlink"},
            )
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise PluginError(
            f"claimed input file does not exist: {relative_path}",
            code="PLUGIN_INPUT_FILE_NOT_FOUND",
            context={"path": relative_path},
        ) from error
    try:
        resolved.relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as error:
        raise PluginError(
            f"claimed input file escapes its artifact root: {relative_path}",
            code="PLUGIN_INPUT_FILE_INVALID",
            context={"path": relative_path, "reason": "containment"},
        ) from error
    if candidate.is_symlink() or not resolved.is_file():
        raise PluginError(
            f"claimed input is not a regular file: {relative_path}",
            code="PLUGIN_INPUT_FILE_INVALID",
            context={"path": relative_path, "reason": "not_regular"},
        )
    return resolved


def _matches_contract(
    path: Path,
    contract: DataContract,
    *,
    exact_claim: bool,
) -> bool:
    """Inspect a candidate and strictly validate exact dataset claims."""

    try:
        schema = pq.ParquetFile(path).schema_arrow
    except Exception as error:
        raise PluginError(
            f"could not inspect input Parquet file {path}: {error}",
            code="PLUGIN_INPUT_PARQUET_INVALID",
            context={"path": str(path)},
        ) from error
    metadata = schema.metadata or {}
    declared = metadata.get(b"molcascade.contract")
    if declared is None:
        if exact_claim:
            raise PluginError(
                f"claimed Parquet file has no contract metadata: {path}",
                code="PLUGIN_INPUT_CONTRACT_METADATA_MISSING",
                context={"path": str(path), "expected_contract": contract.id},
            )
    else:
        try:
            declared_id = declared.decode("ascii")
        except UnicodeDecodeError as error:
            if not exact_claim:
                return False
            raise PluginError(
                f"claimed Parquet file has invalid contract metadata: {path}",
                code="PLUGIN_INPUT_CONTRACT_MISMATCH",
                context={"path": str(path), "expected_contract": contract.id},
            ) from error
        if declared_id != contract.id:
            if not exact_claim:
                return False
            raise PluginError(
                f"claimed Parquet file declares {declared_id}, expected {contract.id}",
                code="PLUGIN_INPUT_CONTRACT_MISMATCH",
                context={
                    "path": str(path),
                    "declared_contract": declared_id,
                    "expected_contract": contract.id,
                },
            )
    try:
        validate_schema(schema, contract)
    except ContractError as error:
        if not exact_claim and declared is None:
            return False
        raise PluginError(
            f"input Parquet schema does not satisfy {contract.id}: {path}: {error}",
            code="PLUGIN_INPUT_CONTRACT_MISMATCH",
            context={"path": str(path), "expected_contract": contract.id},
        ) from error
    return True


def discover_contract_files(stage_input: StageInput, contract: DataContract) -> tuple[Path, ...]:
    """Resolve an exact dataset view, with a scan fallback only for legacy inputs."""

    if stage_input.contract_id != contract.id:
        raise PluginError(
            f"stage input declares {stage_input.contract_id}, expected {contract.id}",
            code="PLUGIN_INPUT_CONTRACT_MISMATCH",
            context={
                "declared_contract": stage_input.contract_id,
                "expected_contract": contract.id,
            },
        )
    root = stage_input.root
    if root.is_symlink() or not root.exists():
        raise PluginError(
            f"input artifact root does not exist or is a symlink: {root}",
            code="PLUGIN_INPUT_NOT_FOUND",
            context={"root": str(root)},
        )
    if stage_input.file_paths:
        if not root.is_dir():
            raise PluginError(
                f"exact dataset input root is not a directory: {root}",
                code="PLUGIN_INPUT_NOT_FOUND",
                context={"root": str(root)},
            )
        candidates = tuple(
            _resolve_claimed_file(root, relative_path)
            for relative_path in stage_input.file_paths
        )
        for path in candidates:
            _matches_contract(path, contract, exact_claim=True)
        return candidates

    candidates = (root,) if root.is_file() else tuple(sorted(root.rglob("*.parquet")))
    selected: list[Path] = []
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        if _matches_contract(path, contract, exact_claim=False):
            selected.append(path)
    if not selected:
        raise PluginError(
            f"no {contract.id} Parquet dataset found beneath {root}",
            code="PLUGIN_INPUT_DATASET_NOT_FOUND",
            context={"root": str(root), "contract_id": contract.id},
        )
    return tuple(selected)


def iter_contract_batches(
    stage_input: StageInput,
    contract: DataContract,
    *,
    batch_size: int,
) -> Iterator[pa.RecordBatch]:
    """Stream validated-schema batches from every matching artifact file."""

    for path in discover_contract_files(stage_input, contract):
        for batch in iter_parquet_batches(path, batch_size=batch_size):
            validate_schema(batch.schema, contract)
            yield batch


def open_stage_database(path: Path) -> sqlite3.Connection:
    """Open an ephemeral, deterministic global-index database in staging."""

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    return connection


def query_record_batches(
    connection: sqlite3.Connection,
    query: str,
    *,
    schema: pa.Schema,
    batch_size: int,
) -> Iterator[pa.RecordBatch]:
    """Convert a deterministic SQLite query into bounded Arrow batches."""

    cursor = connection.execute(query)
    while rows := cursor.fetchmany(batch_size):
        values: list[dict[str, object]] = []
        for row in rows:
            converted: dict[str, object] = {}
            for field, value in zip(schema, row, strict=True):
                # SQLite has no native Boolean storage class and returns 0/1
                # integers for CASE expressions.  Convert only at the exact
                # Arrow-schema boundary; accepting arbitrary truthy values
                # would hide a malformed query result.
                if pa.types.is_boolean(field.type) and value is not None:
                    if value not in (0, 1, False, True):
                        raise PluginError(
                            f"SQLite query returned a non-boolean value for {field.name}",
                            code="PLUGIN_QUERY_TYPE_INVALID",
                            context={"field": field.name, "value": str(value)},
                        )
                    value = bool(value)
                converted[field.name] = value
            values.append(converted)
        yield pa.RecordBatch.from_pylist(values, schema=schema)


def write_query_parquet(
    connection: sqlite3.Connection,
    query: str,
    *,
    schema: pa.Schema,
    destination: Path,
    batch_size: int,
) -> None:
    """Stream a SQLite result into an exact-schema Parquet output."""

    write_parquet_batches(
        query_record_batches(
            connection,
            query,
            schema=schema,
            batch_size=batch_size,
        ),
        destination,
        schema=schema,
        row_group_size=batch_size,
    )


def require_single_input(
    inputs: Mapping[str, StageInput],
    *,
    contract_id: str,
) -> StageInput:
    """Resolve exactly one named stage input for a contract."""

    matches = [value for value in inputs.values() if value.contract_id == contract_id]
    if len(matches) != 1:
        raise PluginError(
            f"stage requires exactly one {contract_id} input, found {len(matches)}",
            code="PLUGIN_INPUT_CARDINALITY_INVALID",
            context={"contract_id": contract_id, "matching_inputs": len(matches)},
        )
    return matches[0]


__all__ = [
    "discover_contract_files",
    "iter_contract_batches",
    "open_stage_database",
    "query_record_batches",
    "require_single_input",
    "write_query_parquet",
]
