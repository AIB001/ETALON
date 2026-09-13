"""Bounded, fail-closed gates over typed numeric evidence datasets.

Prediction and synthesis plugins deliberately emit evidence rather than silently
turning a score into policy.  The plugins here make that policy explicit while
keeping the join disk-backed and producing one auditable decision per parent.
"""

from __future__ import annotations

import json
import math
import sqlite3
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import (
    iter_contract_batches,
    open_stage_database,
    write_query_parquet,
)
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import (
    DECISION_V1,
    DERIVED_METRIC_V1,
    DOCKING_SCORE_V1,
    PARENT_V1,
    PREDICTION_V1,
    SYNTHESIS_SCORE_V1,
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

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")


class SynthesisScoreDirection(StrEnum):
    """Direction values defined by ``synthesis_score/v1``."""

    HIGHER_EASIER = "HIGHER_EASIER"
    HIGHER_HARDER = "HIGHER_HARDER"


class _NumericBoundsConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    minimum: float | None = None
    maximum: float | None = None
    batch_size: int = Field(default=16_384, ge=1, le=250_000)

    @field_validator("minimum", "maximum")
    @classmethod
    def _bounds_are_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("numeric evidence bounds must be finite")
        return value

    @model_validator(mode="after")
    def _bounds_are_explicit_and_ordered(self) -> Self:
        if self.minimum is None and self.maximum is None:
            raise ValueError("at least one of minimum or maximum is required")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("minimum must be no greater than maximum")
        return self


class PredictionEvidenceGateConfig(_NumericBoundsConfig):
    """Bind one endpoint/model prediction to an inclusive numeric window."""

    endpoint_id: str = Field(min_length=1, max_length=256)
    model_id: str | None = Field(default=None, min_length=1, max_length=4096)
    semantics_label: str = Field(min_length=1, max_length=1024)

    @field_validator("endpoint_id", "model_id", "semantics_label")
    @classmethod
    def _labels_are_unambiguous(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip() or any(character in value for character in "\r\n\x00")
        ):
            raise ValueError(
                "identifier and semantics labels must not contain surrounding or "
                "control whitespace"
            )
        return value


class SynthesisScoreEvidenceGateConfig(_NumericBoundsConfig):
    """Bind one synthesis method and its declared direction to a score window."""

    method_id: str | None = Field(default=None, min_length=1, max_length=4096)
    expected_direction: SynthesisScoreDirection

    @field_validator("method_id")
    @classmethod
    def _method_is_unambiguous(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip() or any(character in value for character in "\r\n\x00")
        ):
            raise ValueError("method_id must not contain surrounding or control whitespace")
        return value

    @field_validator("expected_direction", mode="before")
    @classmethod
    def _parse_direction(cls, value: Any) -> Any:
        return SynthesisScoreDirection(value) if isinstance(value, str) else value


class DockingScoreDirection(StrEnum):
    """Direction values defined by ``docking_score/v1``."""

    LOWER_STRONGER = "LOWER_STRONGER"
    HIGHER_STRONGER = "HIGHER_STRONGER"


class DockingScoreKind(StrEnum):
    """Score scales defined by ``docking_score/v1``.

    The scale is part of the threshold, not decoration.  ``-8.5`` is a strong
    Vina score and an impossible CNN score; ``0.9`` is the reverse.  A window
    typed against one scale and applied to another would reject everything or
    accept everything, and it would do so quietly, which is why the gate makes
    the operator name the scale and then refuses to run when the evidence
    disagrees.
    """

    VINA_KCAL_MOL = "VINA_KCAL_MOL"
    VINARDO_KCAL_MOL = "VINARDO_KCAL_MOL"
    AD4_KCAL_MOL = "AD4_KCAL_MOL"
    CNN_SCORE = "CNN_SCORE"
    CNN_AFFINITY = "CNN_AFFINITY"
    KARMADOCK_MDN = "KARMADOCK_MDN"


class DockingScoreEvidenceGateConfig(_NumericBoundsConfig):
    """Bind one engine, one receptor and one score scale to a numeric window."""

    engine_id: str | None = Field(default=None, min_length=1, max_length=256)
    #: The sha256 of the receptor the scores were computed against.  Left unset
    #: it is resolved from the evidence, which is the common case: a cascade
    #: docks one target.  Set, it is a promise that this gate's threshold was
    #: chosen for *these* bytes -- and evidence from a different structure then
    #: fails closed instead of being silently compared against it.
    receptor_id: str | None = Field(default=None, min_length=1, max_length=256)
    expected_score_kind: DockingScoreKind
    expected_direction: DockingScoreDirection

    @field_validator("engine_id", "receptor_id")
    @classmethod
    def _identities_are_unambiguous(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip() or any(character in value for character in "\r\n\x00")
        ):
            raise ValueError(
                "engine_id and receptor_id must not contain surrounding or control whitespace"
            )
        return value

    @field_validator("expected_score_kind", mode="before")
    @classmethod
    def _parse_score_kind(cls, value: Any) -> Any:
        return DockingScoreKind(value) if isinstance(value, str) else value

    @field_validator("expected_direction", mode="before")
    @classmethod
    def _parse_docking_direction(cls, value: Any) -> Any:
        return DockingScoreDirection(value) if isinstance(value, str) else value


class DerivedMetricUnits(StrEnum):
    """Scales defined by ``derived_metric/v1``.

    A derived number without its scale cannot be gated.  Strain measured as an
    MMFF94s energy difference and strain read off a torsion library are both
    small positive reals, and both are called strain, but ``8`` means "keep
    three quarters of the population" on one scale and something else entirely
    on the other.  The operator therefore names the scale the threshold was
    chosen for, and evidence on a different scale fails closed.
    """

    KCAL_PER_MOL = "KCAL_PER_MOL"
    KCAL_PER_MOL_PER_HEAVY_ATOM = "KCAL_PER_MOL_PER_HEAVY_ATOM"
    KCAL_PER_MOL_PER_HEAVY_ATOM_POW = "KCAL_PER_MOL_PER_HEAVY_ATOM_POW"
    TEU = "TEU"
    COUNT = "COUNT"


class DerivedMetricDirection(StrEnum):
    """Direction values defined by ``derived_metric/v1``."""

    HIGHER_BETTER = "HIGHER_BETTER"
    LOWER_BETTER = "LOWER_BETTER"


class DerivedMetricStatus(StrEnum):
    """Status values defined by ``derived_metric/v1``."""

    OK = "OK"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"
    BACKEND_FAILED = "BACKEND_FAILED"


class UnscorableAction(StrEnum):
    """What to do with a molecule the metric declined to score."""

    REJECT = "reject"
    ACCEPT = "accept"


class DerivedMetricEvidenceGateConfig(_NumericBoundsConfig):
    """Bind one derived metric, one method and one scale to a numeric window."""

    metric_id: str = Field(min_length=1, max_length=256)
    #: The digest of the parameters the metric was computed with.  Left unset it
    #: is resolved from the evidence, which is the common case: one featurizer
    #: wrote the column.  Set, it is a promise that this threshold was chosen for
    #: *those* parameters, so a re-run under a different exponent or conformer
    #: seed is refused rather than compared against a window it does not fit.
    method_id: str | None = Field(default=None, min_length=1, max_length=4096)
    expected_units: DerivedMetricUnits
    expected_direction: DerivedMetricDirection
    #: A molecule whose row carries a status other than ``OK`` has no number the
    #: window can be applied to, and the default is to reject it.  That is not
    #: caution for its own sake: on the MMFF94s strain backend roughly a tenth of
    #: molecules have a reference state the conformer search failed to find, and
    #: accepting them would turn the largest systematic failure mode of the
    #: method into the one route through the gate that nothing is checked on.
    on_unscorable: UnscorableAction = UnscorableAction.REJECT

    @field_validator("metric_id", "method_id")
    @classmethod
    def _identities_are_unambiguous(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip() or any(character in value for character in "\r\n\x00")
        ):
            raise ValueError(
                "metric_id and method_id must not contain surrounding or control whitespace"
            )
        return value

    @field_validator("expected_units", mode="before")
    @classmethod
    def _parse_units(cls, value: Any) -> Any:
        return DerivedMetricUnits(value) if isinstance(value, str) else value

    @field_validator("expected_direction", mode="before")
    @classmethod
    def _parse_derived_direction(cls, value: Any) -> Any:
        return DerivedMetricDirection(value) if isinstance(value, str) else value

    @field_validator("on_unscorable", mode="before")
    @classmethod
    def _parse_unscorable_action(cls, value: Any) -> Any:
        return UnscorableAction(value) if isinstance(value, str) else value


_GateConfig = (
    DerivedMetricEvidenceGateConfig
    | DockingScoreEvidenceGateConfig
    | PredictionEvidenceGateConfig
    | SynthesisScoreEvidenceGateConfig
)


def _validated_config(
    request: StageRequest,
    model: type[_GateConfig],
    *,
    label: str,
) -> _GateConfig:
    try:
        return model.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid {label} configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _classify_inputs(
    inputs: dict[str, StageInput],
    *,
    evidence_contract: DataContract,
    label: str,
) -> tuple[StageInput, StageInput]:
    allowed = {PARENT_V1.id, evidence_contract.id}
    unsupported = sorted(
        port for port, stage_input in inputs.items() if stage_input.contract_id not in allowed
    )
    if unsupported:
        raise PluginError(
            f"{label} received unsupported input contracts",
            code="EVIDENCE_GATE_INPUT_CONTRACT_INVALID",
            context={"request_ports": unsupported},
        )
    parents = [value for value in inputs.values() if value.contract_id == PARENT_V1.id]
    evidence = [
        value for value in inputs.values() if value.contract_id == evidence_contract.id
    ]
    if len(parents) != 1 or len(evidence) != 1 or len(inputs) != 2:
        raise PluginError(
            f"{label} requires exactly one parent and one evidence input",
            code="EVIDENCE_GATE_INPUT_CARDINALITY_INVALID",
            context={
                "parent_input_count": len(parents),
                "evidence_input_count": len(evidence),
                "request_input_count": len(inputs),
            },
        )
    return parents[0], evidence[0]


def _prepare_staging(context: StageContext, *, database_name: str) -> Path:
    context.staging_root.mkdir(parents=True, exist_ok=True)
    database_path = context.staging_root / database_name
    paths = (
        database_path,
        context.staging_root / _PARENT_PATH,
        context.staging_root / _DECISION_PATH,
    )
    existing = next((path for path in paths if path.exists() or path.is_symlink()), None)
    if existing is not None:
        raise PluginError(
            "evidence-gate staging path already exists",
            code="PLUGIN_STAGING_NOT_EMPTY",
            context={"path": str(existing.relative_to(context.staging_root))},
        )
    return database_path


def _create_common_schema(connection: sqlite3.Connection) -> None:
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
                    "parent input contains duplicate identifiers",
                    code="EVIDENCE_GATE_DUPLICATE_PARENT_ID",
                    context={"parent_id": parent_id},
                ) from error
            count += 1
    if count == 0:
        raise PluginError(
            "evidence gate parent input contains no rows",
            code="EVIDENCE_GATE_EMPTY_PARENT_INPUT",
        )
    return count


