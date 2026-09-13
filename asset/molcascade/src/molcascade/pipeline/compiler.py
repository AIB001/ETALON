"""Compile immutable user configuration into an executable ordered pipeline.

Execution remains stage-ordered, but data flow is not restricted to adjacency:
an explicit input binding may retain any named output of an earlier enabled
stage.  When bindings are omitted, the compiler selects the most recent
compatible ``primary`` output.  Every selected port and exact versioned
contract is frozen into the compiled plan before plugin code can run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import BaseModel, JsonValue, ValidationError

from molcascade.config.models import PipelineConfig, StageConfig
from molcascade.errors import PipelineError, PluginError
from molcascade.pipeline.models import PipelineRevision
from molcascade.pipeline.revision import freeze_pipeline
from molcascade.plugins.api import StagePlugin
from molcascade.plugins.manifest import PluginDescriptor, PluginKind
from molcascade.plugins.registry import PluginRegistry

# Configuration slots are domain roles, not free-form aliases for plugin kinds.
# Keeping the relationship here makes a replacement plugin prove that it serves
# the same scientific role before contract compatibility is considered.  The
# mapping can be replaced explicitly by an embedding application when it adds a
# reviewed domain-specific slot.
DEFAULT_SLOT_KINDS: Mapping[str, PluginKind] = MappingProxyType(
    {
        "source": PluginKind.SOURCE,
        "standardize": PluginKind.STANDARDIZER,
        "standardizer": PluginKind.STANDARDIZER,
        "hard_gate": PluginKind.GATE,
        "gate": PluginKind.GATE,
        "synthesis": PluginKind.SYNTHESIS,
        "dock": PluginKind.DOCK,
        "docking": PluginKind.DOCK,
        "featurize": PluginKind.FEATURIZER,
        "featurizer": PluginKind.FEATURIZER,
        "train": PluginKind.TRAINER,
        "trainer": PluginKind.TRAINER,
        "predict": PluginKind.PREDICTOR,
        "predictor": PluginKind.PREDICTOR,
        "applicability": PluginKind.APPLICABILITY,
        "scaffold": PluginKind.SCAFFOLDER,
        "scaffolder": PluginKind.SCAFFOLDER,
        "cluster": PluginKind.CLUSTERER,
        "clusterer": PluginKind.CLUSTERER,
        "select": PluginKind.SELECTOR,
        "selector": PluginKind.SELECTOR,
        "enumerate": PluginKind.ENUMERATOR,
        "enumerator": PluginKind.ENUMERATOR,
        "export": PluginKind.EXPORTER,
        "exporter": PluginKind.EXPORTER,
    }
)


@dataclass(frozen=True, slots=True)
class CompiledInputBinding:
    """One verified request-port edge to a named earlier stage output."""

    request_port: str
    source_stage_id: str
    source_port: str
    contract_id: str

    @property
    def source_key(self) -> tuple[str, str]:
        """Return the retained-context key used by the local runner."""

        return (self.source_stage_id, self.source_port)


@dataclass(frozen=True, slots=True)
class CompiledStage:
    """One resolved enabled stage in the fixed execution order.

    ``input_contract`` and ``input_port`` retain the version-1 convenience view
    for a single automatic edge.  ``input_bindings`` is authoritative and can
    express multiple retained side datasets without copying them through every
    intermediate artifact.
    """

    stage_id: str
    slot: str
    plugin_key: str
    descriptor: PluginDescriptor
    config: Mapping[str, JsonValue]
    input_contract: str | None
    output_contract: str
    input_port: str | None
    output_port: Literal["primary"] = "primary"
    input_bindings: tuple[CompiledInputBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledPipeline:
    """An immutable revision paired with its resolved enabled-stage plan."""

    revision: PipelineRevision
    stages: tuple[CompiledStage, ...]

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("compiled pipeline must contain at least one stage")

    @property
    def revision_id(self) -> str:
        """Return the content identity of the complete user configuration."""

        return self.revision.revision_id


@dataclass(frozen=True, slots=True)
class _ResolvedStage:
    config: StageConfig
    plugin: StagePlugin
    descriptor: PluginDescriptor
    normalized_config: Mapping[str, JsonValue]


def _require_exact_plugin_reference(stage: StageConfig) -> None:
    plugin_id, separator, version = stage.plugin.rpartition("@")
    if not separator or not plugin_id or not version:
        raise PipelineError(
            f"stage {stage.id!r} must pin an exact plugin ID@version",
            code="PIPELINE_PLUGIN_VERSION_REQUIRED",
            hint="Use the exact semantic version shown by the plugin registry.",
            context={"stage_id": stage.id, "plugin": stage.plugin},
        )


def _normalise_plugin_config(
    stage: StageConfig,
    plugin: StagePlugin,
) -> Mapping[str, JsonValue]:
    """Validate an optional plugin-owned Pydantic model and freeze its defaults."""

    config_model: Any = getattr(plugin, "config_model", None)
    if config_model is None:
        # StageConfig already owns a recursively immutable, detached JSON value.
        return stage.config
    if not isinstance(config_model, type) or not issubclass(config_model, BaseModel):
        raise PipelineError(
            f"plugin {plugin.descriptor.key} exposes an invalid config model",
            code="PIPELINE_PLUGIN_CONFIG_MODEL_INVALID",
            context={
                "stage_id": stage.id,
                "plugin": plugin.descriptor.key,
                "actual_type": type(config_model).__name__,
            },
        )
    try:
        validated = config_model.model_validate(dict(stage.config))
    except ValidationError as error:
        raise PipelineError(
            f"configuration for stage {stage.id!r} is invalid: {error}",
            code="PIPELINE_STAGE_CONFIG_INVALID",
            hint=f"Check the configuration schema for {plugin.descriptor.key}.",
            context={
                "stage_id": stage.id,
                "plugin": plugin.descriptor.key,
                "error_count": error.error_count(),
            },
        ) from error

    dumped = cast(dict[str, JsonValue], validated.model_dump(mode="json"))
    # Reuse the public configuration boundary to recursively freeze nested JSON
    # and detach the compiled runtime values from the Pydantic model instance.
    normalized = StageConfig(
        id=stage.id,
        slot=stage.slot,
        plugin=stage.plugin,
        config=dumped,
        enabled=True,
    )
    return normalized.config


class PipelineCompiler:
    """Resolve one trusted, exact plugin for each enabled linear stage."""

    def __init__(
        self,
        registry: PluginRegistry,
        *,
        slot_kinds: Mapping[str, PluginKind] = DEFAULT_SLOT_KINDS,
    ) -> None:
        if not isinstance(registry, PluginRegistry):
            raise TypeError("registry must be a PluginRegistry")
        checked_slots: dict[str, PluginKind] = {}
        for slot, kind in slot_kinds.items():
            if not isinstance(slot, str) or not slot:
                raise ValueError("slot map keys must be non-empty strings")
            if not isinstance(kind, PluginKind):
                raise TypeError("slot map values must be PluginKind members")
            checked_slots[slot] = kind
        if not checked_slots:
            raise ValueError("slot map must not be empty")
        self._registry = registry
        self._slot_kinds: Mapping[str, PluginKind] = MappingProxyType(checked_slots)

    @property
    def slot_kinds(self) -> Mapping[str, PluginKind]:
        """Return the immutable capability policy used by this compiler."""

        return self._slot_kinds

    def _resolve_stage(self, stage: StageConfig) -> _ResolvedStage:
        _require_exact_plugin_reference(stage)
        expected_kind = self._slot_kinds.get(stage.slot)
        if expected_kind is None:
            raise PipelineError(
                f"stage {stage.id!r} uses an unknown pipeline slot: {stage.slot}",
                code="PIPELINE_SLOT_UNKNOWN",
                hint="Use a slot declared by the pipeline compiler's slot policy.",
                context={"stage_id": stage.id, "slot": stage.slot},
            )

        # get(), rather than entry(), is deliberate: compiling an untrusted
        # plugin must remain impossible until the exact registry entry is trusted.
        plugin = self._registry.get(stage.plugin)
        descriptor = plugin.descriptor
        if descriptor.key != stage.plugin:
            # Registry lookup should make this unreachable, but retain the
            # invariant at the compiler boundary for alternative registries.
            raise PipelineError(
                f"resolved plugin identity differs for stage {stage.id!r}",
                code="PIPELINE_PLUGIN_IDENTITY_MISMATCH",
                context={
                    "stage_id": stage.id,
                    "requested": stage.plugin,
                    "resolved": descriptor.key,
                },
            )
        if descriptor.kind is not expected_kind and not descriptor.tier_neutral_policy:
            raise PipelineError(
                (
                    f"plugin {descriptor.key} has kind {descriptor.kind.value!r}, "
                    f"not the kind required by slot {stage.slot!r}"
                ),
                code="PIPELINE_SLOT_KIND_MISMATCH",
                hint=(
                    "Choose a replacement plugin declared for the same capability slot. "
                    "Only an explicitly declared tier-neutral policy may cross slot kinds."
                ),
                context={
                    "stage_id": stage.id,
                    "slot": stage.slot,
                    "plugin": descriptor.key,
                    "expected_kind": expected_kind.value,
                    "actual_kind": descriptor.kind.value,
                },
            )
        return _ResolvedStage(
            config=stage,
            plugin=plugin,
            descriptor=descriptor,
            normalized_config=_normalise_plugin_config(stage, plugin),
        )

    def compile(
        self,
        pipeline: PipelineConfig | PipelineRevision | Mapping[str, Any],
    ) -> CompiledPipeline:
        """Compile enabled stages and freeze explicit or safe primary bindings."""

        revision = (
            pipeline
            if isinstance(pipeline, PipelineRevision)
            else freeze_pipeline(pipeline)
        )
        enabled = tuple(stage for stage in revision.config.stages if stage.enabled)
        resolved = tuple(self._resolve_stage(stage) for stage in enabled)

        first = resolved[0]
        if first.descriptor.kind is not PluginKind.SOURCE:
            raise PipelineError(
                "the first enabled pipeline stage must be a source",
                code="PIPELINE_SOURCE_REQUIRED",
                hint="Enable a source stage before stages that consume artifacts.",
                context={
                    "stage_id": first.config.id,
                    "plugin": first.descriptor.key,
                    "actual_kind": first.descriptor.kind.value,
                },
            )

        stages: list[CompiledStage] = []
        resolved_by_id = {item.config.id: item for item in resolved}
        for index, item in enumerate(resolved):
            if index and item.descriptor.kind is PluginKind.SOURCE:
                raise PipelineError(
                    "source plugins may only appear as the first enabled stage",
                    code="PIPELINE_SOURCE_POSITION_INVALID",
                    context={
                        "stage_id": item.config.id,
                        "plugin": item.descriptor.key,
                    },
                )

            bindings: list[CompiledInputBinding] = []
            if item.config.inputs:
                if index == 0:
                    raise PipelineError(
                        "the source stage cannot declare input bindings",
                        code="PIPELINE_SOURCE_INPUT_INVALID",
                        context={"stage_id": item.config.id},
                    )
                for configured in item.config.inputs:
                    upstream = resolved_by_id.get(configured.stage)
                    # PipelineConfig already rejects unknown, disabled, and
                    # forward references.  Keep this compiler boundary check
                    # because a PipelineRevision must never be trusted merely
                    # because it is already a model instance.
                    compiled_stage_ids = {stage.stage_id for stage in stages}
                    if upstream is None or configured.stage not in compiled_stage_ids:
                        raise PipelineError(
                            "input binding references an unavailable stage",
                            code="PIPELINE_BINDING_STAGE_UNKNOWN",
                            context={
                                "stage_id": item.config.id,
                                "request_port": configured.request_port,
                                "source_stage_id": configured.stage,
                            },
                        )
                    contract_id = upstream.descriptor.output_ports.get(configured.port)
                    if contract_id is None:
                        raise PipelineError(
                            "input binding references an output port not declared by its plugin",
                            code="PIPELINE_BINDING_PORT_UNKNOWN",
                            hint="Use a port from the source plugin's output_ports descriptor.",
                            context={
                                "stage_id": item.config.id,
                                "request_port": configured.request_port,
                                "source_stage_id": configured.stage,
                                "source_port": configured.port,
                                "available_ports": sorted(
                                    upstream.descriptor.output_ports
                                ),
                            },
                        )
                    if contract_id not in item.descriptor.inputs:
                        raise PipelineError(
                            "input binding contract is not accepted by the downstream plugin",
                            code="PIPELINE_BINDING_CONTRACT_INCOMPATIBLE",
                            hint=(
                                "Bind a source port whose exact contract appears in "
                                "plugin inputs."
                            ),
                            context={
                                "stage_id": item.config.id,
                                "request_port": configured.request_port,
                                "source_stage_id": configured.stage,
                                "source_port": configured.port,
                                "contract_id": contract_id,
                                "accepted_contracts": list(item.descriptor.inputs),
                            },
                        )
                    bindings.append(
                        CompiledInputBinding(
                            request_port=configured.request_port,
                            source_stage_id=configured.stage,
                            source_port=configured.port,
                            contract_id=contract_id,
                        )
                    )
            elif index:
                # Safe convenience for simple chains: select the latest earlier
                # primary port with a contract accepted by this plugin.  Side
                # ports are never inferred.
                for previous in reversed(stages):
                    if previous.output_contract in item.descriptor.inputs:
                        bindings.append(
                            CompiledInputBinding(
                                request_port="primary",
                                source_stage_id=previous.stage_id,
                                source_port=previous.output_port,
                                contract_id=previous.output_contract,
                            )
                        )
                        break
                if not bindings:
                    previous = stages[-1]
                    raise PluginError(
                        (
                            "no earlier primary output is compatible with plugin "
                            f"{item.descriptor.key}"
                        ),
                        code="PLUGIN_CONTRACT_INCOMPATIBLE",
                        hint="Add an explicit input binding to a compatible retained output.",
                        context={
                            "downstream": item.descriptor.key,
                            "downstream_inputs": list(item.descriptor.inputs),
                            "earlier_primary_outputs": [
                                {
                                    "stage_id": previous_stage.stage_id,
                                    "contract_id": previous_stage.output_contract,
                                }
                                for previous_stage in stages
                            ],
                            "immediate_upstream": previous.plugin_key,
                        },
                    )

            input_contract = bindings[0].contract_id if len(bindings) == 1 else None
            input_port = bindings[0].request_port if len(bindings) == 1 else None
            stages.append(
                CompiledStage(
                    stage_id=item.config.id,
                    slot=item.config.slot,
                    plugin_key=item.descriptor.key,
                    descriptor=item.descriptor,
                    config=item.normalized_config,
                    input_contract=input_contract,
                    output_contract=item.descriptor.primary_contract,
                    input_port=input_port,
                    input_bindings=tuple(bindings),
                )
            )
        return CompiledPipeline(revision=revision, stages=tuple(stages))


def compile_pipeline(
    pipeline: PipelineConfig | PipelineRevision | Mapping[str, Any],
    registry: PluginRegistry,
    *,
    slot_kinds: Mapping[str, PluginKind] = DEFAULT_SLOT_KINDS,
) -> CompiledPipeline:
    """Compile ``pipeline`` with an explicit plugin registry and slot policy."""

    return PipelineCompiler(registry, slot_kinds=slot_kinds).compile(pipeline)


__all__ = [
    "DEFAULT_SLOT_KINDS",
    "CompiledInputBinding",
    "CompiledPipeline",
    "CompiledStage",
    "PipelineCompiler",
    "compile_pipeline",
]
