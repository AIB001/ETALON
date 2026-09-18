"""Real CPU component regressions for plan binding and changed-input rejection.

The pinned file is a declared resource sentinel, not a synthetic scientific label.
Hash checks detect persistent changes at checkpoints, not change-and-restore attacks.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout
from etalon.active.graph import inspect_recipe
from etalon.active.schema import Action, Candidate
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose


@pytest.fixture
def execution_case(tmp_path):
    resource = tmp_path / "declared-resource.bin"
    resource.write_bytes(b"frozen-resource-v1")
    config = compose("execution-integrity", [{
        "id": "measure", "title": "Only requested CPU properties", "mode": "serial",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0",
                               settings={"include_sa_score": False})],
    }])
    recipe = CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"),
                                  files=(resource,))
    endpoint = recipe.endpoint("mw", target="test", quantity="molecular_weight", units="Da", cost=2.5)
    candidate = Candidate("ethanol", "CCO", (1.0,))
    action = Action("integrity-action", 1, candidate.id, endpoint.id, 0, endpoint.cost, {})
    workspace = tmp_path / "executions"
    executor = CascadeExecutor(workspace, {endpoint.id: recipe})
    return {"resource": resource, "recipe": recipe, "endpoint": endpoint, "candidate": candidate,
            "action": action, "workspace": workspace, "executor": executor}


def _execute(case):
    return case["executor"](case["action"], case["candidate"], case["endpoint"], None)


def _change(path, operation):
    if operation == "delete":
        path.unlink()
    else:
        path.write_bytes(b"changed-resource-v2")


def _assert_paid_invalid(case, result, code):
    assert result.status == "invalid", result.as_dict()
    assert result.value is None
    assert result.cost == case["endpoint"].cost
    assert result.provenance["failure_code"] == code
    assert result.provenance["action_id"] == case["action"].id
    assert result.provenance["candidate_id"] == case["candidate"].id


@pytest.mark.parametrize("operation", ["change", "delete"])
def test_missing_or_changed_resource_before_dispatch_is_free_and_creates_no_workspace(
        execution_case, monkeypatch, operation):
    case = execution_case
    _change(case["resource"], operation)

    def forbidden(*args, **kwargs):
        raise AssertionError("a blocked protocol must not plan or execute")

    monkeypatch.setattr(Screen, "plan", forbidden)
    monkeypatch.setattr(Screen, "run", forbidden)
    result = _execute(case)
    assert result.status == "blocked" and result.value is None and result.cost == 0
    assert result.provenance["failure_code"] == "PROTOCOL_INPUT_CHANGED"
    assert not case["workspace"].exists()


@pytest.mark.parametrize("operation", ["change", "delete"])
def test_resource_changed_while_run_returns_is_not_admitted_or_refunded(execution_case, monkeypatch, operation):
    case = execution_case
    original = Screen.run
    runs = []

    def run_then_change(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        assert not result.failed
        runs.append(result.run_id)
        _change(case["resource"], operation)
        return result

    monkeypatch.setattr(Screen, "run", run_then_change)
    result = _execute(case)
    assert runs == ["query-" + case["action"].id]
    _assert_paid_invalid(case, result, "PROTOCOL_INPUT_CHANGED")
    assert "after execution" in result.provenance["error"]
    assert result.provenance["evidence_graph"]["readout"]["available"]


@pytest.mark.parametrize("operation", ["change", "delete"])
def test_resource_changed_during_verified_read_is_checked_again_before_admission(
        execution_case, monkeypatch, operation):
    case = execution_case
    original = Screen.read
    changed = []

    def read_then_change(self, artifact_id, *, contract_id=None):
        rows = original(self, artifact_id, contract_id=contract_id)
        if contract_id == "property/v1" and not changed:
            _change(case["resource"], operation)
            changed.append(True)
        return rows

    monkeypatch.setattr(Screen, "read", read_then_change)
    result = _execute(case)
    assert changed
    _assert_paid_invalid(case, result, "PROTOCOL_INPUT_CHANGED")
    assert "before admission" in result.provenance["error"]


@pytest.mark.parametrize("phase", ["run", "read"])
def test_selected_action_library_changed_during_execution_is_not_admitted(execution_case, monkeypatch, phase):
    case = execution_case
    selected = case["workspace"] / case["action"].id / "selected.csv"
    changed = []
    if phase == "run":
        original = Screen.run

        def run_then_change(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            assert not result.failed
            selected.write_text("id,smiles\nethanol,CCN\n", encoding="utf-8")
            changed.append(True)
            return result

        monkeypatch.setattr(Screen, "run", run_then_change)
    else:
        original = Screen.read

        def read_then_change(self, artifact_id, *, contract_id=None):
            rows = original(self, artifact_id, contract_id=contract_id)
            if contract_id == "property/v1":
                selected.write_text("id,smiles\nethanol,CCN\n", encoding="utf-8")
                changed.append(True)
            return rows

        monkeypatch.setattr(Screen, "read", read_then_change)
    result = _execute(case)
    assert changed
    _assert_paid_invalid(case, result, "PROTOCOL_INPUT_CHANGED")
    assert "selected.csv" in result.provenance["error"]


@pytest.mark.parametrize("part", ["compiled_settings", "source_settings", "pipeline", "revision"])
def test_plan_mismatch_is_blocked_before_the_scientific_runner(execution_case, monkeypatch, part):
    case = execution_case
    original = Screen.plan

    def tampered_plan(self, *args, **kwargs):
        plan = original(self, *args, **kwargs)
        if part == "revision":
            return replace(plan, revision_id="0" * 64)
        if part == "pipeline":
            from molcascade.config.models import PipelineConfig

            config = plan._internal["pipeline"].model_dump(mode="json")
            config["stages"][-1]["config"]["include_sa_score"] = True
            plan._internal["pipeline"] = PipelineConfig.model_validate(config)
        else:
            compiled = plan._internal["compiled"]
            stages = list(compiled.stages)
            index = 0 if part == "source_settings" else -1
            settings = {**dict(stages[index].config), "batch_size": 123}
            stages[index] = replace(stages[index], config=settings)
            plan._internal["compiled"] = replace(compiled, stages=tuple(stages))
        return plan

    def forbidden(*args, **kwargs):
        raise AssertionError("mismatched execution plan must never run")

    monkeypatch.setattr(Screen, "plan", tampered_plan)
    monkeypatch.setattr(Screen, "run", forbidden)
    result = _execute(case)
    assert result.status == "blocked" and result.value is None and result.cost == 0
    assert result.provenance["failure_code"] == "PROTOCOL_PLAN_MISMATCH"
    assert "run" not in result.provenance


@pytest.mark.parametrize("field", ["revision_id", "run_id"])
def test_result_revision_and_action_identity_must_match_the_verified_plan(execution_case, monkeypatch, field):
    case = execution_case
    original = Screen.run

    def run_then_misidentify(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        assert not result.failed
        return replace(result, **{field: "different-execution"})

    monkeypatch.setattr(Screen, "run", run_then_misidentify)
    result = _execute(case)
    _assert_paid_invalid(case, result, "PROTOCOL_PLAN_MISMATCH")
    assert "evidence_graph" not in result.provenance


def test_wrong_plugin_in_successful_stage_outcome_does_not_acquire_graph_provenance(execution_case, monkeypatch):
    case = execution_case
    original = Screen.run

    def run_then_change_plugin(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        stages = (*result.stages[:-1], replace(result.stages[-1], plugin="different.plugin@0.1.0"))
        return replace(result, stages=stages)

    monkeypatch.setattr(Screen, "run", run_then_change_plugin)
    result = _execute(case)
    _assert_paid_invalid(case, result, "PROTOCOL_GRAPH_MISMATCH")


def test_real_success_links_action_candidate_verified_plan_and_evidence_graph(execution_case):
    from rdkit import Chem
    from rdkit.Chem import Descriptors

    case = execution_case
    graph = inspect_recipe(case["recipe"])
    result = _execute(case)
    assert result.status == "ok", result.as_dict()
    assert result.value == pytest.approx(Descriptors.MolWt(Chem.MolFromSmiles(case["candidate"].smiles)))
    assert result.cost == case["endpoint"].cost
    provenance = result.provenance
    assert provenance["mode"] == "live_molcascade"
    assert provenance["action_id"] == case["action"].id
    assert provenance["candidate_id"] == result.candidate_id == case["candidate"].id
    assert provenance["protocol_id"] == case["recipe"].protocol_id
    verified, lineage = provenance["execution_plan_verification"], provenance["evidence_graph"]
    assert lineage["graph_hash"] == verified["graph_hash"] == graph["graph_hash"]
    assert lineage["protocol_id"] == verified["protocol_id"] == provenance["protocol_id"]
    assert lineage["template_revision_id"] == verified["template_revision_id"] == graph["revision_id"]
    assert verified["runtime_revision_id"] == provenance["plan"]["revision_id"] == provenance["run"]["revision_id"]
    assert verified["runtime_revision_id"] != verified["template_revision_id"]
    assert verified["verified_stage_count"] == len(lineage["stages"]) == 3
    assert lineage["readout"]["available"]
    assert lineage["readout"]["artifact_id"] == provenance["artifact_id"]
    assert all(node["available"] for node in lineage["stages"])
    assert "not_performed" in verified["artifact_verification"]
