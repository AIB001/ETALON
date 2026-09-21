"""Explicit live verification; never included in the default offline test suite."""

import argparse
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from molquarry import MolQuarry, MolQuarryError
from molquarry.models import utcnow

PRIMARY = {
    "pubchem": "properties",
    "chembl": "activities",
    "rcsb": "entry",
    "uniprot": "entry",
    "opentargets": "target",
    "alphafold": "prediction",
    "clinicaltrials": "search",
    "mcule": "database_files",
}


def verify_page(source, operation, result):
    if not result.records or not result.provenance:
        raise ValueError("Expected nonempty fixture query and provenance")
    if any(p.cached for p in result.provenance):
        raise ValueError("Smoke verification must use fresh network responses")
    first = result.records[0]
    if source == "pubchem" and operation == "properties":
        assert first["CID"] == 2244 and "InChIKey" in first
    if source == "chembl" and operation == "activities":
        assert first["target_chembl_id"] == "CHEMBL203" and first["standard_type"] == "IC50"
        assert "assay_chembl_id" in first and "standard_relation" in first
    if source == "rcsb" and operation == "entry":
        assert first["rcsb_id"] == "7KNW"
    if source == "uniprot" and operation == "entry":
        assert first["primaryAccession"] == "P00533" and first["sequence"]["value"]
    if source == "opentargets" and operation == "target":
        assert first["approvedSymbol"] == "EGFR"
    if source == "alphafold":
        assert first["uniprotAccession"] == "P00533" and first["cifUrl"]
    if source == "clinicaltrials":
        assert first["protocolSection"]["identificationModule"]["nctId"].startswith("NCT")
    if source == "mcule" and operation == "database_files":
        assert first["id"] == 1 and first["files"][0]["sha256_checksum"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--all-operations", action="store_true", help="Check all 23 query examples")
    parser.add_argument(
        "--downloads", action="store_true", help="Also fetch five small files and plan Mcule bulk"
    )
    parser.add_argument(
        "--pagination", action="store_true", help="Also fetch a second page when provided"
    )
    args = parser.parse_args()
    report = {"checked_at": utcnow(), "queries": [], "downloads": [], "bulk_plans": []}
    with MolQuarry(cache=False) as q:

        def check_source(source):
            provider = q.providers[source]
            operations = list(provider.operations) if args.all_operations else [PRIMARY[source]]
            checked = []
            for operation in operations:
                parameters = provider.operations[operation].example
                item = {"source": source, "operation": operation, "parameters": parameters}
                try:
                    result = q.query(source, operation, **parameters)
                    verify_page(source, operation, result)
                    item.update(
                        status="passed",
                        returned=result.returned,
                        total=result.total,
                        provenance=[p.model_dump() for p in result.provenance],
                    )
                    if args.pagination and result.next_parameters:
                        second = q.query(source, operation, **result.next_parameters)
                        assert second.records, (
                            "Continuation should have records for these known queries"
                        )
                        assert second.parameters != result.parameters
                        item["second_page"] = {
                            "returned": second.returned,
                            "provenance": [p.model_dump() for p in second.provenance],
                        }
                except MolQuarryError as exc:
                    item.update(status="failed", error=exc.as_dict()["error"])
                except (AssertionError, KeyError, ValueError) as exc:
                    item.update(
                        status="failed", error={"code": "semantic_check", "message": str(exc)}
                    )
                checked.append(item)
                print(f"{source}.{operation}: {item['status']}", flush=True)
            return checked

        with ThreadPoolExecutor(max_workers=8) as pool:
            for checked in pool.map(check_source, PRIMARY):
                report["queries"].extend(checked)
        if args.downloads:
            # Verify real artifacts in a temporary directory and retain their manifests.
            with tempfile.TemporaryDirectory(prefix="molquarry-smoke-") as temporary:
                for source, operation, parameters, marker in [
                    ("pubchem", "sdf", {"cids": [2244]}, b"$$$$"),
                    ("chembl", "sdf", {"chembl_id": "CHEMBL25"}, b"> <chembl_id>\nCHEMBL25"),
                    ("rcsb", "structure", {"pdb_id": "7KNW"}, b"data_7KNW"),
                    ("uniprot", "fasta", {"accession": "P00533"}, b">sp|P00533|"),
                    ("alphafold", "structure", {"accession": "P00533"}, b"_atom_site."),
                ]:
                    item = {"source": source, "operation": operation}
                    try:
                        plan = q.plan_download(source, operation, **parameters)
                        artifact = q.download(
                            plan, output_dir=Path(temporary) / source, max_bytes=20_000_000
                        )
                        assert marker in Path(artifact.path).read_bytes(), (
                            "Unexpected artifact format"
                        )
                        item.update(
                            status="passed",
                            bytes=artifact.bytes,
                            sha256=artifact.sha256,
                            manifest=artifact.manifest,
                        )
                    except MolQuarryError as exc:
                        item.update(status="failed", error=exc.as_dict()["error"])
                    except AssertionError as exc:
                        item.update(status="failed", error={"message": str(exc)})
                    print(f"download {source}.{operation}: {item['status']}", flush=True)
                    report["downloads"].append(item)
            try:
                plan = q.plan_download("mcule", "dataset", dataset_id=1)
                assert plan.expected_sha256 and plan.estimated_bytes
                report["bulk_plans"].append(
                    {"status": "passed", "downloaded": False, "plan": plan.model_dump()}
                )
            except (MolQuarryError, AssertionError) as exc:
                report["bulk_plans"].append(
                    {"status": "failed", "downloaded": False, "error": str(exc)}
                )
    report["finished_at"] = utcnow()
    report["ok"] = all(
        item["status"] == "passed"
        for key in ("queries", "downloads", "bulk_plans")
        for item in report[key]
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Report: {args.output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