def _ensure_finite(value: object, *, field: str, parent_id: str, label: str) -> None:
    if value is not None and not math.isfinite(float(value)):
        raise PluginError(
            f"{label} contains a non-finite numeric value",
            code="EVIDENCE_GATE_NON_FINITE",
            context={"field": field, "parent_id": parent_id},
        )


def _resolve_optional_identity(
    connection: sqlite3.Connection,
    *,
    configured: str | None,
    query: str,
    parameters: tuple[object, ...],
    identity_name: str,
    label: str,
) -> str:
    if configured is not None:
        return configured
    observed = connection.execute(query, parameters).fetchmany(2)
    if len(observed) != 1:
        raise PluginError(
            f"{label} requires exactly one observed {identity_name} when none is configured",
            code="EVIDENCE_GATE_IDENTITY_AMBIGUOUS",
            context={
                "identity": identity_name,
                "observed_count": len(observed),
                "observed_examples": [str(row[0]) for row in observed],
            },
        )
    return str(observed[0][0])


def _detail_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _retained_evidence_outside_current_population(
    connection: sqlite3.Connection,
    *,
    table: str,
) -> int:
    """Count valid upstream evidence rows ignored after an earlier serial filter."""

    if table not in {
        "derived_evidence",
        "docking_evidence",
        "prediction_evidence",
        "synthesis_evidence",
    }:
        raise ValueError("unknown internal evidence table")
    row = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM {table} e
        LEFT JOIN parents p ON p.parent_id = e.parent_id
        WHERE p.parent_id IS NULL
        """
    ).fetchone()
    assert row is not None
    return int(row[0])


def _outcome_for_value(
    value: float | None,
    *,
    minimum: float | None,
    maximum: float | None,
    reason_prefix: str,
) -> tuple[bool, str]:
    if value is None:
        return False, f"{reason_prefix}_MISSING"
    if minimum is not None and value < minimum:
        return False, f"{reason_prefix}_BELOW_MINIMUM"
    if maximum is not None and value > maximum:
        return False, f"{reason_prefix}_ABOVE_MAXIMUM"
    return True, f"{reason_prefix}_PASS"


def _insert_result(
    connection: sqlite3.Connection,
    *,
    parent: tuple[Any, ...],
    stage_id: str,
    passed: bool,
    reason_code: str,
    policy_id: str,
    detail: dict[str, Any],
) -> None:
    if passed:
        connection.execute("INSERT INTO passed VALUES (?, ?, ?, ?, ?, ?, ?, ?)", parent)
    connection.execute(
        """
        INSERT INTO decisions (
            entity_id, entity_kind, stage_id, outcome, reason_code, rule_id, detail
        ) VALUES (?, 'PARENT', ?, ?, ?, ?, ?)
        """,
        (
            str(parent[0]),
            stage_id,
            "PASS" if passed else "REJECT",
            reason_code,
            policy_id,
            _detail_json(detail),
        ),
    )


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
        FROM passed ORDER BY input_rank
        """,
        schema=PARENT_V1.schema,
        destination=context.staging_root / _PARENT_PATH,
        batch_size=batch_size,
    )
    write_query_parquet(
        connection,
        """
        SELECT entity_id, entity_kind, stage_id, outcome, reason_code, rule_id, detail
        FROM decisions d
        JOIN parents p ON p.parent_id = d.entity_id
        ORDER BY p.input_rank, d.entity_kind, d.stage_id, d.reason_code
        """,
        schema=DECISION_V1.schema,
        destination=context.staging_root / _DECISION_PATH,
        batch_size=batch_size,
    )


