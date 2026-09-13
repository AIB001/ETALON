"""Built-in RDKit standardisation, registration, and global deduplication stage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import Field, ValidationError

from molcascade.chemistry.datasets import (
    iter_contract_batches,
    open_stage_database,
    write_query_parquet,
)
from molcascade.chemistry.identity import (
    ParentStandardizer,
    RegisteredParent,
    StandardizationFailure,
    rdkit_identity_metadata,
)
from molcascade.chemistry.policies import IdentityPolicy
from molcascade.config.canonical import canonical_json
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import (
    DECISION_V1,
    PARENT_SOURCE_MAP_V1,
    PARENT_V1,
    RAW_MOLECULE_V1,
    RAW_MOLECULE_V2,
    DataContract,
)
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


class StandardizePluginConfig(StrictFrozenModel):
    """Strict configuration for the built-in standardizer."""

    identity_policy: IdentityPolicy = Field(default_factory=IdentityPolicy)
    batch_size: int = Field(default=65_536, ge=1, le=1_000_000)


class RDKitStandardizePlugin:
    """Stream raw structures and perform disk-backed exact global deduplication."""

    descriptor = PluginDescriptor(
        id="chemistry.rdkit_standardize",
        version="0.1.0",
        kind=PluginKind.STANDARDIZER,
        inputs=(RAW_MOLECULE_V1.id, RAW_MOLECULE_V2.id),
        outputs=(PARENT_V1.id, PARENT_SOURCE_MAP_V1.id, DECISION_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "mapping": PARENT_SOURCE_MAP_V1.id,
            "decisions": DECISION_V1.id,
        },
        cardinality=Cardinality.MANY_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit parent standardization and registration",
        description=(
            "Versioned parent identity, structured per-source rejection, and exact "
            "global deduplication using an ephemeral SQLite index."
        ),
    )
    config_model = StandardizePluginConfig

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE sources (
                source_record_id TEXT PRIMARY KEY NOT NULL,
                raw_format TEXT NOT NULL
            );
            CREATE TABLE parents (
                parent_id TEXT PRIMARY KEY NOT NULL,
                identity_policy_id TEXT NOT NULL,
                parent_smiles TEXT NOT NULL,
                registration_key TEXT NOT NULL,
                stereo_key TEXT,
                formula TEXT,
                duplicate_count INTEGER NOT NULL CHECK (duplicate_count > 0)
            );
            CREATE TABLE mappings (
                source_record_id TEXT PRIMARY KEY NOT NULL,
                parent_id TEXT NOT NULL,
                relation TEXT NOT NULL
            );
            CREATE INDEX mappings_parent_id ON mappings(parent_id, source_record_id);
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
    def _select_input(request: StageRequest) -> tuple[StageInput, DataContract]:
        supported = {
            RAW_MOLECULE_V1.id: RAW_MOLECULE_V1,
            RAW_MOLECULE_V2.id: RAW_MOLECULE_V2,
        }
        if len(request.inputs) != 1:
            raise PluginError(
                "standardizer requires exactly one raw molecule input",
                code="PLUGIN_INPUT_CARDINALITY_INVALID",
                context={
                    "supported_contracts": sorted(supported),
                    "input_count": len(request.inputs),
                },
            )
        stage_input = next(iter(request.inputs.values()))
        contract_id = stage_input.contract_id
        contract = supported.get(contract_id) if contract_id is not None else None
        if contract is None:
            raise PluginError(
                f"unsupported standardizer input contract: {contract_id}",
                code="PLUGIN_INPUT_CONTRACT_MISMATCH",
                context={
                    "declared_contract": contract_id,
                    "supported_contracts": sorted(supported),
                },
            )
        return stage_input, contract

    @staticmethod
    def _v1_raw_format(row: dict[str, object]) -> str:
        raw_smiles = row.get("raw_smiles")
        raw_molblock = row.get("raw_molblock")
        has_smiles = isinstance(raw_smiles, str) and bool(raw_smiles.strip())
        has_molblock = isinstance(raw_molblock, str) and bool(raw_molblock.strip())
        if has_smiles and has_molblock:
            return "SMILES+MOLBLOCK"
        if has_smiles:
            return "SMILES"
        if has_molblock:
            return "MOLBLOCK"
        return "UNSPECIFIED"

    @staticmethod
    def _audit_detail(
        raw_format: str,
        *,
        message: str | None = None,
        parent_id: str | None = None,
    ) -> str:
        payload: dict[str, str] = {"raw_format": raw_format}
        if message is not None:
            payload["message"] = message[:3500]
        if parent_id is not None:
            payload["parent_id"] = parent_id
        return canonical_json(payload)

    @staticmethod
    def _decision(
        connection: sqlite3.Connection,
        *,
        entity_id: str,
        stage_id: str,
        outcome: str,
        reason_code: str,
        detail: str | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO decisions (
                entity_id, entity_kind, stage_id, outcome, reason_code, rule_id, detail
            ) VALUES (?, 'SOURCE_RECORD', ?, ?, ?, NULL, ?)
            """,
            (entity_id, stage_id, outcome, reason_code, detail),
        )

    @staticmethod
    def _register_parent(
        connection: sqlite3.Connection,
        parent: RegisteredParent,
    ) -> None:
        cursor = connection.execute(
            """
            INSERT INTO parents (
                parent_id, identity_policy_id, parent_smiles, registration_key,
                stereo_key, formula, duplicate_count
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(parent_id) DO UPDATE SET
                duplicate_count = parents.duplicate_count + 1
            WHERE parents.identity_policy_id = excluded.identity_policy_id
              AND parents.parent_smiles = excluded.parent_smiles
              AND parents.registration_key = excluded.registration_key
              AND parents.stereo_key = excluded.stereo_key
              AND parents.formula = excluded.formula
            """,
            (
                parent.parent_id,
                parent.identity_policy_id,
                parent.parent_smiles,
                parent.registration_key,
                parent.stereo_key,
                parent.formula,
            ),
        )
        if cursor.rowcount != 1:
            raise PluginError(
                "parent ID collision produced inconsistent canonical parent values",
                code="STANDARDIZE_PARENT_ID_COLLISION",
                context={"parent_id": parent.parent_id},
            )

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        try:
            config = self.config_model.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid standardizer configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"plugin": self.descriptor.key, "error_count": error.error_count()},
            ) from error
        stage_input, input_contract = self._select_input(request)
        context.staging_root.mkdir(parents=True, exist_ok=True)
        database_path = context.staging_root / ".rdkit-standardize-index.sqlite3"
        if database_path.exists():
            raise PluginError(
                f"standardizer staging database already exists: {database_path}",
                code="PLUGIN_STAGING_NOT_EMPTY",
                context={"path": str(database_path)},
            )

        connection: sqlite3.Connection | None = None
        try:
            connection = open_stage_database(database_path)
            self._create_schema(connection)
            standardizer = ParentStandardizer(config.identity_policy)
            input_count = 0
            reject_count = 0
            format_counts: dict[str, int] = {}
            with connection:
                for batch in iter_contract_batches(
                    stage_input,
                    input_contract,
                    batch_size=config.batch_size,
                ):
                    for row in batch.to_pylist():
                        input_count += 1
                        if input_contract.id == RAW_MOLECULE_V2.id:
                            value = row.get("raw_format")
                            raw_format = value if isinstance(value, str) else "UNSPECIFIED"
                        else:
                            raw_format = self._v1_raw_format(row)
                        format_counts[raw_format] = format_counts.get(raw_format, 0) + 1
                        source_id = row.get("source_record_id")
                        if not isinstance(source_id, str) or not source_id:
                            raise PluginError(
                                "raw molecule contains an invalid source_record_id",
                                code="STANDARDIZE_SOURCE_ID_INVALID",
                            )
                        try:
                            connection.execute(
                                "INSERT INTO sources (source_record_id, raw_format) VALUES (?, ?)",
                                (source_id, raw_format),
                            )
                        except sqlite3.IntegrityError as error:
                            raise PluginError(
                                f"duplicate source_record_id across input partitions: {source_id}",
                                code="STANDARDIZE_DUPLICATE_SOURCE_ID",
                                context={"source_record_id": source_id},
                            ) from error
                        try:
                            if input_contract.id == RAW_MOLECULE_V2.id:
                                parent = standardizer.standardize(
                                    raw_format=row.get("raw_format"),
                                    raw_structure=row.get("raw_structure"),
                                )
                            else:
                                parent = standardizer.standardize(
                                    raw_smiles=row.get("raw_smiles"),
                                    raw_molblock=row.get("raw_molblock"),
                                )
                        except StandardizationFailure as error:
                            reject_count += 1
                            self._decision(
                                connection,
                                entity_id=source_id,
                                stage_id=request.stage_id,
                                outcome="REJECT",
                                reason_code=error.reason_code,
                                detail=self._audit_detail(
                                    raw_format,
                                    message=error.detail,
                                ),
                            )
                            continue

                        self._register_parent(connection, parent)
                        connection.execute(
                            """
                            INSERT INTO mappings (source_record_id, parent_id, relation)
                            VALUES (?, ?, 'PENDING')
                            """,
                            (source_id, parent.parent_id),
                        )
                        self._decision(
                            connection,
                            entity_id=source_id,
                            stage_id=request.stage_id,
                            outcome="PASS",
                            reason_code="STANDARDIZED",
                            detail=self._audit_detail(
                                raw_format,
                                parent_id=parent.parent_id,
                            ),
                        )
                        for notice in parent.notices:
                            self._decision(
                                connection,
                                entity_id=source_id,
                                stage_id=request.stage_id,
                                outcome="WARN",
                                reason_code=notice.code,
                                detail=self._audit_detail(
                                    raw_format,
                                    message=notice.detail,
                                ),
                            )

                if input_count == 0:
                    raise PluginError(
                        "standardizer input dataset contains no rows",
                        code="STANDARDIZE_EMPTY_INPUT",
                    )
                connection.execute(
                    """
                    UPDATE mappings
                    SET relation = CASE
                        WHEN source_record_id = (
                            SELECT MIN(peer.source_record_id)
                            FROM mappings AS peer
                            WHERE peer.parent_id = mappings.parent_id
                        ) THEN 'PRIMARY'
                        ELSE 'DUPLICATE'
                    END
                    """
                )
                duplicates = connection.execute(
                    """
                    SELECT mappings.source_record_id, sources.raw_format
                    FROM mappings
                    JOIN sources USING (source_record_id)
                    WHERE mappings.relation = 'DUPLICATE'
                    ORDER BY mappings.source_record_id
                    """
                )
                for source_id, raw_format in duplicates:
                    self._decision(
                        connection,
                        entity_id=source_id,
                        stage_id=request.stage_id,
                        outcome="WARN",
                        reason_code="EXACT_DUPLICATE",
                        detail=self._audit_detail(
                            raw_format,
                            message=(
                                "source occurrence maps to an existing canonical parent"
                            ),
                        ),
                    )
                refined_sources = connection.execute(
                    """
                    SELECT mappings.source_record_id, sources.raw_format
                    FROM mappings
                    JOIN sources USING (source_record_id)
                    JOIN parents ON parents.parent_id = mappings.parent_id
                    WHERE parents.registration_key IN (
                        SELECT registration_key
                        FROM parents
                        GROUP BY registration_key
                        HAVING COUNT(*) > 1
                    )
                    ORDER BY mappings.source_record_id
                    """
                )
                for source_id, raw_format in refined_sources:
                    self._decision(
                        connection,
                        entity_id=source_id,
                        stage_id=request.stage_id,
                        outcome="WARN",
                        reason_code="REGISTRATION_EQUIVALENCE_REFINED",
                        detail=self._audit_detail(
                            raw_format,
                            message=(
                                "broad RegistrationHash equivalence was split by the "
                                "canonical parseable parent representation"
                            ),
                        ),
                    )

            map_count = connection.execute("SELECT COUNT(*) FROM mappings").fetchone()[0]
            parent_count = connection.execute("SELECT COUNT(*) FROM parents").fetchone()[0]
            registration_key_count = connection.execute(
                "SELECT COUNT(DISTINCT registration_key) FROM parents"
            ).fetchone()[0]
            refinement_split_count = parent_count - registration_key_count
            refined_source_count = connection.execute(
                """
                SELECT COUNT(*) FROM decisions
                WHERE reason_code = 'REGISTRATION_EQUIVALENCE_REFINED'
                """
            ).fetchone()[0]
            duplicate_count = map_count - parent_count
            if input_count != reject_count + map_count:
                raise PluginError(
                    "standardization count conservation failed",
                    code="STANDARDIZE_COUNT_MISMATCH",
                    context={
                        "input_count": input_count,
                        "reject_count": reject_count,
                        "map_count": map_count,
                    },
                )
            if map_count != parent_count + duplicate_count:
                raise PluginError(
                    "parent mapping/duplicate count conservation failed",
                    code="STANDARDIZE_DEDUP_COUNT_MISMATCH",
                )

            parent_path = Path("datasets/parents/part-00000.parquet")
            mapping_path = Path("datasets/parent_source_map/part-00000.parquet")
            decision_path = Path("datasets/decisions/part-00000.parquet")
            write_query_parquet(
                connection,
                """
                SELECT parent_id, identity_policy_id, parent_smiles, registration_key,
                       stereo_key, formula, duplicate_count
                FROM parents ORDER BY parent_id
                """,
                schema=PARENT_V1.schema,
                destination=context.staging_root / parent_path,
                batch_size=config.batch_size,
            )
            write_query_parquet(
                connection,
                """
                SELECT source_record_id, parent_id, relation
                FROM mappings ORDER BY source_record_id
                """,
                schema=PARENT_SOURCE_MAP_V1.schema,
                destination=context.staging_root / mapping_path,
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
            implementation = rdkit_identity_metadata(config.identity_policy)
            response_metadata = {
                **implementation,
                "input_contract_id": input_contract.id,
                "format_counts": dict(sorted(format_counts.items())),
                "input_count": input_count,
                "reject_count": reject_count,
                "map_count": map_count,
                "unique_parent_count": parent_count,
                "unique_registration_key_count": registration_key_count,
                "canonical_refinement_split_count": refinement_split_count,
                "canonical_refinement_source_count": refined_source_count,
                "duplicate_occurrence_count": duplicate_count,
                "identity_policy": config.identity_policy.model_dump(mode="json"),
            }
            return StageResponse(
                outputs={
                    "primary": PendingOutput(
                        contract_id=PARENT_V1.id,
                        file_paths=(parent_path.as_posix(),),
                        metadata={"row_count": parent_count, **implementation},
                    ),
                    "mapping": PendingOutput(
                        contract_id=PARENT_SOURCE_MAP_V1.id,
                        file_paths=(mapping_path.as_posix(),),
                        metadata={"row_count": map_count},
                    ),
                    "decisions": PendingOutput(
                        contract_id=DECISION_V1.id,
                        file_paths=(decision_path.as_posix(),),
                        metadata={
                            "rejected_entity_count": reject_count,
                            "duplicate_occurrence_count": duplicate_count,
                            "canonical_refinement_source_count": refined_source_count,
                        },
                    ),
                },
                metadata=response_metadata,
            )
        finally:
            if connection is not None:
                connection.close()
            database_path.unlink(missing_ok=True)


StandardizePlugin = RDKitStandardizePlugin
PLUGIN = RDKitStandardizePlugin()

__all__ = [
    "PLUGIN",
    "RDKitStandardizePlugin",
    "StandardizePlugin",
    "StandardizePluginConfig",
]
