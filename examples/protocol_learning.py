"""Real CPU component pilot of an auditable protocol-evolution outer loop.

The objective is molecular weight ONLY to test the engineering. Adding SA scoring does not
improve MW accuracy; rollout criteria certify sample availability, NOT scientific superiority.
This example does not search arbitrary graphs or run docking, MD or paid model APIs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from etalon.active import ActiveCampaign, CampaignSpec, CampaignStore
from etalon.active.adapters import MOLECULAR_REPRESENTATION, molecular_candidates
from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout
from etalon.active.mutations import DesignSpace
from etalon.active.protocols import ProtocolRegistry, TrialPolicy
from etalon.campaign.design import component, compose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="save the report exclusively; never overwrite an existing file")
    args = parser.parse_args()
    database = args.workspace / "campaign.sqlite"
    if database.exists():
        raise FileExistsError("use a new example workspace; inspect old journals with 'active protocols'")
    if args.output is not None and args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    properties = component("properties", "features.rdkit_properties@0.1.0")
    config = compose("mw-seed", [{"id": "measure", "title": "Explicit components", "criteria": [properties]}])
    seed = CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))
    objective = seed.endpoint("mw-reference", target="protocol-demo", quantity="molecular_weight", units="Da", cost=1)
    store = CampaignStore(database)
    store.configure(CampaignSpec(objective.id, 24, "demonstration_quotes", MOLECULAR_REPRESENTATION,
                                 batch_size=1, policy="decision_aware"), [objective])
    candidates, rejected = molecular_candidates({
        "ethanol": "CCO", "ethylamine": "CCN", "benzene": "c1ccccc1", "pyridine": "c1ccncc1",
        "phenol": "Oc1ccccc1", "propanol": "CCCO", "acetic-acid": "CC(=O)O", "acetamide": "CC(=O)N",
    })
    store.add_candidates(candidates)
    registry = ProtocolRegistry(store)
    registry.bind_seed(objective.id, seed, rationale="reviewed CPU MW reference")
    edit = {"op": "insert_criterion", "tier_id": "measure", "before": "properties",
            "criterion": component("synthesis", "synthesis.rdkit_sa_score@0.1.0")}
    space = DesignSpace((edit,), max_edits=1)
    space_id = registry.bind_space(space, rationale="allow only this explicit CPU SA-before-properties variant")
    executor = CascadeExecutor.from_journal(args.workspace / "calculations", store)
    campaign = ActiveCampaign(store, executor)
    campaign.run(max_rounds=4)  # Real objective data, NOT uncharged historical warm-start labels.
    historical = store.observations()
    proposal = registry.propose(objective.id, "mw-with-sa", space_id=space_id, edits=[edit], cost=1,
                                rationale="test a custom component graph under a fixed objective",
                                proposed_by="reviewed-example")
    certificate = registry.validate(proposal)
    if not certificate["ok"]:
        raise RuntimeError(certificate)
    registry.start_trial(proposal, TrialPolicy(budget=3, max_actions=3, min_admitted=3, min_pairs=3,
                                             max_failure_fraction=0),
                         rationale="authorize at most three quoted CPU queries for this variant")
    # Rehydration shows that a separate process can restore the approved recipe, not an
    # in-memory dispatcher mutation pretending to be durable registration.
    resumed = CampaignStore(database)
    campaign = ActiveCampaign(resumed, CascadeExecutor.from_journal(args.workspace / "calculations", resumed))
    campaign.run(max_rounds=3)
    report = registry.report(proposal)
    registry.promote(proposal, rationale="three admitted paired observations satisfy the preset operational criteria")
    before_retirement_model, _, _ = campaign.plan()
    registry.retire(proposal, rationale="pilot complete; adding SA does not improve the demonstration MW objective")
    model, choices, _ = campaign.plan()
    assert all(choice.endpoint_id != "mw-with-sa" for choice in choices)
    assert model is not None and before_retirement_model is not None
    assert model.fingerprint == before_retirement_model.fingerprint  # Retiring does not erase acquired labels.
    assert store.observations()[:len(historical)] == historical
    report = {"database": str(database.resolve()), "rejected_smiles": rejected,
                      "proposal_id": proposal, "trial_report": report,
                      "final_protocol_state": registry.get(proposal)["status"],
                      "balance": store.balance(), "learned_model": model.snapshot(),
                      "recommendation": campaign.recommend(),
                      "claim": "Real component/control-plane validation only; not CADD efficacy or graph-search innovation"}
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.write("\n")
    print(json.dumps(report if args.output is None else {"output": str(args.output.resolve()),
                     "balance": report["balance"], "final_protocol_state": report["final_protocol_state"]},
                     indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
