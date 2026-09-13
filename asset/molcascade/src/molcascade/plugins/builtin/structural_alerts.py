"""RDKit FilterCatalog alerts with explicit, conservative policy actions.

An alert is a substructure match, not experimental proof of assay interference,
reactivity, instability, toxicity, or failure.  PAINS therefore defaults to
``WARN`` and all catalog actions remain project-configurable.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator

from molcascade.chemistry.alerts import parse_for_alert_matching
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
_CATALOG_NAMES = ("PAINS", "BRENK", "NIH", "ZINC")


class CatalogAction(StrEnum):
    IGNORE = "ignore"
    WARN = "warn"
    REJECT = "reject"


class StructuralAlertConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    decision_buffer_size: int = Field(default=50_000, ge=1, le=1_000_000)
    pains_action: CatalogAction = CatalogAction.WARN
    brenk_action: CatalogAction = CatalogAction.IGNORE
    nih_action: CatalogAction = CatalogAction.IGNORE
    zinc_action: CatalogAction = CatalogAction.IGNORE

    @field_validator(
        "pains_action",
        "brenk_action",
        "nih_action",
        "zinc_action",
        mode="before",
    )
    @classmethod
    def _parse_action(cls, value: Any) -> Any:
        return CatalogAction(value) if isinstance(value, str) else value

    def actions(self) -> dict[str, CatalogAction]:
        return {
            "PAINS": self.pains_action,
            "BRENK": self.brenk_action,
            "NIH": self.nih_action,
            "ZINC": self.zinc_action,
        }


def _validated_config(request: StageRequest) -> StructuralAlertConfig:
    try:
        return StructuralAlertConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid structural-alert configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


@cache
def _load_catalogs(
    enabled: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Build the requested RDKit catalogs once per process.

    Cached because a worker handles many shards in sequence and rebuilding the
    PAINS catalog for each of them would cost more than the matching does.  The
    key is the enabled names alone, which is what the catalogs depend on.
    """

    from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams

    catalogs: dict[str, Any] = {}
    sizes: dict[str, int] = {}
    for catalog_name in enabled:
        try:
            catalog_enum = getattr(FilterCatalogParams.FilterCatalogs, catalog_name)
            catalog = FilterCatalog(FilterCatalogParams(catalog_enum))
        except (AttributeError, RuntimeError, ValueError) as error:
            raise PluginError(
                f"RDKit structural-alert catalog is unavailable: {catalog_name}",
                code="STRUCTURAL_ALERT_CATALOG_UNAVAILABLE",
                hint="Use an RDKit build containing FilterCatalog or ignore this catalog.",
                context={"catalog": catalog_name},
            ) from error
        catalogs[catalog_name] = catalog
        sizes[catalog_name] = int(catalog.GetNumEntries())
    return catalogs, sizes


def _enabled_catalogs(config: StructuralAlertConfig) -> tuple[str, ...]:
    actions = config.actions()
    return tuple(
        name for name in _CATALOG_NAMES if actions[name] is not CatalogAction.IGNORE
    )


def _policy_id(config: StructuralAlertConfig, backend_version: str, sizes: dict[str, int]) -> str:
    actions = config.actions()
    return "structural-alert-policy:sha256:" + canonical_sha256(
        {
            "backend": "rdkit",
            "backend_version": backend_version,
            "catalog_actions": {name: actions[name].value for name in _CATALOG_NAMES},
            "catalog_sizes": sizes,
            "semantics": "substructure-triage-alert-not-experimental-proof",
        }
    )


