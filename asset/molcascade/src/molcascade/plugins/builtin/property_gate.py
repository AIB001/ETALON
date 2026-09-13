"""Configurable RDKit physicochemical range gate.

This gate is intentionally separate from the structural hard-rule catalogue:
teams can keep invalid/reactive chemistry terminal while treating drug-like
property windows as a replaceable project policy.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Self

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, model_validator

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")


class PropertyRangeGateConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    mw_min: float | None = Field(default=100.0, ge=0.0, le=10_000.0)
    mw_max: float | None = Field(default=700.0, ge=0.0, le=10_000.0)
    clogp_min: float | None = Field(default=-3.0, ge=-100.0, le=100.0)
    clogp_max: float | None = Field(default=8.0, ge=-100.0, le=100.0)
    tpsa_min: float | None = Field(default=0.0, ge=0.0, le=10_000.0)
    tpsa_max: float | None = Field(default=250.0, ge=0.0, le=10_000.0)
    hbd_max: int | None = Field(default=10, ge=0, le=1_000)
    hba_max: int | None = Field(default=15, ge=0, le=1_000)
    rotatable_bonds_max: int | None = Field(default=20, ge=0, le=10_000)
    heavy_atom_min: int | None = Field(default=3, ge=0, le=100_000)
    heavy_atom_max: int | None = Field(default=100, ge=0, le=100_000)
    absolute_formal_charge_max: int | None = Field(default=3, ge=0, le=100)

    @model_validator(mode="after")
    def _ordered_ranges(self) -> Self:
        for lower_name, upper_name in (
            ("mw_min", "mw_max"),
            ("clogp_min", "clogp_max"),
            ("tpsa_min", "tpsa_max"),
            ("heavy_atom_min", "heavy_atom_max"),
        ):
            lower = getattr(self, lower_name)
            upper = getattr(self, upper_name)
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"{lower_name} must be no greater than {upper_name}")
        return self


def _policy_id(config: PropertyRangeGateConfig, backend_version: str) -> str:
    """The identity of the window this gate applied, independent of batching.

    ``batch_size`` is removed before hashing: it decides how many rows are held
    in memory at once and nothing about which molecules pass, so two runs that
    differ only in batching must produce the same policy identifier.
    """

    policy = config.model_dump(mode="json")
    policy.pop("batch_size", None)
    return "property-gate-policy:sha256:" + canonical_sha256(
        {"backend": "rdkit", "backend_version": backend_version, "policy": policy}
    )


def _check(
    value: float | int,
    *,
    name: str,
    minimum: float | int | None,
    maximum: float | int | None,
) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    label = name.upper()
    if minimum is not None and value < minimum:
        findings.append(
            (f"PROPERTY_{label}_BELOW_MIN", f"{name}={value!r} is below {minimum!r}")
        )
    if maximum is not None and value > maximum:
        findings.append(
            (f"PROPERTY_{label}_ABOVE_MAX", f"{name}={value!r} is above {maximum!r}")
        )
    return findings


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Gate one contiguous range of parents, in whichever process owns it.

    Module-level and self-contained on purpose: ``spawn`` re-imports this module
    in a fresh interpreter and calls the function by name, so it may close over
    nothing.  The config is re-validated here rather than trusted, which is
    cheap and keeps a worker fail-closed on anything the pickle did not carry.
    """

    from rdkit import Chem, rdBase
    from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

    config = PropertyRangeGateConfig.model_validate(dict(task.config))
    policy_id = _policy_id(config, rdBase.rdkitVersion)
    rows_in = 0
    passed_count = 0
    decision_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parents,
        pq.ParquetWriter(
            task.output_paths["decisions"], DECISION_V1.schema, compression="zstd"
        ) as decisions,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            passed: list[dict[str, Any]] = []
            decision_rows: list[dict[str, Any]] = []
            for row in batch.to_pylist():
                rows_in += 1
                parent_id = row.get("parent_id")
                molecule = Chem.MolFromSmiles(
                    row.get("parent_smiles")
                    if isinstance(row.get("parent_smiles"), str)
                    else ""
                )
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed by RDKit property gate",
                        code="PROPERTY_GATE_PARENT_INVALID",
                        context={"parent_id": str(parent_id)},
                    )
                properties: dict[str, float | int] = {
                    "mw": float(Descriptors.MolWt(molecule)),
                    "clogp": float(Crippen.MolLogP(molecule)),
                    "tpsa": float(rdMolDescriptors.CalcTPSA(molecule)),
                    "hbd": int(rdMolDescriptors.CalcNumHBD(molecule)),
                    "hba": int(rdMolDescriptors.CalcNumHBA(molecule)),
                    "rotatable_bonds": int(Lipinski.NumRotatableBonds(molecule)),
                    "heavy_atom_count": int(molecule.GetNumHeavyAtoms()),
                    "absolute_formal_charge": abs(int(Chem.GetFormalCharge(molecule))),
                }
                if any(
                    isinstance(value, float) and not math.isfinite(value)
                    for value in properties.values()
                ):
                    raise PluginError(
                        "RDKit produced a non-finite gate property",
                        code="PROPERTY_GATE_NON_FINITE",
                        context={"parent_id": str(parent_id)},
                    )
                findings: list[tuple[str, str]] = []
                findings += _check(
                    properties["mw"], name="mw", minimum=config.mw_min, maximum=config.mw_max
                )
                findings += _check(
                    properties["clogp"],
                    name="clogp",
                    minimum=config.clogp_min,
                    maximum=config.clogp_max,
                )
                findings += _check(
                    properties["tpsa"],
                    name="tpsa",
                    minimum=config.tpsa_min,
                    maximum=config.tpsa_max,
                )
                findings += _check(
                    properties["hbd"], name="hbd", minimum=None, maximum=config.hbd_max
                )
                findings += _check(
                    properties["hba"], name="hba", minimum=None, maximum=config.hba_max
                )
                findings += _check(
                    properties["rotatable_bonds"],
                    name="rotatable_bonds",
                    minimum=None,
                    maximum=config.rotatable_bonds_max,
                )
                findings += _check(
                    properties["heavy_atom_count"],
                    name="heavy_atom_count",
                    minimum=config.heavy_atom_min,
                    maximum=config.heavy_atom_max,
                )
                findings += _check(
                    properties["absolute_formal_charge"],
                    name="absolute_formal_charge",
                    minimum=None,
                    maximum=config.absolute_formal_charge_max,
                )
                if findings:
                    for reason_code, detail in findings:
                        decision_rows.append(
                            {
                                "entity_id": parent_id,
                                "entity_kind": "PARENT",
                                "stage_id": task.stage_id,
                                "outcome": "REJECT",
                                "reason_code": reason_code,
                                "rule_id": policy_id,
                                "detail": detail,
                            }
                        )
                else:
                    passed.append(row)
                    passed_count += 1
                    decision_rows.append(
                        {
                            "entity_id": parent_id,
                            "entity_kind": "PARENT",
                            "stage_id": task.stage_id,
                            "outcome": "PASS",
                            "reason_code": "PROPERTY_RANGE_PASS",
                            "rule_id": policy_id,
                            "detail": json.dumps(
                                properties,
                                ensure_ascii=False,
                                allow_nan=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        }
                    )
                decision_count += max(1, len(findings))
            if passed:
                parents.write_table(pa.Table.from_pylist(passed, schema=PARENT_V1.schema))
            decisions.write_table(pa.Table.from_pylist(decision_rows, schema=DECISION_V1.schema))
    return ShardOutcome(
        rows_in=rows_in,
        rows_out={"primary": passed_count, "decisions": decision_count},
    )


class RDKitPropertyRangeGatePlugin:
    descriptor = PluginDescriptor(
        id="chemistry.rdkit_property_range_gate",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit physicochemical range gate",
        description=(
            "Project-defined MW/logP/TPSA/HBD/HBA/rotor/charge windows with "
            "full decisions."
        ),
    )
    config_model = PropertyRangeGateConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        try:
            config = self.config_model.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid property gate configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"error_count": error.error_count()},
            ) from error
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        policy_id = _policy_id(config, rdBase.rdkitVersion)
        result = shard_stage(
            worker=_run_shard,
            stage_input=stage_input,
            contract=PARENT_V1,
            output_paths={
                "primary": _PARENT_PATH.as_posix(),
                "decisions": _DECISION_PATH.as_posix(),
            },
            context=context,
            stage_id=request.stage_id,
            config=config.model_dump(mode="json"),
        )
        if result.rows_in == 0:
            raise PluginError(
                "property-gate input contains no parents",
                code="PROPERTY_GATE_EMPTY_INPUT",
            )
        passed_count = result.rows_out["primary"]
        decision_count = result.rows_out["decisions"]
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": passed_count},
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    result.file_paths["decisions"],
                    {"row_count": decision_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": result.rows_in,
                "output_count": passed_count,
                "reject_count": result.rows_in - passed_count,
                "decision_count": decision_count,
                "policy_id": policy_id,
                "backend": "rdkit",
                "backend_version": rdBase.rdkitVersion,
                **result.response_metadata(),
            },
        )


__all__ = ["PropertyRangeGateConfig", "RDKitPropertyRangeGatePlugin"]
