"""Real CPU-only component execution: the harness must not impose a default funnel."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from etalon.active import ActiveCampaign, CampaignSpec, CampaignStore, Candidate, StateError
from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout
from etalon.boundary.screen import Screen
from etalon.campaign.design import catalogue, component, compose


def recipe(*, two=False):
    criteria = [component("properties", "features.rdkit_properties@0.1.0")]
    if two:
        criteria.append(component("second_properties", "features.rdkit_properties@0.1.0"))
    config = compose("custom-minimal", [{"id": "measure", "title": "Requested components only",
                                         "mode": "serial", "criteria": criteria}])
    return CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))


def test_single_component_executes_on_real_molecules_without_default_tiers(tmp_path):
    from rdkit import Chem
    from rdkit.Chem import Descriptors

    protocol = recipe()
    endpoint = protocol.endpoint("mw-v1", target="demo", quantity="molecular_weight", units="Da", cost=1)
    store = CampaignStore(tmp_path / "journal.sqlite")
    store.configure(CampaignSpec(endpoint.id, 3, "CPU_quotes", "test/1", batch_size=1), [endpoint])
    smiles = {"a": "CCO", "b": "c1ccccc1", "c": "CC(=O)O"}
    store.add_candidates([Candidate(key, text, (float(i),)) for i, (key, text) in enumerate(smiles.items())])
    executor = CascadeExecutor(tmp_path / "runs", {endpoint.id: protocol})
    result = ActiveCampaign(store, executor).run(max_rounds=3)
    observations = store.observations()
    assert result["state"]["admitted"] == 3, observations
    assert [r["model"]["training_size"] for r in store.rounds()] == [0, 1, 2]
    for entry in observations:
        measured = entry["result"]
        assert measured["value"] == pytest.approx(Descriptors.MolWt(Chem.MolFromSmiles(smiles[measured["candidate_id"]])))
        assert measured["provenance"]["plan"]["tiers"][0]["criteria"] == ["properties"]
        assert len(measured["provenance"]["plan"]["tiers"]) == 1
        assert measured["provenance"]["molcascade_parent_id"]
    assert len(list((tmp_path / "runs").iterdir())) == 3


def test_flat_pipeline_and_reordered_custom_cascades_compile(tmp_path):
    library = tmp_path / "molecules.csv"
    library.write_text("id,smiles\na,CCO\n", encoding="utf-8")
    original = json.loads(recipe(two=True).configuration)
    path = tmp_path / "cascade.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    screen = Screen(tmp_path / "runs")
    first = screen.plan(path, library)
    assert first.tiers[0]["criteria"] == ["properties", "second_properties"]
    original["tiers"][0]["criteria"].reverse()
    path.write_text(json.dumps(original), encoding="utf-8")
    second = screen.plan(path, library)
    assert first.revision_id != second.revision_id
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps(first._internal["pipeline"].model_dump(mode="json")), encoding="utf-8")
    plan = screen.plan(flat)
    assert plan.configuration_kind == "pipeline"
    assert plan.tiers == () and plan.revision_id == first.revision_id
    run = screen.run(plan)
    assert not run.failed
    with pytest.raises(ValueError, match="bind their own inputs"):
        screen.plan(flat, library)


def test_new_protocol_can_be_added_between_rounds_but_cannot_rewrite_old_labels(tmp_path):
    original = recipe()
    redesigned = recipe(two=True)
    first = original.endpoint("v1", target="t", quantity="mw", units="Da", cost=1)
    second = redesigned.endpoint("v2", target="t", quantity="mw", units="Da", cost=2)
    store = CampaignStore(tmp_path / "journal.sqlite")
    store.configure(CampaignSpec("v1", 10, "credits", "test/1", batch_size=1), [first])
    store.add_candidates([Candidate("a", "CCO", (1.0,)), Candidate("b", "CCN", (2.0,))])
    executor = CascadeExecutor(tmp_path / "runs", {first.id: original})
    campaign = ActiveCampaign(store, executor)
    campaign.run_round()
    historical = store.observations()
    assert historical[0]["admitted"], historical
    store.register_endpoints([second], rationale="evaluate a redesigned component graph")
    executor.register(second, redesigned)
    assert store.configuration()[0].objective == "v1"
    assert set(campaign.plan()[0].endpoints) == {"v1", "v2"}
    assert store.observations() == historical
    campaign.run(max_rounds=3)
    assert any(o["admitted"] and o["result"]["endpoint_id"] == "v2" for o in store.observations())
    assert original.protocol_id != redesigned.protocol_id
    with pytest.raises(StateError, match="existing id"):
        store.register_endpoints([replace(second, id="v1")], rationale="incorrect overwrite")
    assert any(e["kind"] == "endpoint_registered" for e in store.events())


def test_recipe_pins_file_contents_and_refuses_changed_input_before_spending(tmp_path):
    file = tmp_path / "reference.dat"
    file.write_text("v1", encoding="utf-8")
    original = recipe()
    pinned = CascadeRecipe.freeze(json.loads(original.configuration),
                                  Readout("properties", "property/v1", "mw"), files=(file,))
    endpoint = pinned.endpoint("v1", target="t", quantity="mw", units="Da", cost=1)
    store = CampaignStore(tmp_path / "journal.sqlite")
    store.configure(CampaignSpec("v1", 10, "credits", "test/1"), [endpoint])
    store.add_candidates([Candidate("a", "CCO", (1.0,))])
    file.write_text("v2", encoding="utf-8")
    campaign = ActiveCampaign(store, CascadeExecutor(tmp_path / "runs", {endpoint.id: pinned}))
    campaign.run_round()
    assert store.actions()[0]["status"] == "blocked"
    assert store.balance()["spent"] == 0
    assert not (tmp_path / "runs").exists()


def test_component_catalogue_is_discoverable_without_executing_default_funnel():
    result = catalogue()
    assert result["criteria"]
    assert any(plugin["key"] == "features.rdkit_properties@0.1.0" for plugin in result["plugins"])


def test_component_composition_preserves_the_selected_tautomer(tmp_path):
    protocol = recipe()
    config = json.loads(protocol.configuration)
    assert config["standardize"]["settings"]["identity_policy"]["tautomer_policy"] == "preserve"
    endpoint = protocol.endpoint("mw", target="t", quantity="mw", units="Da", cost=1)
    store = CampaignStore(tmp_path / "tautomer.sqlite")
    store.configure(CampaignSpec("mw", 1, "quotes", "test/1"), [endpoint])
    store.add_candidates([Candidate("lactam", "O=c1cccc[nH]1", (1.0,))])
    campaign = ActiveCampaign(store, CascadeExecutor(tmp_path / "runs", {endpoint.id: protocol}))
    campaign.run_round()
    assert store.observations()[0]["admitted"], store.observations()
