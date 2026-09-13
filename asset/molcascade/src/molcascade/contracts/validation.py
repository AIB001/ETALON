"""Vectorised validation for Arrow contract boundaries."""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa
import pyarrow.compute as pc

from molcascade.contracts.base import DataContract
from molcascade.errors import ContractError


@dataclass(frozen=True, slots=True)
class ContractValidationReport:
    """Small success record suitable for metrics and audit events."""

    contract_id: str
    row_count: int
    columns: tuple[str, ...]


def validate_schema(actual: pa.Schema, contract: DataContract) -> None:
    """Validate column presence, names, Arrow types, and nullability.

    Column order and schema/field metadata are informational rather than
    semantic.  An actual non-nullable field is compatible with a nullable
    contract field (it is a stronger guarantee); the inverse is rejected.
    """

    if not isinstance(actual, pa.Schema):
        raise ContractError(
            "data schema must be a pyarrow.Schema",
            code="CONTRACT_SCHEMA_TYPE_INVALID",
            context={"contract_id": contract.id, "actual_type": type(actual).__name__},
        )

    duplicate_columns = sorted(
        {name for name in actual.names if actual.names.count(name) > 1}
    )
    actual_names = set(actual.names)
    expected_names = set(contract.schema.names)
    missing_columns = sorted(set(contract.required_columns) - actual_names)
    unexpected_columns = (
        []
        if contract.allow_additional_columns
        else sorted(actual_names - expected_names)
    )
    type_mismatches: list[dict[str, str]] = []
    nullability_mismatches: list[str] = []

    for name in sorted(actual_names & expected_names):
        # Duplicate names are already reported and make name lookup ambiguous.
        if actual.get_field_index(name) == -1:
            continue
        expected_field = contract.schema.field(name)
        actual_field = actual.field(name)
        if not actual_field.type.equals(expected_field.type):
            type_mismatches.append(
                {
                    "column": name,
                    "expected": str(expected_field.type),
                    "actual": str(actual_field.type),
                }
            )
        if not expected_field.nullable and actual_field.nullable:
            nullability_mismatches.append(name)

    if (
        duplicate_columns
        or missing_columns
        or unexpected_columns
        or type_mismatches
        or nullability_mismatches
    ):
        raise ContractError(
            f"Arrow schema is incompatible with {contract.id}",
            code="CONTRACT_SCHEMA_MISMATCH",
            hint="Write the stage output with the exact versioned contract schema.",
            context={
                "contract_id": contract.id,
                "duplicate_columns": duplicate_columns,
                "missing_required_columns": missing_columns,
                "unexpected_columns": unexpected_columns,
                "type_mismatches": type_mismatches,
                "nullable_required_columns": nullability_mismatches,
            },
        )


def _normalise_table(data: pa.Table | pa.RecordBatch, contract: DataContract) -> pa.Table:
    if isinstance(data, pa.Table):
        return data
    if isinstance(data, pa.RecordBatch):
        return pa.Table.from_batches([data])
    raise ContractError(
        "contract validation requires a pyarrow.Table or pyarrow.RecordBatch",
        code="CONTRACT_DATA_TYPE_INVALID",
        context={"contract_id": contract.id, "actual_type": type(data).__name__},
    )


def _validate_non_null_columns(table: pa.Table, contract: DataContract) -> None:
    null_counts = {
        field.name: table.column(field.name).null_count
        for field in contract.schema
        if not field.nullable and field.name in table.column_names
    }
    invalid = {name: count for name, count in null_counts.items() if count > 0}
    if invalid:
        raise ContractError(
            f"non-nullable columns contain nulls for {contract.id}",
            code="CONTRACT_NULL_VALUE",
            context={"contract_id": contract.id, "null_counts": invalid},
        )


