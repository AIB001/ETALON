"""Configurable Mordred descriptor window gate.

The RDKit range gate in :mod:`molcascade.plugins.builtin.property_gate` covers the
eight properties every medicinal chemist already argues about.  This gate covers
the other sixteen hundred.  Mordred implements its own descriptor library --
ring-topology counts, Kier shape indices, autocorrelations, framework fractions --
so a project that wants to gate on "no more than one bridgehead atom" or "at least
two aromatic rings" can do it without anyone writing a bespoke plugin.

Two properties of Mordred drove the design here, and both are the kind of thing
that quietly corrupts a screen if it is not handled deliberately:

*Missing is normal, not exceptional.*  Mordred returns a
:class:`mordred.error.MissingValueBase` instance -- not an exception, not a NaN --
whenever a descriptor is undefined for an input.  ``Kier3`` is Missing for
ethanol.  ``Vabc`` and every Kier index are Missing for ``[Na+].[Cl-]`` because
they are undefined across disconnected fragments.  Coercing those to ``0.0`` and
comparing would silently invert the meaning of the filter: a ``Vabc >= 100``
window would reject every salt for the wrong reason, and a ``Vabc <= 500`` window
would pass every salt for the wrong reason.  So a molecule whose descriptor
cannot be computed is rejected with its own reason code.  It was never shown to
be inside the window, and a gate is not allowed to guess.

*Case is load-bearing.*  ``naRing`` counts aromatic rings and ``nARing`` counts
aliphatic rings.  They differ by one letter's case and mean opposite things.  A
typo does not error -- it silently applies the wrong filter to a million
molecules.  That is why the cascade catalogue offers these through a labelled
choice list rather than a free-text box, and why an unknown name here fails loudly
with near-miss suggestions instead of being ignored.

Mordred builds on RDKit for molecule handling, so this is a wider descriptor
library rather than an independent second opinion on the same numbers.  Where the
two do overlap -- ``SLogP``, ``TopoPSA``, ``nHBDon`` -- they agree, which is worth
knowing but is not a cross-check.

Citation: Moriwaki H, Tian Y-S, Kawashita N, Takagi T. Mordred: a molecular
descriptor calculator. J Cheminform. 2018;10:4. doi:10.1186/s13321-018-0258-y
"""

from __future__ import annotations

import difflib
import json
import math
import re
from pathlib import Path
from typing import Any, Self

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator, model_validator

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

#: Every name in Mordred's 2D registry matches this.  Verified against the
#: installed release by ``tests/plugins/test_mordred_gate.py``; the point is to
#: reject junk before it reaches the calculator, not to be clever.
_DESCRIPTOR_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_()\-.]{0,63}")

#: A ceiling, not a recommendation.  Mordred costs roughly 0.4 ms per descriptor
#: per molecule, so 32 windows is about 15 ms/molecule -- four hours per million
#: on one core.  Anything past that belongs in a featurizer, not a gate.
_MAX_WINDOWS = 32

#: Turned into ``REJECT`` reason codes.  Mordred's own uppercase name would be
#: nicer, but the classes are not part of its documented API surface.
_UNAVAILABLE_SUFFIX = "UNAVAILABLE"
_NON_FINITE_SUFFIX = "NON_FINITE"


def _reason_code(descriptor: str, suffix: str) -> str:
    """Build a stable, greppable reason code from a Mordred descriptor name.

    Mordred names carry parentheses and hyphens (``TopoPSA(NO)``, ``AETA_beta_ns_d``)
    which read badly in a reason code, so they collapse to underscores.  The
    collapse is not injective -- ``TopoPSA(NO)`` and ``TopoPSA_NO_`` would produce
    the same code -- which is why the descriptor's exact name is also written into
    the decision ``detail`` field, where nothing is lost.
    """

    slug = re.sub(r"[^A-Za-z0-9]+", "_", descriptor).strip("_").upper()
    return f"MORDRED_{slug}_{suffix}"


