"""Built-in conservative project hard-chemistry gate."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import Field, ValidationError
from rdkit import rdBase

from molcascade.chemistry.datasets import (
    iter_contract_batches,
    open_stage_database,
    require_single_input,
    write_query_parquet,
)
from molcascade.chemistry.gates import HardGateEvaluator, hard_gate_policy_id
from molcascade.chemistry.policies import HardGatePolicy
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)


class HardGatePluginConfig(StrictFrozenModel):
    """Strict configuration for the built-in hard gate."""

    policy: HardGatePolicy = Field(default_factory=HardGatePolicy)
    batch_size: int = Field(default=65_536, ge=1, le=1_000_000)


class RDKitHardGatePlugin:
    """Apply only versioned hard rules and emit PAINS as a warning."""

    descriptor = PluginDescriptor(
        id="chemistry.rdkit_hard_gate",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit conservative hard chemistry gate",
        description=(
            "Project element/SMARTS hard rejection with explicit decisions; PAINS is "
            "reported as a warning and never rejected by the built-in catalog."
        ),
    )
    config_model = HardGatePluginConfig

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE seen (
                parent_id TEXT PRIMARY KEY NOT NULL
            );
            CREATE TABLE passed (
                parent_id TEXT PRIMARY KEY NOT NULL,
                identity_policy_id TEXT NOT NULL,
                parent_smiles TEXT NOT NULL,
                registration_key TEXT NOT NULL,
                stereo_key TEXT,
                formula TEXT,
                duplicate_count INTEGER NOT NULL
            );
            CREATE TABLE decisions (
                entity_id TEXT NOT NULL,
                entity_kind TEXT NOT NULL,
                stage_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                rule_id TEXT,
                detail TEXT,
                PRIMARY KEY (entity_id, entity_kind, stage_id, reason_code)
            );
            """
        )

    @staticmethod
    def _decision(
        connection: sqlite3.Connection,
        *,
        parent_id: str,
        stage_id: str,
        outcome: str,
        reason_code: str,
        rule_id: str | None,
        detail: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO decisions (
                entity_id, entity_kind, stage_id, outcome, reason_code, rule_id, detail
            ) VALUES (?, 'PARENT', ?, ?, ?, ?, ?)
            """,
            (parent_id, stage_id, outcome, reason_code, rule_id, detail),
        )

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        try:
            config = self.config_model.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid hard-gate configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"plugin": self.descriptor.key, "error_count": error.error_count()},
            ) from error
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        evaluator = HardGateEvaluator(config.policy)
        context.staging_root.mkdir(parents=True, exist_ok=True)
        database_path = context.staging_root / ".rdkit-hard-gate-index.sqlite3"
        if database_path.exists():
            raise PluginError(
                f"hard-gate staging database already exists: {database_path}",
                code="PLUGIN_STAGING_NOT_EMPTY",
                context={"path": str(database_path)},
            )

        connection: sqlite3.Connection | None = None
        try:
            connection = open_stage_database(database_path)
            self._create_schema(connection)
            input_count = 0
            pass_count = 0
            reject_count = 0
            warning_count = 0
            with connection:
                for batch in iter_contract_batches(
                    stage_input,
                    PARENT_V1,
                    batch_size=config.batch_size,
                ):
                    for row in batch.to_pylist():
                        input_count += 1
                        parent_id = row.get("parent_id")
                        parent_smiles = row.get("parent_smiles")
                        if not isinstance(parent_id, str) or not parent_id:
                            raise PluginError(
                                "parent input contains an invalid parent_id",
                                code="HARD_GATE_PARENT_ID_INVALID",
                            )
                        try:
                            connection.execute(
                                "INSERT INTO seen (parent_id) VALUES (?)",
                                (parent_id,),
                            )
                        except sqlite3.IntegrityError as error:
                            raise PluginError(
                                f"duplicate parent_id across input partitions: {parent_id}",
                                code="HARD_GATE_DUPLICATE_PARENT_ID",
                                context={"parent_id": parent_id},
                            ) from error
                        evaluation = evaluator.evaluate(
                            parent_smiles if isinstance(parent_smiles, str) else ""
                        )
                        if evaluation.passed:
                            pass_count += 1
                            connection.execute(
                                """
                                INSERT INTO passed (
                                    parent_id, identity_policy_id, parent_smiles,
                                    registration_key, stereo_key, formula, duplicate_count
                                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    row.get("parent_id"),
                                    row.get("identity_policy_id"),
                                    row.get("parent_smiles"),
                                    row.get("registration_key"),
                                    row.get("stereo_key"),
                                    row.get("formula"),
                                    row.get("duplicate_count"),
                                ),
                            )
                            self._decision(
                                connection,
                                parent_id=parent_id,
                                stage_id=request.stage_id,
                                outcome="PASS",
                                reason_code="HARD_GATE_PASS",
                                rule_id=None,
                                detail="parent passed all configured hard rules",
                            )
                        else:
                            reject_count += 1
                            for finding in evaluation.rejects:
                                self._decision(
                                    connection,
                                    parent_id=parent_id,
                                    stage_id=request.stage_id,
                                    outcome="REJECT",
                                    reason_code=finding.reason_code,
                                    rule_id=finding.rule_id,
                                    detail=finding.detail,
                                )
                        for finding in evaluation.warnings:
                            warning_count += 1
                            self._decision(
                                connection,
                                parent_id=parent_id,
                                stage_id=request.stage_id,
                                outcome="WARN",
                                reason_code=finding.reason_code,
                                rule_id=finding.rule_id,
                                detail=finding.detail,
                            )

                if input_count == 0:
                    raise PluginError(
                        "hard-gate input dataset contains no rows",
                        code="HARD_GATE_EMPTY_INPUT",
                    )
            if input_count != pass_count + reject_count:
                raise PluginError(
                    "hard-gate count conservation failed",
                    code="HARD_GATE_COUNT_MISMATCH",
                    context={
                        "input_count": input_count,
                        "pass_count": pass_count,
                        "reject_count": reject_count,
                    },
                )

            parent_path = Path("datasets/passed_parents/part-00000.parquet")
            decision_path = Path("datasets/decisions/part-00000.parquet")
            write_query_parquet(
                connection,
                """
                SELECT parent_id, identity_policy_id, parent_smiles, registration_key,
                       stereo_key, formula, duplicate_count
                FROM passed ORDER BY parent_id
                """,
                schema=PARENT_V1.schema,
                destination=context.staging_root / parent_path,
                batch_size=config.batch_size,
            )
            write_query_parquet(
                connection,
                """
                SELECT entity_id, entity_kind, stage_id, outcome, reason_code, rule_id, detail
                FROM decisions
                ORDER BY entity_id, entity_kind, stage_id, reason_code
                """,
                schema=DECISION_V1.schema,
                destination=context.staging_root / decision_path,
                batch_size=config.batch_size,
            )
            policy_json = config.policy.model_dump(mode="json")
            policy_id = hard_gate_policy_id(config.policy)
            return StageResponse(
                outputs={
                    "primary": PendingOutput(
                        contract_id=PARENT_V1.id,
                        file_paths=(parent_path.as_posix(),),
                        metadata={"row_count": pass_count},
                    ),
                    "decisions": PendingOutput(
                        contract_id=DECISION_V1.id,
                        file_paths=(decision_path.as_posix(),),
                        metadata={
                            "rejected_entity_count": reject_count,
                            "warning_count": warning_count,
                            "hard_gate_policy_id": policy_id,
                            "rdkit_version": rdBase.rdkitVersion,
                        },
                    ),
                },
                metadata={
                    "input_count": input_count,
                    "pass_count": pass_count,
                    "reject_count": reject_count,
                    "warning_count": warning_count,
                    "hard_gate_policy_id": policy_id,
                    "policy": policy_json,
                    "rdkit_version": rdBase.rdkitVersion,
                },
            )
        finally:
            if connection is not None:
                connection.close()
            database_path.unlink(missing_ok=True)


HardGatePlugin = RDKitHardGatePlugin
PLUGIN = RDKitHardGatePlugin()

__all__ = [
    "PLUGIN",
    "HardGatePlugin",
    "HardGatePluginConfig",
    "RDKitHardGatePlugin",
]
