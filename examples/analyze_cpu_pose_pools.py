"""Reanalyse a completed bounded PoseBusters panel without executing docking.

Outputs are new development artifacts. The two stages are explicit: first seal
conditioning-only descriptors, then join already-seen outcomes for diagnosis.
Neither stage trains a selector or changes a task's default branch.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from etalon.research.evidence_refs import file_identity
from etalon.research.pose_pool import diagnose_cpu_pool
from etalon.research.publication import _save


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def analyse(root, destination):
    root, destination = Path(root).resolve(strict=True), Path(destination).absolute()
    if destination.resolve().is_relative_to(root) or destination.exists():
        raise ValueError("output must be a new directory outside the retained task")
    panel_path = root / "cases.json"
    panel_identity = file_identity(panel_path)
    panel = read(panel_path)
    if not 1 <= len(panel["cases"]) <= 64:
        raise ValueError("bounded panel of at most 64 cases required")
    cases = {c["id"]: c for c in panel["cases"]}
    if len(cases) != len(panel["cases"]):
        raise ValueError("unique case IDs required")
    receipts = list((root / "executions").glob("*/receipt.json"))
    if not 1 <= len(receipts) <= 256:
        raise ValueError("bounded completed execution ledger required")
    pins = {str(panel_path): panel_identity}
    rows, seen = [], set()
    start = time.perf_counter()
    destination.mkdir(parents=True, exist_ok=False)
    for path in sorted(receipts):
        pins[str(path)] = file_identity(path)
        receipt = read(path)
        if receipt["stage"] != "empirical":
            continue
        key = (receipt["case_id"], receipt["seed"])
        if key in seen or key[0] not in cases or receipt.get("error"):
            raise ValueError("unique successful known CPU receipt required")
        seen.add(key)
        folder = root / "inputs" / key[0]
        for name, identity in cases[key[0]]["inputs"].items():
            source = folder / name
            if not source.resolve().is_relative_to(folder.resolve()) or file_identity(source) != identity:
                raise ValueError("conditioning manifest mismatch")
            pins[str(source)] = identity
        settings = read(folder / "settings.json")
        pool = root / "outputs" / key[0] / str(key[1]) / "empirical.sdf"
        expected_pool = receipt["artifacts"].get(str(pool))
        if expected_pool is None:
            raise ValueError("CPU receipt does not identify the pool")
        pins[str(pool)] = expected_pool
        t = time.perf_counter()
        result = diagnose_cpu_pool(pool, folder / "receptor.pdb", expected_pool=expected_pool,
            expected_receptor=cases[key[0]]["inputs"]["receptor.pdb"], pocket_center=settings["box"]["center"])
        rows.append({"case_id": key[0], "seed": key[1], "cpu_request_id": receipt["request_id"],
                     "cpu_receipt_identity": pins[str(path)], "diagnostic": result,
                     "diagnostic_wall_seconds": time.perf_counter() - t})
    if not rows:
        raise ValueError("no CPU pools")
    # Save before opening any outcome or reference. This establishes processing
    # separation; these existing tasks are still seen, retrospective development.
    descriptor_path = destination / "conditioning-diagnostics.json"
    _save(descriptor_path, {"schema": "etalon-cpu-pool-panel/1", "panel_identity": panel_identity,
        "rows": rows, "scope": "retrospective conditioning-only extraction; not prospective freezing"})
    descriptor_identity = file_identity(descriptor_path)
    outcome_path = root / "comparison.json"
    pins[str(outcome_path)] = file_identity(outcome_path)
    outcomes = read(outcome_path)["rows"]
    labels = {(r["case_id"], r["seed"]): r for r in outcomes}
    if len(labels) != len(outcomes) or set(labels) != seen:
        raise ValueError("outcome join differs from exact observed case-seed set")
    groups = {name: [] for name in ("rescued", "regressed", "both_pass", "both_fail")}
    for row in rows:
        observed = labels[(row["case_id"], row["seed"])]
        a, b = observed["empirical"]["top1"], observed["cnn"]["top1"]
        if type(a) is not bool or type(b) is not bool:
            raise ValueError("explicit observed outcomes required")
        group = "rescued" if b and not a else "regressed" if a and not b else "both_pass" if a else "both_fail"
        groups[group].append(row)
    summary = {}
    for group, members in groups.items():
        features = {}
        for feature in rows[0]["diagnostic"]["values"]:
            values = [r["diagnostic"]["values"][feature] for r in members
                      if r["diagnostic"]["values"][feature] is not None]
            features[feature] = {"n": len(values), "median": statistics.median(values) if values else None,
                                 "min": min(values) if values else None, "max": max(values) if values else None}
        summary[group] = {"case_seeds": [[r["case_id"], r["seed"]] for r in members],
                          "count": len(members), "unique_cases": len({r["case_id"] for r in members}),
                          "features": features}
    if any(file_identity(Path(path)) != pin for path, pin in pins.items()):
        raise ValueError("retained source changed during reanalysis")
    report = {"schema": "etalon-cpu-pool-development-analysis/1", "groups": summary,
              "conditioning_diagnostics": descriptor_identity, "outcomes": pins[str(outcome_path)],
              "source_files": len(pins), "all_source_identities_unchanged": True,
              "wall_seconds": time.perf_counter() - start, "new_docking_or_cnn_calls": 0,
              "new_policy": None, "automatic_adoption": False,
              "scope": "Seen-panel reanalysis; seeds are correlated within case. No transfer/affinity claim."}
    _save(destination / "source-identities.json", pins)
    _save(destination / "development-analysis.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = analyse(args.root, args.output)
    print(json.dumps({"groups": {k: v["count"] for k, v in result["groups"].items()},
                      "wall_seconds": result["wall_seconds"], "new_docking_or_cnn_calls": 0}))
