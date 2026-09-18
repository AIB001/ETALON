"""Real CPU engineering pilot: learn to select among seven preauthorized edit combinations.

The explicit MW gate rejects these small audit molecules. This deliberately contrasting
fixture tests failure feedback, not scientific usefulness of the gate or CADD efficacy.
All variants keep the same MW readout. No autonomous promotion, docking, MD or paid APIs.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from etalon.active import ActiveCampaign, CampaignSpec, CampaignStore
from etalon.active.adapters import MOLECULAR_REPRESENTATION, molecular_candidates
from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout
from etalon.active.mutations import DesignSpace
from etalon.active.proposer import ProtocolSearch
from etalon.active.protocols import ProtocolRegistry, TrialPolicy
from etalon.campaign.design import component, compose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--policy", choices=("linear_ucb", "audit_ei"), default="linear_ucb")
    parser.add_argument("--opportunity-cost", type=float, help="explicit panel-skill units per demonstration quote unit")
    parser.add_argument("--campaign-rounds", type=int, default=0, help="explicit ordinary CPU AL rounds after closing search")
    args = parser.parse_args()
    if args.policy == "audit_ei":
        if args.opportunity_cost is None or not math.isfinite(args.opportunity_cost) or args.opportunity_cost <= 0:
            parser.error("audit_ei requires a finite positive --opportunity-cost; no automatic exchange rate")
    elif args.opportunity_cost is not None:
        parser.error("--opportunity-cost is only used by audit_ei")
    if args.campaign_rounds < 0:
        parser.error("--campaign-rounds cannot be negative")
    database = args.workspace / "campaign.sqlite"
    if database.exists() or (args.output is not None and args.output.exists()):
        raise FileExistsError("use a new workspace/output; never overwrite an earlier experiment")
    config = compose("fixed-panel-cpu", [{"id": "measure", "title": "Explicit CPU components", "criteria": [
        component("properties", "features.rdkit_properties@0.1.0", settings={"batch_size": 128, "include_sa_score": False})]}])
    recipe = CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))
    objective = recipe.endpoint("mw-reference", target="search-demo", quantity="molecular_weight", units="Da", cost=1)
    store = CampaignStore(database)
    store.configure(CampaignSpec(objective.id, 64, "demonstration_quotes", MOLECULAR_REPRESENTATION,
                                 policy="decision_aware", batch_size=1), [objective])
    molecules = {"ethanol": "CCO", "ethylamine": "CCN", "benzene": "c1ccccc1", "acetate-acid": "CC(=O)O",
                 "pyridine": "c1ccncc1", "phenol": "Oc1ccccc1", "propanol": "CCCO", "acetamide": "CC(=O)N"}
    candidates, rejected = molecular_candidates(molecules)
    store.add_candidates(candidates)
    panel = ("ethanol", "ethylamine", "benzene", "acetate-acid")  # Fixed BEFORE any outcome.
    registry = ProtocolRegistry(store)
    registry.bind_seed(objective.id, recipe, rationale="explicit CPU reference and unchanged MW readout")
    executor = CascadeExecutor.from_journal(args.workspace / "calculations", store)
    for identifier in panel:
        round_id = store.start_round({"mode": "predeclared_reference_panel"})
        action = store.reserve(round_id, identifier, objective.id, {"mode": "predeclared_reference_panel"})
        store.start_action(action.id)
        result = executor(action, store.candidates()[identifier], objective, None)
        store.resolve(action.id, result)
        store.finish_round(round_id, {"actions": [action.id]})
    edits = (
        {"op": "insert_criterion", "tier_id": "measure", "before": "properties",
         "criterion": component("sa", "synthesis.rdkit_sa_score@0.1.0")},
        {"op": "insert_criterion", "tier_id": "measure", "before": "properties",
         "criterion": component("window", "chemistry.rdkit_property_range_gate@0.1.0", settings={"mw_min": 100.0})},
        {"op": "set_setting", "criterion_id": "properties", "path": "/batch_size", "expected": 128, "value": 64},
    )
    space_id = registry.bind_space(DesignSpace(edits, max_edits=3), rationale="review seven explicit CPU edit combinations")
    search = ProtocolSearch(store)
    catalogue = search.catalogue(objective.id, space_id)
    if len(catalogue["variants"]) != 7 or catalogue["rejected"]:
        raise RuntimeError(catalogue)
    search_id = search.authorize(objective.id, space_id, panel_ids=panel,
                                 quotes={v["id"]: 1 for v in catalogue["variants"]},
                                 budget=16, max_trials=4, policy=args.policy, seed=0,
                                 opportunity_cost=args.opportunity_cost,
                                 rationale="four full-panel trials, fixed features/reward and quoted CPU budget")
    decisions = []
    for _ in range(4):
        # Reopening is intentional: no policy state lives only in Python memory.
        resumed = CampaignStore(database)
        search, registry = ProtocolSearch(resumed), ProtocolRegistry(resumed)
        plan = search.plan(search_id)
        if plan["selected"] is None:
            break
        decisions.append(plan)
        proposal = search.propose_next(search_id, expected_snapshot_hash=plan["snapshot_hash"],
                                       rationale="execute the selected preauthorized CPU audit")
        certificate = registry.validate(proposal)
        if not certificate["ok"]:
            raise RuntimeError(certificate)
        record = search.get(search_id)
        chosen = next(v for v in record["body"]["variants"] if v["id"] == plan["selected"])
        registry.start_trial(proposal, TrialPolicy(**chosen["trial_limits"]), rationale="explicit fixed-panel trial authorization")
        search.run_audit(search_id, CascadeExecutor.from_journal(args.workspace / "calculations", resumed), max_actions=4)
        search.score(search_id)
        registry.retire(proposal, rationale="engineering audit complete; no claim of improved scientific protocol")
    record = search.get(search_id)
    if args.policy == "linear_ucb":
        assert len(record["experiments"]) == 4
    assert [d["training_size"] for d in decisions] == list(range(len(decisions)))
    final_plan = search.plan(search_id)
    assert final_plan["training_size"] == len(decisions)
    search.close(search_id, rationale="bounded engineering pilot complete; retain all evidence")
    balance_after_search = store.balance()
    continuation = None
    if args.campaign_rounds:
        # This is a separately requested run, never a side effect of economic_stop.
        campaign = ActiveCampaign(store, CascadeExecutor.from_journal(args.workspace / "calculations", store))
        continuation = campaign.run(max_rounds=args.campaign_rounds)
    report = {"search_id": search_id, "database": str(database.resolve()), "rejected_smiles": rejected,
              "decisions": decisions, "final_plan_before_close": final_plan, "record": search.get(search_id),
              "balance_after_search": balance_after_search, "campaign_continuation": continuation,
              "state": store.status(), "summary": {"actions": len(store.actions()), "balance": store.balance(),
                    "search_stop_reason": final_plan["stop_reason"], "search_trials": len(decisions),
                    "skills": [e["reward"]["skill"] for e in record["experiments"]],
                    "failures": [e["reward"]["failure_count"] for e in record["experiments"]]},
              "claim": "Real CPU feedback/control validation only; selected audit panel is training, not independent CADD performance evidence"}
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.write("\n")
    print(json.dumps(report if args.output is None else {"output": str(args.output.resolve()), **report["summary"]},
                     indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
