"""Local scaffold, clustering, and budget-selection plugins.

The implementations in this module deliberately favour bounded, inspectable
algorithms over opaque all-pairs operations.  Global bookkeeping is kept in a
stage-local SQLite database; only cluster representatives are retained in
Python memory by the streaming leader implementation.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
from pydantic import Field, ValidationError

from molcascade.chemistry.datasets import (
    iter_contract_batches,
    open_stage_database,
    require_single_input,
    write_query_parquet,
)
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import (
    CLUSTER_ASSIGNMENT_V1,
    PARENT_V1,
    SCAFFOLD_ASSIGNMENT_V1,
    SELECTION_DECISION_V1,
)
from molcascade.errors import PluginError
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_IMPLEMENTATION_VERSION = 1
_PARENT_PATH = Path("datasets/parents/part-00000.parquet")
_SCAFFOLD_PATH = Path("datasets/scaffolds/part-00000.parquet")
_CLUSTER_PATH = Path("datasets/clusters/part-00000.parquet")
_SELECTION_PATH = Path("datasets/selection/part-00000.parquet")


class ScaffoldConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    include_chirality: bool = False


class LeaderClusterConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=8_192, ge=1, le=100_000)
    similarity_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    fingerprint_bits: int = Field(default=2_048, ge=128, le=65_536)
    fingerprint_radius: int = Field(default=2, ge=1, le=6)
    include_chirality: bool = True
    max_clusters: int = Field(default=100_000, ge=1, le=2_000_000)


class ScaffoldClusterConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=8_192, ge=1, le=100_000)
    fingerprint_bits: int = Field(default=2_048, ge=128, le=65_536)
    fingerprint_radius: int = Field(default=2, ge=1, le=6)
    include_chirality: bool = False
    max_clusters: int = Field(default=250_000, ge=1, le=2_000_000)


class HashBudgetConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    # Lowering always writes the cascade's own target over this, so it is only
    # reached by a hand-written flat pipeline that omits the setting.  Matching
    # the shipped cascade keeps that path from silently producing a third of
    # the shortlist the rest of the project is sized for.
    target_count: int = Field(default=45_000, ge=1, le=10_000_000)
    seed: int = Field(default=20_260_823, ge=0, le=2**63 - 1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)


class ScaffoldRoundRobinConfig(HashBudgetConfig):
    max_per_scaffold: int = Field(default=25, ge=1, le=1_000_000)
    include_chirality: bool = False


def _config(model: type[StrictFrozenModel], request: StageRequest) -> Any:
    try:
        return model.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid diversity configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _prepare(context: StageContext, *paths: Path) -> None:
    context.staging_root.mkdir(parents=True, exist_ok=True)
    for relative in paths:
        destination = context.staging_root / relative
        if destination.exists() or destination.is_symlink():
            raise PluginError(
                f"diversity output already exists in staging: {relative.as_posix()}",
                code="PLUGIN_STAGING_NOT_EMPTY",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)


def _parent_columns_sql() -> str:
    return """
        parent_id TEXT PRIMARY KEY NOT NULL,
        identity_policy_id TEXT NOT NULL,
        parent_smiles TEXT NOT NULL,
        registration_key TEXT NOT NULL,
        stereo_key TEXT,
        formula TEXT,
        duplicate_count INTEGER NOT NULL,
        input_rank INTEGER NOT NULL UNIQUE
    """


def _insert_parent(connection: sqlite3.Connection, row: dict[str, Any], rank: int) -> None:
    try:
        connection.execute(
            """
            INSERT INTO parents (
                parent_id, identity_policy_id, parent_smiles, registration_key,
                stereo_key, formula, duplicate_count, input_rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("parent_id"),
                row.get("identity_policy_id"),
                row.get("parent_smiles"),
                row.get("registration_key"),
                row.get("stereo_key"),
                row.get("formula"),
                row.get("duplicate_count"),
                rank,
            ),
        )
    except sqlite3.IntegrityError as error:
        raise PluginError(
            "parent input contains a duplicate or invalid parent_id",
            code="DIVERSITY_PARENT_ID_INVALID",
            context={"parent_id": str(row.get("parent_id"))},
        ) from error


