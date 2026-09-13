"""Docking scores read at a size the molecule did not earn.

A Vina-family score is an extensive quantity: it accumulates roughly linearly
in heavy atoms, so the strongest score in a virtual library is very often just
the largest molecule the generator emitted.  On this project's own STK17B run
the score fell 0.043 kcal/mol per heavy atom across the 4,740 molecules that
passed the docking window (r = -0.31), which is enough for a size-blind
threshold to select for mass rather than for fit.

This plugin does not decide anything about that.  It computes three ways of
reading the same score against the same molecule's size and writes all three as
``derived_metric/v1`` evidence, because a number that was computed and then
discarded cannot be audited:

``ligand_efficiency``
    ``-score / heavy_atoms`` -- Hopkins & Groom's LE (10.1016/S1359-6446(04)
    03069-7), whose LE >= 0.3 rule of thumb is the field's default reading.
    Note what the arithmetic assumes: dividing by size corrects as if score were
    strictly proportional to heavy atoms, an implied -0.34 kcal/mol per heavy
    atom at HAC 26.  The measured slope on this project's population is about
    -0.043, so LE over-corrects by roughly sevenfold and systematically favours
    small, compact molecules.
``score_per_hac_pow``
    ``-score / heavy_atoms ** n``, ``n = 2`` by default following REvoLd's
    choice, a weaker correction than LE for the same reason.
``score_baseline_residual``
    ``(intercept + slope * heavy_atoms) - score`` -- how much better the score
    is than a molecule of that size typically scores here.  This is the one that
    does not systematically prefer either end of the size range, because its
    coefficients are the population's own regression rather than an assumed
    proportionality.  The defaults are measured on this project's run, not taken
    from the literature, and they should be re-fit for a different receptor or
    scoring function.

Only kcal/mol scales are accepted.  Dividing a KarmaDock MDN score by heavy
atoms produces a real number, but it is not ligand efficiency and there is no
published threshold for it, so an unsupported scale stops the stage instead.
That check also happens to be the fastest way to notice that a downstream tier
was bound to the wrong docking stage.
"""

from __future__ import annotations

import json
import math
import sqlite3
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, JsonValue, ValidationError, field_validator

from molcascade.chemistry.datasets import (
    iter_contract_batches,
    open_stage_database,
    write_query_parquet,
)
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DERIVED_METRIC_V1, DOCKING_SCORE_V1, PARENT_V1
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

_PARENT_PATH = Path("datasets/parents/part-00000.parquet")
_METRIC_PATH = Path("datasets/derived_metrics/part-00000.parquet")
_IMPLEMENTATION_VERSION = 1

#: Only extensive scores on an interaction-energy scale can be divided by size.
#: The three excluded members of ``docking_score/v1``'s vocabulary are a CNN
#: pose score, a CNN affinity and KarmaDock's mixture-density score, none of
#: which is in kcal/mol.
_SUPPORTED_SCORE_KINDS = ("VINA_KCAL_MOL", "VINARDO_KCAL_MOL", "AD4_KCAL_MOL")


class NormalizedScoreKind(StrEnum):
    """The subset of ``docking_score/v1.score_kind`` that has kcal/mol units."""

    VINA_KCAL_MOL = "VINA_KCAL_MOL"
    VINARDO_KCAL_MOL = "VINARDO_KCAL_MOL"
    AD4_KCAL_MOL = "AD4_KCAL_MOL"


class NormalizedDockingScoreConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    #: Which engine's scores to normalize.  Left unset it resolves to the one
    #: engine present in the bound evidence, which is the common case because a
    #: docking stage writes only its own rows.
    engine_id: str | None = Field(default=None, min_length=1, max_length=256)
    receptor_id: str | None = Field(default=None, min_length=1, max_length=256)
    #: Declared rather than inferred, so that switching an engine from Vina to
    #: Vinardo -- two kcal/mol scales that shift the same molecule's LE -- stops
    #: the run rather than quietly moving every threshold.
    expected_score_kind: NormalizedScoreKind = NormalizedScoreKind.VINA_KCAL_MOL
    hac_exponent: float = Field(default=2.0, ge=1.0, le=4.0)
    #: kcal/mol per heavy atom, and kcal/mol, of the population's own
    #: score-versus-size regression.  Measured on 4,740 gate-passing molecules
    #: of this project's STK17B run (r = -0.310); not a literature constant.
    baseline_slope: float = Field(default=-0.0427, ge=-2.0, le=0.0)
    baseline_intercept: float = Field(default=-7.66, ge=-100.0, le=100.0)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)

    @field_validator("expected_score_kind", mode="before")
    @classmethod
    def _parse_score_kind(cls, value: object) -> object:
        # Strict validation wants the member itself, and a config that arrived
        # as JSON only has the string.
        if isinstance(value, str):
            try:
                return NormalizedScoreKind(value)
            except ValueError as error:
                raise ValueError(
                    "size normalization is defined only for kcal/mol docking scales "
                    f"({', '.join(_SUPPORTED_SCORE_KINDS)}); "
                    f"{value!r} has no interaction-energy units"
                ) from error
        return value