class MordredWindow(StrictFrozenModel):
    """One descriptor and the inclusive band a molecule has to land in."""

    descriptor: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Mordred 2D descriptor name, exactly as Mordred spells it. Case is "
            "significant: 'naRing' counts aromatic rings, 'nARing' counts "
            "aliphatic ones."
        ),
    )
    minimum: float | None = Field(default=None, description="Inclusive lower bound.")
    maximum: float | None = Field(default=None, description="Inclusive upper bound.")

    @model_validator(mode="after")
    def _usable_window(self) -> Self:
        if not _DESCRIPTOR_NAME.fullmatch(self.descriptor):
            raise ValueError(
                f"{self.descriptor!r} is not a syntactically valid Mordred descriptor name"
            )
        for name in ("minimum", "maximum"):
            bound = getattr(self, name)
            if bound is not None and not math.isfinite(bound):
                raise ValueError(f"{name} must be a finite number")
        if self.minimum is None and self.maximum is None:
            # A window with no bounds cannot reject anything, so it is either a
            # mistake or an attempt to record a descriptor.  Recording belongs in
            # a featurizer stage, where the number lands in the artifact instead
            # of being computed and thrown away.
            raise ValueError(
                f"window on {self.descriptor!r} sets neither minimum nor maximum, "
                "so it can never reject anything"
            )
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError(
                f"window on {self.descriptor!r} has minimum greater than maximum, "
                "so it can never accept anything"
            )
        return self


