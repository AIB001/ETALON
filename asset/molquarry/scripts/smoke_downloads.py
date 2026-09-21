"""Explicit small-artifact live checks; never downloads a full vendor catalog."""

import argparse
import concurrent.futures
import json
from pathlib import Path
from uuid import uuid4

from molquarry import MolQuarry, MolQuarryError
from molquarry.models import utcnow

CASES = [
    ("chebi", "molfile", {"chebi_id": "15365"}),
    ("tdc", "dataset", {"name": "caco2_wang"}),
    ("ord", "dataset", {"path": "data/00/ord_dataset-00005539a1e04c809a9a78647bea649c.parquet"}),
    ("depmap", "file", {"article_id": 27993248, "file_id": 51063560}),
    ("drugcentral", "artifact", {"query": "FDA_Approved.csv"}),
    ("biolip", "artifact", {"query": "readme_ligand.txt"}),
]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=Path(".molquarry/download-smoke.json"))
    p.add_argument("--source", action="append")
    args = p.parse_args()
    run_id = uuid4().hex[:12]

    def check(case):
        source, op, params = case
        row = {"source": source, "operation": op, "checked_at": utcnow()}
        try:
            with MolQuarry(cache=False) as q:
                if op == "artifact":
                    items = q.query(source, "files", **params).records
                    file = next(x for x in items if x["kind"] == "file")
                    params = {k: file[k] for k in ["root", "path", "url"]}
                plan = q.plan_download(source, op, **params)
                result = q.download(
                    plan, output_dir=q.home / "verification" / run_id / source, max_bytes=10_000_000
                )
                row.update(
                    status="passed",
                    bytes=result.bytes,
                    sha256=result.sha256,
                    manifest=result.manifest,
                )
        except MolQuarryError as exc:
            row.update(status="failed", error=exc.as_dict()["error"])
        except Exception as exc:
            row.update(status="failed", error={"code": type(exc).__name__, "message": str(exc)})
        print(source, row["status"], row.get("bytes", row.get("error")), flush=True)
        return row

    cases = [x for x in CASES if not args.source or x[0] in args.source]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(check, cases))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"checked_at": utcnow(), "checks": rows}, indent=2, ensure_ascii=False) + "\n"
    )
    return int(any(r["status"] != "passed" for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
