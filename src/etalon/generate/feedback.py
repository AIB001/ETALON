"""Decision-ready generation feedback from native PRISM manifests, without rerunning models."""
from __future__ import annotations

from collections import Counter
import argparse
import json
import math
from pathlib import Path
import time

from etalon.runtime.files import file_identity
from etalon.generate.audit import wilson


def candidate_rows(manifests):
    """Keep run identity: candidate names alone collide between generation chunks."""
    for source in manifests:
        path = Path(source).absolute()
        body = json.loads(path.read_text(encoding="utf-8"))
        pin = {"path": str(path), **file_identity(path)}
        for model, run in body["models"].items():
            yield pin, body, model, run


def generation_feedback(manifests, criteria):
    """Evaluate explicit property windows; missing properties remain unknown.

    Criteria apply to PRISM quality.plausibility fields. Rejection counts can
    overlap. No thresholds are learned or relaxed, and no method is selected.
    """
    if not isinstance(criteria, dict) or not criteria:
        raise ValueError("declare the property windows for this decision")
    for name, bounds in criteria.items():
        if (not isinstance(name, str) or not isinstance(bounds, dict) or not bounds
                or set(bounds) - {"min", "max"}
                or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in bounds.values())
                or bounds.get("min", -math.inf) > bounds.get("max", math.inf)):
            raise ValueError("criteria use finite min/max bounds")
    started = time.monotonic()
    rows, sources = [], []
    seen = set()
    for pin, manifest, model, run in candidate_rows(manifests):
        if (pin["sha256"], model) in seen:
            continue
        seen.add((pin["sha256"], model))
        if pin not in sources:
            sources.append(pin)
        failed, missing, statuses = Counter(), Counter(), Counter()
        passed = rejected = unknown = 0
        smiles = set()
        candidates = run.get("candidates", [])
        for candidate in candidates:
            properties = candidate.get("quality", {}).get("plausibility", {})
            outcomes = []
            for name, bounds in criteria.items():
                value = properties.get(name)
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                    missing[name] += 1
                    outcomes.append(None)
                else:
                    good = bounds.get("min", -math.inf) <= value <= bounds.get("max", math.inf)
                    outcomes.append(good)
                    if not good:
                        failed[name] += 1
            if False in outcomes:
                rejected += 1
            elif None in outcomes:
                unknown += 1
            else:
                passed += 1
            statuses[candidate.get("audit_status", "unknown")] += 1
            if candidate.get("canonical_smiles"):
                smiles.add(candidate["canonical_smiles"])
        evaluated = passed + rejected
        rows.append({"model": model, "request_hash": manifest.get("request_hash"),
            "manifest_sha256": pin["sha256"], "run_status": run.get("status"),
            "requested": run.get("requested"), "native_generated": run.get("generated"),
            "candidate_records": len(candidates), "quarantined": len(run.get("quarantined", [])),
            "unique_declared_smiles": len(smiles), "within_window": passed,
            "outside_window": rejected, "unknown": unknown,
            "window_rate_among_resolved": passed / evaluated if evaluated else None,
            "nominal_wilson_interval": list(wilson(passed, evaluated)) if evaluated else None,
            "criterion_failures_overlapping": dict(failed), "missing_properties": dict(missing),
            "native_quality_statuses": dict(statuses)})
    return {"criteria": criteria, "runs": rows, "sources": sources,
        "meaning": "property-window analysis, not affinity, synthesis feasibility or independent model comparison",
        "interval_scope": "descriptive binomial intervals; related generated structures need not be independent",
        "followup": "Host chooses conditions, algorithm, model, more evidence, reuse or stop; no automatic allocation.",
        "analysis_wall_seconds": time.monotonic() - started, "generation_calls": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    feedback = generation_feedback(**request["inputs"])
    path = Path(request["outputs"]["feedback"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feedback, ensure_ascii=False, indent=2), encoding="utf-8")
    args.result.write_text(json.dumps({"schema": "etalon-method-result/1",
        "summary": f"Analyzed {len(feedback['runs'])} native generation runs; no generation repeated.",
        "outputs": {"feedback": str(path)},
        "feedback": {"generation_calls": 0, "analysis_wall_seconds": feedback["analysis_wall_seconds"]}},
        ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
