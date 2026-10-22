"""Offline component-memory chain; no docking, MD, lab or optimizer dispatch.

Read an explicit existing PRISM bundle, retain all its slots, derive a local
catalog from decoded structures, and measure CPU properties through MolCascade.
Fixture provenance remains fixture. An optional earlier research entry supplies
separate existing scientific claims, never labels for these local molecules.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.boundary.quarry import DataBudget
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose
from etalon.data.design import read_bundle
from etalon.data.library import prepare_library
from etalon.data.service import plan_data, run_data
from etalon.research import Artifact, MethodUse, MethodVersion, ResearchJournal, TaskSplit
from etalon.research.components import capabilities, record_component_step, record_json
from etalon.research.composition import WORKFLOW, node_step, record_evaluation
from etalon.research.continuation import CHECKPOINT, STEP, capture_watermarks, read_content, record_content, resume_research
from etalon.research.evidence_refs import file_identity
from etalon.research.method_revision import journal_ref
from etalon.skills import persist_snapshot


def build(root, bundle_path, paused_status, released_entry=None):
    root = Path(root).absolute()
    root.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    store = CampaignStore(root / "research.sqlite")
    store.configure(CampaignSpec("audit", 0, "wall_second", "component-memory-engineering"),
                    [Endpoint("audit", "engineering", "unmeasured", "count", "memory/2", 0, queryable=False)])
    journal = ResearchJournal(store)
    sid, task = "batch2", "component-memory"
    question = "Can component outputs, optional methods and stopped branches be continued without replay?"
    directory = root / "memory"

    def ref(rid):
        return journal_ref(journal, rid, source_id=sid)

    def save(payload):
        return ref(record_content(journal, payload, directory, source_id=sid, source_kind="new_execution"))

    def artifact(payload, **bindings):
        return ref(record_json(journal, payload, directory, source_kind="new_execution", bindings=bindings))

    def version(name, role, content, parents=()):
        return ref(journal.record(MethodVersion(name, (role,), {"artifact_ref": content},
                    evidence_refs=(content["record_id"],), parents=tuple(p["record_id"] for p in parents))))

    catalog = artifact(capabilities(), kind="component_catalog")
    instructions = artifact({"method": "Read only the explicit producer output through its existing validator; retain failures, identities and original bytes; do not dispatch or admit labels."})
    reader = version("verified-component-read", "domain_skill", instructions)
    # Availability is not adoption: preserve a complete native small-skill version
    # but do not claim that this deterministic example applied its instructions.
    native_skill = ref(journal.record(MethodVersion("molquarry-compound-sourcing", ("domain_skill",),
        persist_snapshot("molquarry-compound-sourcing", root / "native-skill"),
        verification_basis="content_verified", entrypoint="SKILL.md")))
    wf = {"schema": WORKFLOW, "name": "existing-design-to-local-properties", "scope": "offline engineering; no new biological evidence",
          "method_refs": [reader], "supersedes": [], "nodes": [], "edges": []}
    for name, cap, views in (("design", "prism.design", ["E", "R"]),
                             ("catalog", "molquarry.data", ["E"]),
                             ("properties", "molcascade.screen", ["X", "R"]),
                             ("expensive", "prism.general_fep_lab", ["X"])):
        wf["nodes"].append({"node_id": name, "capability_id": cap, "method_refs": [] if name == "expensive" else [reader],
                           "views": views, "purpose": name, "skip_when": "Already available, unsupported or no discriminating question"})
    wf["edges"] = [{"from": a, "to": b, "kind": "data", "condition": "Only actual referenced outputs"}
                   for a, b in (("design", "catalog"), ("catalog", "properties"))]
    wf_ref = save(wf)
    steps, components, costs = [], {}, []
    checked = read_bundle(bundle_path)
    source_kind = "fixture" if checked["bundle"]["source"].get("kind") == "fixture" else "existing_real_artifact"

    def attach(name, cap, location, inputs=(), run_id=None, kind=source_kind):
        use = ref(journal.record(MethodUse(({"kind": "task", "id": task, "scope": str(store.path)},),
            ({"method_version_ref": reader["record_id"], "scope": "Read/validate and record only; not original producer execution"},),
            visible_evidence_refs=tuple(r["record_id"] for r in inputs if r["source_id"] == sid),
            reason="Authored example applies this read recipe; host_reported, not native skill telemetry")))
        args = dict(capability_id=cap, location=location, directory=directory, source_id=sid, source_kind=kind,
                    task_id=task, question=question, branch_id="reuse", node_id=name, workflow_ref=wf_ref,
                    reason="Reuse exact existing component outputs", input_refs=list(inputs), method_refs=[reader],
                    use_refs=[use], run_id=run_id)
        t = time.perf_counter()
        result = record_component_step(journal, **args)
        events, balance = store.events(), store.balance()
        assert record_component_step(journal, **args) == result
        assert store.events() == events and store.balance() == balance
        costs.append({"operation": name + "_read_and_idempotency_check", "wall_seconds": time.perf_counter() - t})
        components[name] = result
        steps.append(result["step_ref"])
        return result

    design = attach("design", "prism.design", bundle_path, [catalog])
    # Native candidate IDs survive as the local catalog's identifiers; no failed
    # or undecoded slot is silently converted into a molecule or numeric label.
    candidates = [r for r in checked["bundle"]["candidates"] if r["identity"] is not None]
    import csv
    csv_path = root / "catalog-input.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "smiles"])
        writer.writerows((r["candidate_id"], r["identity"]["canonical_isomeric_smiles"]) for r in candidates)
    request = {"kind": "import_catalog", "source": "chembl", "path": str(csv_path),
               "options": {"source_version": "EXPLICIT-LOCAL-DERIVED-CATALOG-NOT-CHEMBL-ASSAYS"}, "search": {"limit": 100}}
    budget = DataBudget(max_requests=0)
    plan = plan_data(request, budget=budget)
    t = time.perf_counter()
    acquired = run_data(request, root, run_id="catalog", budget=budget, expected_plan_id=plan["plan_id"])
    library = prepare_library(Path(acquired["snapshot"]), root, run_id="library",
                              id_field="fields.id", smiles_field="fields.smiles")
    costs.append({"operation": "local_catalog_and_library", "wall_seconds": time.perf_counter() - t})
    source = attach("catalog", "molquarry.data", acquired["snapshot"], [design["step_ref"]])
    library_node = record_component_step(journal, capability_id="etalon.library", location=library["snapshot"],
        directory=directory, source_id=sid, source_kind=source_kind, task_id=task, question=question,
        branch_id="reuse", node_id="library", reason="Retain existing library mapping", input_refs=[source["step_ref"]])
    steps.append(library_node["step_ref"])
    config = root / "cpu-properties.json"
    config.write_text(json.dumps(compose("local-properties", [{"id": "properties", "title": "RDKit CPU properties",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}])), encoding="utf-8")
    screen = Screen(root / "screen")
    screen_plan = screen.plan(config, Path(library["snapshot"]) / "library.csv")
    t = time.perf_counter()
    result = screen.run(screen_plan, run_id="properties")
    costs.append({"operation": "actual_cpu_properties", "wall_seconds": time.perf_counter() - t})
    attach("properties", "molcascade.screen", screen.workspace, [library_node["step_ref"]], run_id="properties")
    (root / "accounting.json").write_text(json.dumps({"operations": costs, "money": None}, indent=2), encoding="utf-8")
    assert result.terminal and result.outcome == "committed", result.as_dict()
    stop = save(node_step(task_id=task, question=question, branch_id="expensive", node_id="expensive",
        capability_id="prism.general_fep_lab", workflow_ref=wf_ref, state="skipped",
        reason="No connected general FEP/lab endpoint; science pause remains in force", input_refs=[wf_ref]))
    steps.append(stop)

    split = ref(journal.record(TaskSplit(("engineering-dev",), ("unrun-validation",), ("unrun-transfer",),
        {"engineering-dev": "fixed", "unrun-validation": "not-released", "unrun-transfer": "not-released"},
        "engineering correctness; no independent comparison claimed")))
    metric = {"name": "comparable_correctness", "family": "regression", "unit": "fraction",
              "denominator": "completed comparable cases", "missing_policy": "unrun/unknown separate; never zero"}
    metrics = [metric, {**metric, "name": "reference_positive_retention", "family": "retention",
                         "denominator": "complete reference positive panel; unknown if unavailable"},
               {**metric, "name": "funnel_progress", "family": "funnel", "denominator": "explicit per-node entrants and measured outcomes"}]
    protocol = artifact({"metrics": metrics, "scope": "prospective comparison entry; trials not run"})
    initial = artifact({"workflow_ref": wf_ref, "available_capability_ref": catalog, "shared_environment": "fixed offline CPU"})
    plans = []
    for target in ("method", "branch_selection", "workflow", "workflow_composition"):
        role = "usage_method" if target in {"branch_selection", "workflow_composition"} else "domain_skill"
        baseline = version("baseline-" + target, role, wf_ref if target == "workflow" else instructions)
        proposed = artifact({"target": target, "proposal": "Compare explicit skip/reuse or local read changes", "tested": False})
        candidate = version("candidate-" + target, role, proposed, [baseline])
        p = save({"schema": STEP, "task_id": task, "question": question, "branch_id": "comparison",
            "operation": "evaluation_plan", "reason": "Prepare bounded comparison; no optimizer called",
            "input_refs": [wf_ref], "output_refs": [], "method_refs": [baseline, candidate], "accounting_refs": [],
            "source_watermarks": [], "supersedes": [], "detail": {"target": target, "subjects": [candidate],
            "comparators": [baseline], "protocol_ref": protocol, "task_split_ref": split, "initial_conditions_ref": initial,
            "use_refs": [], "metrics": metrics, "trigger_refs": [stop]}})
        plans.append(p)
        steps.append(p)
    diagnostic = record_evaluation(journal, plans[0], directory=directory, source_id=sid, source_kind="new_execution",
        conclusion="Comparison not run; benefit unknown; keep current methods", scope="diagnostic planning only")
    steps.append(diagnostic["step_ref"])

    old_sources, claims, watermarks = [], [], []
    if released_entry:
        old_entry = json.loads(Path(released_entry).read_text("utf-8"))
        previous = resume_research(old_entry["workspace"], old_entry["checkpoint_ref"])
        if previous["issues"]:
            raise ValueError("earlier evidence needs review")
        old_sources = json.loads(Path(old_entry["workspace"]).read_text("utf-8"))["sources"]
        claims = previous["claim_refs"]
        watermarks = capture_watermarks(old_entry["workspace"], source_ids=[s["source_id"] for s in old_sources])
        assert all("no promotion" in read_content(old_entry["workspace"], c)["statement"] for c in claims)
    cp = save({"schema": CHECKPOINT, "task_id": task, "question": question, "step_refs": steps,
        "claim_refs": claims, "counter_refs": [], "method_refs": [reader, native_skill],
        "branches": [{"branch_id": bid, "state": state, "basis_refs": basis, "next_read_refs": [],
                      "reconsider_when": "New discriminating evidence and the applicable authorization"}
                     for bid, state, basis in (("reuse", "resolved", [components["properties"]["step_ref"]]),
                                               ("expensive", "stopped", [stop]), ("comparison", "waiting", plans))],
        "pending_handles": [], "source_watermarks": watermarks, "supersedes": [],
        "status_guards": [{"location": str(Path(paused_status).absolute()), **file_identity(paused_status), "state": "paused"}]})
    ws = root / "etalon-workspace.json"
    ws.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": task,
        "sources": [*old_sources, {"source_id": sid, "kind": "journal", "location": str(store.path),
            "identity": {"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]}}],
        "status_refs": [str(Path(paused_status).absolute())], "location_map": []}, indent=2), encoding="utf-8")
    entry = {"workspace": str(ws), "checkpoint_ref": cp}
    (root / "entry.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")
    context = resume_research(ws, cp)
    assert not context["issues"] and not context["warnings"], context
    report = {"entry": entry, "source_kind": source_kind, "components": components,
        "workflow_ref": wf_ref, "evaluation_plans": plans, "diagnostic": diagnostic,
        "steps": len(steps), "checked_refs": len(context["checked_refs"]), "source_http_requests": acquired["usage"]["requests"],
        "new_docking_md_lab_calls": 0, "accounting": costs, "wall_seconds": time.perf_counter() - started,
        "money": None, "scientific_balance": store.balance(), "python": sys.executable,
        "prior_claims_are_separate": "Earlier R4 claims are not evidence of properties or affinity of these input molecules."}
    (root / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--design-bundle", required=True, type=Path)
    parser.add_argument("--paused-status", required=True, type=Path)
    parser.add_argument("--released-entry", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    output_existed = args.output.exists()
    try:
        result = build(args.output, args.design_bundle, args.paused_status, args.released_entry)
    except Exception as error:
        if not output_existed and args.output.is_dir() and not (args.output / "failure.json").exists():
            (args.output / "failure.json").write_text(json.dumps({"error_type": type(error).__name__,
                "message": str(error), "wall_seconds": time.perf_counter() - started,
                "scope": "local engineering; failure outputs preserved", "money": None}, indent=2), encoding="utf-8")
        raise
    print(json.dumps({k: result[k] for k in ("entry", "source_kind", "steps", "checked_refs", "wall_seconds")}, ensure_ascii=False))
