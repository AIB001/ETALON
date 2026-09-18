"""Adversarial CPU-only checks of recipe identity and complete execution evidence."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout
from etalon.active.graph import inspect_recipe
from etalon.active.mutations import DesignSpace, mutate_recipe
from etalon.active.schema import Action, Candidate, canonical
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose


@pytest.fixture
def execution(tmp_path):
    config = compose("complete-evidence", [{
        "id": "measure", "title": "Explicit ordered CPU components", "mode": "serial",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0"),
                     component("after_readout", "features.rdkit_properties@0.1.0")],
    }])
    recipe = CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))
    endpoint = recipe.endpoint("mw", target="demo", quantity="mw", units="Da", cost=3)
    candidate = Candidate("ethanol", "CCO", (1.0,))
    action = Action("test-action", 1, candidate.id, endpoint.id, 0, endpoint.cost)
    return {"recipe": recipe, "endpoint": endpoint, "candidate": candidate, "action": action,
            "executor": CascadeExecutor(tmp_path / "runs", {endpoint.id: recipe}), "root": tmp_path / "runs"}


def execute(case):
    return case["executor"](case["action"], case["candidate"], case["endpoint"], None)


def assert_paid_rejected(case, result, code):
    assert result.value is None and result.status in {"failed", "invalid"}, result.as_dict()
    assert result.cost == case["endpoint"].cost
    assert result.provenance["failure_code"] == code
    assert result.provenance["action_id"] == case["action"].id


@pytest.mark.parametrize("declared", ["explicit", "configuration"])
def test_symlink_retargeting_changes_the_referenced_resource_identity(
        execution, tmp_path, monkeypatch, declared):
    original, replacement, link = (tmp_path / name for name in ("original.bin", "replacement.bin", "alias.bin"))
    original.write_bytes(b"original resource")
    replacement.write_bytes(b"replacement resource")
    link.symlink_to(original)
    config = json.loads(execution["recipe"].configuration)
    if declared == "configuration":
        config["target"] = {"name": "resource-sentinel", "receptor_path": str(link),
                            "box": {"center_x": 0, "center_y": 0, "center_z": 0,
                                    "size_x": 10, "size_y": 10, "size_z": 10}}
    recipe = CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"),
                                  files=(link,) if declared == "explicit" else ())
    endpoint = recipe.endpoint("mw", target="demo", quantity="mw", units="Da", cost=3)
    executor = CascadeExecutor(execution["root"], {endpoint.id: recipe})
    link.unlink()
    link.symlink_to(replacement)

    def forbidden(*args, **kwargs):
        pytest.fail("retargeted protocol inputs must be refused before scientific execution")

    monkeypatch.setattr(Screen, "run", forbidden)
    result = executor(execution["action"], execution["candidate"], endpoint, None)
    assert result.status == "blocked" and result.cost == 0
    assert result.provenance["failure_code"] == "PROTOCOL_INPUT_CHANGED"
    assert not execution["root"].exists()


def test_manually_constructed_recipe_cannot_omit_configured_resource_pins(execution, tmp_path):
    resource = tmp_path / "receptor.pdb"
    resource.write_text("declared sentinel, not a docking input", encoding="utf-8")
    config = json.loads(execution["recipe"].configuration)
    config["target"] = {"name": "resource-sentinel", "receptor_path": str(resource),
                        "box": {"center_x": 0, "center_y": 0, "center_z": 0,
                                "size_x": 10, "size_y": 10, "size_z": 10}}
    forged = replace(execution["recipe"], configuration=canonical(config))
    with pytest.raises(ValueError, match="unpinned"):
        inspect_recipe(forged)


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "different-molecule"), ("endpoint_id", "different-endpoint"),
    ("id", "../outside"), ("id", "absolute-fixture"), ("id", ".."),
    ("replicate", 1), ("round_id", 0),
])
def test_bad_action_binding_is_rejected_before_creating_a_workspace(execution, tmp_path, monkeypatch, field, value):
    if value == "absolute-fixture":
        value = str(tmp_path / "outside")
    execution["action"] = replace(execution["action"], **{field: value})

    def forbidden(*args, **kwargs):
        pytest.fail("invalid action binding must not launch scientific computation")

    monkeypatch.setattr(Screen, "run", forbidden)
    with pytest.raises(ValueError, match="action|replica|round"):
        execute(execution)
    assert not execution["root"].exists()


@pytest.mark.parametrize("fault", ["missing", "pending", "failed_without_error", "run_failed"])
def test_successful_early_readout_cannot_certify_an_incomplete_requested_recipe(execution, monkeypatch, fault):
    original = Screen.run

    def incomplete(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        assert not result.failed
        if fault == "run_failed":
            return replace(result, status="FAILED")
        stages = list(result.stages)
        if fault == "missing":
            stages.pop()
        else:
            stages[-1] = replace(stages[-1], status="PENDING" if fault == "pending" else "FAILED", error=None)
        return replace(result, stages=tuple(stages))

    monkeypatch.setattr(Screen, "run", incomplete)
    assert_paid_rejected(execution, execute(execution), "CASCADE_INCOMPLETE")


@pytest.mark.parametrize("fault", ["duplicate", "conflicting_identity"])
def test_parent_row_duplicates_and_cross_stage_identity_conflicts_are_not_overwritten(
        execution, monkeypatch, fault):
    original = Screen.read
    changed = []

    def corrupt_one_parent_table(self, artifact_id, *, contract_id=None):
        rows = original(self, artifact_id, contract_id=contract_id)
        if contract_id == "parent/v1" and not changed:
            changed.append(True)
            if fault == "duplicate":
                return [*rows, dict(rows[0])]
            return [{**rows[0], "parent_smiles": "CCN"}, *rows[1:]]
        return rows

    monkeypatch.setattr(Screen, "read", corrupt_one_parent_table)
    result = execute(execution)
    assert changed
    assert_paid_rejected(execution, result, "CHEMICAL_STATE_CARDINALITY" if fault == "duplicate" else "CHEMICAL_STATE_MISMATCH")


@pytest.mark.parametrize("value", [True, "46.069", {}, float("inf")])
def test_invalid_scalar_type_is_retained_as_a_paid_structured_failure(execution, monkeypatch, value):
    original = Screen.read

    def wrong_scalar(self, artifact_id, *, contract_id=None):
        rows = original(self, artifact_id, contract_id=contract_id)
        return [{**row, "mw": value} for row in rows] if contract_id == "property/v1" else rows

    monkeypatch.setattr(Screen, "read", wrong_scalar)
    assert_paid_rejected(execution, execute(execution), "READOUT_INVALID")


def test_artifact_read_exception_after_compute_is_paid_and_has_structured_provenance(execution, monkeypatch):
    original = Screen.read

    def failed_read(self, artifact_id, *, contract_id=None):
        if contract_id == "property/v1":
            raise OSError("simulated lost artifact after successful execution")
        return original(self, artifact_id, contract_id=contract_id)

    monkeypatch.setattr(Screen, "read", failed_read)
    result = execute(execution)
    assert_paid_rejected(execution, result, "ARTIFACT_READ_FAILED")
    assert "lost artifact" in result.provenance["error"]
    assert "run" in result.provenance


def test_last_pre_dispatch_resource_change_is_free_and_never_launches_the_runner(execution, tmp_path, monkeypatch):
    from etalon.active import graph

    resource = tmp_path / "resource.bin"
    resource.write_bytes(b"initial")
    recipe = CascadeRecipe.freeze(json.loads(execution["recipe"].configuration),
                                  Readout("properties", "property/v1", "mw"), files=(resource,))
    endpoint = recipe.endpoint("mw", target="demo", quantity="mw", units="Da", cost=3)
    executor = CascadeExecutor(execution["root"], {endpoint.id: recipe})
    original = graph.verify_execution_plan

    def verify_then_change(*args, **kwargs):
        verified = original(*args, **kwargs)
        resource.write_bytes(b"changed during planning")
        return verified

    def forbidden(*args, **kwargs):
        pytest.fail("a known resource change must block before computation")

    monkeypatch.setattr(graph, "verify_execution_plan", verify_then_change)
    monkeypatch.setattr(Screen, "run", forbidden)
    result = executor(execution["action"], execution["candidate"], endpoint, None)
    assert result.status == "blocked" and result.cost == 0
    assert result.provenance["failure_code"] == "PROTOCOL_INPUT_CHANGED"


def test_existing_action_directory_is_never_silently_reused_or_reexecuted(execution, monkeypatch):
    result = execute(execution)
    assert result.status == "ok"
    selected = execution["root"] / execution["action"].id / "selected.csv"
    before = selected.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("a repeated action directory cannot relaunch a query")

    monkeypatch.setattr(Screen, "run", forbidden)
    with pytest.raises(FileExistsError):
        execute(execution)
    assert selected.read_bytes() == before


@pytest.mark.parametrize("protected", ["schema_version", "weights", "model_path"])
def test_parent_setting_replacement_cannot_remove_protected_child_fields(execution, tmp_path, protected):
    config = json.loads(execution["recipe"].configuration)
    value = 1
    if protected == "weights":
        value = "pinned-model-identifier"
    elif protected == "model_path":
        resource = tmp_path / "model.bin"
        resource.write_bytes(b"model sentinel")
        value = str(resource)
    config["tiers"][0]["criteria"][0]["settings"]["nested"] = {protected: value, "threshold": 2}
    base = CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))
    edit = {"op": "set_setting", "criterion_id": "properties", "path": "/nested",
            "expected": {protected: value, "threshold": 2}, "value": {"threshold": 3}}
    with pytest.raises(ValueError, match="protected|resource|schema"):
        mutate_recipe(base, [edit], DesignSpace((edit,)))


def test_resource_freeze_rejects_named_pipes_instead_of_blocking_on_read(execution, tmp_path):
    fifo = tmp_path / "not-a-regular-file"
    os.mkfifo(fifo)
    with pytest.raises(OSError, match="regular file"):
        CascadeRecipe.freeze(json.loads(execution["recipe"].configuration),
                             Readout("properties", "property/v1", "mw"), files=(fifo,))


def test_distinct_symlink_names_are_pinned_even_when_their_current_target_is_shared(execution, tmp_path):
    resource = tmp_path / "shared.bin"
    resource.write_bytes(b"shared input")
    first, second = tmp_path / "first.bin", tmp_path / "second.bin"
    first.symlink_to(resource)
    second.symlink_to(resource)
    recipe = CascadeRecipe.freeze(json.loads(execution["recipe"].configuration),
                                  Readout("properties", "property/v1", "mw"), files=(first, second))
    assert {name for name, _ in recipe.input_files} == {str(first), str(second)}
    assert len(inspect_recipe(recipe)["input_files"]) == 2


@pytest.mark.parametrize("attempts", [-1, True, 1.5])
def test_invalid_attempt_counts_cannot_become_graph_execution_evidence(execution, monkeypatch, attempts):
    original = Screen.run

    def wrong_count(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        return replace(result, stages=(*result.stages[:-1], replace(result.stages[-1], attempts=attempts)))

    monkeypatch.setattr(Screen, "run", wrong_count)
    assert_paid_rejected(execution, execute(execution), "PROTOCOL_GRAPH_MISMATCH")


def test_empty_artifact_identifier_is_not_complete_execution_evidence(execution, monkeypatch):
    original = Screen.run

    def absent_artifact(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        return replace(result, stages=(*result.stages[:-1], replace(result.stages[-1], artifact_id="")))

    monkeypatch.setattr(Screen, "run", absent_artifact)
    assert_paid_rejected(execution, execute(execution), "CASCADE_INCOMPLETE")


def test_keyboard_interrupt_during_artifact_read_propagates_for_explicit_recovery(execution, monkeypatch):
    original = Screen.read

    def interrupted(self, artifact_id, *, contract_id=None):
        if contract_id == "property/v1":
            raise KeyboardInterrupt("operator requested explicit recovery")
        return original(self, artifact_id, contract_id=contract_id)

    monkeypatch.setattr(Screen, "read", interrupted)
    with pytest.raises(KeyboardInterrupt, match="explicit recovery"):
        execute(execution)
    assert (execution["root"] / execution["action"].id / "selected.csv").exists()


def test_concurrent_calls_for_one_action_create_only_one_scientific_execution(execution, monkeypatch):
    original = Screen.run
    started = []

    def counted(self, *args, **kwargs):
        started.append(kwargs["run_id"])
        return original(self, *args, **kwargs)

    def attempt(_):
        try:
            return execute(execution)
        except FileExistsError:
            return None

    monkeypatch.setattr(Screen, "run", counted)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sum(outcome is None for outcome in outcomes) == 1
    assert [outcome.status for outcome in outcomes if outcome is not None] == ["ok"]
    assert started == ["query-" + execution["action"].id]


def test_mutation_refuses_a_symlink_resource_retarget_without_running_science(execution, tmp_path):
    first, second, alias = (tmp_path / name for name in ("before.bin", "after.bin", "input.bin"))
    first.write_bytes(b"before")
    second.write_bytes(b"after")
    alias.symlink_to(first)
    base = CascadeRecipe.freeze(json.loads(execution["recipe"].configuration),
                                Readout("properties", "property/v1", "mw"), files=(alias,))
    alias.unlink()
    alias.symlink_to(second)
    edit = {"op": "reorder_criteria", "tier_id": "measure", "order": ["after_readout", "properties"]}
    with pytest.raises(ValueError, match="resource changed"):
        mutate_recipe(base, [edit], DesignSpace((edit,)))
    assert not execution["root"].exists()
