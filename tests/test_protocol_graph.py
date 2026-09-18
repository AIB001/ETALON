"""Compile-only evidence certificates must never manufacture execution evidence."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from etalon.active.cascade import CascadeRecipe, Readout
from etalon.active.graph import execution_lineage, inspect_recipe, verify_execution_plan
from etalon.active.schema import canonical, digest
from etalon.boundary.screen import ScreenPlan, StageOutcome
from etalon.campaign.design import component, compose


def _recipe(*, backend="features.rdkit_properties@0.1.0", readout=None, settings=None):
    config = compose("graph-fixture", [{"id": "measure", "title": "Requested only",
                                       "mode": "serial", "criteria": [
                                           component("measurement", backend, settings=settings)]}])
    return CascadeRecipe.freeze(config, readout or Readout("measurement", "property/v1", "mw"))


def _outcomes(manifest):
    return [StageOutcome(node["stage_id"], node["plugin_key"], "SUCCEEDED", 1,
                         f"artifact-{node['stage_id']}", None) for node in manifest["stages"]]


def test_inspection_compiles_only_requested_components_without_execution_or_output(monkeypatch, tmp_path):
    recipe = _recipe()

    from molcascade.plugins import create_builtin_registry
    from molcascade.runtime.runner import LocalRunner

    def forbidden(*args, **kwargs):
        raise AssertionError("compile-only inspection attempted execution or filesystem mutation")

    monkeypatch.chdir(tmp_path)
    for entry in create_builtin_registry():
        monkeypatch.setattr(type(entry.plugin), "execute", forbidden)
    monkeypatch.setattr(LocalRunner, "run", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    manifest = inspect_recipe(recipe)
    assert list(tmp_path.iterdir()) == []
    assert [node["stage_id"] for node in manifest["stages"]] == ["library", "standardize", "measurement"]
    assert manifest["library_is_placeholder"] and not manifest["target_is_placeholder"]
    assert manifest["revision_scope"] == "placeholder_library_template"
    assert manifest["readout"]["port"] == "properties"
    assert manifest["readout"]["value_type"] == "double"
    assert manifest["readout"]["nullable"] is True
    assert manifest["stages"][-1]["input_bindings"] == [{
        "request_port": "primary", "source_stage_id": "standardize",
        "source_port": "primary", "contract_id": "parent/v1"}]


def test_graph_identity_is_stable_and_binds_normalized_settings_and_readout():
    recipe = _recipe()
    original = inspect_recipe(recipe)
    assert original == inspect_recipe(recipe)
    assert original["graph_hash"] == digest({k: v for k, v in original.items() if k != "graph_hash"})
    changed = inspect_recipe(_recipe(settings={"include_sa_score": False}))
    assert changed["graph_hash"] != original["graph_hash"]
    assert changed["stages"][-1]["settings_hash"] != original["stages"][-1]["settings_hash"]
    logp = inspect_recipe(_recipe(readout=Readout("measurement", "property/v1", "clogp")))
    assert logp["graph_hash"] != original["graph_hash"]
    assert logp["revision_id"] == original["revision_id"]


def test_non_property_numeric_contract_is_supported():
    recipe = _recipe(backend="synthesis.rdkit_sa_score@0.1.0",
                     readout=Readout("measurement", "synthesis_score/v1", "score"))
    manifest = inspect_recipe(recipe)
    assert manifest["readout"]["port"] == "synthesis_scores"


@pytest.mark.parametrize(("readout", "message"), [
    (Readout("absent", "property/v1", "mw"), "stage is absent"),
    (Readout("measurement", "synthesis_score/v1", "score"), "contract is absent"),
    (Readout("measurement", "property/v1", "missing"), "column is absent"),
    (Readout("measurement", "property/v1", "calculator_id"), "numeric contract type"),
    (Readout("measurement", "property/v1", "mw", {"missing": 1}), "unknown contract fields"),
    (Readout("library", "raw_molecule/v1", "source_index"), "parent_id"),
])
def test_bad_readout_is_rejected_before_scientific_execution(readout, message):
    with pytest.raises(ValueError, match=message):
        inspect_recipe(_recipe(readout=readout))


def test_known_filters_and_integer_values_are_permitted():
    manifest = inspect_recipe(_recipe(readout=Readout(
        "measurement", "property/v1", "heavy_atom_count", {"calculator_id": "declared-id"})))
    assert manifest["readout"]["value_type"] == "int32"
    assert manifest["readout"]["filters"] == {"calculator_id": "declared-id"}


def test_missing_side_evidence_is_a_persistable_value_error():
    recipe = _recipe(backend="derived.normalized_docking_score@0.1.0",
                     readout=Readout("measurement", "derived_metric/v1", "value"))
    with pytest.raises(ValueError, match="earlier stage produces") as caught:
        inspect_recipe(recipe)
    assert caught.value.__cause__ is not None


def test_explicit_nonadjacent_evidence_edges_survive_graph_and_lineage():
    criteria = [component("sa_first", "synthesis.rdkit_sa_score@0.1.0"),
                component("sa_second", "synthesis.rdkit_sa_score@0.1.0"),
                component("gate", "synthesis.numeric_evidence_gate@0.1.0",
                          settings={"maximum": 6.0, "expected_direction": "HIGHER_HARDER"},
                          evidence_from={"synthesis_score/v1": "sa_first"})]
    config = compose("side-evidence", [{"id": "custom", "title": "Explicit evidence",
                                        "mode": "serial", "criteria": criteria}])
    recipe = CascadeRecipe.freeze(config, Readout("sa_first", "synthesis_score/v1", "score"))
    manifest = inspect_recipe(recipe)
    gate = next(node for node in manifest["stages"] if node["stage_id"] == "gate")
    assert {edge["source_stage_id"] for edge in gate["input_bindings"]} == {"sa_first", "sa_second"}
    lineage = execution_lineage(manifest, _outcomes(manifest))
    gate_run = next(node for node in lineage["stages"] if node["stage_id"] == "gate")
    assert {edge["source_artifact_id"] for edge in gate_run["input_bindings"]} == {
        "artifact-sa_first", "artifact-sa_second"}
    config["tiers"][0]["criteria"][-1]["evidence_from"] = {}
    ambiguous = CascadeRecipe.freeze(config, Readout("sa_first", "synthesis_score/v1", "score"))
    with pytest.raises(ValueError, match="earlier stages"):
        inspect_recipe(ambiguous)


def test_compile_does_not_invent_a_missing_docking_target():
    config = compose("missing-target", [{"id": "dock", "title": "Explicit docking",
                                         "mode": "serial", "criteria": [
                                             component("prepare", "docking.rdkit_conformers@0.1.0"),
                                             component("dock", "docking.gnina@0.3.0")]}])
    recipe = CascadeRecipe.freeze(config, Readout("dock", "docking_score/v1", "score"))
    with pytest.raises(ValueError, match="receptor_path"):
        inspect_recipe(recipe)


def test_invalid_plugin_settings_are_rejected_by_compiler():
    recipe = _recipe(settings={"include_sa_score": "not-a-boolean"})
    with pytest.raises(ValueError, match="configuration for stage"):
        inspect_recipe(recipe)


def test_changed_commit_and_files_are_rejected(tmp_path):
    recipe = _recipe()
    with pytest.raises(ValueError, match="infrastructure changed"):
        inspect_recipe(replace(recipe, infrastructure_commit="different"))
    source = tmp_path / "weights.bin"
    source.write_bytes(b"version-one")
    pinned = CascadeRecipe.freeze(json.loads(recipe.configuration),
                                  Readout("measurement", "property/v1", "mw"), files=(source,))
    assert inspect_recipe(pinned)["input_files"] == [list(pinned.input_files[0])]
    source.write_bytes(b"version-two")
    with pytest.raises(ValueError, match="protocol input changed"):
        inspect_recipe(pinned)
    source.unlink()
    with pytest.raises(ValueError, match="No such file"):
        inspect_recipe(pinned)


def test_bound_library_cannot_be_smuggled_into_manually_constructed_recipe():
    recipe = _recipe()
    config = json.loads(recipe.configuration)
    config["library"]["path"] = "/not-a-real-library.csv"
    with pytest.raises(ValueError, match="leave library.path unset"):
        inspect_recipe(replace(recipe, configuration=canonical(config)))


def test_execution_lineage_tracks_actual_ports_and_artifacts():
    manifest = inspect_recipe(_recipe())
    lineage = execution_lineage(manifest, _outcomes(manifest))
    readout = lineage["readout"]
    assert readout["available"] and readout["artifact_id"] == "artifact-measurement"
    assert lineage["stages"][-1]["input_bindings"][0]["source_artifact_id"] == "artifact-standardize"
    assert "not_performed_by_lineage" in lineage["artifact_verification"]


@pytest.mark.parametrize("status", ["FAILED", "BLOCKED", "PENDING", "RUNNING"])
def test_unsuccessful_and_missing_stages_do_not_become_evidence(status):
    manifest = inspect_recipe(_recipe())
    outcomes = _outcomes(manifest)
    # Even an externally supplied artifact id is not evidence after a failed status.
    outcomes[1] = replace(outcomes[1], status=status)
    lineage = execution_lineage(manifest, outcomes)
    assert not lineage["readout"]["available"]
    assert lineage["stages"][-1]["reported_committed"]
    assert not lineage["stages"][-1]["input_bindings"][0]["source_available"]
    absent = execution_lineage(manifest, outcomes[:1])
    assert absent["readout"]["artifact_id"] is None
    assert absent["readout"]["status"] == "NOT_REPORTED"
    assert not absent["readout"]["available"]


def test_error_on_success_status_and_missing_artifact_still_block_evidence():
    manifest = inspect_recipe(_recipe())
    outcomes = _outcomes(manifest)
    outcomes[-1] = replace(outcomes[-1], error={"message": "materialization failed"})
    assert not execution_lineage(manifest, outcomes)["readout"]["available"]
    outcomes[-1] = replace(outcomes[-1], error=None, artifact_id=None)
    assert not execution_lineage(manifest, outcomes)["readout"]["available"]


def test_verified_cache_status_is_preserved_without_claiming_fresh_execution():
    manifest = inspect_recipe(_recipe())
    outcomes = [replace(row, status="CACHED", attempts=0) for row in _outcomes(manifest)]
    lineage = execution_lineage(manifest, outcomes)
    assert lineage["readout"]["available"] and lineage["readout"]["status"] == "CACHED"
    assert all(row["attempts"] == 0 for row in lineage["stages"])


def test_execution_and_graph_identity_mismatches_fail_closed():
    manifest = inspect_recipe(_recipe())
    outcomes = _outcomes(manifest)
    with pytest.raises(ValueError, match="duplicate or unknown"):
        execution_lineage(manifest, [*outcomes, outcomes[0]])
    with pytest.raises(ValueError, match="duplicate or unknown"):
        execution_lineage(manifest, [replace(outcomes[0], stage_id="extra")])
    with pytest.raises(ValueError, match="plugin does not match"):
        execution_lineage(manifest, [replace(outcomes[0], plugin="different@0.1.0")])
    tampered = deepcopy(manifest)
    tampered["stages"][-1]["settings_hash"] = "edited"
    with pytest.raises(ValueError, match="identity is invalid"):
        execution_lineage(tampered, outcomes)


def test_graph_rejects_inconsistent_dependencies_even_if_hash_was_recomputed():
    manifest = inspect_recipe(_recipe())
    outcomes = _outcomes(manifest)
    manifest["stages"][-1]["input_bindings"][0]["source_port"] = "missing"
    manifest["graph_hash"] = digest({k: v for k, v in manifest.items() if k != "graph_hash"})
    with pytest.raises(ValueError, match="contract does not match"):
        execution_lineage(manifest, outcomes)


def _execution_plan(recipe, library):
    from molcascade.cascade.lower import lower_cascade
    from molcascade.cascade.models import CascadeConfig
    from molcascade.pipeline import PipelineCompiler
    from molcascade.plugins import create_builtin_registry

    registry = create_builtin_registry()
    pipeline = lower_cascade(CascadeConfig.model_validate_json(recipe.configuration), registry=registry,
                             library_path=str(library)).pipeline
    compiled = PipelineCompiler(registry).compile(pipeline)
    return ScreenPlan(compiled.revision_id, len(compiled.stages), (), (), (), (),
                      _internal={"registry": registry, "pipeline": pipeline, "compiled": compiled})


def test_execution_plan_binding_is_pure_compile_and_names_real_revision(monkeypatch, tmp_path):
    recipe = _recipe()
    manifest = inspect_recipe(recipe)
    library = tmp_path / "not-created.csv"
    plan = _execution_plan(recipe, library)

    def forbidden(*args, **kwargs):
        raise AssertionError("runtime binding check executed science or wrote files")

    for entry in plan._internal["registry"]:
        monkeypatch.setattr(type(entry.plugin), "execute", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    verified = verify_execution_plan(recipe, manifest, plan, library)
    assert verified["runtime_revision_id"] == plan.revision_id
    assert verified["template_revision_id"] == manifest["revision_id"]
    assert verified["runtime_revision_id"] != verified["template_revision_id"]
    assert verified["verified_stage_count"] == 3
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("part", ["settings", "source_settings", "bindings", "ports", "plugin"])
def test_execution_plan_rejects_modified_compiled_stage_with_unchanged_revision(tmp_path, part):
    recipe = _recipe()
    manifest = inspect_recipe(recipe)
    library = tmp_path / "selected.csv"
    plan = _execution_plan(recipe, library)
    compiled = plan._internal["compiled"]
    stages = list(compiled.stages)
    index = 0 if part == "source_settings" else -1
    stage = stages[index]
    if part in {"settings", "source_settings"}:
        settings = dict(stage.config)
        settings["batch_size"] += 1
        changed = replace(stage, config=settings)
    elif part == "bindings":
        changed = replace(stage, input_bindings=(replace(stage.input_bindings[0], source_stage_id="library"),))
    elif part == "ports":
        changed = replace(stage, output_port="not_the_primary_port")
    else:
        changed = replace(stage, plugin_key="different.plugin@0.1.0")
    stages[index] = changed
    plan._internal["compiled"] = replace(compiled, stages=tuple(stages))
    with pytest.raises(ValueError, match="compiled plan settings, bindings or descriptors"):
        verify_execution_plan(recipe, manifest, plan, library)


def test_execution_plan_rejects_display_revision_mismatch_and_different_library(tmp_path):
    recipe = _recipe()
    manifest = inspect_recipe(recipe)
    library = tmp_path / "selected.csv"
    plan = _execution_plan(recipe, library)
    with pytest.raises(ValueError, match="plan revision"):
        verify_execution_plan(recipe, manifest, replace(plan, revision_id="0" * 64), library)
    with pytest.raises(ValueError, match="plan revision"):
        verify_execution_plan(recipe, manifest, plan, tmp_path / "another.csv")


def test_execution_plan_checks_the_pipeline_that_screen_will_actually_run(tmp_path):
    recipe = _recipe()

    from molcascade.config.models import PipelineConfig

    manifest = inspect_recipe(recipe)
    library = tmp_path / "selected.csv"
    plan = _execution_plan(recipe, library)
    config = plan._internal["pipeline"].model_dump(mode="json")
    config["stages"][-1]["config"]["include_sa_score"] = False
    plan._internal["pipeline"] = PipelineConfig.model_validate(config)
    with pytest.raises(ValueError, match="executable pipeline differs"):
        verify_execution_plan(recipe, manifest, plan, library)


def test_execution_plan_requires_a_matching_template_certificate(tmp_path):
    recipe = _recipe()
    manifest = inspect_recipe(_recipe(settings={"include_sa_score": False}))
    library = tmp_path / "selected.csv"
    with pytest.raises(ValueError, match="certificate does not match"):
        verify_execution_plan(recipe, manifest, _execution_plan(recipe, library), library)


def test_execution_library_binding_cannot_switch_reader_semantics(tmp_path):
    recipe = _recipe()
    manifest = inspect_recipe(recipe)
    library = tmp_path / "unexpected.sdf"
    plan = _execution_plan(recipe, library)
    with pytest.raises(ValueError, match="beyond the source path"):
        verify_execution_plan(recipe, manifest, plan, library)
