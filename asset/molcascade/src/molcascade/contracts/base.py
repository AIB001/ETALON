"""Core types for versioned Arrow data contracts.

Contracts describe a batch-shaped boundary.  They deliberately contain no
row-by-row Pydantic model and no chemistry implementation; that keeps the data
plane usable for millions of records and avoids coupling the core package to
RDKit.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

import pyarrow as pa

if TYPE_CHECKING:
    from molcascade.contracts.validation import ContractValidationReport

_CONTRACT_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,126}/v[1-9][0-9]*$")


def _freeze_string_mapping(values: Mapping[str, str]) -> Mapping[str, str]:
    """Return a detached, read-only copy of a string mapping."""

    return MappingProxyType(dict(values))


def _freeze_enums(
    values: Mapping[str, frozenset[str] | set[str] | tuple[str, ...]],
) -> Mapping[str, frozenset[str]]:
    """Return detached enum declarations with deterministic value semantics."""

    return MappingProxyType({name: frozenset(options) for name, options in values.items()})


@dataclass(frozen=True, slots=True)
class DataContract:
    """A named, versioned Arrow schema plus machine-checkable invariants.

    ``schema`` lists every column allowed by the contract. ``required_columns``
    may be a subset, permitting explicitly declared optional columns to be
    absent.  Unknown columns are rejected unless ``allow_additional_columns``
    is deliberately enabled on a future extension contract.

    ``invariants`` is descriptive metadata for audit reports and UI help.  The
    invariants implemented by the core validator are required columns,
    non-nullable values, primary-key uniqueness, and enumerated values.  A
    plugin may enforce additional domain invariants, but executable callbacks
    are intentionally not stored in a contract descriptor.
    """

    id: str
    schema: pa.Schema
    primary_key: tuple[str, ...]
    required_columns: tuple[str, ...] = ()
    enums: Mapping[str, frozenset[str] | set[str] | tuple[str, ...]] = field(
        default_factory=dict
    )
    at_least_one_nonempty: tuple[tuple[str, ...], ...] = ()
    invariants: Mapping[str, str] = field(default_factory=dict)
    allow_additional_columns: bool = False

    def __post_init__(self) -> None:
        if not _CONTRACT_ID_RE.fullmatch(self.id):
            raise ValueError(
                "contract id must be lowercase, end in '/vN', and contain only "
                "letters, digits, '.', '_' or '-'"
            )
        if not isinstance(self.schema, pa.Schema):
            raise TypeError("schema must be a pyarrow.Schema")
        if len(self.schema.names) != len(set(self.schema.names)):
            raise ValueError("contract schema column names must be unique")

        required = self.required_columns or tuple(self.schema.names)
        primary_key = tuple(self.primary_key)
        if not primary_key:
            raise ValueError("a data contract must declare a non-empty primary key")
        if len(primary_key) != len(set(primary_key)):
            raise ValueError("primary-key columns must be unique")
        if len(required) != len(set(required)):
            raise ValueError("required columns must be unique")

        known = set(self.schema.names)
        missing_required = sorted(set(required) - known)
        missing_primary_key = sorted(set(primary_key) - set(required))
        if missing_required:
            raise ValueError(
                f"required columns are absent from schema: {', '.join(missing_required)}"
            )
        if missing_primary_key:
            raise ValueError(
                "primary-key columns must also be required: "
                + ", ".join(missing_primary_key)
            )
        nullable_primary_key = [
            name for name in primary_key if self.schema.field(name).nullable
        ]
        if nullable_primary_key:
            raise ValueError(
                "primary-key columns must be declared non-nullable: "
                + ", ".join(nullable_primary_key)
            )

        frozen_enums = _freeze_enums(self.enums)
        for column, options in frozen_enums.items():
            if column not in known:
                raise ValueError(f"enum column is absent from schema: {column}")
            arrow_type = self.schema.field(column).type
            if not (pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type)):
                raise ValueError(f"enum column must have a string Arrow type: {column}")
            if not options:
                raise ValueError(f"enum declaration must not be empty: {column}")
            if any(not isinstance(option, str) or not option for option in options):
                raise ValueError(f"enum values must be non-empty strings: {column}")

        nonempty_groups = tuple(tuple(group) for group in self.at_least_one_nonempty)
        for group in nonempty_groups:
            if len(group) < 2 or len(group) != len(set(group)):
                raise ValueError(
                    "at-least-one-nonempty groups need at least two unique columns"
                )
            missing = sorted(set(group) - known)
            if missing:
                raise ValueError(
                    "at-least-one-nonempty columns are absent from schema: "
                    + ", ".join(missing)
                )
            non_string = [
                name
                for name in group
                if not (
                    pa.types.is_string(self.schema.field(name).type)
                    or pa.types.is_large_string(self.schema.field(name).type)
                )
            ]
            if non_string:
                raise ValueError(
                    "at-least-one-nonempty columns must have string Arrow types: "
                    + ", ".join(non_string)
                )

        if any(not key or not value for key, value in self.invariants.items()):
            raise ValueError("invariant codes and descriptions must be non-empty")

        object.__setattr__(self, "primary_key", primary_key)
        object.__setattr__(self, "required_columns", tuple(required))
        object.__setattr__(self, "enums", frozen_enums)
        object.__setattr__(self, "at_least_one_nonempty", nonempty_groups)
        object.__setattr__(self, "invariants", _freeze_string_mapping(self.invariants))

    @property
    def contract_id(self) -> str:
        """Descriptive alias for :attr:`id`."""

        return self.id

    @property
    def enum_values(self) -> Mapping[str, frozenset[str]]:
        """Return the immutable per-column enumeration declarations."""

        # ``__post_init__`` normalises the union-valued constructor annotation.
        return self.enums  # type: ignore[return-value]

    @property
    def invariant_metadata(self) -> Mapping[str, str]:
        """Descriptive alias used by manifest and UI adapters."""

        return self.invariants

    def validate_schema(self, actual: pa.Schema) -> None:
        """Validate an Arrow schema against this contract.

        See :func:`molcascade.contracts.validation.validate_schema`.
        """

        from molcascade.contracts.validation import validate_schema

        validate_schema(actual, self)

    def validate(self, data: pa.Table | pa.RecordBatch) -> ContractValidationReport:
        """Validate a complete Arrow table or record batch."""

        from molcascade.contracts.validation import validate_table

        return validate_table(data, self)


__all__ = ["DataContract"]
