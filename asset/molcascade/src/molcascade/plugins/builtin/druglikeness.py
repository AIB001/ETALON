"""Typed RDKit drug-likeness evidence with an optional policy gate.

Rule-of-Five and QED values are evidence, not measurements of efficacy or
safety.  This plugin therefore annotates by default; a project must explicitly
configure a threshold and ``failure_action='reject'`` to remove molecules.
"""

from __future__ import annotations

import json
import math
from enum import StrEnum
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, DRUG_LIKENESS_V1, PARENT_V1
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
_EVIDENCE_PATH = Path("datasets/drug_likeness/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")
_IMPLEMENTATION_VERSION = 1


class FailureAction(StrEnum):
    WARN = "warn"
    REJECT = "reject"


class DrugLikenessConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    maximum_rule_of_five_violations: int | None = Field(default=None, ge=0, le=4)
    minimum_qed: float | None = Field(default=None, ge=0.0, le=1.0)
    failure_action: FailureAction = FailureAction.WARN

    @field_validator("failure_action", mode="before")
    @classmethod
    def _parse_failure_action(cls, value: Any) -> Any:
        return FailureAction(value) if isinstance(value, str) else value


def _validated_config(request: StageRequest) -> DrugLikenessConfig:
    try:
        return DrugLikenessConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid drug-likeness configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _method_ids(backend_version: str) -> tuple[str, str, str]:
    """The three identities this stage stamps onto its evidence.

    Computed from the toolkit version and literal definitions alone, so the
    parent and every worker derive the same strings independently rather than
    shipping them across the process boundary.
    """

    rule_of_five_method_id = "rule-of-five:sha256:" + canonical_sha256(
        {
            "reference_doi": "10.1016/S0169-409X(00)00129-0",
            "definitions": {
                "mw": ">500 Da",
                "clogp": ">5",
                "hbd": ">5",
                "hba": ">10",
            },
            "calculator_backend": "rdkit.QED.properties",
            "backend_version": backend_version,
            "implementation_version": _IMPLEMENTATION_VERSION,
        }
    )
    qed_method_id = "qed:sha256:" + canonical_sha256(
        {
            "reference_doi": "10.1038/nchem.1243",
            "backend": "rdkit.QED.qed",
            "backend_version": backend_version,
            "weights": "rdkit.QED.WEIGHT_MEAN",
            "implementation_version": _IMPLEMENTATION_VERSION,
        }
    )
    calculator_id = "drug-likeness:sha256:" + canonical_sha256(
        {
            "rule_of_five_method_id": rule_of_five_method_id,
            "qed_method_id": qed_method_id,
            "toolkit_version": backend_version,
        }
    )
    return rule_of_five_method_id, qed_method_id, calculator_id


def _policy_id(config: DrugLikenessConfig) -> str:
    """``batch_size`` decides memory, not verdicts, so it is not policy."""

    return "drug-likeness-policy:sha256:" + canonical_sha256(
        config.model_dump(mode="json", exclude={"batch_size"})
    )