def _parent_query(*, selected_only: bool = False) -> str:
    join = "JOIN chosen USING (parent_id)" if selected_only else ""
    return f"""
        SELECT parent_id, identity_policy_id, parent_smiles, registration_key,
               stereo_key, formula, duplicate_count
        FROM parents {join}
        ORDER BY parents.input_rank
    """


def _write_parents(
    connection: sqlite3.Connection,
    context: StageContext,
    *,
    batch_size: int,
    selected_only: bool = False,
) -> None:
    write_query_parquet(
        connection,
        _parent_query(selected_only=selected_only),
        schema=PARENT_V1.schema,
        destination=context.staging_root / _PARENT_PATH,
        batch_size=batch_size,
    )


def _molecule(smiles: Any, parent_id: Any) -> Any:
    from rdkit import Chem

    molecule = Chem.MolFromSmiles(smiles if isinstance(smiles, str) else "")
    if molecule is None:
        raise PluginError(
            "registered parent cannot be parsed for diversity analysis",
            code="DIVERSITY_PARENT_INVALID",
            context={"parent_id": str(parent_id)},
        )
    return molecule


def _scaffold_values(molecule: Any, *, include_chirality: bool) -> tuple[str, str, str, str, bool]:
    from rdkit import Chem
    from rdkit.Chem.Scaffolds import MurckoScaffold

    scaffold = MurckoScaffold.GetScaffoldForMol(molecule)
    if scaffold.GetNumAtoms() == 0:
        return "ACYCLIC", "", "ACYCLIC", "", True
    exact = Chem.MolToSmiles(scaffold, canonical=True, isomericSmiles=include_chirality)
    generic_molecule = MurckoScaffold.MakeScaffoldGeneric(scaffold)
    generic = Chem.MolToSmiles(
        generic_molecule,
        canonical=True,
        isomericSmiles=False,
    )
    exact_id = "scaffold:sha256:" + hashlib.sha256(exact.encode("utf-8")).hexdigest()
    generic_id = "scaffold:sha256:" + hashlib.sha256(generic.encode("utf-8")).hexdigest()
    return exact_id, exact, generic_id, generic, False


def _fingerprint_generator(config: Any) -> tuple[Any, str]:
    from rdkit import rdBase
    from rdkit.Chem import rdFingerprintGenerator

    generator = rdFingerprintGenerator.GetMorganGenerator(
        radius=config.fingerprint_radius,
        fpSize=config.fingerprint_bits,
        includeChirality=config.include_chirality,
    )
    spec_id = "fingerprint:sha256:" + canonical_sha256(
        {
            "backend": "rdkit",
            "backend_version": rdBase.rdkitVersion,
            "kind": "morgan",
            "radius": config.fingerprint_radius,
            "bit_length": config.fingerprint_bits,
            "include_chirality": config.include_chirality,
        }
    )
    return generator, spec_id


