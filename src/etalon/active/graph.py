"""Compile recipe evidence graphs without executing scientific components.

This is a structural certificate, not proof that a backend is installed, a nullable
field will be populated, or a molecule will survive a gate. The placeholder-library
revision identifies the template; a real action has its own input-bound revision.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from etalon.active.schema import canonical, digest
from etalon.boundary.infra import load

if TYPE_CHECKING:
    from etalon.active.cascade import CascadeRecipe
    from etalon.boundary.screen import ScreenPlan, StageOutcome


def inspect_recipe(recipe: CascadeRecipe) -> dict[str, Any]:
    """Validate pinned inputs, contracts and readout, returning a canonical graph.

    Only the built-in registry is used: no entry-point discovery, backend probes,
    version commands, filesystem outputs or plugin ``execute`` calls occur here.
    Infrastructure identity follows ETALON's pinned-import boundary; rehashing the
    complete vendored source tree remains ``tools/verify_assets.py``'s responsibility.
    Compiler/configuration/file diagnostics become ValueError for proposal journals.
    """
    try:
        return _inspect_recipe(recipe)
    except Exception as error:
        raise ValueError(f"recipe graph inspection failed: {error}") from error


def _inspect_recipe(recipe: CascadeRecipe) -> dict[str, Any]:
    from etalon.active.cascade import (
        _absolute_input_path,
        _configuration_input_paths,
        _file_hash,
    )

    infra = load("molcascade")
    if infra.source_commit != recipe.infrastructure_commit:
        raise ValueError("MolCascade infrastructure changed after recipe registration")
    seen_paths: set[str] = set()
    for name, expected in recipe.input_files:
        path = Path(name)
        absolute = str(_absolute_input_path(path))
        if not path.is_absolute() or absolute in seen_paths:
            raise ValueError("protocol inputs must have unique absolute paths")
        seen_paths.add(absolute)
        if _file_hash(path) != expected:
            raise ValueError(f"protocol input changed: {name}")
    required = {str(path) for path in _configuration_input_paths(json.loads(recipe.configuration))}
    if missing := required - seen_paths:
        raise ValueError(f"protocol configuration references unpinned input paths: {sorted(missing)}")

    import pyarrow as pa
    from molcascade.cascade.lower import lower_cascade
    from molcascade.cascade.models import CascadeConfig
    from molcascade.contracts import get_contract
    from molcascade.pipeline import PipelineCompiler
    from molcascade.plugins import create_builtin_registry

    config = CascadeConfig.model_validate(json.loads(recipe.configuration))
    if config.library.path is not None:
        raise ValueError("active recipes must leave library.path unset")
    registry = create_builtin_registry()
    lowered = lower_cascade(config, registry=registry, allow_missing_library=True,
                            allow_missing_target=False)
    compiled = PipelineCompiler(registry).compile(lowered.pipeline)
    stages = []
    for stage in compiled.stages:
        ports = dict(stage.descriptor.output_ports)
        if len(set(ports.values())) != len(ports):
            raise ValueError(f"ambiguous output contract ports at stage {stage.stage_id}")
        origin = lowered.origin_of(stage.stage_id)
        stages.append({
            "stage_id": stage.stage_id, "plugin_key": stage.plugin_key, "slot": stage.slot,
            "settings_hash": digest(dict(stage.config)),
            "input_bindings": [asdict(binding) for binding in stage.input_bindings],
            "output_ports": ports,
            "determinism": stage.descriptor.determinism.value,
            "cardinality": stage.descriptor.cardinality.value,
            "origin": asdict(origin) if origin is not None else None,
        })

    readout = json.loads(recipe.readout_json)
    if (not isinstance(readout, dict)
            or set(readout) != {"stage_id", "contract_id", "value_column", "filters"}
            or not isinstance(readout["filters"], dict)
            or any(not isinstance(readout[key], str) or not readout[key]
                   for key in ("stage_id", "contract_id", "value_column"))):
        raise ValueError("readout must explicitly identify stage, contract, numeric column and filters")
    matches = [stage for stage in stages if stage["stage_id"] == readout["stage_id"]]
    if len(matches) != 1:
        raise ValueError("readout stage is absent or ambiguous")
    ports = [port for port, contract in matches[0]["output_ports"].items()
             if contract == readout["contract_id"]]
    if len(ports) != 1:
        raise ValueError("readout contract is absent or ambiguous at its stage")
    contract = get_contract(readout["contract_id"])
    schema = contract.schema
    if "parent_id" not in schema.names:
        raise ValueError("readout contract must identify a parent_id")
    if not (pa.types.is_string(schema.field("parent_id").type)
            or pa.types.is_large_string(schema.field("parent_id").type)):
        raise ValueError("readout parent_id must be a string field")
    column = readout["value_column"]
    if column not in schema.names:
        raise ValueError("readout value column is absent from its contract")
    value_type = schema.field(column).type
    if not (pa.types.is_integer(value_type) or pa.types.is_floating(value_type)
            or pa.types.is_decimal(value_type)):
        raise ValueError("readout value column must have a numeric contract type")
    unknown = set(readout["filters"]) - set(schema.names)
    if unknown:
        raise ValueError(f"readout filters contain unknown contract fields: {sorted(unknown)}")
    canonical(readout)  # Reject NaN and other non-JSON filter values even for manually built recipes.

    manifest = {
        "schema_version": 1, "protocol_id": recipe.protocol_id,
        "certificate_scope": "compile_only",
        "revision_id": compiled.revision_id, "revision_scope": "placeholder_library_template",
        "infrastructure_commit": infra.source_commit,
        "input_files": [list(item) for item in recipe.input_files],
        "library_is_placeholder": lowered.library_is_placeholder,
        "target_is_placeholder": lowered.target_is_placeholder,
        "stages": stages,
        "readout": {**readout, "port": ports[0], "value_type": str(value_type),
                    "nullable": schema.field(column).nullable,
                    "primary_key": list(contract.primary_key)},
        "validation_scope": "structural_only; runtime cardinality, finite value and status remain required",
    }
    return {**manifest, "graph_hash": digest(manifest)}


def _stage_identity(stage: Any) -> dict[str, Any]:
    return {
        "stage_id": stage.stage_id, "slot": stage.slot, "plugin_key": stage.plugin_key,
        "descriptor": stage.descriptor.model_dump(mode="json"), "config": dict(stage.config),
        "input_contract": stage.input_contract, "output_contract": stage.output_contract,
        "input_port": stage.input_port, "output_port": stage.output_port,
        "input_bindings": [asdict(binding) for binding in stage.input_bindings],
    }


def _compiled_identity(compiled: Any) -> dict[str, Any]:
    return {"revision": compiled.revision.model_dump(mode="json"),
            "stages": [_stage_identity(stage) for stage in compiled.stages]}


def verify_execution_plan(recipe: CascadeRecipe, manifest: Mapping[str, Any],
                          plan: ScreenPlan, library_path: str | Path) -> dict[str, Any]:
    """Bind a template certificate to the exact input-bound plan before execution.

    The executable ``pipeline`` is checked as well as its cached ``compiled`` view:
    Screen.run executes the former. Only the source's library path may differ from
    the certified template, not its reader settings, plugin or other stage semantics.
    The source file is not executed/read and no backend probe is launched. This is
    not artifact verification or a sandbox against arbitrary injected Python code.
    """
    try:
        return _verify_execution_plan(recipe, manifest, plan, library_path)
    except Exception as error:
        raise ValueError(f"execution plan verification failed: {error}") from error


def _verify_execution_plan(recipe: CascadeRecipe, manifest: Mapping[str, Any],
                           plan: ScreenPlan, library_path: str | Path) -> dict[str, Any]:
    if canonical(dict(manifest)) != canonical(inspect_recipe(recipe)):
        raise ValueError("compile certificate does not match the recipe")
    path = Path(library_path)
    if not path.is_absolute():
        raise ValueError("execution library must use an explicit absolute path")

    from molcascade.cascade.lower import lower_cascade
    from molcascade.cascade.models import CascadeConfig
    from molcascade.pipeline import PipelineCompiler
    from molcascade.plugins import create_builtin_registry

    registry = create_builtin_registry()
    config = CascadeConfig.model_validate(json.loads(recipe.configuration))
    template = PipelineCompiler(registry).compile(lower_cascade(
        config, registry=registry, allow_missing_library=True, allow_missing_target=False).pipeline)
    expected = PipelineCompiler(registry).compile(lower_cascade(
        config, registry=registry, library_path=str(path), allow_missing_target=False).pipeline)
    if len(template.stages) != len(expected.stages):
        raise ValueError("library binding changed the stage topology")
    for index, (before, after) in enumerate(zip(template.stages, expected.stages, strict=True)):
        static_stage, runtime_stage = _stage_identity(before), _stage_identity(after)
        if index == 0:
            # A reader switch or delimiter change is not merely binding a new input.
            static_stage["config"]["path"] = str(path)
        if canonical(static_stage) != canonical(runtime_stage):
            raise ValueError("library binding changed protocol settings beyond the source path")
    if (plan.configuration_kind != "cascade" or plan.stage_count != len(expected.stages)
            or plan.revision_id != expected.revision_id):
        raise ValueError("plan revision, stage count or configuration kind does not match the recipe")
    actual = plan._internal["compiled"]
    identity = _compiled_identity(expected)
    if canonical(_compiled_identity(actual)) != canonical(identity):
        raise ValueError("compiled plan settings, bindings or descriptors differ from the recipe")

    actual_registry = plan._internal["registry"]
    if type(actual_registry) is not type(registry):
        raise ValueError("execution registry is not the expected built-in registry type")
    for stage in expected.stages:
        entry, trusted = actual_registry.entry(stage.plugin_key), registry.entry(stage.plugin_key)
        if (not entry.trusted or entry.origin != "builtin"
                or type(entry.plugin) is not type(trusted.plugin)):
            raise ValueError("execution registry contains a different plugin implementation")
    executable = PipelineCompiler(actual_registry).compile(plan._internal["pipeline"])
    if canonical(_compiled_identity(executable)) != canonical(identity):
        raise ValueError("executable pipeline differs from its verified compiled plan")
    return {
        "schema_version": 1, "protocol_id": recipe.protocol_id, "graph_hash": manifest["graph_hash"],
        "template_revision_id": manifest["revision_id"], "runtime_revision_id": expected.revision_id,
        "library_path": str(path), "compiled_plan_hash": digest(identity),
        "verified_stage_count": len(expected.stages), "scope": "compile_only_runtime_binding",
        "artifact_verification": "not_performed; use Screen.read after execution",
    }


def execution_lineage(manifest: Mapping[str, Any],
                      outcomes: Sequence[StageOutcome]) -> dict[str, Any]:
    """Attach reported artifacts to graph edges without inventing absent evidence.

    This function does not read or verify artifact bytes. ``available`` means the
    runner reported successful materialization and all graph ancestors likewise;
    callers must still use ``Screen.read`` for byte verification and label admission.
    """
    payload = {key: value for key, value in manifest.items() if key != "graph_hash"}
    if manifest.get("schema_version") != 1 or digest(payload) != manifest.get("graph_hash"):
        raise ValueError("graph manifest identity is invalid")
    nodes = manifest["stages"]
    by_id = {node["stage_id"]: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("graph contains duplicate stage identifiers")
    reported = {}
    for outcome in outcomes:
        if outcome.stage_id in reported or outcome.stage_id not in by_id:
            raise ValueError("execution contains duplicate or unknown stage identifiers")
        if outcome.plugin != by_id[outcome.stage_id]["plugin_key"]:
            raise ValueError("execution plugin does not match the compiled graph")
        if type(outcome.attempts) is not int or outcome.attempts < 0:
            raise ValueError("stage attempts must be a nonnegative integer")
        reported[outcome.stage_id] = outcome
    annotated = []
    materialized: dict[str, dict[str, Any]] = {}
    for node in nodes:
        outcome = reported.get(node["stage_id"])
        edges = []
        for binding in node["input_bindings"]:
            source = materialized.get(binding["source_stage_id"])
            if source is None:
                raise ValueError("graph binding does not reference an earlier stage")
            upstream = by_id[binding["source_stage_id"]]
            if upstream["output_ports"].get(binding["source_port"]) != binding["contract_id"]:
                raise ValueError("graph binding contract does not match its source port")
            edges.append({**binding, "source_artifact_id": source["artifact_id"],
                          "source_available": source["available"]})
        committed = (outcome is not None and outcome.status in {"SUCCEEDED", "CACHED"}
                     and isinstance(outcome.artifact_id, str) and bool(outcome.artifact_id)
                     and outcome.error is None)
        entry = {
            "stage_id": node["stage_id"], "plugin_key": node["plugin_key"],
            "status": outcome.status if outcome is not None else "NOT_REPORTED",
            "attempts": outcome.attempts if outcome is not None else 0,
            "artifact_id": outcome.artifact_id if outcome is not None else None,
            "error": outcome.error if outcome is not None else None,
            "reported_committed": committed,
            "available": committed and all(edge["source_available"] for edge in edges),
            "input_bindings": edges, "output_ports": dict(node["output_ports"]),
        }
        annotated.append(entry)
        materialized[node["stage_id"]] = entry
    readout = manifest["readout"]
    readout_stage = materialized[readout["stage_id"]]
    return {
        "schema_version": 1, "graph_hash": manifest["graph_hash"],
        "protocol_id": manifest["protocol_id"], "stages": annotated,
        "template_revision_id": manifest["revision_id"],
        "artifact_verification": "not_performed_by_lineage; use Screen.read",
        "readout": {**readout, "artifact_id": readout_stage["artifact_id"],
                    "status": readout_stage["status"], "available": readout_stage["available"]},
    }
