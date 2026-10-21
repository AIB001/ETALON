"""Portable synthetic SDK examples; no scientific files, native calls or labels.

Run with an ETALON interpreter: python research_methods.py [--output PATH].
Functions are independent examples, not a required sequence or research runner.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from etalon.research.fixed_pool import subset_rank_outcome, two_chunk_catalog
from etalon.research.online import execute_visible_task
from etalon.research.pose_selection import selection_diagnostic
from etalon.research.support import loss_interval
from etalon.research.transfer import (
    GroupSplit, calibrate_policy, evaluate_policies, fit_policy, next_action,
)


def synthetic_inputs():
    """Complete task rows with declared independent groups and fixture evidence."""
    split = GroupSplit({f"t{i}": {
        "role": "train" if i < 4 else "calibration" if i < 6 else "test",
        "group": f"g{i}", "family": f"f{i}",
    } for i in range(8)}, "synthetic engineering groups, not real independence", "0" * 64)
    catalog = two_chunk_catalog(initial_quote=1., first_quote=1., second_quote=1.,
                               loss_definition="synthetic binary task error")
    rows = []
    for i in range(8):
        x, z = (i % 4) // 2, i % 2
        evidence = [f"unadmitted-fixture-t{i}"]
        rows.append({
            "task_id": f"t{i}", "sample_unit": "task", "independence_group": f"g{i}",
            "source_kind": "synthetic_fixture", "evidence": evidence,
            "outcomes": {"q1": ("same", "changed")[x], "q2": ("same", "changed")[z]},
            "losses": {"fixed_pool_vina": x ^ z, "fixed_pool_vinardo": 1 - (x ^ z), "abstain": 1.},
            "resource_costs": {r.id: .1 for r in catalog.resources},
            "loss_definition": catalog.loss_definition, "cost_unit": catalog.cost_unit,
            "outcome_evidence": {p.id: evidence for p in catalog.probes},
            "loss_evidence": {d: evidence for d in catalog.decision_resources},
            "cost_evidence": {r.id: evidence for r in catalog.resources},
        })
    return split, catalog, rows


def train_fixture(*, calibration=False):
    split, catalog, rows = synthetic_inputs()
    grid = {"mixture_weights": [0., .25], "cost_weights": [.001],
            "selection_cost_weight": .001, "probability_selection_metric": "group_mean_brier"}
    policy = fit_policy(rows[:4], split=split, catalog=catalog, budget=3.,
                        max_depth=2, cost_weight=.001, static_order=("q1", "q2"),
                        calibration_selection=grid if calibration else None)
    return policy, rows


def transfer_example():
    policy, rows = train_fixture(calibration=True)
    calibrated, calibration_report = calibrate_policy(policy, rows[4:6])
    # Calibration replay is not an independent test. No test release is fabricated.
    comparison = evaluate_policies(calibrated, rows[4:6], role="calibration")
    initial = {"policy_sha256": calibrated["policy_sha256"],
               "resource_costs": {"r0": .1}, "evidence": ["fixture-prefix-only"]}
    suggestion = next_action(calibrated, [], initial_receipt=initial)
    return {"training_policy_sha256": policy["policy_sha256"],
            "calibrated_policy_sha256": calibrated["policy_sha256"],
            "calibration": calibration_report, "comparison": comparison,
            "suggestion_only": suggestion, "executed": False,
            "test_labels_read": False, "independent_gain": None}


def visible_example(*, missing=False):
    """Compose paid-subset semantics with visible acquisition using fixture callbacks."""
    policy, _ = train_fixture()
    events = []
    ids = tuple(f"c{i}" for i in range(6))
    def acquire(rid, reason):
        events.append({"kind": "fixture_acquire", "resource_id": rid, "reason": reason})
        indices = range(6) if rid == "r0" else range(3) if rid == "r1" else range(3, 6)
        scores = {f"c{i}": -float(i) for i in indices}
        if rid == "r1" and missing:
            scores["c1"] = None
        if rid == "r2":
            scores["c3"], scores["c4"] = scores["c4"], scores["c3"]
        return {"execution_id": "fixture-" + rid, "result_sha256": "0" * 64,
                "result_artifact_id": "unadmitted-fixture-" + rid,
                "cost": {"wall_second": .1},
                "slots": [{"candidate_id": cid, "score": score} for cid, score in scores.items()]}
    def receipt_builder(admissions, probe_id):
        events.append({"kind": "visible_receipt", "probe_id": probe_id})
        result = {"policy_sha256": policy["policy_sha256"], "evidence": ["fixture-only"],
                  "resource_costs": {rid: .1 for rid in admissions if probe_id is None or rid != "r0"}}
        if probe_id is not None:
            rid = {"q1": "r1", "q2": "r2"}[probe_id]
            subset = tuple(s["candidate_id"] for s in admissions[rid]["slots"])
            baseline = {s["candidate_id"]: s["score"] for s in admissions["r0"]["slots"]}
            alternate = {s["candidate_id"]: s["score"] for s in admissions[rid]["slots"]}
            outcome = subset_rank_outcome(candidate_ids=subset,
                                         vina={cid: baseline[cid] for cid in subset}, vinardo=alternate)
            result.update(probe_id=probe_id, outcome=outcome["outcome"])
        return result
    result = execute_visible_task(policy, task_id="t6", candidate_ids=ids,
                                  acquire=acquire, receipt_builder=receipt_builder)
    return {"prediction": result, "events": events, "native_calls": 0,
            "evidence_admitted": False, "cost_basis": "synthetic declared callback costs",
            "measured_scientific_cost": None}


def pose_example():
    return selection_diagnostic([
        {"id": "fixture-a", "slot": 0, "admitted": True, "score": -2., "mode_rmsd_A": {"m": 3.}},
        {"id": "fixture-b", "slot": 1, "admitted": True, "score": -1., "mode_rmsd_A": {"m": .5}},
        {"id": "fixture-c", "slot": 2, "admitted": False, "score": None, "mode_rmsd_A": {}},
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = {"scope": "synthetic SDK engineering only", "transfer": transfer_example(),
              "visible": visible_example(), "missing_visible": visible_example(missing=True),
              "pose": pose_example(), "unknown_loss": loss_interval(None, [0., 1.]),
              "native_calls": 0, "scientific_gain": None}
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    else:
        print(text)
    return report


if __name__ == "__main__":
    main()
