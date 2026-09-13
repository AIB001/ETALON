"""Lower a tier-first cascade into the executable flat pipeline.

The cascade is what a user edits; the pipeline is what the compiler, cache,
artifact store, and runner already understand.  Lowering is deliberately total
and deterministic: the same cascade and the same registry always produce the
same stage list, in the same order, with the same generated stage identifiers,
so the pipeline revision identity stays stable across regenerations.

All wiring that the old node canvas asked users to draw by hand is derived here
from three facts: the tier order, the tier mode, and each plugin's declared
contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast

from molcascade.cascade.introspect import with_schema_version
from molcascade.cascade.library import resolve_library
from molcascade.cascade.models import (
    CascadeConfig,
    CriterionConfig,
    LibraryFormat,
    StepConfig,
    TargetConfig,
    TierConfig,
    TierMode,
)
from molcascade.cascade.target import placeholder_target
from molcascade.config.models import PipelineConfig
from molcascade.contracts import DECISION_V1, PARENT_V1
from molcascade.errors import ConfigError
from molcascade.plugins.manifest import PluginDescriptor, PluginKind
from molcascade.plugins.registry import PluginRegistry, create_builtin_registry

DECISION_JOIN_PLUGIN = "policy.native_decision_join@0.1.0"

# The compiler checks that a stage's slot matches its plugin's kind.  Users of a
# cascade never type a slot, so the canonical name is derived here.
_KIND_SLOTS: Mapping[PluginKind, str] = MappingProxyType(
    {
        PluginKind.SOURCE: "source",
        PluginKind.STANDARDIZER: "standardize",
        PluginKind.GATE: "gate",
        PluginKind.FEATURIZER: "featurize",
        PluginKind.TRAINER: "train",
        PluginKind.PREDICTOR: "predict",
        PluginKind.APPLICABILITY: "applicability",
        PluginKind.SYNTHESIS: "synthesis",
        PluginKind.DOCK: "dock",
        PluginKind.SCAFFOLDER: "scaffold",
        PluginKind.CLUSTERER: "cluster",
        PluginKind.SELECTOR: "select",
        PluginKind.ENUMERATOR: "enumerate",
        PluginKind.EXPORTER: "export",
    }
)


@dataclass(frozen=True, slots=True)
class StageOrigin:
    """Where a generated stage came from, for messages and the UI."""

    stage_id: str
    role: str
    tier_id: str | None = None
    criterion_id: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class LoweredCascade:
    """A cascade rendered as an executable pipeline plus its provenance map."""

    pipeline: PipelineConfig
    library_path: str
    origins: tuple[StageOrigin, ...] = field(default_factory=tuple)
    library_is_placeholder: bool = False
    target_is_placeholder: bool = False

    def origin_of(self, stage_id: str) -> StageOrigin | None:
        return next((origin for origin in self.origins if origin.stage_id == stage_id), None)


def _entry(registry: PluginRegistry, backend: str, *, where: str) -> Any:
    try:
        return registry.entry(backend)
    except Exception as error:  # registry raises its own typed error
        raise ConfigError(
            f"{where} uses backend {backend!r} which is not a registered executable plugin",
            code="CASCADE_BACKEND_UNKNOWN",
            context={"backend": backend, "where": where},
        ) from error


def _descriptor(registry: PluginRegistry, backend: str, *, where: str) -> PluginDescriptor:
    descriptor = _entry(registry, backend, where=where).descriptor
    return cast(PluginDescriptor, descriptor)


def _slot_for(descriptor: PluginDescriptor) -> str:
    slot = _KIND_SLOTS.get(descriptor.kind)
    if slot is None:  # pragma: no cover - PluginKind is exhaustive above
        raise ConfigError(
            f"plugin {descriptor.key!r} has no cascade slot for kind {descriptor.kind.value!r}",
            code="CASCADE_SLOT_UNKNOWN",
            context={"backend": descriptor.key, "kind": descriptor.kind.value},
        )
    return slot


def _port_for_contract(descriptor: PluginDescriptor, contract_id: str) -> str | None:
    return next(
        (port for port, contract in descriptor.output_ports.items() if contract == contract_id),
        None,
    )


def _binding(request_port: str, stage: str, port: str = "primary") -> dict[str, str]:
    return {"request_port": request_port, "stage": stage, "port": port}


def _evidence_port_name(contract_id: str) -> str:
    """Name the request port after the contract the binding carries.

    The name is documentation rather than routing -- plugins classify their
    inputs by contract -- so deriving it keeps generated pipelines readable
    without inventing a second identifier that could drift from the contract.
    """

    return contract_id.rsplit("/", 1)[0].replace(".", "_").replace("-", "_")


class _StageAccumulator:
    """Collect stages in execution order while tracking the current population."""

    def __init__(self) -> None:
        self.stages: list[dict[str, Any]] = []
        self.origins: list[StageOrigin] = []
        self.kinds: set[PluginKind] = set()
        self._producers: dict[str, list[tuple[str, str]]] = {}

    def add(
        self,
        *,
        stage_id: str,
        descriptor: PluginDescriptor,
        settings: Mapping[str, Any],
        inputs: list[dict[str, str]],
        origin: StageOrigin,
    ) -> None:
        self.stages.append(
            {
                "id": stage_id,
                "slot": _slot_for(descriptor),
                "plugin": descriptor.key,
                "inputs": inputs,
                "config": dict(settings),
                "enabled": True,
            }
        )
        self.origins.append(origin)
        self.kinds.add(descriptor.kind)
        for port, contract in descriptor.output_ports.items():
            history = self._producers.setdefault(contract, [])
            if history and history[-1][0] == stage_id:
                # One stage naming the same contract on two ports keeps only its
                # last port, exactly as the single-producer map did before this
                # became a list.  Ambiguity between *stages* is the new error;
                # ambiguity inside one stage is unchanged behaviour.
                history[-1] = (stage_id, port)
            else:
                history.append((stage_id, port))

    def producers(self) -> dict[str, tuple[tuple[str, str], ...]]:
        """Snapshot every stage and port that has produced each contract, in order.

        The whole history rather than the latest entry, because a contract with
        two producers upstream is a question the cascade has to answer out loud
        rather than a race the last writer wins.  ``_evidence_bindings`` reads
        the last entry when there is only one, which is every pipeline that
        lowered before this returned a list.
        """

        return {contract: tuple(history) for contract, history in self._producers.items()}


def _evidence_bindings(
    descriptor: PluginDescriptor,
    *,
    producers: Mapping[str, tuple[tuple[str, str], ...]],
    backend: str,
    where: str,
    evidence_from: Mapping[str, str] = MappingProxyType({}),
) -> list[dict[str, str]]:
    """Bind every side contract a stage consumes to the stage that produces it.

    A criterion reads the population on its ``primary`` port.  Anything else it
    declares -- 3D conformers shared by several docking engines, a fingerprint
    table shared by several similarity criteria -- is precomputed by an earlier
    stage, and binding it here is what lets one expensive computation feed
    several consumers instead of being repeated per consumer.

    Decisions are excluded on purpose: they are wired by the tier's policy join,
    which is the only place allowed to decide what a decision stream means.

    When two earlier stages produce the same contract the binding is genuinely
    undecidable and the cascade refuses to lower.  This matters more than it
    reads: a tier that docks with two engines emits ``docking_score/v1`` twice,
    each stage's table holding only its own engine's rows, so silently taking
    one of them hands a downstream consumer a table where none of the rows it
    expects exist.  That failure surfaces as evidence which is uniformly
    missing -- a gate that appears merely strict -- rather than as a wiring
    mistake, which is why it is caught here at lowering time instead.
    ``CriterionConfig.evidence_from`` is how a criterion says which one it
    means, and it says so in the exported plan where a reader can audit it.
    """

    bindings: list[dict[str, str]] = []
    for contract_id in descriptor.inputs:
        if contract_id in {PARENT_V1.id, DECISION_V1.id}:
            continue
        candidates = producers.get(contract_id, ())
        if not candidates:
            raise ConfigError(
                f"{where} uses {backend!r}, which needs {contract_id!r} evidence that no "
                "earlier stage produces",
                code="CASCADE_CRITERION_EVIDENCE_UNAVAILABLE",
                hint=(
                    "Add the stage that produces this contract above this tier, or choose "
                    "a backend that computes the evidence itself."
                ),
                context={
                    "backend": backend,
                    "where": where,
                    "missing_contract": contract_id,
                    "available_contracts": sorted(producers),
                },
            )
        pinned = evidence_from.get(contract_id)
        if pinned is not None:
            chosen = next((entry for entry in reversed(candidates) if entry[0] == pinned), None)
            if chosen is None:
                raise ConfigError(
                    f"{where} pins {contract_id!r} evidence to stage {pinned!r}, which does "
                    "not produce that contract above this tier",
                    code="CASCADE_CRITERION_EVIDENCE_SOURCE_UNKNOWN",
                    hint=(
                        "Name one of the stages that does produce it, and check the stage "
                        "id rather than the criterion label -- a criterion with a gate "
                        "lowers to more than one stage."
                    ),
                    context={
                        "backend": backend,
                        "where": where,
                        "contract": contract_id,
                        "requested_stage": pinned,
                        "candidate_stages": [stage for stage, _ in candidates],
                    },
                )
        elif len(candidates) > 1:
            raise ConfigError(
                f"{where} uses {backend!r}, which needs {contract_id!r} evidence that "
                f"{len(candidates)} earlier stages produce",
                code="CASCADE_CRITERION_EVIDENCE_AMBIGUOUS",
                hint=(
                    "Set this criterion's 'evidence_from' to the stage you mean, for "
                    f'example {{"{contract_id}": "{candidates[0][0]}"}}. Each producing '
                    "stage holds only its own rows, so binding the wrong one leaves the "
                    "evidence empty rather than merely different."
                ),
                context={
                    "backend": backend,
                    "where": where,
                    "contract": contract_id,
                    "candidate_stages": [stage for stage, _ in candidates],
                },
            )
        else:
            chosen = candidates[-1]
        bindings.append(_binding(_evidence_port_name(contract_id), chosen[0], chosen[1]))
    return bindings


def _lower_criterion(
    criterion: CriterionConfig,
    *,
    tier: TierConfig,
    registry: PluginRegistry,
    accumulator: _StageAccumulator,
    parent_stage: str,
    parent_port: str,
    producers: Mapping[str, tuple[tuple[str, str], ...]],
    target: TargetConfig | None = None,
) -> tuple[str, str, tuple[str, str] | None]:
    """Emit a criterion's producer and optional gate.

    Returns the stage/port carrying the surviving population after this
    criterion plus the stage/port carrying its ``decision/v1`` stream, if any.
    """

    where = f"criterion {criterion.id!r} in tier {tier.id!r}"
    entry = _entry(registry, criterion.backend, where=where)
    producer = cast(PluginDescriptor, entry.descriptor)
    if PARENT_V1.id not in producer.inputs:
        raise ConfigError(
            f"{where} uses {criterion.backend!r}, which does not accept a parent population",
            code="CASCADE_CRITERION_NOT_APPLICABLE",
            context={"backend": criterion.backend, "where": where},
        )
    inputs = [_binding("primary", parent_stage, parent_port)]
    inputs.extend(
        _evidence_bindings(
            producer,
            producers=producers,
            evidence_from=criterion.evidence_from,
            backend=criterion.backend,
            where=where,
        )
    )
    accumulator.add(
        stage_id=criterion.id,
        descriptor=producer,
        settings=_target_settings(entry, criterion.settings, target),
        inputs=inputs,
        origin=StageOrigin(
            stage_id=criterion.id,
            role="criterion",
            tier_id=tier.id,
            criterion_id=criterion.id,
            label=criterion.label or producer.display_name,
        ),
    )
    survivor_port = _port_for_contract(producer, PARENT_V1.id)
    if survivor_port is None:
        raise ConfigError(
            f"{where} uses {criterion.backend!r}, which does not emit a parent population",
            code="CASCADE_CRITERION_NO_POPULATION",
            context={"backend": criterion.backend, "where": where},
        )

    if criterion.gate is None:
        decision_port = _port_for_contract(producer, DECISION_V1.id)
        decision = (criterion.id, decision_port) if decision_port else None
        return criterion.id, survivor_port, decision

    gate_where = f"threshold of {where}"
    gate = _descriptor(registry, criterion.gate.backend, where=gate_where)
    evidence_contracts = sorted(
        set(gate.inputs) & set(producer.outputs) - {PARENT_V1.id, DECISION_V1.id}
    )
    if len(evidence_contracts) != 1:
        raise ConfigError(
            f"{gate_where} cannot bind: {criterion.backend!r} and {criterion.gate.backend!r} "
            f"share {len(evidence_contracts)} evidence contracts, expected exactly one",
            code="CASCADE_GATE_EVIDENCE_AMBIGUOUS",
            context={
                "producer": criterion.backend,
                "gate": criterion.gate.backend,
                "shared_contracts": evidence_contracts,
            },
        )
    evidence_port = _port_for_contract(producer, evidence_contracts[0])
    gate_decision_port = _port_for_contract(gate, DECISION_V1.id)
    if gate_decision_port is None:
        raise ConfigError(
            f"{gate_where} uses {criterion.gate.backend!r}, which emits no decisions",
            code="CASCADE_GATE_NO_DECISIONS",
            context={"gate": criterion.gate.backend},
        )
    gate_survivor_port = _port_for_contract(gate, PARENT_V1.id)
    if gate_survivor_port is None:
        raise ConfigError(
            f"{gate_where} uses {criterion.gate.backend!r}, which emits no parent population",
            code="CASCADE_GATE_NO_POPULATION",
            context={"gate": criterion.gate.backend},
        )
    assert evidence_port is not None  # guaranteed by the shared-contract check above
    accumulator.add(
        stage_id=criterion.gate_stage_id,
        descriptor=gate,
        settings=criterion.gate.settings,
        inputs=[
            _binding("parents", criterion.id, survivor_port),
            _binding("evidence", criterion.id, evidence_port),
        ],
        origin=StageOrigin(
            stage_id=criterion.gate_stage_id,
            role="threshold",
            tier_id=tier.id,
            criterion_id=criterion.id,
            label=f"{criterion.label or producer.display_name} threshold",
        ),
    )
    return (
        criterion.gate_stage_id,
        gate_survivor_port,
        (criterion.gate_stage_id, gate_decision_port),
    )


def _lower_tier(
    tier: TierConfig,
    *,
    registry: PluginRegistry,
    accumulator: _StageAccumulator,
    anchor: tuple[str, str],
    target: TargetConfig | None = None,
) -> tuple[str, str]:
    criteria = tier.active_criteria
    if not criteria:
        return anchor

    if tier.mode is TierMode.SERIAL:
        stage, port = anchor
        for criterion in criteria:
            stage, port, _ = _lower_criterion(
                criterion,
                tier=tier,
                registry=registry,
                accumulator=accumulator,
                parent_stage=stage,
                parent_port=port,
                producers=accumulator.producers(),
                target=target,
            )
        return stage, port

    # Parallel modes: every criterion reads the population entering the tier so
    # that no criterion can hide evidence from another by filtering first.  The
    # same rule applies to side evidence, so the producer map is frozen before
    # the loop and a criterion can never bind a sibling in its own tier.
    visible = accumulator.producers()
    decisions: list[tuple[str, str, str]] = []
    survivors: list[tuple[str, str]] = []
    for criterion in criteria:
        stage, port, decision = _lower_criterion(
            criterion,
            tier=tier,
            registry=registry,
            accumulator=accumulator,
            parent_stage=anchor[0],
            parent_port=anchor[1],
            producers=visible,
            target=target,
        )
        survivors.append((stage, port))
        if decision is None:
            raise ConfigError(
                f"criterion {criterion.id!r} in parallel tier {tier.id!r} produces no decision; "
                "give it a threshold or move it to a serial tier",
                code="CASCADE_PARALLEL_CRITERION_WITHOUT_DECISION",
                context={"tier": tier.id, "criterion": criterion.id},
            )
        decisions.append((criterion.id, decision[0], decision[1]))

    if len(decisions) == 1:
        # One criterion combined with ALL/ANY is that criterion.  Emitting a
        # single-input join would only add a stage without changing survivors.
        return survivors[0]

    join = _descriptor(registry, DECISION_JOIN_PLUGIN, where=f"tier {tier.id!r}")
    settings: dict[str, Any] = {"mode": tier.mode.join_mode}
    if tier.mode is TierMode.AT_LEAST:
        settings["min_pass_count"] = tier.minimum_passes
    settings = with_schema_version(DECISION_JOIN_PLUGIN, settings, registry=registry)
    inputs = [_binding("parents", anchor[0], anchor[1])]
    inputs.extend(
        _binding(f"decision_{criterion_id}", stage_id, port)
        for criterion_id, stage_id, port in decisions
    )
    accumulator.add(
        stage_id=tier.join_stage_id,
        descriptor=join,
        settings=settings,
        inputs=inputs,
        origin=StageOrigin(
            stage_id=tier.join_stage_id,
            role="policy",
            tier_id=tier.id,
            label=f"{tier.title} policy",
        ),
    )
    join_port = _port_for_contract(join, PARENT_V1.id) or "primary"
    return tier.join_stage_id, join_port


def _lower_step(
    step: StepConfig,
    *,
    role: str,
    registry: PluginRegistry,
    accumulator: _StageAccumulator,
    anchor: tuple[str, str] | None,
    settings: Mapping[str, Any] | None = None,
    target: TargetConfig | None = None,
) -> tuple[str, str]:
    entry = _entry(registry, step.backend, where=f"{role} step {step.id!r}")
    descriptor = cast(PluginDescriptor, entry.descriptor)
    inputs = [] if anchor is None else [_binding("primary", anchor[0], anchor[1])]
    accumulator.add(
        stage_id=step.id,
        descriptor=descriptor,
        settings=_target_settings(
            entry,
            step.settings if settings is None else settings,
            target,
        ),
        inputs=inputs,
        origin=StageOrigin(stage_id=step.id, role=role, label=descriptor.display_name),
    )
    return step.id, _port_for_contract(descriptor, PARENT_V1.id) or "primary"


def lower_cascade(
    cascade: CascadeConfig,
    *,
    registry: PluginRegistry | None = None,
    library_path: str | None = None,
    library_format: LibraryFormat | None = None,
    allow_missing_library: bool = False,
    allow_missing_target: bool = False,
    target: TargetConfig | None = None,
) -> LoweredCascade:
    """Render a cascade as an ordered, compilable :class:`PipelineConfig`.

    Set ``allow_missing_library`` to compile a cascade that names no library --
    the shape of the funnel is checkable long before a file is chosen.  The
    result is marked as carrying a placeholder path and must not be executed.

    ``allow_missing_target`` is the same courtesy for the protein, and needed for
    the same reason: the receptor arrives on the command line, so a docking
    cascade on disk normally carries no target block at all, and without this the
    compiler's per-stage validation would make every such file look broken.  The
    result is marked as carrying a placeholder target and must not be executed;
    a run is refused, by stage and by flag, in
    :func:`~molcascade.backends.preflight.preflight_docking_target`.

    ``target`` overrides the cascade's own target block, which is how the run-
    time flags (``--receptor`` and friends) reach the docking stages without the
    cascade file on disk having to be rewritten first.
    """

    active_registry = registry or create_builtin_registry()
    accumulator = _StageAccumulator()
    active_target = cascade.target if target is None else target
    target_is_placeholder = False
    if active_target is None and allow_missing_target:
        active_target = placeholder_target(cascade, registry=active_registry)
        # None when nothing in the cascade docks, which is not a placeholder --
        # there was nothing to stand in for.
        target_is_placeholder = active_target is not None

    source_descriptor = _descriptor(
        active_registry, cascade.ingest.backend, where="the ingest step"
    )
    resolved = resolve_library(
        cascade.library,
        override_path=library_path,
        override_format=library_format,
        base_settings=cascade.ingest.settings,
        base_plugin=source_descriptor.key,
        allow_placeholder=allow_missing_library,
    )
    if resolved.plugin != source_descriptor.key:
        source_descriptor = _descriptor(active_registry, resolved.plugin, where="the ingest step")
    accumulator.add(
        stage_id=cascade.ingest.id,
        descriptor=source_descriptor,
        settings=resolved.settings,
        inputs=[],
        origin=StageOrigin(stage_id=cascade.ingest.id, role="ingest", label="Molecule library"),
    )
    anchor: tuple[str, str] = (cascade.ingest.id, "primary")

    if cascade.standardize is not None and cascade.standardize.enabled:
        anchor = _lower_step(
            cascade.standardize,
            role="standardize",
            registry=active_registry,
            accumulator=accumulator,
            anchor=anchor,
            target=active_target,
        )

    for tier in cascade.tiers:
        if not tier.enabled:
            continue
        anchor = _lower_tier(
            tier,
            registry=active_registry,
            accumulator=accumulator,
            anchor=anchor,
            target=active_target,
        )

    for step in cascade.finalize.steps:
        if not step.enabled:
            continue
        settings = _finalize_settings(step, cascade, registry=active_registry)
        anchor = _lower_step(
            step,
            role="finalize",
            registry=active_registry,
            accumulator=accumulator,
            anchor=anchor,
            settings=settings,
            target=active_target,
        )

    pipeline = PipelineConfig.model_validate(
        {
            "schema_version": 1,
            "name": cascade.name,
            "description": cascade.description,
            "stages": accumulator.stages,
            "metadata": _pipeline_metadata(
                cascade,
                resolved.path,
                docking_included=PluginKind.DOCK in accumulator.kinds,
            ),
        }
    )
    return LoweredCascade(
        pipeline=pipeline,
        library_path=resolved.path,
        origins=tuple(accumulator.origins),
        library_is_placeholder=resolved.is_placeholder,
        target_is_placeholder=target_is_placeholder,
    )


#: The marker field.  A plugin that declares somewhere to find a receptor is a
#: plugin that docks against one; nothing else in the tree declares it.  Keying
#: off a declared field rather than off :class:`PluginKind` means a future
#: preparation stage that needs the same protein is covered without being
#: reclassified, and a plugin that merely happens to have a ``name`` option is
#: never handed the target's name.
_TARGET_MARKER_FIELD = "receptor_path"


def _target_values(target: TargetConfig) -> dict[str, Any]:
    """Expand one target block into the fields the engines spell.

    Only the site definition the user actually gave is expanded.  A box that was
    derived from a reference ligand arrives here already expanded -- deriving it
    twice, once per engine, is how two engines end up searching two volumes.

    ``pocket_pdb_path`` accepts the generic ``pocket_path`` as its source
    because a pocket PDB supplied as *the site definition* and one supplied as
    *KarmaDock's override* are the same file used for the same purpose; the
    override wins when both are set, which is what makes it an override.
    """

    values: dict[str, Any] = {"receptor_path": target.receptor_path}
    if target.receptor_sha256 is not None:
        # Written only when pinned, so an unpinned target does not put a null in
        # the stage config and change its cache key for no scientific reason.
        values["receptor_sha256"] = target.receptor_sha256
    if target.box is not None:
        values.update(target.box.settings)
    if target.reference_ligand_path is not None:
        values["reference_ligand_path"] = target.reference_ligand_path
    if target.receptor_pdbqt_path is not None:
        values["receptor_pdbqt_path"] = target.receptor_pdbqt_path
    pocket = target.pocket_pdb_path or target.pocket_path
    if pocket is not None:
        values["pocket_pdb_path"] = pocket
    return values


def _target_settings(
    entry: Any,
    settings: Mapping[str, Any],
    target: TargetConfig | None,
) -> dict[str, Any]:
    """Push the cascade's single target into one docking stage's settings.

    The same "write only what the plugin declares" rule ``_finalize_settings``
    uses, and for a stronger reason: three engines in one tier that each carried
    their own receptor could be pointed at three different proteins, and their
    consensus would be a comparison between numbers that are not about the same
    thing.  The target therefore *overrides* whatever the stage settings said --
    a per-engine receptor is not a customisation, it is a disagreement.

    A missing target is not an error here.  A cascade is authored, validated and
    exported long before anyone chooses a protein, and refusing to lower one
    would make the docking tier unauthorable.  The requirement is enforced
    against the stages that need it, at the point a run is about to start, by
    :func:`molcascade.backends.preflight.preflight_docking_target`.
    """

    resolved = dict(settings)
    if target is None:
        return resolved
    config_model = getattr(entry.plugin, "config_model", None)
    fields = set(getattr(config_model, "model_fields", {}))
    if _TARGET_MARKER_FIELD not in fields:
        return resolved
    for name, value in _target_values(target).items():
        if name in fields:
            resolved[name] = value
    return resolved


def _finalize_settings(
    step: StepConfig,
    cascade: CascadeConfig,
    *,
    registry: PluginRegistry,
) -> dict[str, Any]:
    """Push the single authoritative shortlist size and seed into selectors.

    Only fields the selected plugin actually declares are written, so a
    third-party selector without a ``seed`` option is not handed one.
    """

    entry = _entry(registry, step.backend, where=f"finalize step {step.id!r}")
    settings = dict(step.settings)
    if entry.descriptor.kind is not PluginKind.SELECTOR:
        return settings
    config_model = getattr(entry.plugin, "config_model", None)
    fields = set(getattr(config_model, "model_fields", {}))
    if "target_count" in fields:
        settings["target_count"] = cascade.finalize.target_count
    if "seed" in fields:
        settings["seed"] = cascade.finalize.seed
    return settings


def _pipeline_metadata(
    cascade: CascadeConfig,
    library_path: str,
    *,
    docking_included: bool,
) -> dict[str, Any]:
    metadata = dict(cascade.metadata)
    metadata.update(
        {
            "cascade_name": cascade.name,
            "cascade_schema_version": cascade.schema_version,
            "final_parent_target": cascade.finalize.target_count,
            "random_seed": cascade.finalize.seed,
            "library_path": library_path,
            "docking_included": docking_included,
            "tiers": [
                {
                    "id": tier.id,
                    "title": tier.title,
                    "mode": tier.mode.value,
                    "minimum_passes": tier.minimum_passes,
                    "criteria": [criterion.id for criterion in tier.active_criteria],
                }
                for tier in cascade.tiers
                if tier.enabled and tier.active_criteria
            ],
        }
    )
    return metadata


__all__ = [
    "DECISION_JOIN_PLUGIN",
    "LoweredCascade",
    "StageOrigin",
    "lower_cascade",
]
