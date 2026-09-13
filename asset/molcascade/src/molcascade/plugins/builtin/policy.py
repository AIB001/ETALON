"""Executable policy joins for parallel screening evidence branches.

Metric and alert plugins can run from the same retained ``parent/v1`` dataset.
This module provides the explicit barrier which combines their complete
``decision/v1`` datasets.  The join is deliberately separate from calculation:
changing an AND/OR policy never changes the upstream scientific evidence.
"""

from __future__ import annotations

import json
import sqlite3
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import (
    iter_contract_batches,
    open_stage_database,
    write_query_parquet,
)
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.plugins.api import (
    PendingOutput,
    StageContext,
    StageInput,
    StageRequest,
    StageResponse,
)
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_DECISION_PATH = Path("datasets/policy_decisions/part-00000.parquet")


class DecisionJoinMode(StrEnum):
    """Supported branch-level policy semantics."""

    ALL_REQUIRED = "all_required"
    ANY_REQUIRED = "any_required"
    MIN_PASS_COUNT = "min_pass_count"


class DecisionJoinConfig(StrictFrozenModel):
    """Frozen policy for combining parallel, complete decision branches."""

    schema_version: int = Field(default=1, ge=1, le=1)
    mode: DecisionJoinMode = DecisionJoinMode.ALL_REQUIRED
    min_pass_count: int | None = Field(default=None, ge=1, le=1_000)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)

    @field_validator("mode", mode="before")
    @classmethod
    def _parse_mode(cls, value: Any) -> Any:
        return DecisionJoinMode(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _validate_mode_options(self) -> DecisionJoinConfig:
        if self.mode is DecisionJoinMode.MIN_PASS_COUNT and self.min_pass_count is None:
            raise ValueError("min_pass_count is required when mode is min_pass_count")
        if self.mode is not DecisionJoinMode.MIN_PASS_COUNT and self.min_pass_count is not None:
            raise ValueError("min_pass_count is only valid when mode is min_pass_count")
        return self


def _classify_inputs(
    inputs: dict[str, StageInput],
) -> tuple[StageInput, list[tuple[str, StageInput]]]:
    parents = [(port, value) for port, value in inputs.items() if value.contract_id == PARENT_V1.id]
    decisions = sorted(
        (
            (port, value)
            for port, value in inputs.items()
            if value.contract_id == DECISION_V1.id
        ),
        key=lambda item: item[0],
    )
    unsupported = sorted(
        port
        for port, value in inputs.items()
        if value.contract_id not in {PARENT_V1.id, DECISION_V1.id}
    )
    if unsupported:
        raise PluginError(
            "decision join received unsupported input contracts",
            code="POLICY_JOIN_INPUT_CONTRACT_INVALID",
            context={"request_ports": unsupported},
        )
    if len(parents) != 1:
        raise PluginError(
            "decision join requires exactly one parent input",
            code="POLICY_JOIN_PARENT_INPUT_INVALID",
            context={"parent_input_count": len(parents)},
        )
    if not decisions:
        raise PluginError(
            "decision join requires at least one decision branch",
            code="POLICY_JOIN_DECISIONS_REQUIRED",
        )
    return parents[0][1], decisions


class NativeDecisionJoinPlugin:
    """Filter parents using an explicit policy over parallel decision branches."""

    descriptor = PluginDescriptor(
        id="policy.native_decision_join",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id, DECISION_V1.id),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        tier_neutral_policy=True,
        display_name="Parallel decision policy join",
        description=(
            "An explicit ALL/ANY/minimum-pass barrier over complete decision branches; "
            "upstream evidence remains immutable and independently auditable."
        ),
    )
    config_model = DecisionJoinConfig

    @staticmethod
    def _schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE parents (
                parent_id TEXT PRIMARY KEY NOT NULL,
                identity_policy_id TEXT NOT NULL,
                parent_smiles TEXT NOT NULL,
                registration_key TEXT NOT NULL,
                stereo_key TEXT,
                formula TEXT,
                duplicate_count INTEGER NOT NULL
            );
            CREATE TABLE decision_keys (
                request_port TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                entity_kind TEXT NOT NULL,
                source_stage_id TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                PRIMARY KEY (
                    request_port, entity_id, entity_kind, source_stage_id, reason_code
                )
            );
            CREATE TABLE branch_stages (
                request_port TEXT NOT NULL,
                source_stage_id TEXT NOT NULL,
                PRIMARY KEY (request_port, source_stage_id)
            );
            CREATE TABLE branch_outcomes (
                request_port TEXT NOT NULL,
                parent_id TEXT NOT NULL,
                has_pass INTEGER NOT NULL DEFAULT 0,
                has_reject INTEGER NOT NULL DEFAULT 0,
                has_warn INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (request_port, parent_id)
            );
            CREATE INDEX branch_outcomes_parent
                ON branch_outcomes (parent_id, request_port);
            CREATE TABLE passed AS SELECT * FROM parents WHERE 0;
            CREATE UNIQUE INDEX passed_parent_id ON passed (parent_id);
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
    def _insert_parents(
        connection: sqlite3.Connection,
        stage_input: StageInput,
        *,
        batch_size: int,
    ) -> int:
        count = 0
        for batch in iter_contract_batches(stage_input, PARENT_V1, batch_size=batch_size):
            for row in batch.to_pylist():
                try:
                    connection.execute(
                        """
                        INSERT INTO parents (
                            parent_id, identity_policy_id, parent_smiles, registration_key,
                            stereo_key, formula, duplicate_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["parent_id"],
                            row["identity_policy_id"],
                            row["parent_smiles"],
                            row["registration_key"],
                            row["stereo_key"],
                            row["formula"],
                            row["duplicate_count"],
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise PluginError(
                        "parent input contains duplicate identifiers",
                        code="POLICY_JOIN_DUPLICATE_PARENT_ID",
                        context={"parent_id": str(row.get("parent_id"))},
                    ) from error
                count += 1
        if count == 0:
            raise PluginError(
                "decision join parent input contains no rows",
                code="POLICY_JOIN_EMPTY_INPUT",
            )
        return count

    @staticmethod
    def _insert_branch(
        connection: sqlite3.Connection,
        *,
        request_port: str,
        stage_input: StageInput,
        batch_size: int,
    ) -> None:
        row_count = 0
        for batch in iter_contract_batches(stage_input, DECISION_V1, batch_size=batch_size):
            for row in batch.to_pylist():
                row_count += 1
                if row["entity_kind"] != "PARENT":
                    raise PluginError(
                        "decision join accepts only PARENT decisions",
                        code="POLICY_JOIN_ENTITY_KIND_INVALID",
                        context={
                            "request_port": request_port,
                            "entity_kind": str(row["entity_kind"]),
                        },
                    )
                try:
                    connection.execute(
                        """
                        INSERT INTO decision_keys (
                            request_port, entity_id, entity_kind, source_stage_id, reason_code
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            request_port,
                            row["entity_id"],
                            row["entity_kind"],
                            row["stage_id"],
                            row["reason_code"],
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise PluginError(
                        "decision branch contains a duplicate decision key",
                        code="POLICY_JOIN_DUPLICATE_DECISION",
                        context={
                            "request_port": request_port,
                            "parent_id": str(row["entity_id"]),
                            "source_stage_id": str(row["stage_id"]),
                            "reason_code": str(row["reason_code"]),
                        },
                    ) from error
                connection.execute(
                    "INSERT OR IGNORE INTO branch_stages VALUES (?, ?)",
                    (request_port, row["stage_id"]),
                )
                outcome = row["outcome"]
                connection.execute(
                    """
                    INSERT INTO branch_outcomes (
                        request_port, parent_id, has_pass, has_reject, has_warn
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (request_port, parent_id) DO UPDATE SET
                        has_pass = MAX(has_pass, excluded.has_pass),
                        has_reject = MAX(has_reject, excluded.has_reject),
                        has_warn = MAX(has_warn, excluded.has_warn)
                    """,
                    (
                        request_port,
                        row["entity_id"],
                        int(outcome == "PASS"),
                        int(outcome == "REJECT"),
                        int(outcome == "WARN"),
                    ),
                )
        if row_count == 0:
            raise PluginError(
                "decision branch contains no rows",
                code="POLICY_JOIN_EMPTY_DECISION_BRANCH",
                context={"request_port": request_port},
            )

    @staticmethod
    def _validate_branches(
        connection: sqlite3.Connection,
        *,
        parent_count: int,
        request_ports: list[str],
    ) -> dict[str, str]:
        stages: dict[str, str] = {}
        for port in request_ports:
            stage_rows = connection.execute(
                "SELECT source_stage_id FROM branch_stages WHERE request_port = ?",
                (port,),
            ).fetchall()
            if len(stage_rows) != 1:
                raise PluginError(
                    "each decision input must contain exactly one source stage",
                    code="POLICY_JOIN_BRANCH_STAGE_INVALID",
                    context={"request_port": port, "stage_count": len(stage_rows)},
                )
            stages[port] = str(stage_rows[0][0])
            unknown = connection.execute(
                """
                SELECT b.parent_id FROM branch_outcomes b
                LEFT JOIN parents p ON p.parent_id = b.parent_id
                WHERE b.request_port = ? AND p.parent_id IS NULL
                ORDER BY b.parent_id LIMIT 1
                """,
                (port,),
            ).fetchone()
            if unknown is not None:
                raise PluginError(
                    "decision branch refers to a parent outside the joined parent input",
                    code="POLICY_JOIN_UNKNOWN_PARENT",
                    context={"request_port": port, "parent_id": str(unknown[0])},
                )
            covered, conflicts = connection.execute(
                """
                SELECT COUNT(*),
                       SUM(CASE WHEN has_pass = 1 AND has_reject = 1 THEN 1 ELSE 0 END)
                FROM branch_outcomes WHERE request_port = ?
                """,
                (port,),
            ).fetchone()
            if covered != parent_count:
                missing = connection.execute(
                    """
                    SELECT p.parent_id FROM parents p
                    LEFT JOIN branch_outcomes b
                      ON b.parent_id = p.parent_id AND b.request_port = ?
                    WHERE b.parent_id IS NULL ORDER BY p.parent_id LIMIT 1
                    """,
                    (port,),
                ).fetchone()
                raise PluginError(
                    "decision branch does not cover every parent exactly once",
                    code="POLICY_JOIN_INCOMPLETE_BRANCH",
                    context={
                        "request_port": port,
                        "parent_count": parent_count,
                        "covered_parent_count": int(covered),
                        "example_missing_parent_id": str(missing[0]) if missing else None,
                    },
                )
            if conflicts:
                raise PluginError(
                    "a decision branch marks the same parent both PASS and REJECT",
                    code="POLICY_JOIN_CONFLICTING_OUTCOME",
                    context={"request_port": port, "conflict_count": int(conflicts)},
                )
        return stages

    @staticmethod
    def _policy_passes(
        config: DecisionJoinConfig,
        *,
        pass_count: int,
        reject_count: int,
        branch_count: int,
    ) -> bool:
        if config.mode is DecisionJoinMode.ALL_REQUIRED:
            return reject_count == 0 and pass_count == branch_count
        if config.mode is DecisionJoinMode.ANY_REQUIRED:
            return pass_count >= 1
        assert config.min_pass_count is not None
        return pass_count >= config.min_pass_count

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        try:
            config = self.config_model.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid decision-join configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"error_count": error.error_count()},
            ) from error
        parent_input, decision_inputs = _classify_inputs(dict(request.inputs))
        branch_count = len(decision_inputs)
        if (
            config.mode is DecisionJoinMode.MIN_PASS_COUNT
            and config.min_pass_count is not None
            and config.min_pass_count > branch_count
        ):
            raise PluginError(
                "min_pass_count cannot exceed the number of decision branches",
                code="POLICY_JOIN_THRESHOLD_INVALID",
                context={
                    "min_pass_count": config.min_pass_count,
                    "branch_count": branch_count,
                },
            )

        context.staging_root.mkdir(parents=True, exist_ok=True)
        database_path = context.staging_root / ".decision-policy-join.sqlite3"
        if database_path.exists() or database_path.is_symlink():
            raise PluginError(
                "decision-join staging database already exists",
                code="PLUGIN_STAGING_NOT_EMPTY",
                context={"path": str(database_path)},
            )
        for relative in (_PARENT_PATH, _DECISION_PATH):
            destination = context.staging_root / relative
            if destination.exists() or destination.is_symlink():
                raise PluginError(
                    "decision-join output already exists in staging",
                    code="PLUGIN_STAGING_NOT_EMPTY",
                    context={"path": relative.as_posix()},
                )

        connection: sqlite3.Connection | None = None
        try:
            connection = open_stage_database(database_path)
            self._schema(connection)
            with connection:
                parent_count = self._insert_parents(
                    connection,
                    parent_input,
                    batch_size=config.batch_size,
                )
                for request_port, stage_input in decision_inputs:
                    self._insert_branch(
                        connection,
                        request_port=request_port,
                        stage_input=stage_input,
                        batch_size=config.batch_size,
                    )
                source_stages = self._validate_branches(
                    connection,
                    parent_count=parent_count,
                    request_ports=[port for port, _ in decision_inputs],
                )
                policy_id = "decision-join-policy:sha256:" + canonical_sha256(
                    {
                        "implementation": self.descriptor.key,
                        "mode": config.mode.value,
                        "min_pass_count": config.min_pass_count,
                        "branches": source_stages,
                    }
                )

                passed_count = 0
                rejected_count = 0
                rows = connection.execute(
                    """
                    SELECT p.parent_id, p.identity_policy_id, p.parent_smiles,
                           p.registration_key, p.stereo_key, p.formula, p.duplicate_count,
                           b.request_port, b.has_pass, b.has_reject, b.has_warn
                    FROM parents p
                    JOIN branch_outcomes b ON b.parent_id = p.parent_id
                    ORDER BY p.parent_id, b.request_port
                    """
                )
                current_parent: tuple[Any, ...] | None = None
                branch_rows: list[tuple[str, int, int, int]] = []

                def commit_parent() -> None:
                    nonlocal passed_count, rejected_count, current_parent, branch_rows
                    if current_parent is None:
                        return
                    # A covered WARN-only branch retained the parent and therefore
                    # satisfies the branch. Missing evidence is still rejected by
                    # the coverage check, while any REJECT remains authoritative.
                    pass_ports = [
                        row[0]
                        for row in branch_rows
                        if not row[2] and (row[1] or row[3])
                    ]
                    reject_ports = [row[0] for row in branch_rows if row[2]]
                    warn_ports = [row[0] for row in branch_rows if row[3]]
                    selected = self._policy_passes(
                        config,
                        pass_count=len(pass_ports),
                        reject_count=len(reject_ports),
                        branch_count=branch_count,
                    )
                    parent_id = str(current_parent[0])
                    if selected:
                        passed_count += 1
                        connection.execute(
                            "INSERT INTO passed VALUES (?, ?, ?, ?, ?, ?, ?)",
                            current_parent,
                        )
                    else:
                        rejected_count += 1
                    detail = json.dumps(
                        {
                            "mode": config.mode.value,
                            "pass_branches": pass_ports,
                            "reject_branches": reject_ports,
                            "warn_branches": warn_ports,
                            "source_stages": source_stages,
                        },
                        allow_nan=False,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    connection.execute(
                        """
                        INSERT INTO decisions (
                            entity_id, entity_kind, stage_id, outcome,
                            reason_code, rule_id, detail
                        ) VALUES (?, 'PARENT', ?, ?, ?, ?, ?)
                        """,
                        (
                            parent_id,
                            request.stage_id,
                            "PASS" if selected else "REJECT",
                            "POLICY_JOIN_PASS" if selected else "POLICY_JOIN_REJECT",
                            policy_id,
                            detail,
                        ),
                    )

                for row in rows:
                    parent = tuple(row[:7])
                    if current_parent is not None and parent[0] != current_parent[0]:
                        commit_parent()
                        branch_rows = []
                    current_parent = parent
                    branch_rows.append((str(row[7]), int(row[8]), int(row[9]), int(row[10])))
                commit_parent()

            write_query_parquet(
                connection,
                """
                SELECT parent_id, identity_policy_id, parent_smiles, registration_key,
                       stereo_key, formula, duplicate_count
                FROM passed ORDER BY parent_id
                """,
                schema=PARENT_V1.schema,
                destination=context.staging_root / _PARENT_PATH,
                batch_size=config.batch_size,
            )
            write_query_parquet(
                connection,
                """
                SELECT entity_id, entity_kind, stage_id, outcome, reason_code, rule_id, detail
                FROM decisions ORDER BY entity_id, entity_kind, stage_id, reason_code
                """,
                schema=DECISION_V1.schema,
                destination=context.staging_root / _DECISION_PATH,
                batch_size=config.batch_size,
            )
            return StageResponse(
                outputs={
                    "primary": PendingOutput(
                        PARENT_V1.id,
                        (_PARENT_PATH.as_posix(),),
                        {"row_count": passed_count},
                    ),
                    "decisions": PendingOutput(
                        DECISION_V1.id,
                        (_DECISION_PATH.as_posix(),),
                        {
                            "row_count": parent_count,
                            "policy_id": policy_id,
                            "branch_count": branch_count,
                        },
                    ),
                },
                metadata={
                    "input_count": parent_count,
                    "output_count": passed_count,
                    "reject_count": rejected_count,
                    "branch_count": branch_count,
                    "source_stages": source_stages,
                    "policy_id": policy_id,
                    "mode": config.mode.value,
                },
            )
        finally:
            if connection is not None:
                connection.close()
            database_path.unlink(missing_ok=True)


__all__ = [
    "DecisionJoinConfig",
    "DecisionJoinMode",
    "NativeDecisionJoinPlugin",
]
