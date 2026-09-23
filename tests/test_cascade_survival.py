"""Post-readout gates must govern endpoint admission, including parallel joins."""

from __future__ import annotations

import pytest

from etalon.active import ActiveCampaign, CampaignSpec, CampaignStore
from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout
from etalon.active.schema import Action, Candidate
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose


def _recipe(*, mode="serial", pass_branch=False, export=False):
    gates = [component("reject", "chemistry.rdkit_property_range_gate@0.1.0",
                       settings={"mw_min": 100})]
    if pass_branch:
        gates.append(component("accept", "chemistry.rdkit_property_range_gate@0.1.0",
                               settings={"mw_min": 0}))
    config = compose("post-readout-gate", [
        {"id": "measure", "title": "Early readout", "criteria": [
            component("properties", "features.rdkit_properties@0.1.0")]},
        {"id": "selection", "title": "Subsequent admission gate", "mode": mode,
         "criteria": gates},
    ], finalize={"steps": [{"id": "export", "backend": "export.rdkit_sdf_shortlist@0.1.0",
                            "settings": {"schema_version": 1}}]} if export else None)
    return CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))


def _execute(tmp_path, protocol):
    endpoint = protocol.endpoint("mw", target="test", quantity="molecular_weight", units="Da", cost=2)
    candidate = Candidate("ethanol", "CCO", (1.0,))
    action = Action("survival", 1, candidate.id, endpoint.id, 0, endpoint.cost, {})
    return CascadeExecutor(tmp_path, {endpoint.id: protocol})(action, candidate, endpoint, None)


def test_late_gate_refusal_is_costed_and_never_enters_training(tmp_path):
    protocol = _recipe()
    endpoint = protocol.endpoint("mw", target="test", quantity="molecular_weight", units="Da", cost=2)
    store = CampaignStore(tmp_path / "campaign.sqlite")
    store.configure(CampaignSpec(endpoint.id, 2, "CPU_quotes", "test/1", batch_size=1), [endpoint])
    store.add_candidates([Candidate("ethanol", "CCO", (1.0,))])
    campaign = ActiveCampaign(store, CascadeExecutor(tmp_path / "runs", {endpoint.id: protocol}))
    campaign.run(max_rounds=1)
    observation, = store.observations()
    result = observation["result"]
    assert result["status"] == "invalid" and result["value"] is None
    assert result["provenance"]["failure_code"] == "CANDIDATE_FILTERED"
    assert result["provenance"]["raw_readout"]["value"] == pytest.approx(46.069)
    assert result["provenance"]["raw_readout"]["artifact_id"]
    assert result["provenance"]["terminal_population"]["candidate_survived"] is False
    assert not observation["admitted"] and store.observations(admitted_only=True) == []
    assert store.balance()["spent"] == 2
    provenance = result["provenance"]
    screen = Screen(provenance["workspace"])
    artifact = provenance["terminal_population"]["artifact_id"]
    assert screen.read(artifact, contract_id="parent/v1") == []
    with pytest.raises(KeyError):
        screen.read(artifact, contract_id="docking_score/v1")


@pytest.mark.parametrize("mode,accepted,export", [("any", True, False), ("all", False, False),
                                                ("any", True, True)])
def test_final_join_controls_survival_and_exporter_preserves_it(tmp_path, mode, accepted, export):
    result = _execute(tmp_path, _recipe(mode=mode, pass_branch=True, export=export))
    terminal = result.provenance["terminal_population"]
    assert terminal["candidate_survived"] is accepted
    assert terminal["stage_id"] not in {"reject", "accept"}
    if export:
        # MolCascade's SDF exporter explicitly passes through the final parents.
        assert terminal["stage_id"] == "export"
    assert result.status == ("ok" if accepted else "invalid")
    if accepted:
        assert result.value == pytest.approx(46.069)
    else:
        assert result.value is None
    assert result.cost == 2
    if not accepted:
        assert result.provenance["failure_code"] == "CANDIDATE_FILTERED"