class RDKitMurckoScaffoldPlugin:
    descriptor = PluginDescriptor(
        id="scaffold.rdkit_murcko",
        version="0.1.0",
        kind=PluginKind.SCAFFOLDER,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, SCAFFOLD_ASSIGNMENT_V1.id),
        output_ports={"primary": PARENT_V1.id, "scaffolds": SCAFFOLD_ASSIGNMENT_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit Murcko scaffold",
        description="Exact and generic Murcko assignments with an explicit acyclic channel.",
    )
    config_model = ScaffoldConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        config = _config(self.config_model, request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        _prepare(context, _PARENT_PATH, _SCAFFOLD_PATH)
        definition_id = "scaffold-definition:sha256:" + canonical_sha256(
            {
                "backend": "rdkit.murcko",
                "backend_version": rdBase.rdkitVersion,
                "implementation_version": _IMPLEMENTATION_VERSION,
                "include_chirality": config.include_chirality,
            }
        )
        parent_destination = context.staging_root / _PARENT_PATH
        scaffold_destination = context.staging_root / _SCAFFOLD_PATH
        count = 0
        try:
            import pyarrow.parquet as pq

            with (
                pq.ParquetWriter(
                    parent_destination, PARENT_V1.schema, compression="zstd"
                ) as parents,
                pq.ParquetWriter(
                    scaffold_destination,
                    SCAFFOLD_ASSIGNMENT_V1.schema,
                    compression="zstd",
                ) as scaffolds,
            ):
                for batch in iter_contract_batches(
                    stage_input,
                    PARENT_V1,
                    batch_size=config.batch_size,
                ):
                    assignments: list[dict[str, Any]] = []
                    for row in batch.select(["parent_id", "parent_smiles"]).to_pylist():
                        molecule = _molecule(row["parent_smiles"], row["parent_id"])
                        exact_id, exact, generic_id, generic, acyclic = _scaffold_values(
                            molecule,
                            include_chirality=config.include_chirality,
                        )
                        assignments.append(
                            {
                                "parent_id": row["parent_id"],
                                "scaffold_definition_id": definition_id,
                                "exact_scaffold_id": exact_id,
                                "exact_scaffold_smiles": exact or None,
                                "generic_scaffold_id": generic_id,
                                "generic_scaffold_smiles": generic or None,
                                "ring_system_id": None if acyclic else exact_id,
                                "acyclic": acyclic,
                                "toolkit_version": rdBase.rdkitVersion,
                            }
                        )
                    parents.write_batch(batch)
                    scaffolds.write_table(
                        pa.Table.from_pylist(assignments, schema=SCAFFOLD_ASSIGNMENT_V1.schema)
                    )
                    count += batch.num_rows
            if count == 0:
                raise PluginError("scaffold input contains no parents", code="SCAFFOLD_EMPTY_INPUT")
        except BaseException:
            parent_destination.unlink(missing_ok=True)
            scaffold_destination.unlink(missing_ok=True)
            raise
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id, (_PARENT_PATH.as_posix(),), {"row_count": count}
                ),
                "scaffolds": PendingOutput(
                    SCAFFOLD_ASSIGNMENT_V1.id,
                    (_SCAFFOLD_PATH.as_posix(),),
                    {"row_count": count, "scaffold_definition_id": definition_id},
                ),
            },
            metadata={
                "input_count": count,
                "output_count": count,
                "scaffold_definition_id": definition_id,
                "backend": "rdkit.murcko",
                "backend_version": rdBase.rdkitVersion,
            },
        )


