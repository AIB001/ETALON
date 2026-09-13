"""Versioned, strict chemistry policies used by built-in RDKit stages."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from molcascade.config.models import StrictFrozenModel


class FragmentPolicy(StrEnum):
    """How a single parent is chosen from disconnected components."""

    LARGEST_ORGANIC = "largest_organic"
    LARGEST = "largest"
    REJECT_MULTICOMPONENT = "reject_multicomponent"


class ChargePolicy(StrEnum):
    """How ionisation and formal charge are normalised."""

    PRESERVE = "preserve"
    REIONIZE = "reionize"
    UNCHARGE_WHERE_POSSIBLE = "uncharge_where_possible"


class MetalDisconnectionPolicy(StrEnum):
    """Whether covalent metal bonds are normalized before fragment selection."""

    DISCONNECT = "disconnect"
    PRESERVE = "preserve"


class TautomerPolicy(StrEnum):
    """Whether tautomer spelling participates in parent identity."""

    PRESERVE = "preserve"
    CANONICALIZE = "canonicalize"
    INSENSITIVE = "insensitive"


class StereoPolicy(StrEnum):
    """Whether specified stereochemistry participates in parent identity."""

    SENSITIVE = "sensitive"
    INSENSITIVE = "insensitive"


class UndefinedStereoPolicy(StrEnum):
    """Treatment of potential stereocentres without an assignment."""

    ALLOW = "allow"
    WARN = "warn"
    REJECT = "reject"


class IsotopePolicy(StrEnum):
    """Treatment of isotope labels in parent identity."""

    SENSITIVE = "sensitive"
    INSENSITIVE = "insensitive"
    REJECT = "reject"


class AtomMapPolicy(StrEnum):
    """Treatment of reaction atom-map annotations.

    RDKit RegistrationHash intentionally ignores atom-map numbers.  Preserving
    them in ``parent_smiles`` would therefore permit two visibly different
    parents to share an identity.  MolCascade only permits stripping or
    rejecting them at this boundary.
    """

    REMOVE = "remove"
    REJECT = "reject"


class AtomLabelPolicy(StrEnum):
    """Treatment of CXSMILES atom labels at the small-molecule boundary."""

    REMOVE = "remove"
    REJECT = "reject"


class RegistrationHashVersion(StrEnum):
    """RDKit heteroatom tautomer hash generation selected explicitly."""

    V1 = "v1"
    V2 = "v2"


_IDENTITY_ENUM_FIELDS = {
    "fragment_policy": FragmentPolicy,
    "charge_policy": ChargePolicy,
    "metal_disconnection_policy": MetalDisconnectionPolicy,
    "tautomer_policy": TautomerPolicy,
    "stereo_policy": StereoPolicy,
    "undefined_stereo_policy": UndefinedStereoPolicy,
    "isotope_policy": IsotopePolicy,
    "atom_map_policy": AtomMapPolicy,
    "atom_label_policy": AtomLabelPolicy,
    "registration_hash_version": RegistrationHashVersion,
}


class IdentityPolicy(StrictFrozenModel):
    """Complete identity-affecting policy for canonical parent registration."""

    schema_version: int = Field(default=1, ge=1, le=1)
    fragment_policy: FragmentPolicy = FragmentPolicy.LARGEST_ORGANIC
    charge_policy: ChargePolicy = ChargePolicy.REIONIZE
    metal_disconnection_policy: MetalDisconnectionPolicy = (
        MetalDisconnectionPolicy.DISCONNECT
    )
    tautomer_policy: TautomerPolicy = TautomerPolicy.INSENSITIVE
    stereo_policy: StereoPolicy = StereoPolicy.SENSITIVE
    undefined_stereo_policy: UndefinedStereoPolicy = UndefinedStereoPolicy.WARN
    isotope_policy: IsotopePolicy = IsotopePolicy.SENSITIVE
    atom_map_policy: AtomMapPolicy = AtomMapPolicy.REMOVE
    atom_label_policy: AtomLabelPolicy = AtomLabelPolicy.REMOVE
    normalize_functional_groups: bool = True
    reject_ambiguous_fragment_ties: bool = True
    registration_hash_version: RegistrationHashVersion = RegistrationHashVersion.V2

    @field_validator(*_IDENTITY_ENUM_FIELDS, mode="before")
    @classmethod
    def _parse_enums(cls, value: Any, info: Any) -> Any:
        enum_type = _IDENTITY_ENUM_FIELDS[info.field_name]
        return enum_type(value) if isinstance(value, str) else value


_RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_RESERVED_GATE_REASON_CODES = frozenset(
    {
        "DISALLOWED_ELEMENT",
        "EMPTY_OR_FRAGMENT_ONLY",
        "HARD_GATE_PASS",
        "MULTICOMPONENT_PARENT",
        "PAINS_ALERT",
        "PARSE_ERROR",
        "QUERY_FEATURE_NOT_ALLOWED",
        "UNSUPPORTED_STRUCTURAL_METADATA",
        "VALENCE_ERROR",
    }
)


class HardSmartsRule(StrictFrozenModel):
    """One project-approved substructure rule which causes hard rejection."""

    id: str = Field(min_length=1, max_length=128)
    smarts: str = Field(min_length=1, max_length=4096)
    reason_code: str = Field(default="SEVERE_REACTIVITY", min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    use_chirality: bool = True

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", value):
            raise ValueError("rule id must be a lowercase portable identifier")
        return value

    @field_validator("reason_code")
    @classmethod
    def _validate_reason_code(cls, value: str) -> str:
        if not _RULE_ID_RE.fullmatch(value):
            raise ValueError("reason_code must be an uppercase machine-readable identifier")
        if value in _RESERVED_GATE_REASON_CODES:
            raise ValueError("reason_code is reserved by the built-in gate")
        return value

    @field_validator("smarts")
    @classmethod
    def _validate_smarts(cls, value: str) -> str:
        # Import lazily to keep configuration module import lightweight and to
        # turn a broken core installation into a field-level validation error.
        try:
            from rdkit import Chem, rdBase
        except ImportError as error:  # pragma: no cover - broken core installation
            raise ValueError("validating SMARTS requires RDKit") from error
        with rdBase.BlockLogs():
            query = Chem.MolFromSmarts(value)
        if query is None:
            raise ValueError("smarts is not parseable by the installed RDKit")
        return value


DEFAULT_ALLOWED_ATOMIC_NUMBERS = (1, 5, 6, 7, 8, 9, 14, 15, 16, 17, 34, 35, 53)


class HardGatePolicy(StrictFrozenModel):
    """Conservative project hard-gate policy.

    The default contains a narrow drug-like element allowlist and no speculative
    reactive-group SMARTS.  Projects opt into reviewed hard SMARTS explicitly.
    PAINS is independently reported as a warning and is never a hard default.
    """

    schema_version: int = Field(default=1, ge=1, le=1)
    allowed_atomic_numbers: tuple[int, ...] = DEFAULT_ALLOWED_ATOMIC_NUMBERS
    hard_smarts: tuple[HardSmartsRule, ...] = ()
    flag_pains: bool = True

    @field_validator("allowed_atomic_numbers", "hard_smarts", mode="before")
    @classmethod
    def _accept_json_arrays(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("allowed_atomic_numbers")
    @classmethod
    def _validate_atomic_numbers(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if not values:
            raise ValueError("allowed_atomic_numbers must not be empty")
        if any(number < 1 or number > 118 for number in values):
            raise ValueError("allowed atomic numbers must be between 1 and 118")
        if len(values) != len(set(values)):
            raise ValueError("allowed_atomic_numbers contains duplicates")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def _rule_ids_are_unique(self) -> Self:
        ids = [rule.id for rule in self.hard_smarts]
        if len(ids) != len(set(ids)):
            raise ValueError("hard SMARTS rule ids must be unique")
        return self


__all__ = [
    "DEFAULT_ALLOWED_ATOMIC_NUMBERS",
    "AtomLabelPolicy",
    "AtomMapPolicy",
    "ChargePolicy",
    "FragmentPolicy",
    "HardGatePolicy",
    "HardSmartsRule",
    "IdentityPolicy",
    "IsotopePolicy",
    "MetalDisconnectionPolicy",
    "RegistrationHashVersion",
    "StereoPolicy",
    "TautomerPolicy",
    "UndefinedStereoPolicy",
]