def _finite(value: float, *, parent_id: str, property_name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise PluginError(
            f"RDKit produced non-finite drug-likeness evidence for {property_name}",
            code="DRUG_LIKENESS_NON_FINITE",
            context={"parent_id": parent_id, "property": property_name},
        )
    return result


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Score one contiguous range of parents in whichever process owns it.

    Module-level and closing over nothing: ``spawn`` re-imports this module in a
    fresh interpreter and calls the function by name.  The config is re-validated
    from the task rather than trusted, which is cheap and keeps a worker
    fail-closed on anything the pickle did not carry.
    """

    from rdkit import Chem, rdBase
    from rdkit.Chem import QED

    config = DrugLikenessConfig.model_validate(dict(task.config))
    rule_of_five_method_id, qed_method_id, calculator_id = _method_ids(rdBase.rdkitVersion)
    policy_id = _policy_id(config)
    policy_enabled = (
        config.maximum_rule_of_five_violations is not None or config.minimum_qed is not None
    )
    input_count = 0
    retained_count = 0
    warning_count = 0
    reject_count = 0
    decision_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["evidence"], DRUG_LIKENESS_V1.schema, compression="zstd"
        ) as evidence_writer,
        pq.ParquetWriter(
            task.output_paths["decisions"], DECISION_V1.schema, compression="zstd"
        ) as decision_writer,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            retained_rows: list[dict[str, Any]] = []
            evidence_rows: list[dict[str, Any]] = []
            decision_rows: list[dict[str, Any]] = []
            for row in batch.to_pylist():
                input_count += 1
                parent_id = row.get("parent_id")
                smiles = row.get("parent_smiles")
                molecule = Chem.MolFromSmiles(smiles if isinstance(smiles, str) else "")
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed by RDKit QED",
                        code="DRUG_LIKENESS_PARENT_INVALID",
                        context={"parent_id": str(parent_id)},
                    )
                try:
                    qed_properties = QED.properties(molecule)
                    raw_qed = QED.qed(molecule, qedProperties=qed_properties)
                except (ArithmeticError, RuntimeError, ValueError) as error:
                    raise PluginError(
                        "RDKit drug-likeness calculation failed",
                        code="DRUG_LIKENESS_CALCULATION_FAILED",
                        context={
                            "parent_id": str(parent_id),
                            "error_type": type(error).__name__,
                        },
                    ) from error
                mw = _finite(qed_properties.MW, parent_id=str(parent_id), property_name="mw")
                clogp = _finite(
                    qed_properties.ALOGP, parent_id=str(parent_id), property_name="clogp"
                )
                tpsa = _finite(
                    qed_properties.PSA, parent_id=str(parent_id), property_name="tpsa"
                )
                qed = _finite(
                    raw_qed, parent_id=str(parent_id), property_name="qed_weighted"
                )
                hbd = int(qed_properties.HBD)
                hba = int(qed_properties.HBA)
                violations = sum((mw > 500.0, clogp > 5.0, hbd > 5, hba > 10))
                evidence = {
                    "parent_id": parent_id,
                    "calculator_id": calculator_id,
                    "rule_of_five_method_id": rule_of_five_method_id,
                    "qed_method_id": qed_method_id,
                    "toolkit_version": rdBase.rdkitVersion,
                    "mw": mw,
                    "clogp": clogp,
                    "hbd": hbd,
                    "hba": hba,
                    "tpsa": tpsa,
                    "rotatable_bonds": int(qed_properties.ROTB),
                    "aromatic_ring_count": int(qed_properties.AROM),
                    "qed_alert_count": int(qed_properties.ALERTS),
                    "rule_of_five_violation_count": violations,
                    "rule_of_five_pass": violations == 0,
                    "qed_weighted": qed,
                }
                evidence_rows.append(evidence)
                failures: list[tuple[str, str]] = []
                maximum_violations = config.maximum_rule_of_five_violations
                if maximum_violations is not None and violations > maximum_violations:
                    failures.append(
                        (
                            "DRUG_LIKENESS_RO5_POLICY_FAIL",
                            f"Rule-of-Five violations={violations} exceeds "
                            f"configured maximum={maximum_violations}",
                        )
                    )
                if config.minimum_qed is not None and qed < config.minimum_qed:
                    failures.append(
                        (
                            "DRUG_LIKENESS_QED_POLICY_FAIL",
                            f"weighted QED={qed:.12g} is below configured "
                            f"minimum={config.minimum_qed:.12g}",
                        )
                    )

                rejected = bool(failures and config.failure_action is FailureAction.REJECT)
                if not rejected:
                    retained_rows.append(row)
                    retained_count += 1
                if failures:
                    outcome = config.failure_action.value.upper()
                    if rejected:
                        reject_count += 1
                    else:
                        warning_count += 1
                    for reason_code, message in failures:
                        decision_rows.append(
                            {
                                "entity_id": parent_id,
                                "entity_kind": "PARENT",
                                "stage_id": task.stage_id,
                                "outcome": outcome,
                                "reason_code": reason_code,
                                "rule_id": policy_id,
                                "detail": json.dumps(
                                    {"message": message, "metrics": evidence},
                                    ensure_ascii=True,
                                    allow_nan=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            }
                        )
                else:
                    decision_rows.append(
                        {
                            "entity_id": parent_id,
                            "entity_kind": "PARENT",
                            "stage_id": task.stage_id,
                            "outcome": "PASS",
                            "reason_code": (
                                "DRUG_LIKENESS_POLICY_PASS"
                                if policy_enabled
                                else "DRUG_LIKENESS_EVIDENCE_ANNOTATED"
                            ),
                            "rule_id": policy_id,
                            "detail": json.dumps(
                                evidence,
                                ensure_ascii=True,
                                allow_nan=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        }
                    )
                decision_count += max(1, len(failures))
            if retained_rows:
                parent_writer.write_table(
                    pa.Table.from_pylist(retained_rows, schema=PARENT_V1.schema)
                )
            evidence_writer.write_table(
                pa.Table.from_pylist(evidence_rows, schema=DRUG_LIKENESS_V1.schema)
            )
            decision_writer.write_table(
                pa.Table.from_pylist(decision_rows, schema=DECISION_V1.schema)
            )
    return ShardOutcome(
        rows_in=input_count,
        rows_out={
            "primary": retained_count,
            "evidence": input_count,
            "decisions": decision_count,
        },
        # Neither number is recoverable from a Parquet footer: a warned molecule
        # and a passing one are both retained, so only the shard that made the
        # call can report how many were which.
        metadata={"warning_count": warning_count, "reject_count": reject_count},
    )


class RDKitDrugLikenessPlugin:
    """Calculate QED and Rule-of-Five evidence, then apply one explicit policy."""

    descriptor = PluginDescriptor(
        id="chemistry.rdkit_drug_likeness",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DRUG_LIKENESS_V1.id, DECISION_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "evidence": DRUG_LIKENESS_V1.id,
            "decisions": DECISION_V1.id,
        },
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit Rule of Five and QED",
        description=(
            "Typed Rule-of-Five/QED evidence with project-configurable WARN or REJECT "
            "thresholds; annotation-only by default."
        ),
    )
    config_model = DrugLikenessConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        rule_of_five_method_id, qed_method_id, calculator_id = _method_ids(
            rdBase.rdkitVersion
        )
        policy_id = _policy_id(config)
        policy_enabled = (
            config.maximum_rule_of_five_violations is not None
            or config.minimum_qed is not None
        )
        result = shard_stage(
            worker=_run_shard,
            stage_input=stage_input,
            contract=PARENT_V1,
            output_paths={
                "primary": _PARENT_PATH.as_posix(),
                "evidence": _EVIDENCE_PATH.as_posix(),
                "decisions": _DECISION_PATH.as_posix(),
            },
            context=context,
            stage_id=request.stage_id,
            config=config.model_dump(mode="json"),
        )
        if result.rows_in == 0:
            raise PluginError(
                "drug-likeness input contains no parents",
                code="DRUG_LIKENESS_EMPTY_INPUT",
            )
        input_count = result.rows_in
        retained_count = result.rows_out["primary"]
        decision_count = result.rows_out["decisions"]
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": retained_count},
                ),
                "evidence": PendingOutput(
                    DRUG_LIKENESS_V1.id,
                    result.file_paths["evidence"],
                    {"row_count": input_count, "calculator_id": calculator_id},
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    result.file_paths["decisions"],
                    {"row_count": decision_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": retained_count,
                "reject_count": result.total("reject_count"),
                "warning_entity_count": result.total("warning_count"),
                "decision_count": decision_count,
                "calculator_id": calculator_id,
                "rule_of_five_method_id": rule_of_five_method_id,
                "qed_method_id": qed_method_id,
                "policy_id": policy_id,
                "policy_enabled": policy_enabled,
                "backend": "rdkit",
                "backend_version": rdBase.rdkitVersion,
                **result.response_metadata(),
            },
        )


__all__ = [
    "DRUG_LIKENESS_V1",
    "DrugLikenessConfig",
    "FailureAction",
    "RDKitDrugLikenessPlugin",
]
