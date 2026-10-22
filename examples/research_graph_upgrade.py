"""Read an existing research entry; exercise a tiny CPU parameter comparison.

The CSV must explicitly identify two molecules with id,smiles columns. This is an
engineering check, not a docking/affinity experiment. Prior journals are read
only. New outputs, method records, trace and checkpoint live in a new directory.
One sequential timing pair is insufficient to adopt a speed optimization.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
import time

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose
from etalon.research import Artifact, MethodUse, MethodVersion, ResearchJournal, TaskSplit
from etalon.research.components import record_component_step, record_json
from etalon.research.composition import record_evaluation
from etalon.research.continuation import CHECKPOINT, STEP, capture_watermarks, read_content, record_content, resume_research
from etalon.research.evidence_refs import file_identity
from etalon.research.method_contract import CONTRACT, COMPARISON, assess_comparability, compile_screen_revision, validate_content as validate_method_content
from etalon.research.method_revision import journal_ref
from etalon.research.research_context import build_research_context, load_entry
from etalon.research.research_trace import project_trace, render_trace_html, trace_event


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resume_only(output):
    """Verify already published results after an interrupted final report.

    No Journal writes, compilation or Screen.run. The original failure and
    artifacts remain untouched; only a new read-only acceptance report is saved.
    """
    started = time.perf_counter()
    output = Path(output).absolute()
    destination = output / "read-only-acceptance.json"
    if destination.exists():
        raise FileExistsError("read-only acceptance already exists; preserve it")
    workspace, checkpoint_ref = load_entry(output / "entry.json")
    manifest = json.loads(Path(workspace).read_text("utf-8-sig"))
    before = {s["location"]: file_identity(s["location"]) for s in manifest["sources"]}
    resumed = resume_research(workspace, checkpoint_ref)
    assert not resumed["issues"], resumed
    journal = ResearchJournal(CampaignStore(output / "research.sqlite", read_only=True))
    with journal.store.connection() as db:
        records = [json.loads(row[0]) for row in db.execute("SELECT body FROM metadata WHERE key LIKE 'research:%'").fetchall()]
    artifacts = [(record, json.loads(journal.artifact_path(Artifact(**record)).read_text("utf-8-sig")))
                 for record in records if "path" in record and record["path"].endswith(".json")]
    comparable = [content for _, content in artifacts if isinstance(content, dict) and content.get("schema") == COMPARISON]
    assert len(comparable) == 1
    comparable = validate_method_content(comparable[0])
    assert comparable["status"] == "ready_for_evaluation", comparable
    evaluations = [record for record in records if record.get("schema") == "etalon-method-evaluation/1"]
    assert len(evaluations) == 1
    traces = [content for _, content in artifacts if isinstance(content, dict) and content.get("schema") == "etalon-research-trace/1"]
    outputs, native_states = [], {}
    for branch in ("baseline", "candidate"):
        screen = Screen.open_existing(output / branch)
        state = screen.state("properties")
        assert state and state.outcome == "committed"
        for stage_id in ("standardize", "properties"):
            stage = next(s for s in state.stages if s.stage_id == stage_id)
            assert stage.committed
            rows = screen.read(stage.artifact_id, contract_id="parent/v1" if stage_id == "standardize" else "property/v1")
            assert len(rows) == 2, "All two input molecules must remain represented"
            if stage_id == "properties":
                outputs.append(rows)
        native_states[branch] = state.as_dict()
    assert outputs[0] == outputs[1], "Existing property rows differ"
    costs = [content for record, content in artifacts if record.get("bindings", {}).get("kind") == "accounting"]
    assert len(costs) == 2
    assert before == {path: file_identity(path) for path in before}, "Read-only recovery changed a journal"
    report = {"entry": {"workspace": str(workspace), "checkpoint_ref": checkpoint_ref},
        "acceptance_mode": "read_only_recovery_after_finalization_failure", "property_rows_equal": True,
        "input_count": 2, "comparability": comparable, "evaluation": evaluations[0],
        "trace_summary": project_trace(traces)["summary"], "native_states": native_states,
        "validated_existing_screen_runs": 2, "new_screen_runs": 0, "new_docking_md_lab_calls": 0,
        "old_journals_unchanged": True, "journal_identities": before, "pending_handles": resumed["pending"],
        "authority": resumed["authority"], "review_required": resumed["review_required"],
        "historical_execution_costs": costs, "read_only_validation_wall_seconds": time.perf_counter() - started,
        "default_promotion": "not_performed", "scientific_benefit": "not_established",
        "preserved_failure": file_identity(output / "failure.json") if (output / "failure.json").exists() else None,
        "python": sys.executable, "etalon_module": __import__("etalon").__file__}
    write(destination, report)
    return report


def build(output, entry, library):
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    with Path(library).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if (len(rows) != 2 or any(not row.get("id") or not row.get("smiles") for row in rows)
            or len({row["id"] for row in rows}) != 2):
        raise ValueError("This bounded example requires exactly two distinct id,smiles rows")
    old_workspace, old_ref = load_entry(entry)
    old_manifest = json.loads(Path(old_workspace).read_text("utf-8-sig"))
    old_checkpoint = read_content(old_workspace, old_ref)
    before = {s["location"]: file_identity(s["location"]) for s in old_manifest["sources"]}
    initial_context = build_research_context(entry)
    write(output / "prior-context.json", initial_context)
    assert not resume_research(old_workspace, old_ref)["issues"]
    # Freeze input CSV bytes in this run, rather than mutate any prior input.
    local_library = output / "library.csv"
    local_library.write_bytes(Path(library).read_bytes())
    store = CampaignStore(output / "research.sqlite")
    store.configure(CampaignSpec("audit", 0, "wall_second", "parameter-contract-engineering"),
        [Endpoint("audit", "engineering", "unmeasured", "count", "memory/2", 0, queryable=False)])
    journal = ResearchJournal(store)
    sid, task = "contract-check", "parameter-contract-engineering"
    question = "Does the compiled batch setting reach execution with unchanged chemical checks and property rows?"
    directory = output / "memory"
    traces, steps, plans, methods, receipts, outputs, costs, coverages = [], [], [], [], [], [], [], []

    def ref(rid):
        return journal_ref(journal, rid, source_id=sid)

    def save(value):
        return ref(record_content(journal, value, directory, source_id=sid, source_kind="new_execution"))

    def artifact(value, *, bindings=None):
        return ref(record_json(journal, value, directory, source_kind="new_execution", bindings=bindings))

    contract = {"schema": CONTRACT, "name": "CPU property batching",
        "parameters": [{"path": "/library/batch_size", "category": "execution", "type": "integer",
            "unit": "rows", "default": 65536, "minimum": 1, "maximum": 1000000, "choices": [],
            "required": True, "immutable": False, "depends_on": [],
            "expected_effect": "Batching overhead may differ; one ordered pair cannot establish speed benefit"}],
        "required_checks": ["chemical-identity", "properties"],
        "evaluation": {"quality": "identical property rows for both input molecules", "cost": "wall_seconds",
            "missing": "retain failed/unknown; never coerce to zero", "adoption": "not from one timing pair"}, "supersedes": []}
    contract_ref = save(contract)
    conditions = {"endpoint": "rdkit-properties", "scope": "CPU engineering; no pose or affinity inference"}
    identities = {"library_sha256": file_identity(local_library)["sha256"]}

    def trace(phase, branch, state, detail, *, config=None, prior=(), observed=(), method=(), evidence=(), attempt=None):
        event = trace_event(event_key=task + ":" + str(len(traces)), sequence=len(traces), task_id=task,
            branch_id=branch, node_id="properties", workspace="Quick", phase=phase,
            origin="declared" if phase == "declaration" else "observed", state=state,
            reason="Bounded engineering acceptance; no scientific benefit asserted", detail=detail,
            attempt_id=attempt, config_ref=config, previous_refs=prior, receipt_refs=observed,
            method_refs=method, evidence_refs=evidence)
        value = save(event)
        traces.append(event)
        return value

    for branch, batch in (("baseline", 1), ("candidate", 2)):
        cfg = compose("CPU-properties", [{"id": "properties", "title": "RDKit properties",
            "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}])
        cfg["library"]["batch_size"] = batch
        path = output / (branch + "-config.json")
        write(path, cfg)
        config_ref = artifact({"configuration": cfg, "file_identity": file_identity(path)})
        method = ref(journal.record(MethodVersion("CPU-properties-" + branch, ("domain_skill",),
            {"artifact_ref": config_ref["record_id"]}, parents=tuple(m["record_id"] for m in methods),
            evidence_refs=(contract_ref["record_id"], config_ref["record_id"]))))
        methods.append(method)
        declared = trace("declaration", branch, "selected", {"planned_checks": contract["required_checks"],
            "alternatives": ["batch=1", "batch=2"], "choice": "batch=" + str(batch),
            "reconsider_when": "Independent ordered repeats demonstrate a stable cost/quality change"},
            config=config_ref, method=[method], evidence=[old_ref, contract_ref])
        screen = Screen(output / branch)
        compiled = compile_screen_revision(screen, path, contract=contract, library=local_library,
            input_identities=identities, conditions=conditions,
            stage_checks={"standardize": ["chemical-identity"], "properties": ["properties"]},
            parameter_bindings=[{"parameter": "/library/batch_size", "stage_id": "library", "path": "/batch_size"}])
        plans.append(compiled)
        compiled_ref = save(compiled)
        lowered = trace("compiled", branch, "ready", {"revision_id": compiled["revision_id"],
            "declared_checks": contract["required_checks"], "compiled_checks": contract["required_checks"],
            "missing_checks": compiled["missing_checks"]}, config=config_ref, prior=[declared],
            observed=[compiled_ref], method=[method], attempt=branch)
        use = ref(journal.record(MethodUse(({"kind": "task", "id": task, "scope": str(store.path)},),
            ({"method_version_ref": method["record_id"], "scope": conditions["scope"]},),
            host_conditions=conditions, basis="host_reported",
            reason="Driver declares this compiled revision; subsequent native receipts separately establish execution")))
        native_plan = screen.plan(path, library=local_library)
        assert native_plan.revision_id == compiled["revision_id"]
        start = time.perf_counter()
        result = screen.run(native_plan, run_id="properties")
        elapsed = time.perf_counter() - start
        cost = artifact({"operation": branch, "wall_seconds": elapsed, "money": None,
            "includes": "Screen.run; excludes compilation/context/package setup", "ordered_pair": True},
            bindings={"kind": "accounting"})
        costs.append(cost)
        attached = record_component_step(journal, capability_id="molcascade.screen", location=screen.workspace,
            directory=directory, source_id=sid, source_kind="new_execution", task_id=task, question=question,
            branch_id=branch, node_id="properties", reason="Read verified producer outputs from the preceding normal execution",
            run_id="properties", input_refs=[compiled_ref], method_refs=[method], use_refs=[use], accounting_refs=[cost])
        receipt = attached["receipt_ref"]
        receipts.append(receipt)
        steps.append(attached["step_ref"])
        state = "completed" if result.outcome == "committed" else "failed"
        observed = trace("execution", branch, state, {"tool_name": "Screen.run", "call_id": branch,
            "input_digest": digest(identities), "output_digest": digest(result.as_dict()), "duration_seconds": elapsed,
            "cost": {"amount": elapsed, "unit": "wall_second", "accounting_ref": cost},
            "reuse_of": None, "error": None if state == "completed" else json.dumps(result.as_dict()),
            "completed_checks": contract["required_checks"] if state == "completed" else []},
            config=config_ref, prior=[lowered], observed=[receipt], method=[method], attempt=branch)
        trace("admission", branch, "accepted" if state == "completed" else "rejected",
            {"accepted_refs": [receipt] if state == "completed" else [],
             "rejected_refs": [receipt] if state != "completed" else [], "criteria_ref": contract_ref},
            config=config_ref, prior=[observed], observed=[receipt], method=[method], attempt=branch)
        # Save failures and the route before stopping; no success-shaped fallback.
        write(output / "trace.json", project_trace(traces))
        (output / "route.html").write_text(render_trace_html(traces, title="SND1 · CPU contract acceptance"), encoding="utf-8")
        assert result.outcome == "committed", result.as_dict()
        rows = screen.read(next(s.artifact_id for s in result.stages if s.stage_id == "properties"), contract_id="property/v1")
        outputs.append(rows)
        coverages.append([{"check_id": check, "state": "passed", "receipt_ref": receipt,
            "reuse_basis_ref": None, "input_digest": digest(identities), "conditions_digest": digest(conditions)}
            for check in contract["required_checks"]])

    def verify(row, side):
        idx = 0 if side == "base" else 1
        if row["receipt_ref"] != receipts[idx]:
            return False
        # Native reader verifies source artifacts; stage identity is checked separately.
        reader = Screen.open_existing(output / ("baseline" if idx == 0 else "candidate"))
        result = reader.state("properties")
        stage_id = "standardize" if row["check_id"] == "chemical-identity" else "properties"
        stage = next((s for s in result.stages if s.stage_id == stage_id), None)
        return bool(stage and stage.committed and len(reader.read(stage.artifact_id,
            contract_id="parent/v1" if stage_id == "standardize" else "property/v1")) == 2)

    comparable = assess_comparability(*plans, mode="same_protocol", base_coverage=coverages[0],
        candidate_coverage=coverages[1], verify_receipt=verify)
    comparable_ref = save(comparable)
    equality = artifact({"equal": outputs[0] == outputs[1], "baseline_rows": outputs[0], "candidate_rows": outputs[1],
        "scope": "Property rows only; no pose/affinity/transfer claim"})
    split = ref(journal.record(TaskSplit(("known-molecules",), ("unrun-independent-repeat",), ("unrun-transfer",),
        {"known-molecules": "seen", "unrun-independent-repeat": "not-released", "unrun-transfer": "not-released"},
        "One sequential pair with correlated inputs; diagnostic only")))
    initial = artifact({"conditions": conditions, "input_identities": identities, "prior_checkpoint": old_ref})
    metric = {"name": "property_rows_equal", "family": "regression", "unit": "boolean",
        "denominator": "all explicit input molecules", "missing_policy": "unknown, not zero"}
    plan_ref = save({"schema": STEP, "task_id": task, "question": question, "branch_id": "comparison",
        "operation": "evaluation_plan", "reason": "Evaluate normal execution under fixed obligations", "input_refs": [comparable_ref],
        "output_refs": [], "method_refs": methods, "accounting_refs": [], "source_watermarks": [], "supersedes": [],
        "detail": {"target": "method", "subjects": [methods[1]], "comparators": [methods[0]], "protocol_ref": contract_ref,
            "task_split_ref": split, "initial_conditions_ref": initial, "use_refs": [], "metrics": [metric], "trigger_refs": [comparable_ref]}})
    evaluation = record_evaluation(journal, plan_ref, directory=directory, source_id=sid, source_kind="new_execution",
        result_refs=[equality["record_id"], comparable_ref["record_id"]], accounting_refs=[r["record_id"] for r in costs],
        conclusion="Retain baseline: matching properties, but one ordered timing pair establishes no speed benefit",
        scope="Diagnostic engineering comparison; no adoption, optimization or scientific extrapolation")
    steps.extend([plan_ref, evaluation["step_ref"]])
    checkpoint = save({**old_checkpoint, "task_id": task, "question": question,
        "step_refs": [*old_checkpoint["step_refs"], *steps], "method_refs": [*old_checkpoint["method_refs"], *methods],
        "branches": [*old_checkpoint["branches"], {"branch_id": "batching-engineering", "state": "resolved",
            "basis_refs": [evaluation["step_ref"]], "next_read_refs": [],
            "reconsider_when": "Independent repeats with unchanged obligations; prior research pause remains authoritative"}],
        "source_watermarks": capture_watermarks(old_workspace, source_ids=[s["source_id"] for s in old_manifest["sources"]]),
        "supersedes": [old_ref]})
    workspace = output / "etalon-workspace.json"
    write(workspace, {**old_manifest, "workspace_id": task, "sources": [*old_manifest["sources"],
        {"source_id": sid, "kind": "journal", "location": str(store.path),
         "identity": {"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]}}]})
    new_entry = {"workspace": str(workspace), "checkpoint_ref": checkpoint}
    write(output / "entry.json", new_entry)
    resumed = resume_research(workspace, checkpoint)
    assert not resumed["issues"], resumed
    assert before == {path: file_identity(path) for path in before}, "Prior journal changed"
    report = {"entry": new_entry, "comparability": comparable, "property_rows_equal": outputs[0] == outputs[1],
        "trace_summary": project_trace(traces)["summary"], "evaluation": evaluation,
        "old_journals_unchanged": True, "old_journal_identities": before,
        "pending_handles": resumed["pending"], "new_docking_md_lab_calls": 0,
        "default_promotion": "not_performed", "scientific_benefit": "not_established",
        "python": sys.executable, "etalon_module": __import__("etalon").__file__}
    write(output / "acceptance.json", report)
    assert comparable["status"] == "ready_for_evaluation", comparable
    assert outputs[0] == outputs[1], "Property rows differ; retain failure and do not claim equivalence"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--entry", type=Path)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--resume-only", action="store_true", help="Validate existing entry/results without running or writing any Journal")
    args = parser.parse_args()
    output_existed = args.output.exists()
    if not args.resume_only and (args.entry is None or args.library is None):
        parser.error("--entry and --library are required for a new execution")
    try:
        report = resume_only(args.output) if args.resume_only else build(args.output, args.entry, args.library)
    except Exception as error:
        if not args.resume_only and not output_existed and args.output.is_dir() and not (args.output / "failure.json").exists():
            write(args.output / "failure.json", {"type": type(error).__name__, "message": str(error),
                "scope": "Outputs preserved; no success or benefit inferred"})
        raise
    print(json.dumps({k: report[k] for k in ("entry", "property_rows_equal", "trace_summary", "old_journals_unchanged")}, ensure_ascii=False))
