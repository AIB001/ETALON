"""Opt-in real endpoint checks. Saves successes, account blockers and access failures separately."""

import argparse
import concurrent.futures
import json
from pathlib import Path

from molquarry import MolQuarry, MolQuarryError
from molquarry.http import HttpClient
from molquarry.models import utcnow

CASES = [
    ("bindingdb", "by_uniprot", {"uniprot": "P00533", "cutoff_nm": 1.0, "limit": 2}),
    ("chebi", "compound", {"chebi_id": "15365"}),
    ("chebi", "search", {"query": "aspirin", "limit": 2}),
    ("chebi", "parents", {"chebi_id": "15365"}),
    ("unichem", "sources", {}),
    ("unichem", "mapping", {"compound": "CHEMBL25", "source_id": 1}),
    ("gpcrdb", "protein", {"entry_name": "adrb2_human"}),
    ("gpcrdb", "structures", {"entry_name": "adrb2_human"}),
    ("gpcrdb", "residues", {"entry_name": "adrb2_human"}),
    ("klifs", "kinases", {"name": "EGFR", "limit": 2}),
    ("klifs", "structures", {"kinase_id": 406, "limit": 2}),
    ("hpa", "gene", {"ensembl_id": "ENSG00000146648"}),
    ("hpa", "search", {"query": "EGFR", "limit": 2}),
    ("gtex", "genes", {"query": "EGFR", "limit": 1}),
    ("gtex", "expression", {"gencode_id": "ENSG00000146648.20", "limit": 2}),
    ("gtex", "eqtl", {"gencode_id": "ENSG00000146648.20", "limit": 2}),
    ("gtex", "datasets", {}),
    ("string", "map_ids", {"identifiers": ["EGFR"], "limit": 2}),
    ("string", "network", {"identifiers": ["EGFR", "GRB2", "KRAS"], "limit": 2}),
    ("string", "partners", {"identifiers": ["EGFR"], "limit": 2}),
    ("string", "enrichment", {"identifiers": ["EGFR", "GRB2", "KRAS"], "limit": 2}),
    ("string", "version", {}),
    ("reactome", "pathway", {"identifier": "R-HSA-177929"}),
    ("reactome", "pathways_by_uniprot", {"identifier": "P00533"}),
    ("clinpgx", "chemical", {"name": "warfarin"}),
    ("clinpgx", "gene", {"symbol": "CYP2C9"}),
    ("clinpgx", "entry", {"identifier": "PA451906"}),
    ("coconut", "search", {"query": "caffeine", "limit": 2}),
    ("lotus", "search", {"query": "LTS0253154", "limit": 2}),
    ("lotus", "exact", {"smiles": "O=C1OC(C(O)=C1O)CO", "limit": 2}),
    ("drugsfda", "search", {"query": 'products.active_ingredients.name:"ASPIRIN"', "limit": 2}),
    ("orangebook", "search", {"query": 'products.active_ingredients.name:"ASPIRIN"', "limit": 2}),
    ("surechembl", "chemical", {"identifier": "SCHEMBL1353"}),
    ("surechembl", "chemical_by_name", {"name": "aspirin"}),
    ("surechembl", "family", {"publication": "US20160355508A1"}),
    ("ord", "tree", {"path": "data/00", "limit": 2}),
    ("tdc", "datasets", {"query": "caco2", "limit": 2}),
    ("tdc", "dataset", {"name": "caco2_wang"}),
    ("depmap", "release", {"article_id": 27993248}),
    ("plinder", "objects", {}),
]
FILE_SOURCES = [
    "bindingdb",
    "biolip",
    "drugcentral",
    "chebi",
    "unichem",
    "zinc",
    "coconut",
    "surechembl",
    "lifechemicals",
    "hpa",
    "reactome",
    "chembl",
    "pubchem",
    "uniprot",
    "opentargets",
]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=Path(".molquarry/extended-smoke.json"))
    p.add_argument("--source", action="append", help="Restrict to a source; repeatable")
    p.add_argument("--continuations", action="store_true")
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()
    # One worker per source preserves each source's pacing. No secret values in report.
    groups = {}
    for source, op, params in CASES + [(s, "files", {"limit": 3}) for s in FILE_SOURCES]:
        if not args.source or source in args.source:
            groups.setdefault(source, []).append((op, params))

    def run(item):
        source, cases = item
        rows = []
        with MolQuarry(cache=False, http=HttpClient(max_attempts=1, timeout=25)) as q:
            for op, params in cases:
                row = {
                    "source": source,
                    "operation": op,
                    "parameters": params,
                    "checked_at": utcnow(),
                }
                try:
                    result = q.query(source, op, **params)
                    row.update(
                        status="passed" if result.returned else "empty",
                        returned=result.returned,
                        total=result.total,
                        next_parameters=result.next_parameters,
                        provenance=[x.model_dump() for x in result.provenance],
                    )
                    if not result.provenance:
                        raise ValueError("Expected live provenance")
                    if args.continuations and result.next_parameters:
                        next_ = q.query(source, op, **result.next_parameters)
                        row["continuation"] = {
                            "status": "passed",
                            "returned": next_.returned,
                            "provenance": [x.model_dump() for x in next_.provenance],
                        }
                except MolQuarryError as e:
                    row.update(
                        status="blocked"
                        if e.code
                        in {
                            "authentication_required",
                            "access_denied",
                            "rate_limited",
                            "network_error",
                        }
                        else "failed",
                        error=e.as_dict()["error"],
                    )
                except Exception as e:
                    row.update(status="failed", error={"code": type(e).__name__, "message": str(e)})
                rows.append(row)
                print(
                    source,
                    op,
                    row["status"],
                    row.get("returned", row.get("error", {}).get("code")),
                    flush=True,
                )
        return rows

    output = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for rows in pool.map(run, groups.items()):
            output.extend(rows)
            args.output.write_text(
                json.dumps({"checked_at": utcnow(), "checks": output}, indent=2, ensure_ascii=False)
                + "\n"
            )
    return 1 if any(r["status"] == "failed" for r in output) else 0


if __name__ == "__main__":
    raise SystemExit(main())
