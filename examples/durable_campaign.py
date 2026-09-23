"""Run a detached, restartable CPU database → screen → active learning workflow.

Install .[cascade,quarry,active,mcp], then:
    python examples/durable_campaign.py --workspace /absolute/new/workspace

The three fixture molecules and molecular-weight readout test execution plumbing.
They are not biological activity evidence. All data requests are explicitly offline.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from etalon.active.adapters import MOLECULAR_REPRESENTATION
from etalon.campaign.design import component, compose
from etalon.runtime.artifacts import configure
from etalon.runtime.executors import prepare_executor
from etalon.runtime.service import plan, status, submit


def specification(workspace: Path, *, mode: str = "ordered") -> dict:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    fixture = workspace / "fixture.csv"
    with fixture.open("x", encoding="utf-8") as handle:
        handle.write("id,smiles\nethanol,CCO\naspirin,CC(=O)Oc1ccccc1C(=O)O\nacetate,CC(=O)[O-].[Na+]\n")
    config = compose("durable-cpu-example", [{"id": "properties", "title": "CPU molecular properties",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}],
        finalize={"steps": [{"id": "export", "backend": "export.rdkit_sdf_shortlist@0.1.0",
                             "settings": {"schema_version": 1}}]})
    configured = configure(workspace, config)
    executor = prepare_executor("molcascade", {"cascade": config,
        "readout": {"stage_id": "properties", "contract_id": "property/v1", "value_column": "mw"}},
        {"id": "mw", "target": "offline-fixture", "quantity": "molecular_weight", "units": "Da", "cost": 1})
    budget = {"max_requests": 0, "max_bytes": 1_000_000, "max_seconds": 30}
    nodes = [
        {"id": "acquire", "operation": "data.acquire", "arguments": {"request": {
            "kind": "import_catalog", "source": "chembl", "path": str(fixture),
            "options": {"source_version": "OFFLINE-FIXTURE-NOT-CHEMBL-DATA"}}, "budget": budget, "min_records": 3},
            "resources": {"http_requests": 0, "http_bytes": 1_000_000}},
        {"id": "library", "operation": "data.prepare", "arguments": {
            "snapshot": {"$ref": "acquire.snapshot"}, "id_field": "fields.id", "smiles_field": "fields.smiles", "min_records": 3}},
        {"id": "screen", "operation": "screen.run", "arguments": {
            "config_path": configured["config_path"], "library_path": {"$ref": "library.library_path"}},
            "resources": {"cpu_quotes": 1}},
        {"id": "export", "operation": "screen.export", "arguments": {
            "workspace": {"$ref": "screen.workspace"}, "run_id": {"$ref": "screen.run_id"}}},
        {"id": "campaign", "operation": "campaign.create", "arguments": {
            "spec": {"objective": "mw", "budget": 3, "cost_unit": "fixture_quotes",
                     "representation": MOLECULAR_REPRESENTATION, "batch_size": 1},
            "endpoints": [executor["endpoint"]], "executors": [executor]}},
        {"id": "import", "operation": "campaign.import", "arguments": {
            "database": {"$ref": "campaign.database"}, "snapshot": {"$ref": "library.snapshot"}}},
        {"id": "learn", "operation": "active.run", "depends_on": ["import"], "arguments": {
            "database": {"$ref": "campaign.database"}, "max_rounds": 3, "min_new_admitted": 3},
            "resources": {"campaign:fixture_quotes": 3}},
        {"id": "sourcing", "operation": "data.acquire", "arguments": {
            "request": {"kind": "sourcing", "input_sdf": {"$ref": "export.path"}, "config": {
                "pubchem": False, "chembl": False, "unichem": False, "max_mcule_queries": 0,
                "local_catalogs": [str(fixture)]}}, "budget": budget},
            "resources": {"http_requests": 0, "http_bytes": 1_000_000}},
    ]
    return {"schema": "etalon-workflow/1", "objective": "Verify three CPU molecular-weight observations and retain source evidence",
            "nodes": nodes, "limits": {"http_requests": 0, "http_bytes": 2_000_000,
                                      "cpu_quotes": 1, "campaign:fixture_quotes": 3},
            "controller": {"mode": mode}, "max_seconds": 300}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    spec = specification(args.workspace)
    prepared = plan(spec)
    (args.workspace / "workflow.json").write_text(json.dumps(spec, indent=2) + "\n")
    job = submit(spec, args.workspace.resolve(), job_id="database-campaign", expected_plan_id=prepared["plan_id"])
    deadline = time.monotonic() + 300
    while job["state"] in {"queued", "starting", "running"} and time.monotonic() < deadline:
        time.sleep(0.2)
        job = status(args.workspace.resolve(), "database-campaign")
    print(json.dumps(job, indent=2))
    if job["state"] != "succeeded":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
