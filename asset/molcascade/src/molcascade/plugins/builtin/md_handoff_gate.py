"""Refuse a structure that is not fit to simulate, and say which field refused it.

``md_system_input/v1`` makes a producer declare what its coordinates are.  This
turns those declarations into a decision, which is the second half of the same
division the rest of this project makes: evidence is published under a contract,
and a separate stage decides what it is worth.

The rules are categorical rather than a numeric window, so this does not reuse the
numeric evidence-gate machinery beside it.  Three fields are checked and each
corresponds to a way a simulation produces a confident number about nothing.

``coordinate_source`` must name real coordinates.  ``TWO_D_DEPICTION`` means every
z is zero and the layout is a drawing; a force-field build from one yields a flat
ligand at the molblock origin, tens of angstrom from any receptor, and every
downstream step reports success.  ``NONE`` means the run produced no geometry at
all, which is the honest state and not a usable one.

``hydrogens`` must not be ``IMPLICIT``.  A record naming heavy atoms only builds a
topology with no hydrogens -- chemically wrong, accepted by every validator
downstream of it.

``status`` must be ``OK``.  A row whose geometry could not be read keeps its place
in the table precisely so that a gate sees it; treating a null molblock as absent
rather than as failed is how an unreadable structure becomes a silent omission.

One further rule is off by default and is about comparability rather than about
physics.  A docked pose is only meaningful against the receptor bytes it was
scored in, so ``require_receptor_for_pose`` refuses a ``DOCKED_POSE`` row carrying
no ``receptor_id``.  It defaults to on, because the contract states the
relationship as an invariant and a gate that did not enforce it would leave the
invariant as prose.

What this gate deliberately does not do is repair anything.  It does not add
hydrogens, embed coordinates, or pick a protonation state.  Those are decisions
with scientific content, and a gate that quietly made them would produce a
structure nobody chose while reporting that the input passed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator

from molcascade.chemistry.datasets import iter_contract_batches
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, MD_SYSTEM_INPUT_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.plugins.api import (
    PendingOutput,
    StageContext,
    StageRequest,
    StageResponse,
)
from molcascade.plugins.builtin.evidence_gates import _classify_inputs
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")

#: The largest handoff population this will index in memory.  A handoff is the
#: set of molecules about to be simulated, which is tens to thousands -- a
#: simulation costs GPU-days per molecule, so a million-row handoff is a
#: configuration mistake rather than a workload.  Refused with an explanation
#: naming where the stage belongs rather than attempted; the numeric gates beside
#: this one join through SQLite because their populations really are large.
_MAX_HANDOFF_ROWS = 200_000

_USABLE_SOURCES = ("DOCKED_POSE", "EMBEDDED_CONFORMER")


class MdHandoffGateConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    decision_buffer_size: int = Field(default=50_000, ge=1, le=1_000_000)

    #: Coordinate sources this gate accepts.  Narrow it to ``docked_pose`` alone
    #: when the downstream calculation puts the ligand back in the pocket and a
    #: free conformer would have to be re-docked anyway.
    allowed_sources: tuple[str, ...] = _USABLE_SOURCES

    #: Hydrogen states this gate accepts.  ``POLAR_ONLY`` is included because some
    #: engines and scoring functions expect exactly that, but a molecular-dynamics
    #: build wants them all; narrow it when the consumer is a force field.
    allowed_hydrogens: tuple[str, ...] = ("EXPLICIT_ALL", "POLAR_ONLY")

    #: Refuse a pose that does not name the receptor it was scored against.
    require_receptor_for_pose: bool = True

    @field_validator("allowed_sources", "allowed_hydrogens", mode="before")
    @classmethod
    def _accept_list_or_comma_separated(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value) if isinstance(value, list) else value

    @field_validator("allowed_sources")
    @classmethod
    def _sources_are_usable(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("allowed_sources must name at least one coordinate source")
        unusable = set(value) - set(_USABLE_SOURCES)
        if unusable:
            # TWO_D_DEPICTION and NONE are legal contract values and are exactly
            # what this gate exists to stop, so permitting one would make the
            # stage a no-op that reads as a safeguard.
            raise ValueError(
                "allowed_sources may only name real coordinates "
                f"({', '.join(_USABLE_SOURCES)}); got {', '.join(sorted(unusable))}"
            )
        return value

    @field_validator("allowed_hydrogens")
    @classmethod
    def _hydrogens_are_present(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("allowed_hydrogens must name at least one state")
        forbidden = set(value) & {"IMPLICIT", "UNKNOWN"}
        if forbidden:
            raise ValueError(
                "allowed_hydrogens may not include "
                f"{', '.join(sorted(forbidden))}: a record naming heavy atoms only "
                "builds a topology with no hydrogens, and every validator "
                "downstream of that accepts it"
            )
        return value

    def policy_id(self) -> str:
        return "md-handoff-gate:sha256:" + canonical_sha256(
            {
                "allowed_sources": sorted(self.allowed_sources),
                "allowed_hydrogens": sorted(self.allowed_hydrogens),
                "require_receptor_for_pose": self.require_receptor_for_pose,
                "semantics": "fitness-to-simulate-not-fitness-to-bind",
            }
        )


def _validated_config(request: StageRequest) -> MdHandoffGateConfig:
    try:
        return MdHandoffGateConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid MD handoff gate configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _verdict(row: dict[str, Any], config: MdHandoffGateConfig) -> tuple[str, str]:
    """``(reason_code, message)`` for a row, or ``("", "")`` when it passes.

    One reason per row and the most fundamental one first: a structure that could
    not be read has nothing else worth saying about it, and a drawing's hydrogen
    state is not the interesting fact about it.
    """

    status = str(row.get("status") or "")
    if status != "OK":
        return (
            f"MD_HANDOFF_STATUS_{status or 'MISSING'}",
            f"the producer could not supply a usable structure (status {status!r})",
        )
    source = str(row.get("coordinate_source") or "")
    if source not in config.allowed_sources:
        if source == "TWO_D_DEPICTION":
            message = (
                "the coordinates are a two-dimensional depiction -- every z is zero "
                "and the layout is arbitrary. It will parameterise and simulate, and "
                "the result will describe a flat molecule at the molblock origin."
            )
        elif source == "NONE":
            message = (
                "this run published no pose and no conformer for the molecule, so "
                "there is no geometry to hand over"
            )
        else:
            message = (
                f"coordinate_source {source!r} is not among the accepted sources "
                f"({', '.join(sorted(config.allowed_sources))})"
            )
        return f"MD_HANDOFF_SOURCE_{source or 'MISSING'}", message
    hydrogens = str(row.get("hydrogens") or "")
    if hydrogens not in config.allowed_hydrogens:
        return (
            f"MD_HANDOFF_HYDROGENS_{hydrogens or 'MISSING'}",
            f"hydrogen state {hydrogens!r} is not among the accepted states "
            f"({', '.join(sorted(config.allowed_hydrogens))})",
        )
    if (
        config.require_receptor_for_pose
        and source == "DOCKED_POSE"
        and not row.get("receptor_id")
    ):
        return (
            "MD_HANDOFF_POSE_WITHOUT_RECEPTOR",
            "a docked pose names no receptor, so nothing records which structure it "
            "was scored in and the number computed from it is not comparable to the "
            "one that selected it",
        )
    return "", ""


def _detail(row: dict[str, Any], message: str) -> str:
    return json.dumps(
        {
            "message": message,
            "coordinate_source": row.get("coordinate_source"),
            "hydrogens": row.get("hydrogens"),
            "hydrogen_count": row.get("hydrogen_count"),
            "heavy_atom_count": row.get("heavy_atom_count"),
            "formal_charge": row.get("formal_charge"),
            "receptor_id": row.get("receptor_id"),
            "status": row.get("status"),
            "status_detail": row.get("status_detail"),
            "stereochemistry_differs_from_name": (
                None
                if not row.get("stereo_smiles")
                else row.get("stereo_smiles") != row.get("parent_smiles")
            ),
        },
        sort_keys=True,
    )


class MdHandoffGatePlugin:
    """Pass only structures fit to simulate, and record why each other one is not."""

    descriptor = PluginDescriptor(
        id="handoff.md_system_input_gate",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id, MD_SYSTEM_INPUT_V1.id),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="MD handoff fitness gate",
        description=(
            "Reject a structure that is not fit to simulate: a two-dimensional "
            "depiction, a record with no explicit hydrogens, a molecule the "
            "producer could not build, or a docked pose that names no receptor. "
            "Repairs nothing -- adding hydrogens or embedding coordinates are "
            "decisions with scientific content, not gate behaviour."
        ),
    )
    config_model = MdHandoffGateConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        parent_input, handoff_input = _classify_inputs(
            dict(request.inputs),
            evidence_contract=MD_SYSTEM_INPUT_V1,
            label="MD handoff gate",
        )
        policy_id = config.policy_id()

        handoff: dict[str, dict[str, Any]] = {}
        for batch in iter_contract_batches(
            handoff_input, MD_SYSTEM_INPUT_V1, batch_size=config.batch_size
        ):
            for row in batch.to_pylist():
                # First row wins per molecule: two handoff stages with different
                # method_ids both satisfy the contract's primary key, and blending
                # them would produce a verdict about neither.
                handoff.setdefault(str(row["parent_id"]), row)
            if len(handoff) > _MAX_HANDOFF_ROWS:
                raise PluginError(
                    f"the MD handoff carries more than {_MAX_HANDOFF_ROWS:,} molecules",
                    code="MD_HANDOFF_POPULATION_TOO_LARGE",
                    hint=(
                        "A handoff is the set of molecules about to be simulated, and "
                        "a simulation costs GPU-days each. Place this stage below the "
                        "shortlist selector rather than above it."
                    ),
                    context={"rows_seen": len(handoff)},
                )

        context.staging_root.mkdir(parents=True, exist_ok=True)
        for relative in (_PARENT_PATH, _DECISION_PATH):
            destination = context.staging_root / relative
            if destination.exists() or destination.is_symlink():
                raise PluginError(
                    f"gate output already exists: {relative.as_posix()}",
                    code="PLUGIN_STAGING_NOT_EMPTY",
                    context={"path": relative.as_posix()},
                )
            destination.parent.mkdir(parents=True, exist_ok=True)

        counters = {
            "input_count": 0,
            "retained_count": 0,
            "reject_count": 0,
            "decision_count": 0,
            "missing_evidence_count": 0,
            "stereochemistry_assigned_count": 0,
        }
        rejected_by: dict[str, int] = {}
        with (
            pq.ParquetWriter(
                context.staging_root / _PARENT_PATH, PARENT_V1.schema, compression="zstd"
            ) as parent_writer,
            pq.ParquetWriter(
                context.staging_root / _DECISION_PATH,
                DECISION_V1.schema,
                compression="zstd",
            ) as decision_writer,
        ):
            decisions: list[dict[str, Any]] = []

            def flush() -> None:
                if decisions:
                    decision_writer.write_table(
                        pa.Table.from_pylist(decisions, schema=DECISION_V1.schema)
                    )
                    decisions.clear()

            for batch in iter_contract_batches(
                parent_input, PARENT_V1, batch_size=config.batch_size
            ):
                rows = batch.to_pylist()
                counters["input_count"] += len(rows)
                retained: list[dict[str, Any]] = []
                for row in rows:
                    parent_id = str(row["parent_id"])
                    evidence = handoff.get(parent_id)
                    if evidence is None:
                        # Fail closed. A molecule with no handoff row has not been
                        # shown to be simulable, and the contract's own rule is that
                        # an absence must be explicit rather than read as a pass.
                        counters["missing_evidence_count"] += 1
                        reason = "MD_HANDOFF_EVIDENCE_ABSENT"
                        message = (
                            "no md_system_input row for this molecule, so nothing has "
                            "declared what its coordinates are"
                        )
                        detail = json.dumps({"message": message}, sort_keys=True)
                    else:
                        reason, message = _verdict(evidence, config)
                        detail = _detail(evidence, message or "fit to simulate")
                        if evidence.get("stereo_smiles") and evidence[
                            "stereo_smiles"
                        ] != evidence.get("parent_smiles"):
                            counters["stereochemistry_assigned_count"] += 1

                    if reason:
                        counters["reject_count"] += 1
                        rejected_by[reason] = rejected_by.get(reason, 0) + 1
                        decisions.append(
                            {
                                "entity_id": parent_id,
                                "entity_kind": "PARENT",
                                "stage_id": request.stage_id,
                                "outcome": "REJECT",
                                "reason_code": reason,
                                "rule_id": policy_id,
                                "detail": detail,
                            }
                        )
                    else:
                        counters["retained_count"] += 1
                        retained.append(row)
                        decisions.append(
                            {
                                "entity_id": parent_id,
                                "entity_kind": "PARENT",
                                "stage_id": request.stage_id,
                                "outcome": "PASS",
                                "reason_code": "MD_HANDOFF_FIT",
                                "rule_id": policy_id,
                                "detail": detail,
                            }
                        )
                    counters["decision_count"] += 1
                if retained:
                    parent_writer.write_table(
                        pa.Table.from_pylist(retained, schema=PARENT_V1.schema)
                    )
                if len(decisions) >= config.decision_buffer_size:
                    flush()
            flush()

        if counters["input_count"] == 0:
            raise PluginError(
                "the MD handoff gate input contains no parents",
                code="MD_HANDOFF_GATE_EMPTY_INPUT",
            )
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    (_PARENT_PATH.as_posix(),),
                    {"row_count": counters["retained_count"]},
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    (_DECISION_PATH.as_posix(),),
                    {"row_count": counters["decision_count"], "policy_id": policy_id},
                ),
            },
            metadata={
                **counters,
                "output_count": counters["retained_count"],
                "policy_id": policy_id,
                "allowed_sources": sorted(config.allowed_sources),
                "allowed_hydrogens": sorted(config.allowed_hydrogens),
                "require_receptor_for_pose": config.require_receptor_for_pose,
                "rejected_by_reason": dict(sorted(rejected_by.items())),
                "repairs_applied": False,
                "network_or_download_invoked_by_adapter": False,
            },
        )


__all__ = ["MdHandoffGateConfig", "MdHandoffGatePlugin"]