class MordredDescriptorGateConfig(StrictFrozenModel):
    """One or more descriptor windows a molecule must satisfy simultaneously.

    A hand-written config lists ``windows`` outright.  The HTML builder cannot --
    its threshold fields are flat name/value pairs with no nesting -- so it writes
    the single-window shorthand ``{"descriptor": ..., "minimum": ..., "maximum":
    ...}`` instead.  That is not a workaround grafted on: a tier already composes
    several criterion blocks with an explicit serial or parallel join, so one
    descriptor per block is the spelling that makes the assembled flow and the
    executed flow the same shape.  Mixing the two forms is rejected rather than
    merged, because a config that says both things is a config whose author was
    not sure which one they meant.
    """

    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    windows: tuple[MordredWindow, ...] = Field(
        min_length=1,
        max_length=_MAX_WINDOWS,
        description="Descriptor windows a molecule must satisfy simultaneously.",
    )

    @field_validator("windows", mode="before")
    @classmethod
    def _arrays_to_tuples(cls, value: Any) -> Any:
        # Strict mode does not coerce, and JSON has no tuples.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="before")
    @classmethod
    def _accept_single_window_shorthand(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        shorthand = {key for key in ("descriptor", "minimum", "maximum") if key in data}
        if not shorthand:
            return data
        if "windows" in data:
            raise ValueError(
                "set either 'windows' or the single-window shorthand "
                f"({', '.join(sorted(shorthand))}), not both"
            )
        if "descriptor" not in data:
            raise ValueError(
                "the single-window shorthand needs 'descriptor' alongside "
                f"{', '.join(sorted(shorthand))}"
            )
        promoted = dict(data)
        window = {"descriptor": promoted.pop("descriptor")}
        for bound in ("minimum", "maximum"):
            if bound in promoted:
                window[bound] = promoted.pop(bound)
        promoted["windows"] = [window]
        return promoted

    @model_validator(mode="after")
    def _distinct_descriptors(self) -> Self:
        seen: set[str] = set()
        for window in self.windows:
            if window.descriptor in seen:
                # Two windows on one descriptor are either contradictory or
                # redundant, and the intersection the user meant is expressible
                # as a single window.  Refusing is cheaper than guessing.
                raise ValueError(f"descriptor {window.descriptor!r} is windowed more than once")
            seen.add(window.descriptor)
        return self


def _resolve(requested: tuple[str, ...]) -> tuple[Any, dict[str, Any]]:
    """Look the requested names up in Mordred's live 2D registry.

    Returns the calculator and a name-to-instance map.  Unknown names raise
    rather than being skipped: a gate that silently drops a filter is worse
    than one that refuses to start, because the run still produces a plausible
    looking artifact.
    """

    from mordred import Calculator
    from mordred import descriptors as mordred_descriptors

    registry = Calculator(mordred_descriptors, ignore_3D=True)
    available = {str(item): item for item in registry.descriptors}
    unknown = [name for name in requested if name not in available]
    if unknown:
        # Case-only collisions come first because they are the ones that would
        # have silently applied the opposite filter had Mordred been lenient;
        # edit-distance neighbours follow, which is what catches a plausible
        # invention like "nAromRing" for the real "naRing".
        folded_index: dict[str, list[str]] = {}
        for candidate in available:
            folded_index.setdefault(candidate.casefold(), []).append(candidate)
        suggestions: dict[str, list[str]] = {}
        for name in unknown:
            folded = name.casefold()
            near: list[str] = sorted(folded_index.get(folded, ()))
            for match in difflib.get_close_matches(folded, folded_index, n=8, cutoff=0.6):
                near.extend(
                    candidate for candidate in sorted(folded_index[match]) if candidate not in near
                )
            if near:
                suggestions[name] = near[:8]
        raise PluginError(
            "unknown Mordred descriptor(s): " + ", ".join(sorted(unknown)),
            code="MORDRED_DESCRIPTOR_UNKNOWN",
            context={
                "unknown": sorted(unknown),
                "registry_size": len(available),
                # Case-only near-misses are the dangerous ones (naRing vs
                # nARing), so they are surfaced first.
                "did_you_mean": {key: value for key, value in sorted(suggestions.items())},
            },
        )
    selected = [available[name] for name in requested]
    return Calculator(selected), available


def _mordred_version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as package_version

    try:
        return package_version("mordredcommunity")
    except PackageNotFoundError:  # pragma: no cover - the original package
        return package_version("mordred")


def _policy_id(
    config: MordredDescriptorGateConfig, *, backend_version: str, rdkit_version: str
) -> str:
    policy = config.model_dump(mode="json")
    policy.pop("batch_size", None)
    return "mordred-gate-policy:sha256:" + canonical_sha256(
        {
            "backend": "mordred",
            "backend_version": backend_version,
            # Mordred delegates molecule handling to RDKit, so an RDKit upgrade
            # can move these numbers even when Mordred does not.
            "rdkit_version": rdkit_version,
            "policy": policy,
        }
    )


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Window one contiguous range of parents in whichever process owns it."""

    from mordred.error import MissingValueBase
    from rdkit import Chem, rdBase

    config = MordredDescriptorGateConfig.model_validate(dict(task.config))
    names = tuple(window.descriptor for window in config.windows)
    # Rebuilt per shard: a Mordred ``Calculator`` holds descriptor instances
    # that do not cross a process boundary, and resolving names is cheap next
    # to evaluating them.
    calculator, _ = _resolve(names)
    policy_id = _policy_id(
        config, backend_version=_mordred_version(), rdkit_version=rdBase.rdkitVersion
    )

    input_count = 0
    passed_count = 0
    decision_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parents,
        pq.ParquetWriter(
            task.output_paths["decisions"], DECISION_V1.schema, compression="zstd"
        ) as decisions,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            passed: list[dict[str, Any]] = []
            decision_rows: list[dict[str, Any]] = []
            for row in batch.to_pylist():
                input_count += 1
                parent_id = row.get("parent_id")
                smiles = row.get("parent_smiles")
                molecule = Chem.MolFromSmiles(smiles if isinstance(smiles, str) else "")
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed by the Mordred gate",
                        code="MORDRED_GATE_PARENT_INVALID",
                        context={"parent_id": str(parent_id)},
                    )
                computed = calculator(molecule).asdict()
                findings: list[tuple[str, str]] = []
                recorded: dict[str, float] = {}
                for window in config.windows:
                    value = computed.get(window.descriptor)
                    if isinstance(value, MissingValueBase):
                        findings.append(
                            (
                                _reason_code(window.descriptor, _UNAVAILABLE_SUFFIX),
                                f"{window.descriptor} is undefined for this molecule "
                                f"({value!s}); it cannot be shown to satisfy the window",
                            )
                        )
                        continue
                    number = float(value)
                    if not math.isfinite(number):
                        findings.append(
                            (
                                _reason_code(window.descriptor, _NON_FINITE_SUFFIX),
                                f"{window.descriptor} evaluated to {number!r}",
                            )
                        )
                        continue
                    recorded[window.descriptor] = number
                    if window.minimum is not None and number < window.minimum:
                        findings.append(
                            (
                                _reason_code(window.descriptor, "BELOW_MIN"),
                                f"{window.descriptor}={number!r} is below {window.minimum!r}",
                            )
                        )
                    if window.maximum is not None and number > window.maximum:
                        findings.append(
                            (
                                _reason_code(window.descriptor, "ABOVE_MAX"),
                                f"{window.descriptor}={number!r} is above {window.maximum!r}",
                            )
                        )
                if findings:
                    for reason_code, detail in findings:
                        decision_rows.append(
                            {
                                "entity_id": parent_id,
                                "entity_kind": "PARENT",
                                "stage_id": task.stage_id,
                                "outcome": "REJECT",
                                "reason_code": reason_code,
                                "rule_id": policy_id,
                                "detail": detail,
                            }
                        )
                else:
                    passed.append(row)
                    passed_count += 1
                    decision_rows.append(
                        {
                            "entity_id": parent_id,
                            "entity_kind": "PARENT",
                            "stage_id": task.stage_id,
                            "outcome": "PASS",
                            "reason_code": "MORDRED_WINDOW_PASS",
                            "rule_id": policy_id,
                            "detail": json.dumps(
                                recorded,
                                ensure_ascii=False,
                                allow_nan=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        }
                    )
                decision_count += max(1, len(findings))
            if passed:
                parents.write_table(pa.Table.from_pylist(passed, schema=PARENT_V1.schema))
            decisions.write_table(
                pa.Table.from_pylist(decision_rows, schema=DECISION_V1.schema)
            )
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": passed_count, "decisions": decision_count},
    )


class MordredDescriptorGatePlugin:
    descriptor = PluginDescriptor(
        id="chemistry.mordred_descriptor_gate",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="Mordred descriptor window gate",
        description=(
            "Windows on any of Mordred's 2D descriptors -- ring topology, Kier shape, "
            "framework fraction -- with a rejection for anything it cannot compute."
        ),
    )
    config_model = MordredDescriptorGateConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        try:
            config = self.config_model.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid Mordred gate configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"error_count": error.error_count()},
            ) from error

        names = tuple(window.descriptor for window in config.windows)
        # Resolved in the parent as well, so an unknown descriptor name stops
        # the stage before a shard is scheduled rather than in every worker.
        _resolve(names)

        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        mordred_version = _mordred_version()
        policy_id = _policy_id(
            config, backend_version=mordred_version, rdkit_version=rdBase.rdkitVersion
        )

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
        input_count = result.rows_in
        if input_count == 0:
            raise PluginError(
                "mordred-gate input contains no parents",
                code="MORDRED_GATE_EMPTY_INPUT",
            )
        passed_count = result.rows_out.get("primary", 0)
        decision_count = result.rows_out.get("decisions", 0)
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": passed_count},
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    result.file_paths["decisions"],
                    {"row_count": decision_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": passed_count,
                "reject_count": input_count - passed_count,
                "decision_count": decision_count,
                "policy_id": policy_id,
                "backend": "mordred",
                "backend_version": mordred_version,
                "rdkit_version": rdBase.rdkitVersion,
                "descriptors": list(names),
                **result.response_metadata(),
            },
        )


__all__ = [
    "MordredDescriptorGateConfig",
    "MordredDescriptorGatePlugin",
    "MordredWindow",
]
