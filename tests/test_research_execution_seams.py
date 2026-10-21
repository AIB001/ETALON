"""Research advice to runtime seams; synthetic Journal, no worker or science dispatch."""
from pathlib import Path
import shutil

import pytest

from etalon.active.store import StateError
from etalon.research import Coupling, Experiment, Observation
from etalon.research.diagnostics import exact_program
from etalon.research.runtime import compile_first_segment, observed_branch, record_program
from etalon.runtime import service
from test_research_optional_methods import build_optional_fixture


def _forbidden(*args, **kwargs):
    raise AssertionError("compilation/observation must not submit an execution")


def _binding(store, refs):
    return {"x": {"operation": "design.import", "arguments": {
        "database": str(store.path), "bundle_path": refs["artifact_path"], "mode": "index_only"}}}


def test_compile_one_selected_segment_and_stop_without_touching_future_or_pending(tmp_path, monkeypatch):
    store, journal, refs, _, model = build_optional_fixture(tmp_path / "fixture")
    monkeypatch.setattr(service, "submit", _forbidden)
    monkeypatch.setattr(service, "_launch", _forbidden)
    balance, labels = store.balance(), store.observations()
    bindings = _binding(store, refs)
    bindings["z"] = {"operation": "not_installed_future_operation", "arguments": {}}
    result = compile_first_segment(journal, refs["plan_id"], bindings)
    assert result["status"] == "compiled_not_executed"
    assert result["probe_id"] == "x"
    nodes = result["workflow"]["nodes"]
    assert len(nodes) == 1 and nodes[0]["operation"] == "design.import"
    assert nodes[0]["resources"] == {model.cost_unit: .35}
    # Schema compilation is not execution readiness: the supplied diagnostic
    # JSON is not a certified design bundle, and the real runtime planner refuses.
    events = store.events()
    with pytest.raises(ValueError, match="design bundle schema"):
        service.plan(result["workflow"])
    assert compile_first_segment(journal, refs["plan_id"], bindings) == result
    assert store.events() == events and store.balance() == balance
    assert balance["spent"] == .25 and balance["reserved"] == 2
    assert store.observations() == labels == []


def test_valid_isolated_bundle_segment_reaches_runtime_plan_without_import_or_dispatch(tmp_path, monkeypatch):
    store, journal, refs, _, _ = build_optional_fixture(tmp_path / "fixture")
    target = tmp_path / "engineering-bundle"
    shutil.copytree(Path(__file__).parent / "fixtures/prism_design_bundle_v1", target)
    bindings = _binding(store, refs)
    bindings["x"]["arguments"]["bundle_path"] = str(target / "bundle.json")
    monkeypatch.setattr(service, "submit", _forbidden)
    monkeypatch.setattr(service, "_launch", _forbidden)
    receipt = compile_first_segment(journal, refs["plan_id"], bindings)
    events, balance = store.events(), store.balance()
    prepared = service.plan(receipt["workflow"])
    assert len(prepared["spec"]["nodes"]) == 1 and len(prepared["inputs"]) == 27
    assert all(Path(path).is_relative_to(target) for path in prepared["inputs"])
    assert prepared == service.plan(receipt["workflow"])
    assert store.events() == events and store.balance() == balance
    assert store.candidates() == {} and store.observations() == []


@pytest.mark.parametrize("case", ["missing", "future", "unregistered"])
def test_invalid_first_bindings_refuse_before_segment_append(tmp_path, case):
    store, journal, refs, _, _ = build_optional_fixture(tmp_path / "fixture")
    bindings = _binding(store, refs)
    if case == "missing":
        bindings = {}
    elif case == "future":
        bindings["x"]["arguments"]["bundle_path"] = {"$ref": "future.path"}
    else:
        bindings["x"]["operation"] = "unregistered.execute"
    before, balance = store.events(), store.balance()
    with pytest.raises((StateError, ValueError)):
        compile_first_segment(journal, refs["plan_id"], bindings)
    assert store.events() == before and store.balance() == balance


def test_explicit_stop_has_no_segment_and_no_state_change(tmp_path):
    store, journal, refs, _, model = build_optional_fixture(tmp_path / "fixture")
    stop = exact_program(model, budget=.44, max_depth=2, cost_weight=1)
    assert stop["program"]["kind"] == "stop"
    program_id = record_program(journal, model, stop)
    before, balance = store.events(), store.balance()
    with pytest.raises(StateError, match="query|stop"):
        compile_first_segment(journal, program_id, _binding(store, refs))
    assert store.events() == before and store.balance() == balance


def test_program_evidence_changed_after_recording_refuses_new_segment(tmp_path):
    store, journal, refs, _, _ = build_optional_fixture(tmp_path / "fixture")
    Path(refs["artifact_path"]).write_text("changed after frozen program", encoding="utf-8")
    before, balance = store.events(), store.balance()
    with pytest.raises(StateError, match="hash|artifact|evidence"):
        compile_first_segment(journal, refs["plan_id"], _binding(store, refs))
    assert store.events() == before and store.balance() == balance


def _observation(journal, refs, *, condition="synthetic", status="measured"):
    experiment = Experiment("fixture-task", "file_audit", {"condition": condition, "probe_id": "x"},
                            (refs["artifact"],), "fixed-readout/1", "branch-instance-" + condition,
                            ("signal",), Coupling())
    journal.record(experiment)
    observation = Observation(experiment.id, status, {"signal": "low"} if status == "measured" else {},
                              {"signal": "category"} if status == "measured" else {},
                              (refs["artifact"],), "" if status == "measured" else "fixture execution failed")
    journal.record(observation)
    return observation.id


def test_same_probe_wrong_explicit_condition_cannot_be_used_as_observed_branch(tmp_path):
    store, journal, refs, _, _ = build_optional_fixture(tmp_path / "fixture")
    observation = _observation(journal, refs, condition="different-preparation")
    before, balance = store.events(), store.balance()
    with pytest.raises(StateError, match="binding|condition|probe"):
        observed_branch(journal, refs["plan_id"], observation, readout="signal")
    assert store.events() == before and store.balance() == balance


def test_recorded_failure_is_model_challenge_without_fee_or_pending_erasure(tmp_path, monkeypatch):
    store, journal, refs, _, _ = build_optional_fixture(tmp_path / "fixture")
    observation = _observation(journal, refs, status="failed")
    monkeypatch.setattr(service, "submit", _forbidden)
    before, balance = store.events(), store.balance()
    result = observed_branch(journal, refs["plan_id"], observation, readout="signal")
    assert result["status"] == "model_challenge" and result["outcome"] == "failed"
    assert result["program"] is None and result["requires_replanning"] is True
    assert journal.get(observation)["values"] == {}
    assert store.events() == before and store.balance() == balance
    assert balance["spent"] == .25 and balance["reserved"] == 2
