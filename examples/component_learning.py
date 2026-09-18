"""CPU-only closed-loop example using real MolCascade components, not synthetic oracle labels.

Run after installing .[cascade]:
    python examples/component_learning.py --workspace runs/components

Molecular weight is used ONLY to exercise the engine; minimizing it is not drug discovery.
The second protocol is deliberately added after a completed round to illustrate redesign without
changing the first protocol's labels. Replace these readouts with your scientific endpoints.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from etalon.active import ActiveCampaign, CampaignSpec, CampaignStore
from etalon.active.adapters import MOLECULAR_REPRESENTATION, molecular_candidates
from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout
from etalon.campaign.design import component, compose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--policy", choices=("cost_aware", "mf_kg", "decision_aware"), default="cost_aware")
    args = parser.parse_args()
    properties = component("properties", "features.rdkit_properties@0.1.0")
    one = compose("one-component", [{"id": "measure", "title": "Properties only",
                                      "criteria": [properties]}])
    two = compose("redesigned", [{"id": "measure", "title": "SA score then properties", "criteria": [
        component("synthesis", "synthesis.rdkit_sa_score@0.1.0"), properties]}])
    first = CascadeRecipe.freeze(one, Readout("properties", "property/v1", "mw"))
    second = CascadeRecipe.freeze(two, Readout("properties", "property/v1", "clogp"))
    objective = first.endpoint("mw-v1", target="component-demo", quantity="molecular_weight", units="Da", cost=1)
    alternative = second.endpoint("clogp-v2", target="component-demo", quantity="logP", units="logP", cost=2)
    candidates, rejected = molecular_candidates({
        "ethanol": "CCO", "benzene": "c1ccccc1", "acetic-acid": "CC(=O)O", "ethylamine": "CCN",
        "pyridine": "c1ccncc1", "propanol": "CCCO", "phenol": "Oc1ccccc1", "acetamide": "CC(=O)N",
    })
    database = args.workspace / "campaign.sqlite"
    store = CampaignStore(database)
    spec = CampaignSpec(objective.id, 24, "demonstration_quotes", MOLECULAR_REPRESENTATION,
                        batch_size=2, policy=args.policy)
    # On resume the registered v2 endpoint already exists, so do not reconfigure the old snapshot.
    if not store.events():
        store.configure(spec, [objective])
    else:
        existing_spec, endpoints = store.configuration()
        if existing_spec != spec or endpoints[objective.id] != objective:
            raise ValueError("existing example journal uses a different configuration")
    store.add_candidates(candidates)
    executor = CascadeExecutor(args.workspace / "calculations", {objective.id: first})
    campaign = ActiveCampaign(store, executor)
    if not store.rounds():
        campaign.run_round()
    store.register_endpoints([alternative], rationale="demonstrate replacing a single component with a custom cascade")
    executor.register(alternative, second)
    result = campaign.run(max_rounds=24)
    print(json.dumps({"database": str(database.resolve()), "rejected_smiles": rejected,
                      "stop_reason": result["stop_reason"], "state": result["state"],
                      "recommendation": campaign.recommend()}, indent=2))


if __name__ == "__main__":
    main()
