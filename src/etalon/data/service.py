"""Bounded database workflows that publish frozen inputs for the CADD harness."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

from etalon.boundary.infra import load
from etalon.boundary.quarry import DataBudget, Quarry
from etalon.data.artifacts import digest, file_hash, new_run, read_snapshot, seal, write_json

_FIELDS = {
    "query": {"source", "operation", "parameters", "max_pages"},
    "collect": {"config"},
    "import_catalog": {"source", "path", "options", "search", "max_pages"},
    "search_catalog": {"parameters", "max_pages"},
    "sourcing": {"input_sdf", "config"},
    "download": {"source", "operation", "parameters"},
    "bundle": {"snapshot", "curation_path", "options"},
}


def _inputs(request: dict[str, Any]) -> list[Path]:
    paths = []
    if request["kind"] == "import_catalog":
        paths.append(request["path"])
    elif request["kind"] == "sourcing":
        paths.append(request["input_sdf"])
        paths.extend(request.get("config", {}).get("local_catalogs", []))
    elif request["kind"] == "bundle":
        paths.append(request["curation_path"])
    result = []
    for value in paths:
        path = Path(value)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("data inputs must be absolute paths to existing files")
        if path.stat().st_size > 1_000_000_000:
            raise ValueError("a data input exceeds the 1 GB ingress limit")
        result.append(path.resolve())
    return result


def plan_data(request: dict[str, Any], *, budget: DataBudget | None = None) -> dict[str, Any]:
    """Validate request schemas and bind local bytes without querying any remote service."""
    # Explicit, though the Quarry below pins MolQuarry on construction. Every schema validated here
    # comes from the vendored package, and which copy that is should not depend on a reader noticing
    # that the context manager happens to be entered first.
    load("molquarry")
    budget = budget or DataBudget()
    if not isinstance(request, dict) or request.get("kind") not in _FIELDS:
        raise ValueError(f"data kind must be one of {sorted(_FIELDS)}")
    kind = request["kind"]
    if set(request) - (_FIELDS[kind] | {"kind"}):
        raise ValueError("unknown data request fields")
    request = copy.deepcopy(request)
    pages = request.get("max_pages", 10)
    if type(pages) is not int or not 1 <= pages <= 100:
        raise ValueError("max_pages must be an integer between 1 and 100")
    with Quarry(Path(".molquarry"), DataBudget(max_requests=0)) as quarry:
        source_snapshot = None
        if kind in {"query", "download"}:
            provider = quarry.client._provider(request["source"])
            quarry.client._validate(provider, request["operation"], request.get("parameters", {}),
                                    is_download=kind == "download")
        elif kind == "collect":
            from molquarry.workflows.inhibitors import InhibitorSearch

            request["config"] = InhibitorSearch.model_validate(request["config"]).model_dump()
        elif kind == "sourcing":
            from molquarry.workflows.sourcing import SourcingConfig

            request["config"] = SourcingConfig.model_validate(request.get("config", {})).model_dump()
        elif kind == "import_catalog":
            from molquarry.local_catalog import ImportOptions, LocalSearch

            quarry.client._provider(request["source"])
            request["options"] = ImportOptions.model_validate(request.get("options", {})).model_dump()
            if not request["options"].get("source_version"):
                raise ValueError("a local catalog must declare its source_version")
            if "snapshot_id" in request.get("search", {}):
                raise ValueError("import_catalog search must use the newly imported snapshot")
            selection = LocalSearch.model_validate({**request.get("search", {}),
                                                    "snapshot_id": "probe-" + "0" * 24})
            if (selection.field is None) != (selection.value is None):
                raise ValueError("catalog field and value must be supplied together")
        elif kind == "search_catalog":
            from molquarry.local_catalog import LocalSearch

            selection = LocalSearch.model_validate(request["parameters"])
            if (selection.field is None) != (selection.value is None):
                raise ValueError("catalog field and value must be supplied together")
        elif kind == "bundle":
            from molquarry.workflows.review import Curation, integrate_review

            path = Path(request["snapshot"])
            if not path.is_absolute():
                raise ValueError("bundle snapshot must be an absolute path")
            source = read_snapshot(path)
            if source["kind"] != "collect":
                raise ValueError("bundle requires a collected target dossier snapshot")
            source_snapshot = {"path": source["snapshot"], "snapshot_id": source["snapshot_id"]}
            _inputs(request)
            curation = Curation.model_validate_json(Path(request["curation_path"]).read_text())
            integrate_review(json.loads((path / "payload/dossier.json").read_text()), curation)
            options = {"purchasing": False, "max_purchase_queries": 20, "max_structures": 10,
                       "max_total_bytes": budget.max_bytes, **request.get("options", {})}
            if set(options) != {"purchasing", "max_purchase_queries", "max_structures", "max_total_bytes"}:
                raise ValueError("unknown bundle options")
            if type(options["purchasing"]) is not bool:
                raise ValueError("purchasing must be an explicit boolean")
            for key, upper in (("max_purchase_queries", 50), ("max_structures", 1000),
                               ("max_total_bytes", budget.max_bytes)):
                value = options[key]
                if type(value) is not int or not (1 if key == "max_total_bytes" else 0) <= value <= upper:
                    raise ValueError(f"bundle {key} is outside its allowed bound")
            request["options"] = options
        inputs = [{"path": str(path), "sha256": file_hash(path), "bytes": path.stat().st_size}
                  for path in _inputs(request)]
        body = {"schema": "etalon-data-plan/1", "request": request,
                "budget": budget.as_dict(), "inputs": inputs,
                "infrastructure": quarry.infra.provenance(), "source_snapshot": source_snapshot}
        return {"plan_id": digest(body), **body}


def _query_pages(client: Any, request: dict[str, Any], output: Path) -> dict[str, Any]:
    load("molquarry")
    from molquarry.errors import MolQuarryError

    parameters = request.get("parameters", {})
    pages, errors, seen = [], [], set()
    for index in range(request.get("max_pages", 10)):
        key = digest(parameters)
        if key in seen:
            raise ValueError("database pagination repeated a continuation")
        seen.add(key)
        try:
            page = (client.local_search(**parameters) if request["kind"] == "search_catalog"
                    else client.query(request["source"], request["operation"], **parameters))
        except MolQuarryError as error:
            errors.append(error.as_dict()["error"])
            break
        value = page.model_dump()
        write_json(output / f"page-{index + 1:04d}.json", value)
        pages.append(value)
        if page.next_parameters is None:
            break
        parameters = page.next_parameters
    continuation = pages[-1]["next_parameters"] if pages else None
    returned = sum(page["returned"] for page in pages)
    total = pages[-1]["total"] if pages else None
    complete = not errors and continuation is None and (total is None or returned >= total)
    result = {"status": "complete" if complete else "partial", "returned": returned,
              "total": total, "pages": len(pages), "next_parameters": continuation,
              "errors": errors, "records": [record for page in pages for record in page["records"]]}
    write_json(output / "records.json", result["records"])
    return {key: value for key, value in result.items() if key != "records"}


def run_data(request: dict[str, Any], workspace: Path, *, run_id: str,
             budget: DataBudget | None = None, expected_plan_id: str | None = None,
             transport: Any = None) -> dict[str, Any]:
    """Execute synchronously and seal evidence; failures preserve an unsealed diagnostic run.

    Calls are bounded, but are not detached jobs. Disconnect recovery is inspection of the
    saved run, followed by a new run id if needed; no hidden resubmission or GPU debit occurs.
    """
    budget = budget or DataBudget()
    plan = plan_data(request, budget=budget)
    if expected_plan_id is not None and expected_plan_id != plan["plan_id"]:
        raise ValueError("data request, budget, inputs or infrastructure changed since planning")
    root = new_run(workspace, run_id, plan)
    request = copy.deepcopy(plan["request"])
    quarry = None
    try:
        replacements = {}
        if plan["inputs"]:
            (root / "inputs").mkdir()
        for index, entry in enumerate(plan["inputs"]):
            source = Path(entry["path"])
            destination = root / "inputs" / f"{index:03d}-{source.name}"
            shutil.copyfile(source, destination)
            if file_hash(destination) != entry["sha256"]:
                raise ValueError("data input changed while freezing it")
            replacements[str(source)] = str(destination)
        if request["kind"] == "import_catalog":
            request["path"] = replacements[str(Path(request["path"]).resolve())]
        if request["kind"] == "sourcing":
            request["input_sdf"] = replacements[str(Path(request["input_sdf"]).resolve())]
            request["config"]["local_catalogs"] = [replacements[str(Path(path).resolve())]
                                                     for path in request["config"]["local_catalogs"]]
        if request["kind"] == "bundle":
            collection = root / "inputs" / "collection"
            shutil.copytree(plan["source_snapshot"]["path"], collection)
            if read_snapshot(collection)["snapshot_id"] != plan["source_snapshot"]["snapshot_id"]:
                raise ValueError("collection changed while freezing bundle inputs")
            request["curation_path"] = replacements[str(Path(request["curation_path"]).resolve())]
        with Quarry(Path(workspace).resolve() / ".molquarry", budget, transport=transport) as quarry:
            output = root / "payload"
            kind = request["kind"]
            if kind in {"query", "search_catalog", "import_catalog"}:
                output.mkdir()
                if kind == "import_catalog":
                    imported = quarry.client.import_catalog(request["source"], request["path"],
                                                            **request["options"])
                    write_json(output / "catalog.json", imported)
                    query = {"kind": "search_catalog", "max_pages": request.get("max_pages", 10),
                             "parameters": {**request.get("search", {}),
                                            "snapshot_id": imported["snapshot_id"]}}
                else:
                    query = request
                result = _query_pages(quarry.client, query, output)
            elif kind == "collect":
                from molquarry.workflows.inhibitors import (
                    InhibitorSearch,
                    collect_inhibitor_evidence,
                )

                result = collect_inhibitor_evidence(quarry.client,
                    InhibitorSearch.model_validate(request["config"]), output)
            elif kind == "sourcing":
                from molquarry.workflows.sourcing import SourcingConfig, screen_sdf

                result = screen_sdf(quarry.client, request["input_sdf"], output,
                                    config=SourcingConfig.model_validate(request["config"]))
            elif kind == "download":
                from molquarry.downloads import public_metadata

                download_plan = quarry.client.plan_download(request["source"], request["operation"],
                                                             **request.get("parameters", {}))
                downloaded = quarry.client.download(download_plan, output_dir=output,
                                                     max_bytes=budget.max_bytes)
                result = {"status": "downloaded", "download": downloaded.model_dump(),
                          "plan": public_metadata(download_plan.model_dump())}
            else:
                from molquarry.workflows.bundle import build_target_bundle

                result = build_target_bundle(quarry.client, root / "inputs/collection/payload/dossier.json",
                    request["curation_path"], output, **request["options"])
            usage = quarry.usage()
            if usage["exhausted"]:
                result = {**result, "upstream_status": result.get("status"), "status": "partial",
                          "budget_exhausted": usage["exhausted"]}
            write_json(root / "result.json", result)
            return seal(root, kind=kind, result=result, infrastructure=quarry.infra.provenance(),
                        plan_id=plan["plan_id"], budget=budget.as_dict(), usage=usage,
                        inputs=plan["inputs"])
    except Exception as error:
        write_json(root / "failure.json", {"type": type(error).__name__, "message": str(error),
                   "budget": budget.as_dict(), "usage": quarry.usage() if quarry else None})
        raise