def _run_cluster(
    *,
    request: StageRequest,
    context: StageContext,
    config: Any,
    method_name: str,
    assign: Callable[
        [Any, dict[str, Any], list[tuple[str, Any]], dict[str, int]],
        tuple[str, str, bool, float, int],
    ],
) -> StageResponse:
    from rdkit import rdBase

    stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
    _prepare(context, _PARENT_PATH, _CLUSTER_PATH)
    database_path = context.staging_root / ".diversity-cluster.sqlite3"
    connection: sqlite3.Connection | None = None
    representatives: list[tuple[str, Any]] = []
    cluster_counts: dict[str, int] = {}
    order_hash = hashlib.sha256()
    generator, fingerprint_spec_id = _fingerprint_generator(config)
    method_id = "cluster-method:sha256:" + canonical_sha256(
        {
            "method": method_name,
            "implementation_version": _IMPLEMENTATION_VERSION,
            "backend_version": rdBase.rdkitVersion,
            "fingerprint_spec_id": fingerprint_spec_id,
            "threshold": float(getattr(config, "similarity_threshold", 0.0)),
        }
    )
    try:
        connection = open_stage_database(database_path)
        connection.executescript(
            f"""
            CREATE TABLE parents ({_parent_columns_sql()});
            CREATE TABLE assignments (
                parent_id TEXT PRIMARY KEY NOT NULL,
                cluster_id TEXT NOT NULL,
                representative_parent_id TEXT NOT NULL,
                is_representative INTEGER NOT NULL,
                similarity REAL NOT NULL,
                assignment_rank INTEGER NOT NULL
            );
            """
        )
        input_count = 0
        with connection:
            for batch in iter_contract_batches(
                stage_input, PARENT_V1, batch_size=config.batch_size
            ):
                for row in batch.to_pylist():
                    parent_id = row.get("parent_id")
                    if not isinstance(parent_id, str) or not parent_id:
                        raise PluginError("invalid parent_id", code="DIVERSITY_PARENT_ID_INVALID")
                    _insert_parent(connection, row, input_count)
                    encoded = parent_id.encode("utf-8")
                    order_hash.update(len(encoded).to_bytes(8, "big"))
                    order_hash.update(encoded)
                    molecule = _molecule(row.get("parent_smiles"), parent_id)
                    fingerprint = generator.GetFingerprint(molecule)
                    cluster_id, representative_id, is_representative, similarity, rank = assign(
                        fingerprint,
                        row,
                        representatives,
                        cluster_counts,
                    )
                    if len(representatives) > config.max_clusters:
                        raise PluginError(
                            "cluster count exceeded the configured safety limit",
                            code="CLUSTER_LIMIT_EXCEEDED",
                            hint="Raise max_clusters only after reducing the candidate pool.",
                            context={"max_clusters": config.max_clusters},
                        )
                    connection.execute(
                        "INSERT INTO assignments VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            parent_id,
                            cluster_id,
                            representative_id,
                            int(is_representative),
                            similarity,
                            rank,
                        ),
                    )
                    input_count += 1
        if input_count == 0:
            raise PluginError("cluster input contains no parents", code="CLUSTER_EMPTY_INPUT")
        input_order_hash = "sha256:" + order_hash.hexdigest()
        _write_parents(connection, context, batch_size=config.batch_size)
        quoted_method = connection.execute("SELECT quote(?)", (method_id,)).fetchone()[0]
        quoted_fingerprint = connection.execute(
            "SELECT quote(?)", (fingerprint_spec_id,)
        ).fetchone()[0]
        quoted_threshold = connection.execute(
            "SELECT quote(?)",
            (float(getattr(config, "similarity_threshold", 0.0)),),
        ).fetchone()[0]
        quoted_order_hash = connection.execute(
            "SELECT quote(?)", (input_order_hash,)
        ).fetchone()[0]
        write_query_parquet(
            connection,
            f"""
            SELECT a.parent_id, {quoted_method} AS cluster_method_id, a.cluster_id,
                   a.representative_parent_id,
                   CASE a.is_representative WHEN 1 THEN TRUE ELSE FALSE END,
                   a.similarity, {quoted_fingerprint} AS fingerprint_spec_id,
                   {quoted_threshold} AS threshold, a.assignment_rank,
                   {quoted_order_hash} AS input_order_hash
            FROM assignments a JOIN parents p USING (parent_id)
            ORDER BY p.input_rank
            """,
            schema=CLUSTER_ASSIGNMENT_V1.schema,
            destination=context.staging_root / _CLUSTER_PATH,
            batch_size=config.batch_size,
        )
    except BaseException:
        (context.staging_root / _PARENT_PATH).unlink(missing_ok=True)
        (context.staging_root / _CLUSTER_PATH).unlink(missing_ok=True)
        raise
    finally:
        if connection is not None:
            connection.close()
        database_path.unlink(missing_ok=True)
        Path(f"{database_path}-journal").unlink(missing_ok=True)
    return StageResponse(
        outputs={
            "primary": PendingOutput(
                PARENT_V1.id, (_PARENT_PATH.as_posix(),), {"row_count": input_count}
            ),
            "clusters": PendingOutput(
                CLUSTER_ASSIGNMENT_V1.id,
                (_CLUSTER_PATH.as_posix(),),
                {"row_count": input_count, "cluster_method_id": method_id},
            ),
        },
        metadata={
            "input_count": input_count,
            "output_count": input_count,
            "cluster_count": len(representatives),
            "cluster_method_id": method_id,
            "fingerprint_spec_id": fingerprint_spec_id,
            "input_order_hash": input_order_hash,
        },
    )