def _remove_work_files(context: StageContext, database_path: Path) -> None:
    """Remove unpublished output and SQLite files after failure or completion."""

    (context.staging_root / _PARENT_PATH).unlink(missing_ok=True)
    (context.staging_root / _DECISION_PATH).unlink(missing_ok=True)
    database_path.unlink(missing_ok=True)
    for suffix in ("-journal", "-wal", "-shm"):
        Path(f"{database_path}{suffix}").unlink(missing_ok=True)


def _insert_prediction_evidence(
    connection: sqlite3.Connection,
    stage_input: StageInput,
    *,
    batch_size: int,
) -> int:
    connection.execute(
        """
        CREATE TABLE prediction_evidence (
            parent_id TEXT NOT NULL,
            endpoint_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            prediction_mean REAL NOT NULL,
            PRIMARY KEY (parent_id, endpoint_id, model_id)
        ) WITHOUT ROWID
        """
    )
    count = 0
    for batch in iter_contract_batches(stage_input, PREDICTION_V1, batch_size=batch_size):
        for row in batch.to_pylist():
            parent_id = str(row["parent_id"])
            endpoint_id = str(row["endpoint_id"])
            model_id = str(row["model_id"])
            if not endpoint_id or not model_id:
                raise PluginError(
                    "prediction evidence contains a blank endpoint or model identity",
                    code="EVIDENCE_GATE_IDENTITY_INVALID",
                    context={"parent_id": parent_id},
                )
            for field in (
                "prediction_mean",
                "prediction_std",
                "interval_lower",
                "interval_upper",
            ):
                _ensure_finite(
                    row.get(field),
                    field=field,
                    parent_id=parent_id,
                    label="prediction evidence",
                )
            lower = row.get("interval_lower")
            upper = row.get("interval_upper")
            if lower is not None and upper is not None and float(lower) > float(upper):
                raise PluginError(
                    "prediction evidence contains an inverted interval",
                    code="EVIDENCE_GATE_INTERVAL_INVALID",
                    context={"parent_id": parent_id},
                )
            try:
                connection.execute(
                    "INSERT INTO prediction_evidence VALUES (?, ?, ?, ?)",
                    (parent_id, endpoint_id, model_id, float(row["prediction_mean"])),
                )
            except sqlite3.IntegrityError as error:
                raise PluginError(
                    "prediction evidence contains a duplicate endpoint/model row",
                    code="EVIDENCE_GATE_DUPLICATE_EVIDENCE",
                    context={
                        "parent_id": parent_id,
                        "endpoint_id": endpoint_id,
                        "model_id": model_id,
                    },
                ) from error
            count += 1
    return count


def _insert_synthesis_evidence(
    connection: sqlite3.Connection,
    stage_input: StageInput,
    *,
    batch_size: int,
) -> int:
    connection.execute(
        """
        CREATE TABLE synthesis_evidence (
            parent_id TEXT NOT NULL,
            method_id TEXT NOT NULL,
            score REAL NOT NULL,
            direction TEXT NOT NULL,
            PRIMARY KEY (parent_id, method_id)
        ) WITHOUT ROWID
        """
    )
    count = 0
    allowed_directions = {item.value for item in SynthesisScoreDirection}
    for batch in iter_contract_batches(
        stage_input,
        SYNTHESIS_SCORE_V1,
        batch_size=batch_size,
    ):
        for row in batch.to_pylist():
            parent_id = str(row["parent_id"])
            method_id = str(row["method_id"])
            direction = str(row["direction"])
            if not method_id or direction not in allowed_directions:
                raise PluginError(
                    "synthesis evidence contains an invalid method identity or direction",
                    code="EVIDENCE_GATE_IDENTITY_INVALID",
                    context={
                        "parent_id": parent_id,
                        "method_id": method_id,
                        "direction": direction,
                    },
                )
            _ensure_finite(
                row.get("score"),
                field="score",
                parent_id=parent_id,
                label="synthesis evidence",
            )
            try:
                connection.execute(
                    "INSERT INTO synthesis_evidence VALUES (?, ?, ?, ?)",
                    (parent_id, method_id, float(row["score"]), direction),
                )
            except sqlite3.IntegrityError as error:
                raise PluginError(
                    "synthesis evidence contains a duplicate method row",
                    code="EVIDENCE_GATE_DUPLICATE_EVIDENCE",
                    context={"parent_id": parent_id, "method_id": method_id},
                ) from error
            count += 1
    return count