class _Metric:
    """One derived quantity: its identity, its units, and what it depends on."""

    __slots__ = ("direction", "metric_id", "parameters", "units")

    def __init__(
        self,
        metric_id: str,
        *,
        units: str,
        direction: str,
        parameters: dict[str, Any],
    ) -> None:
        self.metric_id = metric_id
        self.units = units
        self.direction = direction
        self.parameters = parameters


def _metrics(config: NormalizedDockingScoreConfig) -> tuple[_Metric, ...]:
    """Declare the three metrics, each carrying only its own parameters.

    Keeping the parameter sets disjoint is what makes the ``method_id`` digests
    useful: changing ``hac_exponent`` renames one metric's method and leaves the
    other two byte-identical, so a trace comparing two runs shows which number
    actually moved.
    """

    return (
        _Metric(
            "ligand_efficiency",
            units="KCAL_PER_MOL_PER_HEAVY_ATOM",
            direction="HIGHER_BETTER",
            parameters={},
        ),
        _Metric(
            "score_per_hac_pow",
            units="KCAL_PER_MOL_PER_HEAVY_ATOM_POW",
            direction="HIGHER_BETTER",
            parameters={"hac_exponent": config.hac_exponent},
        ),
        _Metric(
            "score_baseline_residual",
            units="KCAL_PER_MOL",
            direction="HIGHER_BETTER",
            parameters={
                "baseline_slope": config.baseline_slope,
                "baseline_intercept": config.baseline_intercept,
            },
        ),
    )


def _method_id(
    metric: _Metric,
    *,
    config: NormalizedDockingScoreConfig,
    engine_id: str,
    receptor_id: str,
) -> str:
    return "normalized-score:sha256:" + canonical_sha256(
        {
            "implementation_version": _IMPLEMENTATION_VERSION,
            "metric_id": metric.metric_id,
            "units": metric.units,
            "direction": metric.direction,
            "engine_id": engine_id,
            "receptor_id": receptor_id,
            "score_kind": config.expected_score_kind.value,
            "pose_policy": "BEST_POSE",
            "heavy_atom_source": "parent_smiles",
            **metric.parameters,
        }
    )


def _validated_config(request: StageRequest) -> NormalizedDockingScoreConfig:
    try:
        return NormalizedDockingScoreConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid normalized docking-score configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _classify_inputs(inputs: dict[str, StageInput]) -> tuple[StageInput, StageInput]:
    allowed = {PARENT_V1.id, DOCKING_SCORE_V1.id}
    unsupported: list[JsonValue] = [
        port for port in sorted(inputs) if inputs[port].contract_id not in allowed
    ]
    if unsupported:
        raise PluginError(
            "normalized docking score received unsupported input contracts",
            code="NORMALIZED_SCORE_INPUT_CONTRACT_INVALID",
            context={"request_ports": unsupported},
        )
    parents = [value for value in inputs.values() if value.contract_id == PARENT_V1.id]
    evidence = [value for value in inputs.values() if value.contract_id == DOCKING_SCORE_V1.id]
    if len(parents) != 1 or len(evidence) != 1 or len(inputs) != 2:
        raise PluginError(
            "normalized docking score requires exactly one parent and one docking input",
            code="NORMALIZED_SCORE_INPUT_CARDINALITY_INVALID",
            context={
                "parent_input_count": len(parents),
                "evidence_input_count": len(evidence),
                "request_input_count": len(inputs),
            },
        )
    return parents[0], evidence[0]