def _validate_primary_key(table: pa.Table, contract: DataContract) -> None:
    if table.num_rows < 2:
        return

    key_columns = list(contract.primary_key)
    try:
        grouped = table.select(key_columns).group_by(key_columns).aggregate(
            [(key_columns[0], "count")]
        )
        # Use the aggregate's positional column.  A legitimate composite key
        # may itself contain a name such as ``id_count``.
        duplicate_mask = pc.greater(grouped.column(grouped.num_columns - 1), 1)
        duplicate_groups = grouped.filter(duplicate_mask)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as error:
        raise ContractError(
            f"primary key cannot be grouped for {contract.id}",
            code="CONTRACT_PRIMARY_KEY_UNSUPPORTED",
            context={
                "contract_id": contract.id,
                "primary_key": key_columns,
                "arrow_error": str(error),
            },
        ) from error

    if duplicate_groups.num_rows:
        examples = duplicate_groups.select(key_columns).slice(0, 10).to_pylist()
        raise ContractError(
            f"primary key is not unique for {contract.id}",
            code="CONTRACT_PRIMARY_KEY_DUPLICATE",
            context={
                "contract_id": contract.id,
                "primary_key": key_columns,
                "duplicate_group_count": duplicate_groups.num_rows,
                "examples": examples,
            },
        )


def _validate_enums(table: pa.Table, contract: DataContract) -> None:
    for column_name, allowed in contract.enum_values.items():
        if column_name not in table.column_names:
            continue
        observed = {
            value
            for value in pc.unique(table.column(column_name)).to_pylist()
            if value is not None
        }
        invalid = sorted(observed - allowed)
        if invalid:
            raise ContractError(
                f"column {column_name!r} contains values outside its enumeration",
                code="CONTRACT_ENUM_INVALID",
                context={
                    "contract_id": contract.id,
                    "column": column_name,
                    "allowed": sorted(allowed),
                    "invalid": invalid[:20],
                    "invalid_value_count": len(invalid),
                },
            )


def _validate_at_least_one_nonempty(table: pa.Table, contract: DataContract) -> None:
    for columns in contract.at_least_one_nonempty:
        present = [name for name in columns if name in table.column_names]
        if not present:
            invalid_count = table.num_rows
            examples = list(range(min(table.num_rows, 10)))
        else:
            any_nonempty: pa.Array | pa.ChunkedArray | None = None
            for name in present:
                values = table.column(name)
                filled = pc.fill_null(values, pa.scalar("", type=values.type))
                trimmed = pc.utf8_trim_whitespace(filled)
                nonempty = pc.greater(pc.utf8_length(trimmed), 0)
                any_nonempty = (
                    nonempty if any_nonempty is None else pc.or_(any_nonempty, nonempty)
                )
            assert any_nonempty is not None
            invalid = pc.invert(any_nonempty)
            indices = pc.indices_nonzero(invalid)
            invalid_count = len(indices)
            examples = indices.slice(0, 10).to_pylist()

        if invalid_count:
            raise ContractError(
                f"at-least-one-nonempty invariant failed for {contract.id}",
                code="CONTRACT_INVARIANT_VIOLATION",
                context={
                    "contract_id": contract.id,
                    "invariant": "at_least_one_nonempty",
                    "columns": list(columns),
                    "invalid_row_count": invalid_count,
                    "row_index_examples": examples,
                },
            )


def validate_table(
    data: pa.Table | pa.RecordBatch,
    contract: DataContract,
) -> ContractValidationReport:
    """Fully validate one Arrow table or record batch.

    Arrow performs its own internal buffer validation first.  MolCascade then
    checks the declared schema, nulls, primary-key uniqueness, and declared
    string enumerations using batch operations.
    """

    table = _normalise_table(data, contract)
    try:
        table.validate(full=True)
    except pa.ArrowInvalid as error:
        raise ContractError(
            f"Arrow table is internally invalid for {contract.id}",
            code="CONTRACT_ARROW_INVALID",
            context={"contract_id": contract.id, "arrow_error": str(error)},
        ) from error

    validate_schema(table.schema, contract)
    _validate_non_null_columns(table, contract)
    _validate_at_least_one_nonempty(table, contract)
    _validate_primary_key(table, contract)
    _validate_enums(table, contract)
    return ContractValidationReport(
        contract_id=contract.id,
        row_count=table.num_rows,
        columns=tuple(table.column_names),
    )


__all__ = ["ContractValidationReport", "validate_schema", "validate_table"]