def _alert_reason_code(catalog_name: str, description: str) -> str:
    digest = hashlib.sha256(description.encode("utf-8")).hexdigest().upper()
    return f"STRUCTURAL_ALERT_{catalog_name}_{digest}"


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Match one contiguous range of parents against the enabled catalogs."""

    from rdkit import rdBase

    config = StructuralAlertConfig.model_validate(dict(task.config))
    actions = config.actions()
    catalogs, sizes = _load_catalogs(_enabled_catalogs(config))
    policy_id = _policy_id(config, rdBase.rdkitVersion, sizes)
    input_count = 0
    retained_count = 0
    rejected_count = 0
    warning_decision_count = 0
    reject_decision_count = 0
    decision_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["decisions"], DECISION_V1.schema, compression="zstd"
        ) as decision_writer,
    ):
        decision_rows: list[dict[str, Any]] = []

        def flush_decisions() -> None:
            if decision_rows:
                decision_writer.write_table(
                    pa.Table.from_pylist(decision_rows, schema=DECISION_V1.schema)
                )
                decision_rows.clear()

        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            retained_rows: list[dict[str, Any]] = []
            for row in batch.to_pylist():
                input_count += 1
                parent_id = row.get("parent_id")
                smiles = row.get("parent_smiles")
                molecule = parse_for_alert_matching(smiles)
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed by RDKit FilterCatalog",
                        code="STRUCTURAL_ALERT_PARENT_INVALID",
                        context={"parent_id": str(parent_id)},
                    )
                findings: list[tuple[str, CatalogAction, str]] = []
                for catalog_name in _CATALOG_NAMES:
                    catalog = catalogs.get(catalog_name)
                    if catalog is None:
                        continue
                    try:
                        descriptions = sorted(
                            {
                                str(entry.GetDescription())
                                for entry in catalog.GetMatches(molecule)
                            }
                        )
                    except (RuntimeError, ValueError) as error:
                        raise PluginError(
                            "RDKit FilterCatalog matching failed",
                            code="STRUCTURAL_ALERT_MATCH_FAILED",
                            context={
                                "parent_id": str(parent_id),
                                "catalog": catalog_name,
                            },
                        ) from error
                    findings.extend(
                        (catalog_name, actions[catalog_name], description)
                        for description in descriptions
                    )

                rejected = any(
                    action is CatalogAction.REJECT for _, action, _ in findings
                )
                if rejected:
                    rejected_count += 1
                else:
                    retained_count += 1
                    retained_rows.append(row)

                if not findings:
                    decision_rows.append(
                        {
                            "entity_id": parent_id,
                            "entity_kind": "PARENT",
                            "stage_id": task.stage_id,
                            "outcome": "PASS",
                            "reason_code": "STRUCTURAL_ALERTS_CLEAR",
                            "rule_id": policy_id,
                            "detail": (
                                "No match in any enabled RDKit structural-alert catalog."
                            ),
                        }
                    )
                    decision_count += 1
                else:
                    for catalog_name, action, description in findings:
                        outcome = action.value.upper()
                        if action is CatalogAction.WARN:
                            warning_decision_count += 1
                        elif action is CatalogAction.REJECT:
                            reject_decision_count += 1
                        decision_rows.append(
                            {
                                "entity_id": parent_id,
                                "entity_kind": "PARENT",
                                "stage_id": task.stage_id,
                                "outcome": outcome,
                                "reason_code": _alert_reason_code(
                                    catalog_name, description
                                ),
                                "rule_id": policy_id,
                                "detail": json.dumps(
                                    {
                                        "action": action.value,
                                        "alert": description,
                                        "catalog": catalog_name,
                                        "interpretation": (
                                            "substructure triage flag; not "
                                            "experimental proof"
                                        ),
                                    },
                                    ensure_ascii=True,
                                    allow_nan=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            }
                        )
                        decision_count += 1
                        if len(decision_rows) >= config.decision_buffer_size:
                            flush_decisions()
                if len(decision_rows) >= config.decision_buffer_size:
                    flush_decisions()
            if retained_rows:
                parent_writer.write_table(
                    pa.Table.from_pylist(retained_rows, schema=PARENT_V1.schema)
                )
        flush_decisions()
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": retained_count, "decisions": decision_count},
        # A rejected molecule can carry several REJECT rows and a warned one
        # several WARN rows, so neither tally follows from the two row counts.
        metadata={
            "reject_count": rejected_count,
            "warning_decision_count": warning_decision_count,
            "reject_decision_count": reject_decision_count,
        },
    )


class RDKitStructuralAlertPlugin:
    """Apply PAINS/BRENK/NIH/ZINC catalog matches as WARN/REJECT/IGNORE."""

    descriptor = PluginDescriptor(
        id="chemistry.rdkit_structural_alerts",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit structural-alert catalogs",
        description=(
            "PAINS, BRENK, NIH, and ZINC substructure matches with independent "
            "IGNORE/WARN/REJECT actions; PAINS defaults to WARN."
        ),
    )
    config_model = StructuralAlertConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        # Built in the parent too, and deliberately: the catalog sizes are part
        # of the policy identity, so a catalog RDKit cannot supply has to stop
        # the stage here rather than in a worker three shards deep.
        _, catalog_sizes = _load_catalogs(_enabled_catalogs(config))
        policy_id = _policy_id(config, rdBase.rdkitVersion, catalog_sizes)
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
                "structural-alert input contains no parents",
                code="STRUCTURAL_ALERT_EMPTY_INPUT",
            )
        retained_count = result.rows_out["primary"]
        decision_count = result.rows_out["decisions"]
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": retained_count},
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    result.file_paths["decisions"],
                    {"row_count": decision_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": result.rows_in,
                "output_count": retained_count,
                "reject_count": result.total("reject_count"),
                "warning_decision_count": result.total("warning_decision_count"),
                "reject_decision_count": result.total("reject_decision_count"),
                "decision_count": decision_count,
                "policy_id": policy_id,
                "catalog_sizes": catalog_sizes,
                "backend": "rdkit",
                "backend_version": rdBase.rdkitVersion,
                **result.response_metadata(),
            },
        )


__all__ = [
    "CatalogAction",
    "RDKitStructuralAlertPlugin",
    "StructuralAlertConfig",
]
