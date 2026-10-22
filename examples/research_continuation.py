"""Released R4 reanalysis -> ordinary journal memory -> fresh-process continuation.

No model, search, worker, PRISM dispatch, old ledger mutation, or independent
scientific trial. The explicit input is a previously released coordinator audit.
The example retains its derived-evidence scope and measures local analysis time.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import sys
import time

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical, digest
from etalon.research import Artifact, Coupling, Experiment, Observation, ResearchJournal
from etalon.research.continuation import (
    CHECKPOINT, STEP, capture_watermarks, record_content, resume_research,
)
from etalon.research.evidence import query_evidence, read_evidence
from etalon.research.evidence_refs import file_identity, make_ref


def write(path, value):
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def build(released_root, output, status):
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    released_root, status = released_root.absolute(), status.absolute()
    originals = [released_root / ".agent-local/round4/transfer-audit-v2.json",
                 released_root / "integration/round4/PROTOCOL.md"]
    pins = [{"path": str(p), **file_identity(p)} for p in originals]
    inputs = output / "inputs"; inputs.mkdir()
    for path in originals:
        shutil.copyfile(path, inputs / path.name)
        assert file_identity(inputs / path.name) == file_identity(path)
    published_rows = []
    for path, pin in zip(originals, pins):
        local = inputs / path.name
        body = {"path": str(local), **file_identity(local), "source_kind": "existing_real_artifact",
                "role": "diagnostic", "bindings": {"original_path": str(path), "original_sha256": pin["sha256"],
                                                     "scope": "released previously seen coordinator evidence"}}
        published_rows.append({"record_id": path.name, "record_type": "artifact", "visibility": "released", "record": body})
    source_manifest = output / "released.json"
    write(source_manifest, {"schema": "etalon-artifact-manifest/1", "records": published_rows})
    source = {"source_id": "released-r4", "kind": "artifact_manifest", "location": str(source_manifest),
              "identity": {"kind": "snapshot_sha256", "value": file_identity(source_manifest)["sha256"]}}
    ws = output / "etalon-workspace.json"
    manifest = {"schema": "etalon-evidence-workspace/1", "workspace_id": "r4-cost-reanalysis-continuation",
                "sources": [source], "status_refs": [str(status)], "location_map": []}
    write(ws, manifest)
    page = query_evidence(ws, filters={"source_ids": ["released-r4"]})
    external_refs = [i["ref"] for i in page["items"]]
    for ref in external_refs:
        assert read_evidence(ws, ref, view="artifact", limit=1)["verification"]["artifact"] == "verified"
    watermarks = capture_watermarks(ws, source_ids=["released-r4"])

    # New diagnostic journal, zero executable budget and zero executable endpoints.
    # Original science/cost ledgers are referenced, never copied or recharged.
    store = CampaignStore(output / "research.sqlite")
    store.configure(CampaignSpec("audit-only", 0, "wall_second", "released-r4-reanalysis/1"),
                    [Endpoint("audit-only", "released-r4", "diagnostic_only", "fraction", "reanalysis/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    def ref(identifier):
        return make_ref("research", identifier, journal.get(identifier))
    def save(payload):
        return ref(record_content(journal, payload, output / "content", source_id="research", source_kind="new_execution"))
    def step(operation, detail, reason, input_refs=(), output_refs=(), accounting_refs=()):
        return save({"schema": STEP, "task_id": "r4-cost-reanalysis", "question": question,
                     "branch_id": "cost-adjusted-benefit", "operation": operation, "reason": reason,
                     "input_refs": list(input_refs), "output_refs": list(output_refs), "method_refs": [],
                     "accounting_refs": list(accounting_refs), "source_watermarks": watermarks,
                     "supersedes": [], "detail": detail})
    question = "Does the released R4 phi loss improvement survive the frozen cost penalty?"
    search = step("search", {"query": "", "source_ids": ["released-r4"],
        "conditions": "Previously released R4 prepared-pose three-family test, frozen cost weight",
        "coverage": [{"source_id": "released-r4", "status": "complete", "truncated": False, "errors": []}],
        "selected_refs": external_refs, "excluded": [], "refresh_when": "Different tasks, protocol or new released evidence"},
        "Existing released sources suffice; no external search", output_refs=external_refs)
    hypothesis = save({"schema": "etalon-research-claim/1", "question": question,
        "statement": "A lower raw loss alone may not establish cost-adjusted improvement.",
        "subjects": ["phi", "phi0", "cdk2", "hif2a", "tnks2"], "conditions": ["frozen R4 comparison"],
        "epistemic_status": "hypothesis", "support_refs": external_refs, "counter_refs": [],
        "limitations": ["Previously seen three-family pilot; no new science or transfer claim"], "method_refs": [], "supersedes": []})
    plan = step("plan", {"hypothesis_refs": [hypothesis],
        "prediction": "Raw loss gain may be smaller than the frozen additional cost penalty",
        "controls": ["Same released phi/phi0 comparison, same cost weight and families"],
        "readouts": [{"name": "raw_loss_gain", "unit": "fraction"}, {"name": "extra_path_cost", "unit": "wall_second"},
                     {"name": "net_objective_change", "unit": "loss_plus_weighted_cost"}],
        "protocol_refs": external_refs, "decision_rule": "Positive net objective change means phi is worse",
        "stop_rule": "Finish the exact released-data arithmetic; do not submit science"},
        "Existing evidence supports a discriminating arithmetic check", input_refs=[hypothesis, *external_refs])
    local_inputs = []
    for row in published_rows:
        body = row["record"]
        artifact = Artifact(body["path"], sha256=body["sha256"], bytes=body["bytes"], source_kind="existing_real_artifact",
                            role="diagnostic", bindings={**body["bindings"], "origin_source_id": "released-r4", "reuse": "seen_reanalysis"})
        local_inputs.append(journal.record(artifact))
    method = Artifact(str(Path(__file__).absolute()), **file_identity(__file__), source_kind="existing_real_artifact",
                      role="diagnostic", bindings={"kind": "reanalysis_implementation"})
    journal.record(method)
    began_wall, began_cpu = time.perf_counter(), time.process_time()
    audit = json.loads((inputs / originals[0].name).read_text(encoding="utf-8"))
    if audit["schema"] != "coordinator-r4-transfer-audit/1":
        raise ValueError("Requires the explicit released R4 audit schema")
    weight = audit["common_frozen_comparison_cost_weight"]
    phi, baseline = (audit["comparators"][key] for key in ("phi", "phi0"))
    for item in (phi, baseline):
        computed = item["group_mean_scientific_loss"] + weight * item["group_mean_path_cost"]
        if not math.isclose(computed, item["common_frozen_loss_plus_cost"], abs_tol=1e-12, rel_tol=0):
            raise ValueError("Released comparison is inconsistent with its frozen weight")
    values = {"raw_loss_gain": baseline["group_mean_scientific_loss"] - phi["group_mean_scientific_loss"],
              "extra_path_cost": phi["group_mean_path_cost"] - baseline["group_mean_path_cost"],
              "net_objective_change": phi["common_frozen_loss_plus_cost"] - baseline["common_frozen_loss_plus_cost"]}
    timing = {"wall_seconds": time.perf_counter() - began_wall, "cpu_seconds": time.process_time() - began_cpu,
              "scope": "JSON parsing and local arithmetic only; excludes journal/publication/development",
              "money": None, "historical_cost_recharged": False}
    result = output / "reanalysis.json"
    write(result, {"schema": "released-r4-cost-reanalysis/1", "weight": weight, "values": values,
                   "source_pins": pins, "original_comparators": {"phi": phi, "phi0": baseline},
                   "scope": "Previously seen coordinator audit; not new scoring or independent benefit"})
    result_artifact = Artifact(str(result), **file_identity(result), source_kind="new_execution", role="diagnostic", bindings={"kind": "reanalysis"})
    journal.record(result_artifact)
    accounting_path = output / "analysis-accounting.json"; write(accounting_path, timing)
    accounting = Artifact(str(accounting_path), **file_identity(accounting_path), source_kind="new_execution", role="diagnostic", bindings={"kind": "accounting"})
    journal.record(accounting)
    experiment = Experiment("r4-cost-reanalysis", "reanalysis", {"source_group": "released-r4-test-three-families"},
        tuple(local_inputs + [method.id]), "released-cost-arithmetic/1", digest({"inputs": local_inputs, "weight": weight}),
        tuple(values), Coupling(shared_artifacts=tuple(local_inputs), assumptions=("Same seen source; correlated evidence, not independent replication",)))
    journal.record(experiment)
    observation = Observation(experiment.id, "measured", values,
        {"raw_loss_gain": "fraction", "extra_path_cost": "wall_second", "net_objective_change": "loss_plus_weighted_cost"}, (result_artifact.id,))
    journal.record(observation)
    result_step = step("result", {"experiment_refs": [ref(experiment.id)], "observation_refs": [ref(observation.id)], "action_handles": []},
        "Completed actual local reanalysis without an executor action", input_refs=[plan, *external_refs],
        output_refs=[ref(observation.id), ref(result_artifact.id)], accounting_refs=[ref(accounting.id)])
    conclusion = "The raw-loss improvement is outweighed by additional cost; no promotion under the frozen R4 objective."
    if values["net_objective_change"] <= 0:
        conclusion = "The released arithmetic favors phi under this frozen objective; broader benefit remains untested."
    claim = save({"schema": "etalon-research-claim/1", "question": question, "statement": conclusion,
        "subjects": ["phi", "phi0"], "conditions": ["Three seen families; fixed prepared poses", f"cost weight={weight}"],
        "epistemic_status": "interpretation", "support_refs": [ref(observation.id), ref(result_artifact.id)],
        "counter_refs": [], "limitations": ["Derived from released coordinator evidence; no new simulation", "No independent RSI benefit"],
        "method_refs": [], "supersedes": []})
    judgment = step("judgment", {"claim_refs": [claim]}, "Separate raw loss and cost-adjusted conclusions", input_refs=[result_step], output_refs=[claim])
    decision = step("decision", {"choice": "stop", "alternatives": [{"branch_id": "repeat-scoring", "disposition": "skip",
        "reason": "No new discriminating information for this arithmetic question; R4b paused"}], "basis_refs": [claim],
        "reconsider_when": "New independent tasks or a changed scientific mechanism, with separate authorization"},
        "The released-evidence question is resolved; no automatic science continuation", input_refs=[claim])
    cp = save({"schema": CHECKPOINT, "task_id": "r4-cost-reanalysis", "question": question,
        "step_refs": [search, plan, result_step, judgment, decision], "claim_refs": [claim], "counter_refs": [], "method_refs": [],
        "branches": [{"branch_id": "cost-adjusted-benefit", "state": "resolved", "basis_refs": [claim],
            "next_read_refs": [ref(result_artifact.id)], "reconsider_when": "New independently justified evidence"},
            {"branch_id": "repeat-scoring", "state": "stopped", "basis_refs": [claim], "next_read_refs": [],
             "reconsider_when": "Explicit science authorization and a discriminating new question"}],
        "pending_handles": [], "source_watermarks": watermarks,
        "status_guards": [{"location": str(status), **file_identity(status), "state": "paused"}], "supersedes": []})
    manifest["sources"].append({"source_id": "research", "kind": "journal", "location": str(store.path),
                                "identity": {"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]}})
    # Only the new example's manifest is republished; original sources/records stay immutable.
    ws.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    entry = output / "entry.json"
    write(entry, {"workspace": str(ws), "checkpoint_ref": cp})
    for pin in pins:
        assert file_identity(pin["path"]) == {k: pin[k] for k in ("sha256", "bytes")}
    assert store.actions() == [] and store.balance()["spent"] == 0
    report = {"entry": str(entry), "values": values, "observation_ref": ref(observation.id), "claim_ref": claim,
              "module": __file__, "python": sys.executable, "new_scoring_actions": 0, "source_pins": pins,
              "scope": "Real released-evidence reanalysis and memory chain; no new independent scientific experiment"}
    write(output / "build-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("build")
    create.add_argument("--released-root", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--paused-status", required=True, type=Path, help="Explicit authority file, interpreted by caller as paused")
    resume = commands.add_parser("resume")
    resume.add_argument("--entry", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.released_root, args.output, args.paused_status)
    else:
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        result = resume_research(entry["workspace"], entry["checkpoint_ref"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
