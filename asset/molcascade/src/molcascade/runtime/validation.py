"""Fail-closed validation of plugin output before artifact publication."""

from __future__ import annotations

import os
import sqlite3
import struct
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import ConfigDict, JsonValue, TypeAdapter, ValidationError

from molcascade.artifacts import ArtifactOutput, canonical_json_bytes
from molcascade.contracts import ContractRegistry, DataContract
from molcascade.errors import ContractError, PluginError
from molcascade.pipeline import CompiledStage
from molcascade.plugins import PendingOutput, StageResponse

_PARQUET_BATCH_SIZE = 65_536
_JSON_MAPPING = TypeAdapter(
    dict[str, JsonValue],
    config=ConfigDict(strict=True, allow_inf_nan=False),
)


@dataclass(frozen=True, slots=True)
class ValidatedStageResponse:
    """Canonical artifact outputs plus independently observed row counts."""

    outputs: tuple[ArtifactOutput, ...]
    row_counts: Mapping[str, int]


def _plugin_error(
    stage: CompiledStage,
    message: str,
    *,
    code: str,
    context: dict[str, Any] | None = None,
) -> PluginError:
    details: dict[str, Any] = {
        "stage_id": stage.stage_id,
        "plugin": stage.plugin_key,
    }
    if context:
        details.update(context)
    return PluginError(message, code=code, context=details)


def _validate_response_shape(
    stage: CompiledStage,
    response: StageResponse,
) -> None:
    declared_contracts = set(stage.descriptor.outputs)
    unknown = sorted(
        {
            output.contract_id
            for output in response.outputs.values()
            if output.contract_id not in declared_contracts
        }
    )
    if unknown:
        raise _plugin_error(
            stage,
            "plugin emitted a contract which its descriptor does not declare",
            code="PLUGIN_OUTPUT_CONTRACT_UNDECLARED",
            context={"contract_ids": unknown},
        )

    contract_ports: dict[str, list[str]] = {}
    for port, output in response.outputs.items():
        contract_ports.setdefault(output.contract_id, []).append(port)
    duplicates = {
        contract_id: sorted(ports)
        for contract_id, ports in contract_ports.items()
        if len(ports) > 1
    }
    if duplicates:
        raise _plugin_error(
            stage,
            "plugin assigned the same contract to multiple output ports",
            code="PLUGIN_OUTPUT_CONTRACT_DUPLICATE",
            context={"contract_ports": duplicates},
        )

    primary = response.outputs.get(stage.output_port)
    if primary is None:
        raise _plugin_error(
            stage,
            f"plugin did not emit the required {stage.output_port!r} output",
            code="PLUGIN_PRIMARY_OUTPUT_MISSING",
            context={"required_port": stage.output_port},
        )
    if primary.contract_id != stage.output_contract:
        raise _plugin_error(
            stage,
            "plugin primary output does not match the compiled edge contract",
            code="PLUGIN_PRIMARY_OUTPUT_CONTRACT_MISMATCH",
            context={
                "expected_contract": stage.output_contract,
                "actual_contract": primary.contract_id,
            },
        )

    for port, output in response.outputs.items():
        expected_contract = stage.descriptor.output_ports.get(port)
        if expected_contract is None:
            raise _plugin_error(
                stage,
                "plugin emitted an output port absent from its descriptor",
                code="PLUGIN_OUTPUT_PORT_UNDECLARED",
                context={
                    "port": port,
                    "declared_ports": sorted(stage.descriptor.output_ports),
                },
            )
        if output.contract_id != expected_contract:
            raise _plugin_error(
                stage,
                "plugin output port contract differs from its descriptor",
                code="PLUGIN_OUTPUT_PORT_CONTRACT_MISMATCH",
                context={
                    "port": port,
                    "expected_contract": expected_contract,
                    "actual_contract": output.contract_id,
                },
            )

    claimed: dict[str, str] = {}
    claimed_casefolded: dict[str, str] = {}
    for port, output in response.outputs.items():
        for relative_path in output.file_paths:
            previous = claimed.get(relative_path)
            if previous is not None:
                raise _plugin_error(
                    stage,
                    "plugin assigned one file to multiple output ports",
                    code="PLUGIN_OUTPUT_FILE_DUPLICATE",
                    context={
                        "file_path": relative_path,
                        "first_port": previous,
                        "second_port": port,
                    },
                )
            folded = relative_path.casefold()
            previous_case = claimed_casefolded.get(folded)
            if previous_case is not None:
                raise _plugin_error(
                    stage,
                    "plugin output paths collide on case-insensitive filesystems",
                    code="PLUGIN_OUTPUT_FILE_DUPLICATE",
                    context={
                        "first_path": previous_case,
                        "second_path": relative_path,
                    },
                )
            claimed[relative_path] = port
            claimed_casefolded[folded] = relative_path