class NativeStreamingLeaderPlugin:
    descriptor = PluginDescriptor(
        id="cluster.native_streaming_leader",
        version="0.1.0",
        kind=PluginKind.CLUSTERER,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, CLUSTER_ASSIGNMENT_V1.id),
        output_ports={"primary": PARENT_V1.id, "clusters": CLUSTER_ASSIGNMENT_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="Streaming Morgan leader clustering",
        description="O(N×leaders), O(leaders) memory radius clustering; never builds N×N.",
    )
    config_model = LeaderClusterConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import DataStructs

        config = _config(self.config_model, request)

        def assign(
            fingerprint: Any,
            row: dict[str, Any],
            representatives: list[tuple[str, Any]],
            counts: dict[str, int],
        ) -> tuple[str, str, bool, float, int]:
            best_id: str | None = None
            best_similarity = -1.0
            for representative_id, representative_fp in representatives:
                similarity = float(DataStructs.TanimotoSimilarity(fingerprint, representative_fp))
                if similarity > best_similarity:
                    best_id, best_similarity = representative_id, similarity
            parent_id = row["parent_id"]
            is_representative = best_id is None or best_similarity < config.similarity_threshold
            if is_representative:
                representative_id = parent_id
                representatives.append((representative_id, fingerprint))
                best_similarity = 1.0
            else:
                assert best_id is not None
                representative_id = best_id
            cluster_id = "cluster:sha256:" + hashlib.sha256(
                representative_id.encode("utf-8")
            ).hexdigest()
            rank = counts.get(cluster_id, 0)
            counts[cluster_id] = rank + 1
            return cluster_id, representative_id, is_representative, best_similarity, rank

        return _run_cluster(
            request=request,
            context=context,
            config=config,
            method_name="native.streaming_morgan_leader",
            assign=assign,
        )


class RDKitScaffoldGroupClusterPlugin:
    descriptor = PluginDescriptor(
        id="cluster.rdkit_scaffold_groups",
        version="0.1.0",
        kind=PluginKind.CLUSTERER,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, CLUSTER_ASSIGNMENT_V1.id),
        output_ports={"primary": PARENT_V1.id, "clusters": CLUSTER_ASSIGNMENT_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit Murcko scaffold groups",
        description="Linear-time exact Murcko grouping with a separate acyclic group.",
    )
    config_model = ScaffoldClusterConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import DataStructs

        config = _config(self.config_model, request)
        representative_by_scaffold: dict[str, tuple[str, Any]] = {}

        def assign(
            fingerprint: Any,
            row: dict[str, Any],
            representatives: list[tuple[str, Any]],
            counts: dict[str, int],
        ) -> tuple[str, str, bool, float, int]:
            molecule = _molecule(row["parent_smiles"], row["parent_id"])
            exact_id, _, _, _, _ = _scaffold_values(
                molecule,
                include_chirality=config.include_chirality,
            )
            existing = representative_by_scaffold.get(exact_id)
            if existing is None:
                representative_id = row["parent_id"]
                representative_by_scaffold[exact_id] = (representative_id, fingerprint)
                representatives.append((representative_id, fingerprint))
                similarity = 1.0
                is_representative = True
            else:
                representative_id, representative_fp = existing
                similarity = float(DataStructs.TanimotoSimilarity(fingerprint, representative_fp))
                is_representative = False
            cluster_id = "cluster:sha256:" + hashlib.sha256(exact_id.encode("utf-8")).hexdigest()
            rank = counts.get(cluster_id, 0)
            counts[cluster_id] = rank + 1
            return cluster_id, representative_id, is_representative, similarity, rank

        return _run_cluster(
            request=request,
            context=context,
            config=config,
            method_name="rdkit.exact_murcko_groups",
            assign=assign,
        )