def _insert_docking_evidence(
    connection: sqlite3.Connection,
    stage_input: StageInput,
    *,
    batch_size: int,
) -> int:
    """Load every pose, not just the ranked one.

    A docking engine emits several poses per molecule and the pose that decides
    the molecule's fate is the strongest one, which is a question about the
    scores rather than about the ordering.  Loading the whole set means the gate
    can find it for itself instead of trusting ``pose_rank`` to have been
    computed with the same notion of "better" the threshold uses -- and the two
    disagreeing is exactly the kind of silent inversion that would let a run
    keep its worst poses and call them hits.

    ``pose_molblock`` is deliberately not loaded.  It is the largest column in
    the contract by a wide margin and no threshold reads it; the geometry stays
    in the evidence artifact where a later stage can find it.
    """

    connection.execute(
        """
        CREATE TABLE docking_evidence (
            parent_id TEXT NOT NULL,
            engine_id TEXT NOT NULL,
            receptor_id TEXT NOT NULL,
            pose_rank INTEGER NOT NULL,
            score REAL NOT NULL,
            score_kind TEXT NOT NULL,
            direction TEXT NOT NULL,
            PRIMARY KEY (parent_id, engine_id, receptor_id, pose_rank)
        ) WITHOUT ROWID
        """
    )
    count = 0
    allowed_kinds = {item.value for item in DockingScoreKind}
    allowed_directions = {item.value for item in DockingScoreDirection}
    for batch in iter_contract_batches(
        stage_input,
        DOCKING_SCORE_V1,
        batch_size=batch_size,
    ):
        for row in batch.to_pylist():
            parent_id = str(row["parent_id"])
            engine_id = str(row["engine_id"])
            receptor_id = str(row["receptor_id"])
            score_kind = str(row["score_kind"])
            direction = str(row["direction"])
            if (
                not engine_id
                or not receptor_id
                or score_kind not in allowed_kinds
                or direction not in allowed_directions
            ):
                raise PluginError(
                    "docking evidence contains an invalid engine, receptor, scale or direction",
                    code="EVIDENCE_GATE_IDENTITY_INVALID",
                    context={
                        "parent_id": parent_id,
                        "engine_id": engine_id,
                        "receptor_id": receptor_id,
                        "score_kind": score_kind,
                        "direction": direction,
                    },
                )
            pose_rank = int(row["pose_rank"])
            if pose_rank < 0:
                raise PluginError(
                    "docking evidence contains a negative pose rank",
                    code="EVIDENCE_GATE_POSE_RANK_INVALID",
                    context={"parent_id": parent_id, "pose_rank": pose_rank},
                )
            for field in ("score", "secondary_score"):
                _ensure_finite(
                    row.get(field),
                    field=field,
                    parent_id=parent_id,
                    label="docking evidence",
                )
            try:
                connection.execute(
                    "INSERT INTO docking_evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        parent_id,
                        engine_id,
                        receptor_id,
                        pose_rank,
                        float(row["score"]),
                        score_kind,
                        direction,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise PluginError(
                    "docking evidence contains a duplicate pose row",
                    code="EVIDENCE_GATE_DUPLICATE_EVIDENCE",
                    context={
                        "parent_id": parent_id,
                        "engine_id": engine_id,
                        "receptor_id": receptor_id,
                        "pose_rank": pose_rank,
                    },
                ) from error
            count += 1
    return count


def _insert_derived_evidence(
    connection: sqlite3.Connection,
    stage_input: StageInput,
    *,
    batch_size: int,
) -> int:
    """Load every metric in the table, not only the one this gate reads.

    One featurizer writes several metrics at once -- a ligand efficiency, a
    size-normalised score and a baseline residual come out of the same pass over
    the docking scores -- so the table this gate is handed usually holds columns
    it will not threshold.  Loading all of them costs one integer comparison per
    row and buys an error message that can list what *is* available when the
    configured ``metric_id`` is a typo.

    ``source_json`` is deliberately not loaded.  It is provenance for the trace,
    not an input to any threshold.
    """

    connection.execute(
        """
        CREATE TABLE derived_evidence (
            parent_id TEXT NOT NULL,
            metric_id TEXT NOT NULL,
            method_id TEXT NOT NULL,
            value REAL,
            units TEXT NOT NULL,
            direction TEXT NOT NULL,
            status TEXT NOT NULL,
            PRIMARY KEY (parent_id, metric_id, method_id)
        ) WITHOUT ROWID
        """
    )
    count = 0
    allowed_units = {item.value for item in DerivedMetricUnits}
    allowed_directions = {item.value for item in DerivedMetricDirection}
    allowed_statuses = {item.value for item in DerivedMetricStatus}
    value_required = {
        DerivedMetricStatus.OK.value,
        DerivedMetricStatus.OUT_OF_DOMAIN.value,
    }
    for batch in iter_contract_batches(
        stage_input,
        DERIVED_METRIC_V1,
        batch_size=batch_size,
    ):
        for row in batch.to_pylist():
            parent_id = str(row["parent_id"])
            metric_id = str(row["metric_id"])
            method_id = str(row["method_id"])
            units = str(row["units"])
            direction = str(row["direction"])
            status = str(row["status"])
            if (
                not metric_id
                or not method_id
                or units not in allowed_units
                or direction not in allowed_directions
                or status not in allowed_statuses
            ):
                raise PluginError(
                    "derived metric evidence contains an invalid identity, scale, "
                    "direction or status",
                    code="EVIDENCE_GATE_IDENTITY_INVALID",
                    context={
                        "parent_id": parent_id,
                        "metric_id": metric_id,
                        "method_id": method_id,
                        "units": units,
                        "direction": direction,
                        "status": status,
                    },
                )
            value = row.get("value")
            if status in value_required and value is None:
                # The contract cannot express this: ``value`` has to stay
                # nullable so a failure keeps its row.  A row that claims a
                # number was computed and then carries none means the producer
                # disagrees with itself, and neither half can be trusted enough
                # to gate on -- least of all by reading the null as a rejection,
                # which would look exactly like a molecule that missed the
                # window.
                raise PluginError(
                    "derived metric evidence claims a status that requires a value "
                    "but carries none",
                    code="DERIVED_METRIC_GATE_STATUS_INCONSISTENT",
                    context={
                        "parent_id": parent_id,
                        "metric_id": metric_id,
                        "method_id": method_id,
                        "status": status,
                    },
                )
            _ensure_finite(
                value,
                field="value",
                parent_id=parent_id,
                label="derived metric evidence",
            )
            try:
                connection.execute(
                    "INSERT INTO derived_evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        parent_id,
                        metric_id,
                        method_id,
                        None if value is None else float(value),
                        units,
                        direction,
                        status,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise PluginError(
                    "derived metric evidence contains a duplicate metric row",
                    code="EVIDENCE_GATE_DUPLICATE_EVIDENCE",
                    context={
                        "parent_id": parent_id,
                        "metric_id": metric_id,
                        "method_id": method_id,
                    },
                ) from error
            count += 1
    return count


def _require_metric_present(
    connection: sqlite3.Connection,
    *,
    metric_id: str,
) -> None:
    """Refuse a metric_id nothing in the current population wrote.

    Without this the gate would run perfectly and reject everything, because a
    LEFT JOIN on a name no row carries is indistinguishable from a featurizer
    that scored nobody.  The available names are the whole diagnosis, so they go
    in the error.
    """

    present = connection.execute(
        """
        SELECT 1 FROM derived_evidence e
        JOIN parents p ON p.parent_id = e.parent_id
        WHERE e.metric_id = ? LIMIT 1
        """,
        (metric_id,),
    ).fetchone()
    if present is not None:
        return
    available: list[JsonValue] = [
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT metric_id FROM derived_evidence ORDER BY metric_id"
        ).fetchmany(32)
    ]
    raise PluginError(
        "derived metric evidence contains no rows for the configured metric",
        code="DERIVED_METRIC_GATE_METRIC_UNAVAILABLE",
        hint="Set metric_id to one of the metrics the upstream stage wrote.",
        context={"metric_id": metric_id, "available_metric_ids": available},
    )


def _outcome_for_status(status: str, *, action: UnscorableAction) -> tuple[bool, str]:
    """Turn a non-OK status into a decision without consulting the value.

    The value is deliberately not compared against the window here.  A strain of
    -19.4 kcal/mol sits comfortably inside ``maximum=8`` and means the reference
    conformer search failed, so reading it as a pass would admit precisely the
    molecules the metric knows it could not measure.
    """

    if action is UnscorableAction.ACCEPT:
        return True, "DERIVED_METRIC_UNSCORABLE_ACCEPTED"
    return False, f"DERIVED_METRIC_{status}"


def _evaluate_derived_metric(
    connection: sqlite3.Connection,
    *,
    stage_id: str,
    config: DerivedMetricEvidenceGateConfig,
) -> tuple[int, str, str]:
    _require_metric_present(connection, metric_id=config.metric_id)
    method_id = _resolve_optional_identity(
        connection,
        configured=config.method_id,
        query=(
            "SELECT DISTINCT e.method_id FROM derived_evidence e "
            "JOIN parents p ON p.parent_id = e.parent_id "
            "WHERE e.metric_id = ? ORDER BY e.method_id"
        ),
        parameters=(config.metric_id,),
        identity_name="method_id for the configured metric",
        label="derived metric evidence gate",
    )
    observed = connection.execute(
        """
        SELECT DISTINCT units, direction
        FROM derived_evidence
        WHERE metric_id = ? AND method_id = ?
        ORDER BY units, direction
        """,
        (config.metric_id, method_id),
    ).fetchmany(3)
    mismatched_units: list[JsonValue] = [
        units
        for units in sorted({str(row[0]) for row in observed})
        if units != config.expected_units.value
    ]
    if mismatched_units:
        raise PluginError(
            "derived metric evidence is on a different scale than the configured policy",
            code="DERIVED_METRIC_GATE_UNITS_MISMATCH",
            context={
                "metric_id": config.metric_id,
                "method_id": method_id,
                "expected_units": config.expected_units.value,
                "observed_units": mismatched_units,
            },
        )
    mismatched_directions: list[JsonValue] = [
        direction
        for direction in sorted({str(row[1]) for row in observed})
        if direction != config.expected_direction.value
    ]
    if mismatched_directions:
        raise PluginError(
            "derived metric evidence direction conflicts with the configured policy",
            code="EVIDENCE_GATE_DIRECTION_MISMATCH",
            context={
                "metric_id": config.metric_id,
                "method_id": method_id,
                "expected_direction": config.expected_direction.value,
                "observed_directions": mismatched_directions,
            },
        )
    policy_id = "derived-metric-gate:sha256:" + canonical_sha256(
        {
            "implementation_version": 1,
            "metric_id": config.metric_id,
            "method_id": method_id,
            "units": config.expected_units.value,
            "direction": config.expected_direction.value,
            "minimum": config.minimum,
            "maximum": config.maximum,
            "bounds_inclusive": True,
            "missing_policy": "REJECT",
            "unscorable_policy": config.on_unscorable.value,
        }
    )
    passed_count = 0
    cursor = connection.execute(
        """
        SELECT p.parent_id, p.identity_policy_id, p.parent_smiles,
               p.registration_key, p.stereo_key, p.formula,
               p.duplicate_count, p.input_rank, e.value, e.status
        FROM parents p
        LEFT JOIN derived_evidence e
          ON e.parent_id = p.parent_id AND e.metric_id = ? AND e.method_id = ?
        ORDER BY p.input_rank
        """,
        (config.metric_id, method_id),
    )
    while rows := cursor.fetchmany(config.batch_size):
        for row in rows:
            parent = tuple(row[:8])
            value = None if row[8] is None else float(row[8])
            status = None if row[9] is None else str(row[9])
            if status is None:
                # Absent evidence is a different failure from a metric that
                # declined to score: the molecule was never measured at all, so
                # ``on_unscorable`` has no bearing on it and it fails closed.
                passed, reason_code = _outcome_for_value(
                    None,
                    minimum=config.minimum,
                    maximum=config.maximum,
                    reason_prefix="DERIVED_METRIC",
                )
            elif status != DerivedMetricStatus.OK.value:
                passed, reason_code = _outcome_for_status(
                    status,
                    action=config.on_unscorable,
                )
            else:
                passed, reason_code = _outcome_for_value(
                    value,
                    minimum=config.minimum,
                    maximum=config.maximum,
                    reason_prefix="DERIVED_METRIC",
                )
            _insert_result(
                connection,
                parent=parent,
                stage_id=stage_id,
                passed=passed,
                reason_code=reason_code,
                policy_id=policy_id,
                detail={
                    "metric_id": config.metric_id,
                    "method_id": method_id,
                    "units": config.expected_units.value,
                    "direction": config.expected_direction.value,
                    "value": value,
                    "status": status,
                    "minimum": config.minimum,
                    "maximum": config.maximum,
                    "bounds_inclusive": True,
                    "unscorable_policy": config.on_unscorable.value,
                    "derived_not_measured": True,
                },
            )
            passed_count += int(passed)
    return passed_count, method_id, policy_id


def _evaluate_prediction(
    connection: sqlite3.Connection,
    *,
    stage_id: str,
    config: PredictionEvidenceGateConfig,
) -> tuple[int, str, str]:
    model_id = _resolve_optional_identity(
        connection,
        configured=config.model_id,
        query=(
            "SELECT DISTINCT e.model_id FROM prediction_evidence e "
            "JOIN parents p ON p.parent_id = e.parent_id "
            "WHERE e.endpoint_id = ? ORDER BY e.model_id"
        ),
        parameters=(config.endpoint_id,),
        identity_name="model_id for the configured endpoint",
        label="prediction evidence gate",
    )
    policy_id = "prediction-gate:sha256:" + canonical_sha256(
        {
            "implementation_version": 1,
            "endpoint_id": config.endpoint_id,
            "model_id": model_id,
            "semantics_label": config.semantics_label,
            "minimum": config.minimum,
            "maximum": config.maximum,
            "bounds_inclusive": True,
            "missing_policy": "REJECT",
        }
    )
    passed_count = 0
    cursor = connection.execute(
        """
        SELECT p.parent_id, p.identity_policy_id, p.parent_smiles,
               p.registration_key, p.stereo_key, p.formula,
               p.duplicate_count, p.input_rank, e.prediction_mean
        FROM parents p
        LEFT JOIN prediction_evidence e
          ON e.parent_id = p.parent_id
         AND e.endpoint_id = ?
         AND e.model_id = ?
        ORDER BY p.input_rank
        """,
        (config.endpoint_id, model_id),
    )
    while rows := cursor.fetchmany(config.batch_size):
        for row in rows:
            parent = tuple(row[:8])
            value = None if row[8] is None else float(row[8])
            passed, reason_code = _outcome_for_value(
                value,
                minimum=config.minimum,
                maximum=config.maximum,
                reason_prefix="PREDICTION",
            )
            _insert_result(
                connection,
                parent=parent,
                stage_id=stage_id,
                passed=passed,
                reason_code=reason_code,
                policy_id=policy_id,
                detail={
                    "endpoint_id": config.endpoint_id,
                    "model_id": model_id,
                    "prediction_mean": value,
                    "minimum": config.minimum,
                    "maximum": config.maximum,
                    "bounds_inclusive": True,
                    "semantics_label": config.semantics_label,
                },
            )
            passed_count += int(passed)
    return passed_count, model_id, policy_id


def _evaluate_synthesis(
    connection: sqlite3.Connection,
    *,
    stage_id: str,
    config: SynthesisScoreEvidenceGateConfig,
) -> tuple[int, str, str]:
    method_id = _resolve_optional_identity(
        connection,
        configured=config.method_id,
        query=(
            "SELECT DISTINCT e.method_id FROM synthesis_evidence e "
            "JOIN parents p ON p.parent_id = e.parent_id ORDER BY e.method_id"
        ),
        parameters=(),
        identity_name="method_id",
        label="synthesis evidence gate",
    )
    observed_directions = connection.execute(
        "SELECT DISTINCT direction FROM synthesis_evidence WHERE method_id = ?",
        (method_id,),
    ).fetchmany(2)
    unexpected = [
        str(row[0])
        for row in observed_directions
        if str(row[0]) != config.expected_direction.value
    ]
    if unexpected:
        raise PluginError(
            "synthesis evidence direction conflicts with the configured policy",
            code="EVIDENCE_GATE_DIRECTION_MISMATCH",
            context={
                "method_id": method_id,
                "expected_direction": config.expected_direction.value,
                "observed_directions": [str(row[0]) for row in observed_directions],
            },
        )
    policy_id = "synthesis-gate:sha256:" + canonical_sha256(
        {
            "implementation_version": 1,
            "method_id": method_id,
            "expected_direction": config.expected_direction,
            "minimum": config.minimum,
            "maximum": config.maximum,
            "bounds_inclusive": True,
            "missing_policy": "REJECT",
        }
    )
    passed_count = 0
    cursor = connection.execute(
        """
        SELECT p.parent_id, p.identity_policy_id, p.parent_smiles,
               p.registration_key, p.stereo_key, p.formula,
               p.duplicate_count, p.input_rank, e.score
        FROM parents p
        LEFT JOIN synthesis_evidence e
          ON e.parent_id = p.parent_id AND e.method_id = ?
        ORDER BY p.input_rank
        """,
        (method_id,),
    )
    while rows := cursor.fetchmany(config.batch_size):
        for row in rows:
            parent = tuple(row[:8])
            value = None if row[8] is None else float(row[8])
            passed, reason_code = _outcome_for_value(
                value,
                minimum=config.minimum,
                maximum=config.maximum,
                reason_prefix="SYNTHESIS_SCORE",
            )
            _insert_result(
                connection,
                parent=parent,
                stage_id=stage_id,
                passed=passed,
                reason_code=reason_code,
                policy_id=policy_id,
                detail={
                    "method_id": method_id,
                    "direction": config.expected_direction.value,
                    "score": value,
                    "minimum": config.minimum,
                    "maximum": config.maximum,
                    "bounds_inclusive": True,
                    "proxy_not_route": True,
                },
            )
            passed_count += int(passed)
    return passed_count, method_id, policy_id


def _evaluate_docking(
    connection: sqlite3.Connection,
    *,
    stage_id: str,
    config: DockingScoreEvidenceGateConfig,
) -> tuple[int, str, str, str]:
    engine_id = _resolve_optional_identity(
        connection,
        configured=config.engine_id,
        query=(
            "SELECT DISTINCT e.engine_id FROM docking_evidence e "
            "JOIN parents p ON p.parent_id = e.parent_id ORDER BY e.engine_id"
        ),
        parameters=(),
        identity_name="engine_id",
        label="docking evidence gate",
    )
    receptor_id = _resolve_optional_identity(
        connection,
        configured=config.receptor_id,
        query=(
            "SELECT DISTINCT e.receptor_id FROM docking_evidence e "
            "JOIN parents p ON p.parent_id = e.parent_id "
            "WHERE e.engine_id = ? ORDER BY e.receptor_id"
        ),
        parameters=(engine_id,),
        identity_name="receptor_id for the configured engine",
        label="docking evidence gate",
    )
    observed = connection.execute(
        """
        SELECT DISTINCT score_kind, direction
        FROM docking_evidence
        WHERE engine_id = ? AND receptor_id = ?
        ORDER BY score_kind, direction
        """,
        (engine_id, receptor_id),
    ).fetchmany(3)
    mismatched = [
        (str(row[0]), str(row[1]))
        for row in observed
        if str(row[0]) != config.expected_score_kind.value
        or str(row[1]) != config.expected_direction.value
    ]
    if mismatched:
        # Two distinct ways to be wrong -- the wrong scale, or the right scale
        # read backwards -- and both turn a threshold into noise, so they share
        # one refusal rather than being ranked against each other.
        raise PluginError(
            "docking evidence scale or direction conflicts with the configured policy",
            code="EVIDENCE_GATE_SCORE_SEMANTICS_MISMATCH",
            context={
                "engine_id": engine_id,
                "receptor_id": receptor_id,
                "expected_score_kind": config.expected_score_kind.value,
                "expected_direction": config.expected_direction.value,
                "observed": [
                    {"score_kind": kind, "direction": direction}
                    for kind, direction in mismatched
                ],
            },
        )
    stronger = config.expected_direction is DockingScoreDirection.LOWER_STRONGER
    policy_id = "docking-gate:sha256:" + canonical_sha256(
        {
            "implementation_version": 1,
            "engine_id": engine_id,
            "receptor_id": receptor_id,
            "score_kind": config.expected_score_kind.value,
            "direction": config.expected_direction.value,
            "pose_policy": "BEST_POSE",
            "minimum": config.minimum,
            "maximum": config.maximum,
            "bounds_inclusive": True,
            "missing_policy": "REJECT",
        }
    )
    passed_count = 0
    cursor = connection.execute(
        # The bare ``pose_rank`` is taken from the row the aggregate selected:
        # SQLite defines that for a query with exactly one min() or max(), which
        # is what makes it possible to record *which* pose decided the molecule
        # without a second pass over the poses.
        f"""
        SELECT p.parent_id, p.identity_policy_id, p.parent_smiles,
               p.registration_key, p.stereo_key, p.formula,
               p.duplicate_count, p.input_rank, b.score, b.pose_rank, b.pose_count
        FROM parents p
        LEFT JOIN (
            SELECT parent_id,
                   {"MIN(score)" if stronger else "MAX(score)"} AS score,
                   pose_rank,
                   COUNT(*) AS pose_count
            FROM docking_evidence
            WHERE engine_id = ? AND receptor_id = ?
            GROUP BY parent_id
        ) b ON b.parent_id = p.parent_id
        ORDER BY p.input_rank
        """,
        (engine_id, receptor_id),
    )
    while rows := cursor.fetchmany(config.batch_size):
        for row in rows:
            parent = tuple(row[:8])
            value = None if row[8] is None else float(row[8])
            passed, reason_code = _outcome_for_value(
                value,
                minimum=config.minimum,
                maximum=config.maximum,
                reason_prefix="DOCKING_SCORE",
            )
            _insert_result(
                connection,
                parent=parent,
                stage_id=stage_id,
                passed=passed,
                reason_code=reason_code,
                policy_id=policy_id,
                detail={
                    "engine_id": engine_id,
                    "receptor_id": receptor_id,
                    "score_kind": config.expected_score_kind.value,
                    "direction": config.expected_direction.value,
                    "score": value,
                    "pose_rank": None if row[9] is None else int(row[9]),
                    "pose_count": 0 if row[10] is None else int(row[10]),
                    "minimum": config.minimum,
                    "maximum": config.maximum,
                    "bounds_inclusive": True,
                    "pose_policy": "BEST_POSE",
                    "not_affinity": True,
                },
            )
            passed_count += int(passed)
    return passed_count, engine_id, receptor_id, policy_id


class NativePredictionEvidenceGatePlugin:
    """Filter parents using one exact ``prediction/v1`` endpoint and model."""

    descriptor = PluginDescriptor(
        id="prediction.numeric_evidence_gate",
        version="0.1.0",
        kind=PluginKind.PREDICTOR,
        inputs=(PARENT_V1.id, PREDICTION_V1.id),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="Prediction evidence threshold gate",
        description="Exact endpoint/model numeric window with fail-closed missing evidence.",
    )
    config_model = PredictionEvidenceGateConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(
            request,
            PredictionEvidenceGateConfig,
            label="prediction evidence gate",
        )
        assert isinstance(config, PredictionEvidenceGateConfig)
        parent_input, evidence_input = _classify_inputs(
            dict(request.inputs),
            evidence_contract=PREDICTION_V1,
            label="prediction evidence gate",
        )
        database_path = _prepare_staging(
            context,
            database_name=".prediction-evidence-gate.sqlite3",
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = open_stage_database(database_path)
            _create_common_schema(connection)
            input_count = _insert_parents(
                connection,
                parent_input,
                batch_size=config.batch_size,
            )
            evidence_count = _insert_prediction_evidence(
                connection,
                evidence_input,
                batch_size=config.batch_size,
            )
            retained_evidence_ignored_count = (
                _retained_evidence_outside_current_population(
                    connection,
                    table="prediction_evidence",
                )
            )
            passed_count, model_id, policy_id = _evaluate_prediction(
                connection,
                stage_id=request.stage_id,
                config=config,
            )
            _write_outputs(
                connection,
                context,
                batch_size=config.batch_size,
            )
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
                    {"row_count": input_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "evidence_count": evidence_count,
                "retained_evidence_ignored_count": retained_evidence_ignored_count,
                "output_count": passed_count,
                "rejected_count": input_count - passed_count,
                "endpoint_id": config.endpoint_id,
                "model_id": model_id,
                "policy_id": policy_id,
                "missing_policy": "REJECT",
                "bounds_inclusive": True,
            },
        )


class NativeSynthesisScoreEvidenceGatePlugin:
    """Filter parents using one exact ``synthesis_score/v1`` method."""

    descriptor = PluginDescriptor(
        id="synthesis.numeric_evidence_gate",
        version="0.1.0",
        kind=PluginKind.SYNTHESIS,
        inputs=(PARENT_V1.id, SYNTHESIS_SCORE_V1.id),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="Synthesis-score evidence threshold gate",
        description="Exact method/direction numeric window with fail-closed missing evidence.",
    )
    config_model = SynthesisScoreEvidenceGateConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(
            request,
            SynthesisScoreEvidenceGateConfig,
            label="synthesis evidence gate",
        )
        assert isinstance(config, SynthesisScoreEvidenceGateConfig)
        parent_input, evidence_input = _classify_inputs(
            dict(request.inputs),
            evidence_contract=SYNTHESIS_SCORE_V1,
            label="synthesis evidence gate",
        )
        database_path = _prepare_staging(
            context,
            database_name=".synthesis-evidence-gate.sqlite3",
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = open_stage_database(database_path)
            _create_common_schema(connection)
            input_count = _insert_parents(
                connection,
                parent_input,
                batch_size=config.batch_size,
            )
            evidence_count = _insert_synthesis_evidence(
                connection,
                evidence_input,
                batch_size=config.batch_size,
            )
            retained_evidence_ignored_count = (
                _retained_evidence_outside_current_population(
                    connection,
                    table="synthesis_evidence",
                )
            )
            passed_count, method_id, policy_id = _evaluate_synthesis(
                connection,
                stage_id=request.stage_id,
                config=config,
            )
            _write_outputs(
                connection,
                context,
                batch_size=config.batch_size,
            )
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
                    {"row_count": input_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "evidence_count": evidence_count,
                "retained_evidence_ignored_count": retained_evidence_ignored_count,
                "output_count": passed_count,
                "rejected_count": input_count - passed_count,
                "method_id": method_id,
                "expected_direction": config.expected_direction.value,
                "policy_id": policy_id,
                "missing_policy": "REJECT",
                "bounds_inclusive": True,
                "proxy_not_route": True,
            },
        )


class NativeDockingScoreEvidenceGatePlugin:
    """Filter parents on one engine's best pose against one receptor.

    This is what makes a docking engine usable as a criterion rather than as a
    terminal report.  An engine emits ``docking_score/v1`` and stops there --
    deliberately, since where the cutoff belongs is a decision about the
    campaign and not about the software -- so a parallel tier of engines needs
    something that turns each engine's scores into a decision it can vote with.
    ``_lower_tier`` refuses a parallel criterion that emits no decision, which
    is the mechanism that makes 2-of-3 consensus docking work at all.
    """

    descriptor = PluginDescriptor(
        id="docking.numeric_evidence_gate",
        version="0.1.0",
        kind=PluginKind.DOCK,
        inputs=(PARENT_V1.id, DOCKING_SCORE_V1.id),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="Docking-score evidence threshold gate",
        description="Exact engine/receptor/scale window over the best pose, rejecting on absence.",
    )
    config_model = DockingScoreEvidenceGateConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(
            request,
            DockingScoreEvidenceGateConfig,
            label="docking evidence gate",
        )
        assert isinstance(config, DockingScoreEvidenceGateConfig)
        parent_input, evidence_input = _classify_inputs(
            dict(request.inputs),
            evidence_contract=DOCKING_SCORE_V1,
            label="docking evidence gate",
        )
        database_path = _prepare_staging(
            context,
            database_name=".docking-evidence-gate.sqlite3",
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = open_stage_database(database_path)
            _create_common_schema(connection)
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
            retained_evidence_ignored_count = (
                _retained_evidence_outside_current_population(
                    connection,
                    table="docking_evidence",
                )
            )
            passed_count, engine_id, receptor_id, policy_id = _evaluate_docking(
                connection,
                stage_id=request.stage_id,
                config=config,
            )
            _write_outputs(
                connection,
                context,
                batch_size=config.batch_size,
            )
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
                    {"row_count": input_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": input_count,
                # Poses, not molecules: an engine writes several rows per
                # molecule and the count says how much evidence was read.
                "evidence_count": evidence_count,
                "retained_evidence_ignored_count": retained_evidence_ignored_count,
                "output_count": passed_count,
                "rejected_count": input_count - passed_count,
                "engine_id": engine_id,
                "receptor_id": receptor_id,
                "score_kind": config.expected_score_kind.value,
                "expected_direction": config.expected_direction.value,
                "policy_id": policy_id,
                "missing_policy": "REJECT",
                "bounds_inclusive": True,
                "pose_policy": "BEST_POSE",
                "not_affinity": True,
            },
        )


class NativeDerivedMetricEvidenceGatePlugin:
    """Filter parents on one derived metric computed by one method.

    The numbers this gate reads are arithmetic over evidence an earlier tier
    already produced -- a docking score divided by heavy-atom count, a pose
    energy minus a conformer-ensemble minimum -- and the repository keeps that
    arithmetic in a featurizer rather than folding it into a threshold, so that
    what was computed stays visible in the trace whether or not it decided
    anything.  This is the half that decides.

    One gate reads one metric.  A campaign that wants both a strain ceiling and
    an efficiency floor states them as two criteria, which is what lets the
    tier's own policy join say whether that means both, either, or two of three.
    """

    descriptor = PluginDescriptor(
        id="derived.numeric_evidence_gate",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id, DERIVED_METRIC_V1.id),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="Derived-metric threshold gate",
        description="Exact metric/method/scale window, rejecting absent and unscorable rows.",
    )
    config_model = DerivedMetricEvidenceGateConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(
            request,
            DerivedMetricEvidenceGateConfig,
            label="derived metric evidence gate",
        )
        assert isinstance(config, DerivedMetricEvidenceGateConfig)
        parent_input, evidence_input = _classify_inputs(
            dict(request.inputs),
            evidence_contract=DERIVED_METRIC_V1,
            label="derived metric evidence gate",
        )
        database_path = _prepare_staging(
            context,
            database_name=".derived-metric-evidence-gate.sqlite3",
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = open_stage_database(database_path)
            _create_common_schema(connection)
            input_count = _insert_parents(
                connection,
                parent_input,
                batch_size=config.batch_size,
            )
            evidence_count = _insert_derived_evidence(
                connection,
                evidence_input,
                batch_size=config.batch_size,
            )
            retained_evidence_ignored_count = (
                _retained_evidence_outside_current_population(
                    connection,
                    table="derived_evidence",
                )
            )
            passed_count, method_id, policy_id = _evaluate_derived_metric(
                connection,
                stage_id=request.stage_id,
                config=config,
            )
            _write_outputs(
                connection,
                context,
                batch_size=config.batch_size,
            )
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
                    {"row_count": input_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "evidence_count": evidence_count,
                "retained_evidence_ignored_count": retained_evidence_ignored_count,
                "output_count": passed_count,
                "rejected_count": input_count - passed_count,
                "metric_id": config.metric_id,
                "method_id": method_id,
                "expected_units": config.expected_units.value,
                "expected_direction": config.expected_direction.value,
                "policy_id": policy_id,
                "missing_policy": "REJECT",
                "unscorable_policy": config.on_unscorable.value,
                "bounds_inclusive": True,
                "derived_not_measured": True,
            },
        )


__all__ = [
    "DerivedMetricDirection",
    "DerivedMetricEvidenceGateConfig",
    "DerivedMetricStatus",
    "DerivedMetricUnits",
    "DockingScoreDirection",
    "DockingScoreEvidenceGateConfig",
    "DockingScoreKind",
    "NativeDerivedMetricEvidenceGatePlugin",
    "NativeDockingScoreEvidenceGatePlugin",
    "NativePredictionEvidenceGatePlugin",
    "NativeSynthesisScoreEvidenceGatePlugin",
    "PredictionEvidenceGateConfig",
    "SynthesisScoreDirection",
    "SynthesisScoreEvidenceGateConfig",
    "UnscorableAction",
]