def _resolve_staged_parquet(staging_root: Path, relative_path: str) -> Path:
    """Resolve a declared file while rejecting every symlink component."""

    supplied_root = Path(staging_root)
    if supplied_root.is_symlink():
        raise PluginError(
            "runner staging root may not be a symlink",
            code="PLUGIN_OUTPUT_PATH_INVALID",
        )
    root = supplied_root.resolve(strict=True)
    if not root.is_dir():
        raise PluginError(
            "runner staging root is not a real directory",
            code="PLUGIN_OUTPUT_PATH_INVALID",
        )

    candidate = root
    for part in PurePosixPath(relative_path).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise PluginError(
                f"plugin output path contains a symlink: {relative_path}",
                code="PLUGIN_OUTPUT_PATH_INVALID",
                context={"file_path": relative_path},
            )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise PluginError(
            f"plugin did not create its declared output file: {relative_path}",
            code="PLUGIN_OUTPUT_FILE_MISSING",
            context={"file_path": relative_path},
        ) from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PluginError(
            f"plugin output escapes the staging directory: {relative_path}",
            code="PLUGIN_OUTPUT_PATH_INVALID",
            context={"file_path": relative_path},
        ) from error
    if not resolved.is_file():
        raise PluginError(
            f"plugin output is not a regular file: {relative_path}",
            code="PLUGIN_OUTPUT_FILE_INVALID",
            context={"file_path": relative_path},
        )
    try:
        link_count = resolved.stat().st_nlink
    except OSError as error:
        raise PluginError(
            f"cannot inspect plugin output file: {relative_path}",
            code="PLUGIN_OUTPUT_FILE_INVALID",
            context={"file_path": relative_path},
        ) from error
    if link_count != 1:
        # A hard-linked staged file can retain an alias outside the staging
        # directory.  That alias could mutate the supposedly immutable bytes
        # after publication without changing the artifact directory itself.
        raise PluginError(
            f"plugin output file must not be hard-linked: {relative_path}",
            code="PLUGIN_OUTPUT_FILE_INVALID",
            context={"file_path": relative_path, "link_count": link_count},
        )
    if resolved.suffix.casefold() != ".parquet":
        raise PluginError(
            f"contract output must be Parquet: {relative_path}",
            code="PLUGIN_OUTPUT_FORMAT_INVALID",
            context={"file_path": relative_path},
        )
    return resolved


def _typed_scalar(value: Any) -> Any:
    """Encode an Arrow Python scalar without cross-type key collisions."""

    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        # Preserve -0 and NaN payloads deterministically.  Contract validation
        # decides whether such values are scientifically acceptable.
        return ["float64", struct.pack(">d", value).hex()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ["bytes", bytes(value).hex()]
    if isinstance(value, Decimal):
        decimal_tuple = value.as_tuple()
        return [
            "decimal",
            decimal_tuple.sign,
            list(decimal_tuple.digits),
            decimal_tuple.exponent,
        ]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat(timespec="microseconds")]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, time):
        return ["time", value.isoformat(timespec="microseconds")]
    if isinstance(value, (tuple, list)):
        return ["sequence", [_typed_scalar(item) for item in value]]
    if isinstance(value, Mapping):
        encoded_items = [
            [_typed_scalar(key), _typed_scalar(item)] for key, item in value.items()
        ]
        encoded_items.sort(key=canonical_json_bytes)
        return ["mapping", encoded_items]
    raise ContractError(
        f"primary-key scalar type is unsupported: {type(value).__name__}",
        code="CONTRACT_PRIMARY_KEY_UNSUPPORTED",
        context={"value_type": type(value).__name__},
    )


def _encoded_primary_keys(batch: pa.RecordBatch, contract: DataContract) -> list[bytes]:
    key_table = pa.Table.from_batches([batch]).select(list(contract.primary_key))
    return [
        canonical_json_bytes(
            [_typed_scalar(row[column]) for column in contract.primary_key]
        )
        for row in key_table.to_pylist()
    ]


