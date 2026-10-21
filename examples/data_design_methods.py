"""Small optional SDK compositions; synthetic local data, no network/scoring.

Run with ``python examples/data_design_methods.py --output NEW_DIRECTORY``.
An optional ``--bundle`` checks and indexes an existing design export in a
separate engineering store. This source example is not a wheel console command.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import platform
import sys
import time

import etalon
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.adapters import MOLECULAR_REPRESENTATION
from etalon.boundary.quarry import DataBudget, describe
from etalon.data.artifacts import file_hash, status, write_json
from etalon.data.design import design_index, import_bundle, read_bundle, verify_import
from etalon.data.ingress import review_template
from etalon.data.library import import_candidates, prepare_library
from etalon.data.service import plan_data, run_data


def local_catalog_example(root: Path) -> dict:
    """Create a fresh synthetic CSV/library/store and preserve exact SDK returns."""
    root = root.absolute()
    root.mkdir(parents=True, exist_ok=False)
    catalog = root / "synthetic.csv"
    catalog.write_text("id,smiles\nethanol,CCO\nethanol-alias,OCC\nethylamine,CCN\n",
                       encoding="utf-8")
    request = {"kind": "import_catalog", "source": "chembl", "path": str(catalog),
               "options": {"source_version": "SYNTHETIC-ENGINEERING-NOT-CHEMBL-DATA"},
               "search": {"limit": 100}}
    budget = DataBudget(max_requests=0, max_bytes=1000000, max_seconds=30)
    source_description = describe("chembl")
    write_json(root / "source-description.json", source_description)
    plan = plan_data(request, budget=budget)
    write_json(root / "plan.json", plan)
    acquired = run_data(request, root, run_id="catalog", budget=budget,
                        expected_plan_id=plan["plan_id"])
    prepared = prepare_library(Path(acquired["snapshot"]), root, run_id="library",
                               id_field="fields.id", smiles_field="fields.smiles")
    library = Path(prepared["snapshot"])
    store = CampaignStore(root / "candidates.sqlite")
    endpoint = Endpoint("unmeasured", "SYNTHETIC", "fixture", "au", "fixture/1", 0,
                        queryable=False, requires_handoff=False)
    store.configure(CampaignSpec(endpoint.id, 0, "quotes", MOLECULAR_REPRESENTATION), [endpoint])
    added = import_candidates(store, library)
    replay = import_candidates(store, library)
    # A catalog has no reviewed assay inventory. Refusal is the correct stop,
    # rather than manufacturing a dossier or a label to complete a pipeline.
    try:
        review_template(library, endpoint_id=endpoint.id, protocol=endpoint.protocol)
    except ValueError as error:
        assay = {"state": "not_applicable", "error_type": type(error).__name__,
                 "message": str(error), "decision": "stop_assay_import"}
    else:
        raise AssertionError("a plain catalog unexpectedly supplied assay evidence")
    return {"role": "synthetic_engineering", "catalog": str(catalog),
            "catalog_sha256": file_hash(catalog), "plan_id": plan["plan_id"],
            "acquisition": acquired, "prepared": prepared,
            "verified_snapshot": status(library), "import": added, "replay": replay,
            "assay": assay, "observations": store.observations(),
            "scientific_balance": store.balance(), "database": str(store.path)}


def design_index_example(bundle: Path, root: Path) -> dict:
    """Inspect then index; no pool features, labels, preparation or execution."""
    checked = read_bundle(bundle)
    root = root.absolute()
    root.mkdir(parents=True, exist_ok=False)
    store = CampaignStore(root / "index.sqlite")
    endpoint = Endpoint("unmeasured", "SYNTHETIC", "fixture", "au", "fixture/1", 0,
                        queryable=False, requires_handoff=False)
    store.configure(CampaignSpec(endpoint.id, 0, "quotes", "synthetic-index-only"), [endpoint])
    receipt = import_bundle(store, bundle, mode="index_only")
    replay = import_bundle(store, bundle, mode="index_only")
    verification = verify_import(store, bundle, mode="index_only")
    return {"role": "engineering_index_of_supplied_bundle", "checked": checked,
            "import": receipt, "replay": replay, "verification": verification,
            "index": design_index(store), "candidates": len(store.candidates()),
            "observations": store.observations(), "scientific_balance": store.balance(),
            "database": str(store.path)}


def numerical_advice_example() -> dict:
    """Choose only local existing numerical helpers; no Campaign/Supervisor loop."""
    from etalon.campaign.calibrate import PanelMember, enrichment, separation
    from etalon.generate.productivity import Chunk, Productivity, allocate

    panel = [PanelMember("active", True, "synthetic control"),
             PanelMember("inactive", False, "synthetic control")]
    scores = [{"parent_id": "active", "engine_id": "fixture", "score": -5.0,
               "direction": "LOWER_STRONGER"},
              {"parent_id": "inactive", "engine_id": "fixture", "score": -7.0,
               "direction": "LOWER_STRONGER"}]
    population = [{"parent_id": f"p{i}", "engine_id": "fixture", "score": value,
                   "direction": "LOWER_STRONGER"}
                  for i, value in enumerate((-9.0, -7.0, -5.0, -3.0))]
    ranking = separation(scores, panel)
    placement = enrichment(scores, panel, population)
    loops = [Productivity("spent-loop", "fixture-generator", "fixture-pocket",
                          tuple(Chunk(100, 100, 0, 10) for _ in range(3)))]
    return {"role": "synthetic_numerical_advice", "separation": [asdict(r) for r in ranking],
            "enrichment": [asdict(r) for r in placement], "allocation": allocate(loops),
            "decision": "stop; neither this ranking nor this spent generator supports continuation",
            "scientific_execution": "not_run"}


def configuration_example(root: Path, library: Path) -> dict:
    """Freeze one caller-selected CPU component and plan it without execution."""
    from etalon.campaign.design import component, compose
    from etalon.runtime.artifacts import configure
    from etalon.screening import plan_screen

    root = root.resolve()
    configuration = compose("properties-only", [{"id": "measure", "title": "CPU properties",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}])
    saved = configure(root, configuration)
    plan = plan_screen(saved["config_path"], library, root / "screen")
    return {"configuration": saved, "plan": plan, "execution": "not_run",
            "decision": "stop_after_requested_configuration_and_plan"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    root = args.output.absolute()
    root.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    report = {"environment": {"executable": sys.executable, "python": platform.python_version(),
                               "etalon": etalon.__file__},
              "catalog": local_catalog_example(root / "catalog-example"),
              "numerical_advice": numerical_advice_example()}
    report["configuration"] = configuration_example(
        root / "configuration-example", Path(report["catalog"]["prepared"]["snapshot"]) / "library.csv")
    if args.bundle is not None:
        report["design"] = design_index_example(args.bundle.absolute(), root / "design-example")
    report["engineering_wall_seconds"] = time.perf_counter() - started
    report["cost_scope"] = {"http": "actual acquisition usage in catalog.acquisition.usage",
                            "scientific_execution": "not_run", "monetary_cost": None,
                            "process_startup_seconds": None}
    write_json(root / "result.json", report)
    print(json.dumps({"result": str(root / "result.json"),
                      "sha256": file_hash(root / "result.json"),
                      "wall_seconds": report["engineering_wall_seconds"]}))


if __name__ == "__main__":
    main()
