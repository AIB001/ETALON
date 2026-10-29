"""Built-in RDKit standardisation, registration, and global deduplication stage."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq
from pydantic import Field, ValidationError

from molcascade.artifacts.hashing import sha256_file
from molcascade.chemistry.datasets import (
    discover_contract_files,
    open_stage_database,
    write_query_parquet,
)
from molcascade.chemistry.identity import (
    rdkit_identity_metadata,
)
from molcascade.chemistry.policies import IdentityPolicy
from molcascade.chemistry.sharding import shard_stage
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
from molcascade.io.atomic import atomic_write_bytes
from molcascade.parallel.progress import write_progress
from molcascade.plugins.api import (
    PendingOutput,
    StageContext,
    StageInput,
    StageRequest,
    StageResponse,
)
from molcascade.plugins.builtin.standardize_map import standardize_shard
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
            # Chemistry is independent per source; only global deduplication is serial.
            # Keep map outputs durable using the existing shard checkpoint protocol.
            resources = context.resources.replace(devices=("cpu",) * context.resources.workers)
            checkpoint = resources.checkpoint_dir
            files = discover_contract_files(stage_input, input_contract)
            binding = {
                "map_version": 1, "stage_id": request.stage_id,
                "input_digests": [sha256_file(f) for f in files],
                "config": config.model_dump(mode="json"),
                "implementation": rdkit_identity_metadata(config.identity_policy),
                "shard_rows": min(resources.shard_rows, 10000),
            }
            if checkpoint is not None:
                checkpoint.mkdir(parents=True, exist_ok=True)
                identity_file = checkpoint / "standardize-map.json"
                if identity_file.exists() and json.loads(identity_file.read_text()) != binding:
                    raise PluginError("standardization checkpoint belongs to different inputs or policy",
                                      code="STANDARDIZE_CHECKPOINT_MISMATCH")
                atomic_write_bytes(identity_file, canonical_json(binding).encode())
            mapped_root = context.staging_root / ".standardize-map"
            result = shard_stage(
                worker=standardize_shard, stage_input=stage_input, contract=input_contract,
                output_paths={"registrations": "registrations/part-00000.parquet",
                              "decisions": "decisions/part-00000.parquet"},
                context=StageContext(mapped_root, resources), stage_id=request.stage_id,
                config=config.model_dump(mode="json"), shard_rows=10000,
            )
            input_count = result.rows_in
            reject_count = result.total("reject_count")
            format_counts = Counter()
            for item in result.shard_metadata:
                format_counts.update(item.get("format_counts", {}))
            write_progress(checkpoint, phase="global_merge", effective_workers=1,
                           rows_completed=input_count, rows_total=input_count,
                           merge_rows_loaded=0, map_workers=min(resources.workers, result.shard_count))
            # Append first, index once. Random primary-key updates used to rewrite
            # many gigabytes of B-tree pages while doing every molecule serially.
            connection.execute("PRAGMA cache_size=-65536")
            connection.executescript("""
                CREATE TABLE registrations (source_record_id TEXT, raw_format TEXT,
                    parent_id TEXT, identity_policy_id TEXT, parent_smiles TEXT,
                    registration_key TEXT, stereo_key TEXT, formula TEXT);
                CREATE TABLE decisions (entity_id TEXT, entity_kind TEXT, stage_id TEXT,
                    outcome TEXT, reason_code TEXT, rule_id TEXT, detail TEXT);
            """)
            loaded = 0
            with connection:
                for port, table in [("registrations", "registrations"), ("decisions", "decisions")]:
                    for relative in result.file_paths[port]:
                        for batch in pq.ParquetFile(mapped_root / relative).iter_batches(batch_size=config.batch_size):
                            columns = batch.schema.names
                            connection.executemany(
                                f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})",
                                (tuple(row[c] for c in columns) for row in batch.to_pylist()),
                            )
                            if port == "registrations":
                                loaded += batch.num_rows
                        write_progress(checkpoint, merge_rows_loaded=loaded)
                try:
                    connection.execute("CREATE UNIQUE INDEX registration_source ON registrations(source_record_id)")
                except sqlite3.IntegrityError as error:
                    raise PluginError("duplicate source_record_id across input partitions",
                                      code="STANDARDIZE_DUPLICATE_SOURCE_ID") from error
                connection.execute("CREATE INDEX registration_parent ON registrations(parent_id)")
                conflict = connection.execute("""
                    SELECT parent_id FROM registrations WHERE parent_id IS NOT NULL
                    GROUP BY parent_id HAVING COUNT(DISTINCT identity_policy_id) > 1
                        OR COUNT(DISTINCT parent_smiles) > 1 OR COUNT(DISTINCT registration_key) > 1
                        OR COUNT(DISTINCT COALESCE(stereo_key, '')) > 1
                        OR COUNT(DISTINCT COALESCE(formula, '')) > 1 LIMIT 1
                """).fetchone()
                if conflict:
                    raise PluginError("parent ID collision produced inconsistent canonical parent values",
                                      code="STANDARDIZE_PARENT_ID_COLLISION")
                connection.executescript("""
                    CREATE TABLE sources AS SELECT source_record_id, raw_format FROM registrations;
                    CREATE UNIQUE INDEX sources_id ON sources(source_record_id);
                    CREATE TABLE parents AS
                        SELECT parent_id, identity_policy_id, parent_smiles, registration_key,
                               stereo_key, formula, COUNT(*) AS duplicate_count
                        FROM registrations WHERE parent_id IS NOT NULL GROUP BY parent_id;
                    CREATE UNIQUE INDEX parents_id ON parents(parent_id);
                    CREATE INDEX parents_registration ON parents(registration_key);
                    CREATE TABLE mappings AS SELECT source_record_id, parent_id, 'PENDING' AS relation
                        FROM registrations WHERE parent_id IS NOT NULL;
                    CREATE UNIQUE INDEX mappings_source ON mappings(source_record_id);
                    CREATE INDEX mappings_parent_id ON mappings(parent_id, source_record_id);
                    DROP TABLE registrations;
                """)
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

            connection.execute("CREATE UNIQUE INDEX decisions_key ON decisions(entity_id, entity_kind, stage_id, reason_code)")
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
            shutil.rmtree(mapped_root)
            write_progress(checkpoint, phase="ready_to_publish", effective_workers=0)
            implementation = rdkit_identity_metadata(config.identity_policy)
            response_metadata = {
                **result.response_metadata(),
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