def _prepare_staging(context: StageContext) -> Path:
    context.staging_root.mkdir(parents=True, exist_ok=True)
    database_path = context.staging_root / ".normalized-docking-score.sqlite3"
    paths = (
        database_path,
        context.staging_root / _PARENT_PATH,
        context.staging_root / _METRIC_PATH,
    )
    existing = next((path for path in paths if path.exists() or path.is_symlink()), None)
    if existing is not None:
        raise PluginError(
            "normalized docking-score staging path already exists",
            code="PLUGIN_STAGING_NOT_EMPTY",
            context={"path": str(existing.relative_to(context.staging_root))},
        )
    return database_path


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE parents (
            parent_id TEXT PRIMARY KEY NOT NULL,
            identity_policy_id TEXT NOT NULL,
            parent_smiles TEXT NOT NULL,
            registration_key TEXT NOT NULL,
            stereo_key TEXT,
            formula TEXT,
            duplicate_count INTEGER NOT NULL,
            input_rank INTEGER UNIQUE NOT NULL
        );
        CREATE TABLE docking_evidence (
            parent_id TEXT NOT NULL,
            engine_id TEXT NOT NULL,
            receptor_id TEXT NOT NULL,
            pose_rank INTEGER NOT NULL,
            score REAL NOT NULL,
            score_kind TEXT NOT NULL,
            direction TEXT NOT NULL,
            PRIMARY KEY (parent_id, engine_id, receptor_id, pose_rank)
        ) WITHOUT ROWID;
        CREATE TABLE derived_metrics (
            parent_id TEXT NOT NULL,
            metric_id TEXT NOT NULL,
            method_id TEXT NOT NULL,
            value REAL,
            units TEXT NOT NULL,
            direction TEXT NOT NULL,
            status TEXT NOT NULL,
            status_detail TEXT,
            source_json TEXT,
            input_rank INTEGER NOT NULL,
            metric_rank INTEGER NOT NULL,
            PRIMARY KEY (parent_id, metric_id, method_id)
        ) WITHOUT ROWID;
        """
    )


def _insert_parents(
    connection: sqlite3.Connection,
    stage_input: StageInput,
    *,
    batch_size: int,
) -> int:
    count = 0
    for batch in iter_contract_batches(stage_input, PARENT_V1, batch_size=batch_size):
        for row in batch.to_pylist():
            parent_id = str(row["parent_id"])
            try:
                connection.execute(
                    """
                    INSERT INTO parents (
                        parent_id, identity_policy_id, parent_smiles, registration_key,
                        stereo_key, formula, duplicate_count, input_rank
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        parent_id,
                        row["identity_policy_id"],
                        row["parent_smiles"],
                        row["registration_key"],
                        row["stereo_key"],
                        row["formula"],
                        row["duplicate_count"],
                        count,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise PluginError(
                    "normalized docking score received a duplicate parent",
                    code="NORMALIZED_SCORE_DUPLICATE_PARENT",
                    context={"parent_id": parent_id},
                ) from error
            count += 1
    if count == 0:
        raise PluginError(
            "normalized docking-score parent input contains no rows",
            code="NORMALIZED_SCORE_EMPTY_PARENT_INPUT",
        )
    return count


def _insert_docking_evidence(
    connection: sqlite3.Connection,
    stage_input: StageInput,
    *,
    batch_size: int,
) -> int:
    """Load every pose so the best one is chosen by score rather than by rank.

    Same reasoning as the docking gate: the pose that decides a molecule is the
    strongest one, and a normalized score computed from a different pose than
    the one the gate accepted would describe a molecule the run never kept.
    ``pose_molblock`` is not loaded -- nothing here reads geometry.
    """

    count = 0
    for batch in iter_contract_batches(
        stage_input,
        DOCKING_SCORE_V1,
        batch_size=batch_size,
    ):
        for row in batch.to_pylist():
            parent_id = str(row["parent_id"])
            score = float(row["score"])
            if not math.isfinite(score):
                raise PluginError(
                    "docking evidence contains a non-finite score",
                    code="NORMALIZED_SCORE_NON_FINITE_EVIDENCE",
                    context={"parent_id": parent_id},
                )
            pose_rank = int(row["pose_rank"])
            if pose_rank < 0:
                raise PluginError(
                    "docking evidence contains a negative pose rank",
                    code="NORMALIZED_SCORE_POSE_RANK_INVALID",
                    context={"parent_id": parent_id, "pose_rank": pose_rank},
                )
            try:
                connection.execute(
                    "INSERT INTO docking_evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        parent_id,
                        str(row["engine_id"]),
                        str(row["receptor_id"]),
                        pose_rank,
                        score,
                        str(row["score_kind"]),
                        str(row["direction"]),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise PluginError(
                    "docking evidence contains duplicate rows for one pose",
                    code="NORMALIZED_SCORE_DUPLICATE_EVIDENCE",
                    context={"parent_id": parent_id, "pose_rank": pose_rank},
                ) from error
            count += 1
    if count == 0:
        raise PluginError(
            "normalized docking-score evidence input contains no rows",
            code="NORMALIZED_SCORE_EMPTY_EVIDENCE_INPUT",
            hint=(
                "Check that the criterion is bound to a docking stage that scored this population."
            ),
        )
    return count


def _resolve_identity(
    connection: sqlite3.Connection,
    *,
    configured: str | None,
    column: str,
) -> str:
    if configured is not None:
        return configured
    if column not in {"engine_id", "receptor_id"}:
        raise ValueError("unknown identity column")
    observed = connection.execute(
        f"SELECT DISTINCT {column} FROM docking_evidence ORDER BY {column}"
    ).fetchmany(2)
    if len(observed) != 1:
        raise PluginError(
            f"normalized docking score requires exactly one observed {column} "
            "when none is configured",
            code="NORMALIZED_SCORE_IDENTITY_AMBIGUOUS",
            context={
                "identity": column,
                "observed_count": len(observed),
                "observed_examples": [str(row[0]) for row in observed],
            },
        )
    return str(observed[0][0])


def _require_supported_scale(
    connection: sqlite3.Connection,
    *,
    config: NormalizedDockingScoreConfig,
    engine_id: str,
    receptor_id: str,
) -> None:
    """Refuse to divide anything that is not an interaction energy.

    The mismatch this catches most often is not a mis-set option: it is a
    criterion bound to a different docking stage than the operator meant, which
    is why the hint names the field that pins the binding.  A run that got here
    with KarmaDock's mixture-density score would otherwise report a plausible
    number with no defined meaning.
    """

    observed = connection.execute(
        """
        SELECT DISTINCT score_kind, direction
        FROM docking_evidence
        WHERE engine_id = ? AND receptor_id = ?
        ORDER BY score_kind, direction
        """,
        (engine_id, receptor_id),
    ).fetchmany(4)
    unsupported: list[JsonValue] = [
        str(row[0]) for row in observed if str(row[0]) not in _SUPPORTED_SCORE_KINDS
    ]
    if unsupported:
        raise PluginError(
            "docking evidence is not on a kcal/mol scale, so it cannot be normalized by size",
            code="NORMALIZED_SCORE_SCALE_UNSUPPORTED",
            hint=(
                "Bind this criterion to a kcal/mol docking stage with the "
                "criterion's evidence_from field, or drop the criterion."
            ),
            context={
                "engine_id": engine_id,
                "receptor_id": receptor_id,
                "observed_score_kinds": unsupported,
                "supported_score_kinds": list(_SUPPORTED_SCORE_KINDS),
            },
        )
    mismatched: list[JsonValue] = [
        str(row[0]) for row in observed if str(row[0]) != config.expected_score_kind.value
    ]
    if mismatched:
        raise PluginError(
            "docking evidence is on a different kcal/mol scale than the configured one",
            code="NORMALIZED_SCORE_SEMANTICS_MISMATCH",
            hint="Set expected_score_kind to the scale the bound docking stage writes.",
            context={
                "engine_id": engine_id,
                "expected_score_kind": config.expected_score_kind.value,
                "observed_score_kinds": mismatched,
            },
        )
    inverted: list[JsonValue] = [str(row[1]) for row in observed if str(row[1]) != "LOWER_STRONGER"]
    if inverted:
        raise PluginError(
            "docking evidence on a kcal/mol scale declares that higher scores are stronger",
            code="NORMALIZED_SCORE_DIRECTION_UNSUPPORTED",
            context={
                "engine_id": engine_id,
                "observed_directions": inverted,
            },
        )


def _heavy_atom_count(smiles: str, *, parent_id: str) -> int:
    from rdkit import Chem

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise PluginError(
            "registered parent cannot be parsed for a heavy-atom count",
            code="NORMALIZED_SCORE_PARENT_INVALID",
            context={"parent_id": parent_id},
        )
    count = int(molecule.GetNumHeavyAtoms())
    if count < 1:
        raise PluginError(
            "registered parent has no heavy atoms to normalize by",
            code="NORMALIZED_SCORE_PARENT_INVALID",
            context={"parent_id": parent_id, "heavy_atom_count": count},
        )
    return count


def _source_json(
    metric: _Metric,
    *,
    score: float,
    heavy_atom_count: int,
) -> str:
    """Record the two inputs, so any derived number can be recomputed by hand.

    The engine, receptor, scale and pose policy are constant for the whole stage
    and live in its metadata instead of being repeated on every row.
    """

    return json.dumps(
        {"score": score, "heavy_atom_count": heavy_atom_count, **metric.parameters},
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _value_for(
    metric_id: str,
    *,
    score: float,
    heavy_atom_count: int,
    config: NormalizedDockingScoreConfig,
) -> float:
    if metric_id == "ligand_efficiency":
        return -score / heavy_atom_count
    if metric_id == "score_per_hac_pow":
        return -score / math.pow(heavy_atom_count, config.hac_exponent)
    if metric_id == "score_baseline_residual":
        expected = config.baseline_intercept + config.baseline_slope * heavy_atom_count
        return expected - score
    raise ValueError(f"unknown metric {metric_id!r}")


def _compute_metrics(
    connection: sqlite3.Connection,
    *,
    config: NormalizedDockingScoreConfig,
    engine_id: str,
    receptor_id: str,
) -> tuple[int, dict[str, str]]:
    """Write three rows per parent and report how many had a score to read."""

    metrics = _metrics(config)
    method_ids = {
        metric.metric_id: _method_id(
            metric,
            config=config,
            engine_id=engine_id,
            receptor_id=receptor_id,
        )
        for metric in metrics
    }
    scored_count = 0
    cursor = connection.execute(
        # The bare pose_rank belongs to the row MIN() selected, which SQLite
        # defines for a query with exactly one aggregate of this kind.
        """
        SELECT p.parent_id, p.parent_smiles, p.input_rank, b.score
        FROM parents p
        LEFT JOIN (
            SELECT parent_id, MIN(score) AS score, pose_rank
            FROM docking_evidence
            WHERE engine_id = ? AND receptor_id = ?
            GROUP BY parent_id
        ) b ON b.parent_id = p.parent_id
        ORDER BY p.input_rank
        """,
        (engine_id, receptor_id),
    )
    while rows := cursor.fetchmany(config.batch_size):
        for parent_id, smiles, input_rank, raw_score in rows:
            score = None if raw_score is None else float(raw_score)
            heavy_atom_count = (
                None if score is None else _heavy_atom_count(str(smiles), parent_id=str(parent_id))
            )
            scored_count += int(score is not None)
            for metric_rank, metric in enumerate(metrics):
                if score is None or heavy_atom_count is None:
                    value: float | None = None
                    status = "NOT_APPLICABLE"
                    detail: str | None = (
                        f"no {engine_id} score against {receptor_id} for this parent"
                    )
                    source: str | None = None
                else:
                    value = _value_for(
                        metric.metric_id,
                        score=score,
                        heavy_atom_count=heavy_atom_count,
                        config=config,
                    )
                    if not math.isfinite(value):
                        raise PluginError(
                            "normalized docking score is non-finite",
                            code="NORMALIZED_SCORE_NON_FINITE",
                            context={
                                "parent_id": str(parent_id),
                                "metric_id": metric.metric_id,
                            },
                        )
                    status = "OK"
                    detail = None
                    source = _source_json(
                        metric,
                        score=score,
                        heavy_atom_count=heavy_atom_count,
                    )
                connection.execute(
                    "INSERT INTO derived_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(parent_id),
                        metric.metric_id,
                        method_ids[metric.metric_id],
                        value,
                        metric.units,
                        metric.direction,
                        status,
                        detail,
                        source,
                        int(input_rank),
                        metric_rank,
                    ),
                )
    return scored_count, method_ids


def _write_outputs(
    connection: sqlite3.Connection,
    context: StageContext,
    *,
    batch_size: int,
) -> None:
    write_query_parquet(
        connection,
        """
        SELECT parent_id, identity_policy_id, parent_smiles, registration_key,
               stereo_key, formula, duplicate_count
        FROM parents ORDER BY input_rank
        """,
        schema=PARENT_V1.schema,
        destination=context.staging_root / _PARENT_PATH,
        batch_size=batch_size,
    )
    write_query_parquet(
        connection,
        """
        SELECT parent_id, metric_id, method_id, value, units, direction,
               status, status_detail, source_json
        FROM derived_metrics ORDER BY input_rank, metric_rank
        """,
        schema=DERIVED_METRIC_V1.schema,
        destination=context.staging_root / _METRIC_PATH,
        batch_size=batch_size,
    )


def _remove_work_files(context: StageContext, database_path: Path) -> None:
    (context.staging_root / _PARENT_PATH).unlink(missing_ok=True)
    (context.staging_root / _METRIC_PATH).unlink(missing_ok=True)
    database_path.unlink(missing_ok=True)
    for suffix in ("-journal", "-wal", "-shm"):
        Path(f"{database_path}{suffix}").unlink(missing_ok=True)


class NormalizedDockingScorePlugin:
    """Express one engine's best score per unit of molecular size, three ways."""

    descriptor = PluginDescriptor(
        id="derived.normalized_docking_score",
        version="0.1.0",
        kind=PluginKind.FEATURIZER,
        inputs=(PARENT_V1.id, DOCKING_SCORE_V1.id),
        outputs=(PARENT_V1.id, DERIVED_METRIC_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "derived_metrics": DERIVED_METRIC_V1.id,
        },
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="Size-normalized docking score",
        description=(
            "Ligand efficiency, score/HAC^n and a size-baseline residual over one "
            "engine's best pose; evidence only, thresholds are a separate gate."
        ),
    )
    config_model = NormalizedDockingScoreConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        parent_input, evidence_input = _classify_inputs(dict(request.inputs))
        database_path = _prepare_staging(context)
        connection: sqlite3.Connection | None = None
        try:
            connection = open_stage_database(database_path)
            _create_schema(connection)
            input_count = _insert_parents(
                connection,
                parent_input,
                batch_size=config.batch_size,
            )
            evidence_count = _insert_docking_evidence(
                connection,
                evidence_input,
                batch_size=config.batch_size,
            )
            engine_id = _resolve_identity(
                connection,
                configured=config.engine_id,
                column="engine_id",
            )
            receptor_id = _resolve_identity(
                connection,
                configured=config.receptor_id,
                column="receptor_id",
            )
            _require_supported_scale(
                connection,
                config=config,
                engine_id=engine_id,
                receptor_id=receptor_id,
            )
            scored_count, method_ids = _compute_metrics(
                connection,
                config=config,
                engine_id=engine_id,
                receptor_id=receptor_id,
            )
            if scored_count == 0:
                raise PluginError(
                    "no parent in this population has a score from the selected engine",
                    code="NORMALIZED_SCORE_NO_MATCHING_EVIDENCE",
                    hint=(
                        "Pin the criterion's evidence_from to the docking stage that "
                        "scored this population, or clear engine_id and receptor_id."
                    ),
                    context={
                        "engine_id": engine_id,
                        "receptor_id": receptor_id,
                        "parent_count": input_count,
                        "evidence_count": evidence_count,
                    },
                )
            _write_outputs(connection, context, batch_size=config.batch_size)
        except BaseException:
            if connection is not None:
                connection.close()
                connection = None
            _remove_work_files(context, database_path)
            raise
        finally:
            if connection is not None:
                connection.close()
            database_path.unlink(missing_ok=True)
            for suffix in ("-journal", "-wal", "-shm"):
                Path(f"{database_path}{suffix}").unlink(missing_ok=True)

        metric_count = input_count * len(_metrics(config))
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    (_PARENT_PATH.as_posix(),),
                    {"row_count": input_count},
                ),
                "derived_metrics": PendingOutput(
                    DERIVED_METRIC_V1.id,
                    (_METRIC_PATH.as_posix(),),
                    {"row_count": metric_count},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": input_count,
                "evidence_count": evidence_count,
                "scored_count": scored_count,
                "not_applicable_count": input_count - scored_count,
                "engine_id": engine_id,
                "receptor_id": receptor_id,
                "score_kind": config.expected_score_kind.value,
                "pose_policy": "BEST_POSE",
                "heavy_atom_source": "parent_smiles",
                "method_ids": dict(sorted(method_ids.items())),
                "derived_not_measured": True,
            },
        )


__all__ = [
    "NormalizedDockingScoreConfig",
    "NormalizedDockingScorePlugin",
    "NormalizedScoreKind",
]
