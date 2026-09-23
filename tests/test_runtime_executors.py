"""Registered live CPU execution and controlled PRISM seams, without GPU or network."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_legacy_execution_integrity import simulation as _simulation_fixture

from etalon.active.runner import ActiveCampaign
from etalon.active.schema import Action, CampaignSpec, Candidate, Endpoint
from etalon.active.store import CampaignStore, StateError
from etalon.boundary.screen import Screen
from etalon.boundary.simulate import Environment, Simulate, _run_owned
from etalon.campaign.design import component, compose
from etalon.runtime.executors import (
    build_executor,
    prepare_executor,
    register_executor,
    registered_executors,
)

simulation = _simulation_fixture


def molecular_configuration(files=None):
    return {"cascade": compose("runtime-cpu", [{"id": "measure", "title": "Molecular properties",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}]),
        "readout": {"stage_id": "properties", "contract_id": "property/v1", "value_column": "mw"},
        "files": files or []}


def prepared_molecular(files=None):
    return prepare_executor("molcascade", molecular_configuration(files),
                            {"id": "mw", "target": "fixture", "quantity": "molecular_weight",
                             "units": "Da", "cost": 1.0})


def configured_store(tmp_path, *records):
    endpoints = [Endpoint(**record["endpoint"]) for record in records]
    store = CampaignStore(tmp_path / "campaign.sqlite")
    store.configure(CampaignSpec(endpoints[0].id, 10, "declared_cost", "fixture/1", batch_size=1), endpoints)
    return store


def prepared_prism(tmp_path, *, endpoint=None, configuration=None):
    receptor = tmp_path / "receptor.pdb"
    if not receptor.exists():
        receptor.write_text("explicitly fake receptor for registry tests\n")
    config = {"receptor_path": str(receptor), "python": sys.executable, "production_ns": 0.01,
              **(configuration or {})}
    return prepare_executor("prism", config, {"id": "md", "target": "fixture", "cost": 2.0, **(endpoint or {})})


def test_registered_cpu_executor_reconstructs_and_runs_real_measurements(tmp_path):
    prepared = prepared_molecular()
    store = configured_store(tmp_path, prepared)
    first = register_executor(store.path, prepared, "Explicit CPU properties endpoint")
    assert first["registered"] is True
    assert registered_executors(store.path) == {"mw": prepared}
    store.add_candidates([Candidate("ethanol", "CCO", (1.0,)), Candidate("benzene", "c1ccccc1", (2.0,))])
    checkpoints = []
    executor, receptor = build_executor(store.path, tmp_path / "queries", checkpoint=lambda: checkpoints.append(True))
    assert receptor is None
    report = ActiveCampaign(store, executor).run(max_rounds=2)
    assert report["state"]["admitted"] == 2 and len(checkpoints) == 2
    values = sorted(row["result"]["value"] for row in store.observations())
    assert values == pytest.approx([46.069, 78.114], abs=0.02)
    assert len(list((tmp_path / "queries").iterdir())) == 2
    before = store.events()
    assert register_executor(store.path, prepared, "Idempotent restart inspection")["registered"] is False
    assert store.events() == before


def test_registration_checks_the_existing_endpoint_before_any_registry_write(tmp_path):
    prepared = prepared_molecular()
    store = configured_store(tmp_path, prepared)
    changed = prepare_executor("molcascade", molecular_configuration(),
                               {**prepared["endpoint"], "units": "wrong"})
    before = store.export()
    with pytest.raises(ValueError, match="exactly match"):
        register_executor(store.path, changed, "Attempt incompatible endpoint registration")
    assert store.export() == before


def test_seed_and_executor_registration_share_one_rollback(tmp_path, monkeypatch):
    prepared = prepared_molecular()
    store = configured_store(tmp_path, prepared)
    original = CampaignStore._event

    def fail(db, kind, body):
        if kind == "runtime_executor_registered":
            raise OSError("journal failure after seed binding")
        return original(db, kind, body)

    monkeypatch.setattr(CampaignStore, "_event", staticmethod(fail))
    before = store.export()
    with pytest.raises(OSError, match="journal failure"):
        register_executor(store.path, prepared, "Atomic executor registration")
    assert store.export() == before
    with store.connection() as db:
        assert not db.execute("SELECT 1 FROM metadata WHERE key LIKE 'protocol:recipe:%'").fetchone()


def test_modified_preparation_is_refused_before_mutation(tmp_path):
    prepared = prepared_molecular()
    store = configured_store(tmp_path, prepared)
    changed = copy.deepcopy(prepared)
    changed["configuration"]["readout"]["value_column"] = "different"
    before = store.export()
    with pytest.raises(ValueError, match="identity"):
        register_executor(store.path, changed, "A modified request is not the reviewed executor")
    assert store.export() == before


def test_changed_input_is_refused_at_registration_build_and_dispatch(tmp_path, monkeypatch):
    resource = tmp_path / "resource.txt"
    resource.write_text("original")
    prepared = prepared_molecular([str(resource)])
    store = configured_store(tmp_path, prepared)
    resource.write_text("changed")
    with pytest.raises(ValueError):
        register_executor(store.path, prepared, "Refuse changed scientific input")
    assert registered_executors(store.path) == {}
    resource.write_text("original")
    register_executor(store.path, prepared, "Bind original resource bytes")
    executor, _ = build_executor(store.path, tmp_path / "queries")
    resource.write_text("changed")
    with pytest.raises(ValueError):
        build_executor(store.path, tmp_path / "other-queries")
    monkeypatch.setattr(Screen, "run", lambda *args, **kwargs: pytest.fail("changed resources were dispatched"))
    endpoint = Endpoint(**prepared["endpoint"])
    result = executor(Action("a", 1, "ethanol", endpoint.id, 0, 1),
                      Candidate("ethanol", "CCO", (1.0,)), endpoint, None)
    assert result.status == "blocked" and result.cost == 0
    assert not (tmp_path / "queries").exists()


def test_missing_queryable_dispatcher_refuses_but_historical_endpoint_needs_none(tmp_path):
    prepared = prepared_molecular()
    store = configured_store(tmp_path, prepared)
    with pytest.raises(ValueError, match="no registered executor"):
        build_executor(store.path, tmp_path / "queries")
    historical = Endpoint("history", "fixture", "Kd", "nM", "assay/1", 0,
                          requires_handoff=False, queryable=False)
    store.register_endpoints([historical], rationale="Imported data are not executable tasks")
    register_executor(store.path, prepared, "Register the actual online endpoint")
    build_executor(store.path, tmp_path / "queries")
    assert not (tmp_path / "queries").exists()


def test_pending_round_refuses_new_registration(tmp_path):
    prepared = prepared_molecular()
    store = configured_store(tmp_path, prepared)
    store.start_round({})
    with pytest.raises(StateError, match="pending"):
        register_executor(store.path, prepared, "Cannot bind while another controller is active")
    assert not registered_executors(store.path)


def test_checkpoint_interruption_happens_before_scientific_dispatch(tmp_path):
    prepared = prepared_molecular()
    store = configured_store(tmp_path, prepared)
    register_executor(store.path, prepared, "Prepare cancellation boundary fixture")
    store.add_candidates([Candidate("ethanol", "CCO", (1.0,))])

    def cancelled():
        raise KeyboardInterrupt("cancel requested")

    executor, _ = build_executor(store.path, tmp_path / "queries", checkpoint=cancelled)
    with pytest.raises(KeyboardInterrupt):
        ActiveCampaign(store, executor).run_round()
    assert len(store.status()["pending"]) == 1 and store.balance()["reserved"] == 1
    assert not (tmp_path / "queries").exists()


@pytest.mark.parametrize("field,value", [("quantity", "Kd"), ("units", "nM"), ("direction", "maximize"),
    ("requires_handoff", False), ("queryable", False), ("max_replicates", 2), ("max_replicates", True)])
def test_prism_endpoint_cannot_mislabel_the_available_readout(tmp_path, field, value):
    with pytest.raises(ValueError, match="executor requires"):
        prepared_prism(tmp_path, endpoint={field: value})


@pytest.mark.parametrize("configuration", [{"production_ns": 0}, {"production_ns": True},
    {"production_ns": float("inf")}, {"timeout_per_molecule": -1}, {"python": "relative-python"}])
def test_prism_requires_bounded_explicit_execution_configuration(tmp_path, configuration):
    with pytest.raises(ValueError):
        prepared_prism(tmp_path, configuration=configuration)


def test_prism_registration_never_runs_environment_or_scientific_tools(tmp_path, monkeypatch):
    monkeypatch.setattr("etalon.boundary.simulate.discover", lambda *args, **kwargs: pytest.fail("environment was executed"))
    monkeypatch.setattr(Simulate, "build", lambda *args, **kwargs: pytest.fail("science was executed"))
    prepared = prepared_prism(tmp_path)
    store = configured_store(tmp_path, prepared)
    register_executor(store.path, prepared, "Explicit fixed MM/PBSA readout contract")
    _, receptor = build_executor(store.path, tmp_path / "queries")
    assert receptor == tmp_path / "receptor.pdb"
    assert prepared["endpoint"]["quantity"] == "mmpbsa"
    assert set(prepared["inputs"]) == {sys.executable, str(receptor)}
    assert not (tmp_path / "queries").exists()


def test_prism_dispatch_keeps_action_workspaces_and_does_not_invent_affinity(tmp_path, monkeypatch):
    from etalon.authority.grant import authorize
    from etalon.campaign.expensive import PrismStage
    from etalon.learn.admissible import Measurement

    prepared = prepared_prism(tmp_path)
    store = configured_store(tmp_path, prepared)
    register_executor(store.path, prepared, "Only test PRISM dispatcher wiring with a fake stage")
    def observer(pid):
        pass

    executor, receptor = build_executor(store.path, tmp_path / "queries", process_observer=observer)
    monkeypatch.setattr("etalon.boundary.simulate.discover", lambda python:
                        Environment(Path(python), tmp_path / "fixture-gmx"))
    monkeypatch.setattr("etalon.faults.preflight.check_record", lambda *args, **kwargs: ())
    seen = []

    def no_affinity(self, rows, cheap, grants):
        seen.append((self.simulate.workspace, self.simulate.process_observer, self.production_ns))
        return [Measurement(rows[0]["parent_id"], None, None,
                            provenance={"no_binding_energy": "fake completed MD produced no MM/PBSA file"})]

    monkeypatch.setattr(PrismStage, "__call__", no_affinity)
    endpoint = Endpoint(**prepared["endpoint"])
    candidate = Candidate("ethanol", "CCO", (1.0,), handoff={"parent_id": "ethanol", "parent_smiles": "CCO"})
    grant = authorize([candidate.handoff], receptor_path=receptor).grants[candidate.id]
    for action_id in ("a", "b"):
        result = executor(Action(action_id, 1, candidate.id, endpoint.id, 0, endpoint.cost), candidate, endpoint, grant)
        assert result.status == "failed" and result.value is None and result.cost == endpoint.cost
        assert result.provenance["cost_basis"] == "endpoint quote; not measured"
    assert seen == [(tmp_path / "queries/a", observer, 0.01), (tmp_path / "queries/b", observer, 0.01)]


def test_prism_rejects_inconsistent_receptors_before_dispatch(tmp_path):
    first = prepared_prism(tmp_path)
    other = tmp_path / "other.pdb"
    other.write_text("different receptor")
    second = prepared_prism(tmp_path, endpoint={"id": "md2"}, configuration={"receptor_path": str(other)})
    store = configured_store(tmp_path, first, second)
    for prepared in (first, second):
        register_executor(store.path, prepared, "Register explicit receptor fixture")
    with pytest.raises(ValueError, match="consistent PRISM receptor"):
        build_executor(store.path, tmp_path / "queries")
    assert not (tmp_path / "queries").exists()


def test_prism_build_and_drive_forward_process_observer(simulation, monkeypatch):
    from etalon.boundary import simulate as module

    runner, protein, ligand, _, _ = simulation
    seen = []
    original = module._run_owned
    runner.process_observer = seen.append

    def observe(command, **kwargs):
        kwargs["process_observer"](12345)
        return original(command, **kwargs)

    monkeypatch.setattr(module, "_run_owned", observe)
    build = runner.build(protein, ligand, run_id="forwarding")
    runner.drive(build)
    assert seen == [12345, 12345]


@pytest.mark.skipif(os.name != "posix", reason="owned process groups require POSIX")
def test_process_observer_failure_cleans_newly_started_owned_process():
    observed = []
    neighbour = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], start_new_session=True)

    def cannot_record(pid):
        observed.append(pid)
        raise OSError("ownership journal cannot be written")

    try:
        result = _run_owned([sys.executable, "-c", "import time; time.sleep(10)"],
                            timeout=3, env=os.environ, process_observer=cannot_record)
        assert result.status == "failed" and observed == [result.process_group]
        assert result.cleanup["group_kill_sent"] and result.cleanup["leader_reaped"]
        assert "ownership journal" in result.cleanup["capture_error"]
        with pytest.raises(ProcessLookupError):
            os.kill(result.process_group, 0)
        assert neighbour.poll() is None
    finally:
        neighbour.kill()
        neighbour.wait(timeout=2)


def test_dispatch_rejects_endpoint_changes_before_creating_workspace(tmp_path):
    prepared = prepared_molecular()
    store = configured_store(tmp_path, prepared)
    register_executor(store.path, prepared, "Only the prepared endpoint may execute")
    executor, _ = build_executor(store.path, tmp_path / "queries")
    endpoint = replace(Endpoint(**prepared["endpoint"]), units="mislabelled")
    with pytest.raises(ValueError, match="not bound"):
        executor(Action("a", 1, "ethanol", endpoint.id, 0, 1), Candidate("ethanol", "CCO", (1.0,)), endpoint, None)
    assert not (tmp_path / "queries").exists()