def _selection_response(
    *,
    request: StageRequest,
    context: StageContext,
    config: HashBudgetConfig,
    strategy: str,
    scaffold_key: Callable[[dict[str, Any]], str] | None = None,
) -> StageResponse:
    stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
    _prepare(context, _PARENT_PATH, _SELECTION_PATH)
    database_path = context.staging_root / ".selection.sqlite3"
    connection: sqlite3.Connection | None = None
    policy_payload: dict[str, Any] = {
        "strategy": strategy,
        "implementation_version": _IMPLEMENTATION_VERSION,
        "target_count": config.target_count,
        "seed": config.seed,
    }
    if isinstance(config, ScaffoldRoundRobinConfig):
        policy_payload["max_per_scaffold"] = config.max_per_scaffold
        policy_payload["include_chirality"] = config.include_chirality
    quota_policy_id = "quota-policy:sha256:" + canonical_sha256(policy_payload)
    try:
        connection = open_stage_database(database_path)
        connection.executescript(
            f"""
            CREATE TABLE parents ({_parent_columns_sql()}, tie_break_key TEXT NOT NULL,
                                  scaffold_key TEXT NOT NULL);
            CREATE TABLE chosen (
                parent_id TEXT PRIMARY KEY NOT NULL,
                basket_rank INTEGER NOT NULL,
                global_rank INTEGER NOT NULL
            );
            """
        )
        input_count = 0
        with connection:
            for batch in iter_contract_batches(
                stage_input, PARENT_V1, batch_size=config.batch_size
            ):
                for row in batch.to_pylist():
                    parent_id = row.get("parent_id")
                    if not isinstance(parent_id, str) or not parent_id:
                        raise PluginError("invalid parent_id", code="DIVERSITY_PARENT_ID_INVALID")
                    tie = hashlib.sha256(f"{config.seed}\0{parent_id}".encode()).hexdigest()
                    key = scaffold_key(row) if scaffold_key is not None else "ALL"
                    try:
                        connection.execute(
                            """
                            INSERT INTO parents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                parent_id, row.get("identity_policy_id"), row.get("parent_smiles"),
                                row.get("registration_key"),
                                row.get("stereo_key"),
                                row.get("formula"),
                                row.get("duplicate_count"), input_count, tie, key,
                            ),
                        )
                    except sqlite3.IntegrityError as error:
                        raise PluginError(
                            "parent input contains a duplicate or invalid parent_id",
                            code="DIVERSITY_PARENT_ID_INVALID",
                            context={"parent_id": parent_id},
                        ) from error
                    input_count += 1
        if input_count == 0:
            raise PluginError("selection input contains no parents", code="SELECTION_EMPTY_INPUT")
        if isinstance(config, ScaffoldRoundRobinConfig):
            connection.execute(
                """
                INSERT INTO chosen(parent_id, basket_rank, global_rank)
                WITH ranked AS (
                    SELECT parent_id, tie_break_key,
                           ROW_NUMBER() OVER (
                               PARTITION BY scaffold_key ORDER BY tie_break_key, parent_id
                           ) AS within_scaffold
                    FROM parents
                ), selected AS (
                    SELECT parent_id, within_scaffold, tie_break_key,
                           ROW_NUMBER() OVER (
                               ORDER BY within_scaffold, tie_break_key, parent_id
                           ) - 1 AS global_rank
                    FROM ranked
                    WHERE within_scaffold <= ?
                    ORDER BY within_scaffold, tie_break_key, parent_id
                    LIMIT ?
                )
                SELECT parent_id, within_scaffold - 1, global_rank FROM selected
                """,
                (config.max_per_scaffold, config.target_count),
            )
            basket = "SCAFFOLD_DIVERSITY"
            selected_reason = "SELECTED_SCAFFOLD_ROUND_ROBIN"
        else:
            connection.execute(
                """
                INSERT INTO chosen(parent_id, basket_rank, global_rank)
                SELECT parent_id,
                       ROW_NUMBER() OVER (ORDER BY tie_break_key, parent_id) - 1,
                       ROW_NUMBER() OVER (ORDER BY tie_break_key, parent_id) - 1
                FROM parents ORDER BY tie_break_key, parent_id LIMIT ?
                """,
                (config.target_count,),
            )
            basket = "EXPLORATION"
            selected_reason = "SELECTED_HASH_BUDGET"
        connection.commit()
        selected_count = connection.execute("SELECT COUNT(*) FROM chosen").fetchone()[0]
        _write_parents(
            connection,
            context,
            batch_size=config.batch_size,
            selected_only=True,
        )
        quoted_policy = connection.execute("SELECT quote(?)", (quota_policy_id,)).fetchone()[0]
        quoted_basket = connection.execute("SELECT quote(?)", (basket,)).fetchone()[0]
        quoted_reason = connection.execute("SELECT quote(?)", (selected_reason,)).fetchone()[0]
        write_query_parquet(
            connection,
            f"""
            SELECT p.parent_id, {quoted_policy} AS quota_policy_id,
                   CASE WHEN c.parent_id IS NULL THEN FALSE ELSE TRUE END AS selected,
                   CASE WHEN c.parent_id IS NULL THEN NULL ELSE {quoted_basket} END AS basket,
                   c.basket_rank, c.global_rank, NULL AS pareto_front,
                   NULL AS priority_score,
                   CASE WHEN c.parent_id IS NULL THEN 'NOT_SELECTED_BUDGET'
                        ELSE {quoted_reason} END AS reason_code,
                   p.tie_break_key, {config.seed} AS seed
            FROM parents p LEFT JOIN chosen c USING (parent_id)
            ORDER BY p.input_rank
            """,
            schema=SELECTION_DECISION_V1.schema,
            destination=context.staging_root / _SELECTION_PATH,
            batch_size=config.batch_size,
        )
    except BaseException:
        (context.staging_root / _PARENT_PATH).unlink(missing_ok=True)
        (context.staging_root / _SELECTION_PATH).unlink(missing_ok=True)
        raise
    finally:
        if connection is not None:
            connection.close()
        database_path.unlink(missing_ok=True)
        Path(f"{database_path}-journal").unlink(missing_ok=True)
    return StageResponse(
        outputs={
            "primary": PendingOutput(
                PARENT_V1.id, (_PARENT_PATH.as_posix(),), {"row_count": selected_count}
            ),
            "selection": PendingOutput(
                SELECTION_DECISION_V1.id,
                (_SELECTION_PATH.as_posix(),),
                {"row_count": input_count, "quota_policy_id": quota_policy_id},
            ),
        },
        metadata={
            "input_count": input_count,
            "output_count": selected_count,
            "quota_policy_id": quota_policy_id,
            "strategy": strategy,
            "seed": config.seed,
        },
    )


class NativeHashBudgetSelectorPlugin:
    descriptor = PluginDescriptor(
        id="select.native_hash_budget",
        version="0.1.0",
        kind=PluginKind.SELECTOR,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, SELECTION_DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "selection": SELECTION_DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.SEEDED,
        display_name="Deterministic exploration budget",
        description="Disk-backed seeded hash ranking with an auditable decision for every parent.",
    )
    config_model = HashBudgetConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _config(self.config_model, request)
        return _selection_response(
            request=request,
            context=context,
            config=config,
            strategy="native.seeded_hash_budget",
        )


class NativeScaffoldRoundRobinSelectorPlugin:
    descriptor = PluginDescriptor(
        id="select.native_scaffold_round_robin",
        version="0.1.0",
        kind=PluginKind.SELECTOR,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, SELECTION_DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "selection": SELECTION_DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.SEEDED,
        display_name="Scaffold round-robin budget",
        description=(
            "Breadth-first Murcko allocation with a per-scaffold cap and "
            "deterministic ties."
        ),
    )
    config_model = ScaffoldRoundRobinConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _config(self.config_model, request)

        def scaffold_key(row: dict[str, Any]) -> str:
            molecule = _molecule(row.get("parent_smiles"), row.get("parent_id"))
            exact_id, _, _, _, _ = _scaffold_values(
                molecule,
                include_chirality=config.include_chirality,
            )
            return exact_id

        return _selection_response(
            request=request,
            context=context,
            config=config,
            strategy="native.murcko_round_robin",
            scaffold_key=scaffold_key,
        )


__all__ = [
    "HashBudgetConfig",
    "LeaderClusterConfig",
    "NativeHashBudgetSelectorPlugin",
    "NativeScaffoldRoundRobinSelectorPlugin",
    "NativeStreamingLeaderPlugin",
    "RDKitMurckoScaffoldPlugin",
    "RDKitScaffoldGroupClusterPlugin",
    "ScaffoldClusterConfig",
    "ScaffoldConfig",
    "ScaffoldRoundRobinConfig",
]