def _validate_output_dataset(
    staging_root: Path,
    output: PendingOutput,
    contract: DataContract,
) -> int:
    descriptor, database_name = tempfile.mkstemp(
        prefix=".molcascade-pk-",
        suffix=".sqlite3",
        dir=staging_root,
    )
    os.close(descriptor)
    database_path = Path(database_name)
    connection: sqlite3.Connection | None = None
    total_rows = 0
    try:
        connection = sqlite3.connect(database_path)
        connection.execute("CREATE TABLE seen (key BLOB PRIMARY KEY) WITHOUT ROWID")
        for relative_path in output.file_paths:
            parquet_path = _resolve_staged_parquet(staging_root, relative_path)
            try:
                parquet = pq.ParquetFile(parquet_path)
                contract.validate_schema(parquet.schema_arrow)
                for batch in parquet.iter_batches(batch_size=_PARQUET_BATCH_SIZE):
                    contract.validate(batch)
                    keys = _encoded_primary_keys(batch, contract)
                    try:
                        connection.executemany(
                            "INSERT INTO seen(key) VALUES (?)",
                            ((sqlite3.Binary(key),) for key in keys),
                        )
                    except sqlite3.IntegrityError as error:
                        raise ContractError(
                            f"primary key is not globally unique for {contract.id}",
                            code="CONTRACT_PRIMARY_KEY_DUPLICATE",
                            context={
                                "contract_id": contract.id,
                                "primary_key": list(contract.primary_key),
                                "file_path": relative_path,
                            },
                        ) from error
                    total_rows += batch.num_rows
            except ContractError:
                raise
            except (OSError, ValueError, pa.ArrowException) as error:
                raise ContractError(
                    f"cannot read Parquet output for {contract.id}: {relative_path}",
                    code="CONTRACT_PARQUET_INVALID",
                    context={
                        "contract_id": contract.id,
                        "file_path": relative_path,
                        "arrow_error": str(error),
                    },
                ) from error
        connection.commit()
    finally:
        if connection is not None:
            connection.close()
        database_path.unlink(missing_ok=True)
        Path(f"{database_path}-journal").unlink(missing_ok=True)
    return total_rows


def _validate_declared_row_count(
    stage: CompiledStage,
    *,
    port: str,
    output: PendingOutput,
    actual: int,
) -> None:
    if "row_count" not in output.metadata:
        return
    expected = output.metadata["row_count"]
    if type(expected) is not int or expected < 0:
        raise _plugin_error(
            stage,
            "output row_count metadata must be a non-negative integer",
            code="PLUGIN_OUTPUT_METADATA_INVALID",
            context={"port": port},
        )
    if expected != actual:
        raise ContractError(
            f"declared row count does not match Parquet data for {port!r}",
            code="CONTRACT_ROW_COUNT_MISMATCH",
            context={
                "stage_id": stage.stage_id,
                "contract_id": output.contract_id,
                "port": port,
                "expected_row_count": expected,
                "actual_row_count": actual,
            },
        )


def validate_stage_response(
    stage: CompiledStage,
    response: StageResponse,
    staging_root: str | Path,
    contracts: ContractRegistry,
) -> ValidatedStageResponse:
    """Validate every declared dataset and return commit-ready outputs."""

    if not isinstance(stage, CompiledStage):
        raise TypeError("stage must be a CompiledStage")
    if not isinstance(response, StageResponse):
        raise _plugin_error(
            stage,
            "plugin returned an object other than StageResponse",
            code="PLUGIN_RESPONSE_INVALID",
            context={"actual_type": type(response).__name__},
        )
    if not isinstance(contracts, ContractRegistry):
        raise TypeError("contracts must be a ContractRegistry")

    root = Path(staging_root)
    _validate_response_shape(stage, response)
    try:
        _JSON_MAPPING.validate_python(dict(response.metadata))
        for output in response.outputs.values():
            _JSON_MAPPING.validate_python(dict(output.metadata))
        artifact_outputs = response.artifact_outputs
    except (TypeError, ValueError, ValidationError) as error:
        raise _plugin_error(
            stage,
            f"plugin response metadata is not canonical JSON: {error}",
            code="PLUGIN_RESPONSE_METADATA_INVALID",
        ) from error

    row_counts: dict[str, int] = {}
    for port, output in sorted(response.outputs.items()):
        contract = contracts.get(output.contract_id)
        actual = _validate_output_dataset(root, output, contract)
        _validate_declared_row_count(
            stage,
            port=port,
            output=output,
            actual=actual,
        )
        row_counts[port] = actual

    missing_ports = sorted(set(stage.descriptor.output_ports) - set(response.outputs))
    if missing_ports:
        raise _plugin_error(
            stage,
            "plugin did not emit every output port promised by its descriptor",
            code="PLUGIN_OUTPUT_PORT_MISSING",
            context={"missing_ports": missing_ports},
        )

    return ValidatedStageResponse(
        outputs=artifact_outputs,
        row_counts=row_counts,
    )


__all__ = ["ValidatedStageResponse", "validate_stage_response"]
