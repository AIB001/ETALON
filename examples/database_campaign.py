"""Offline MolQuarry -> MolCascade -> active campaign -> sourcing demonstration.

Install .[cascade,quarry,active], then run in a new workspace:
    python examples/database_campaign.py --workspace runs/database-demo

Uses fixture molecules, real CPU components and zero HTTP requests. Molecular weight
is a plumbing readout only; this example makes no activity or drug-discovery claim.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from etalon.active import ActiveCampaign, CampaignSpec, CampaignStore
from etalon.active.adapters import MOLECULAR_REPRESENTATION
from etalon.active.cascade import CascadeExecutor, CascadeRecipe, Readout
from etalon.boundary.quarry import DataBudget
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose
from etalon.data.artifacts import write_json
from etalon.data.library import import_candidates, prepare_library
from etalon.data.service import run_data


def run(workspace: Path) -> dict:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    fixture = workspace / "fixture.csv"
    with fixture.open("x", encoding="utf-8") as handle:
        handle.write("id,smiles\nethanol,CCO\naspirin,CC(=O)Oc1ccccc1C(=O)O\n"
                     "sodium-acetate,CC(=O)[O-].[Na+]\n")
    offline = DataBudget(max_requests=0)
    acquired = run_data({"kind": "import_catalog", "source": "chembl", "path": str(fixture),
        "options": {"source_version": "OFFLINE-FIXTURE-NOT-CHEMBL-DATA"}, "search": {"limit": 100}},
        workspace, run_id="catalog", budget=offline)
    library = prepare_library(Path(acquired["snapshot"]), workspace, run_id="library",
                               id_field="fields.id", smiles_field="fields.smiles")
    config = compose("database-components", [{"id": "measure", "title": "CPU properties",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}],
        finalize={"steps": [{"id": "export", "backend": "export.rdkit_sdf_shortlist@0.1.0",
                             "settings": {"schema_version": 1}}]})
    config_path = workspace / "cascade.json"
    write_json(config_path, config)
    screen = Screen(workspace / "screen")
    plan = screen.plan(config_path, Path(library["snapshot"]) / "library.csv")
    screened = screen.run(plan, run_id="database-screen")
    if screened.status != "SUCCEEDED":
        raise RuntimeError(screened.as_dict())
    shortlist = screen.export_shortlist(screened.run_id, workspace / "shortlist.sdf")
    recipe = CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))
    endpoint = recipe.endpoint("mw", target="offline-fixture", quantity="molecular_weight", units="Da", cost=1)
    store = CampaignStore(workspace / "campaign.sqlite")
    store.configure(CampaignSpec("mw", 3, "fixture_quotes", MOLECULAR_REPRESENTATION, batch_size=1), [endpoint])
    imported = import_candidates(store, Path(library["snapshot"]))
    campaign = ActiveCampaign(store, CascadeExecutor(workspace / "active-runs", {"mw": recipe}))
    active = campaign.run(max_rounds=3)
    observations = store.observations()
    assert len(observations) == 3 and all(row["admitted"] for row in observations)
    sourced = run_data({"kind": "sourcing", "input_sdf": shortlist["path"], "config": {
        "pubchem": False, "chembl": False, "unichem": False, "max_mcule_queries": 0,
        "local_catalogs": [str(fixture)]}}, workspace, run_id="sourcing", budget=offline)
    report = {"scope": "offline fixtures; real CPU plugins; no affinity, docking, MD or live-stock claim",
              "workspace": str(workspace), "acquisition": acquired["snapshot"],
              "library": library["snapshot"], "identity_summary": library["result"],
              "import": imported, "screen": screened.as_dict(), "active": active["state"],
              "observations": [{"candidate_id": row["result"]["candidate_id"],
                                "admitted": row["admitted"], "value": row["result"]["value"]}
                               for row in observations],
              "sourcing": sourced["snapshot"], "sourcing_result": sourced["result"],
              "http_requests": acquired["usage"]["requests"] + sourced["usage"]["requests"]}
    write_json(workspace / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.workspace), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
