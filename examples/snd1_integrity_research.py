"""Archive a prospective SND1 method comparison through existing research APIs.

This example never launches scientific work. Preparation records an exact frozen
protocol before execution; completion consumes real, identity-bound results.
Publication and native execution recovery remain separate existing contracts.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.research import Artifact, MethodUse, MethodVersion, ResearchJournal, TaskSplit
from etalon.research import improvement as imp
from etalon.research.components import record_json
from etalon.research.composition import node_step, record_evaluation
from etalon.research.continuation import CHECKPOINT, STEP, record_content, resume_research
from etalon.research.evaluation_contract import (
    build_evaluation_contract, evaluate_contract, record_evaluation_artifact,
)
from etalon.research.evidence_refs import file_identity, make_ref
from etalon.research.method_revision import (
    build_method_revision, record_method_revision, resolve_method_revision,
)
from etalon.research.opportunity_state import (
    build_opportunity_disposition, resolve_opportunity_state,
)
from etalon.research.publication import _save, prepare_publication, resume_publication

TASK = "snd1-integrity-20261024"
SID = "snd1-integrity-science"
QUESTION = "Do wider bounds rescue known SND1 failures without control regressions?"
SCOPE = "Seen QOY/QOV conditional pose repair; not blind docking, affinity or independent chemical transfer"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def frozen_rules(protocol):
    gate = protocol["prespecified_candidate_gate"]
    return [
        {"name": "recovered_failures", "family": "retention", "operator": "ge",
         "threshold": gate["minimum_rescues_among_historical_17"], "unit": "case"},
        {"name": "control_regressions", "family": "regression", "operator": "eq",
         "threshold": gate["maximum_regressions_among_previously_passing_unique_controls"], "unit": "case"},
        {"name": "hard_invariant_failures", "family": "regression", "operator": "eq",
         "threshold": gate["maximum_new_hard_violations"], "unit": "case"},
        {"name": "candidate_cpu_ratio", "family": "cost", "operator": "le",
         "threshold": gate["maximum_solver_cpu_ratio_wide_on_over_narrow_on"], "unit": "ratio"},
    ]


def verify_preparation(archive, state):
    records = {}
    for key in ("protocol_ref", "freeze_ref", "selection_ref"):
        ref = state[key]
        body = archive.journal.get(ref["record_id"])
        if ref["source_id"] != SID or digest(body) != ref["record_digest"]:
            raise ValueError("prepared reference identity changed")
        archive.journal.artifact_path(Artifact(**body))
        records[key] = body
    bound = records["protocol_ref"]
    if (str(Path(state["protocol_path"]).absolute()) != bound["path"]
            or state["protocol_identity"] != {k: bound[k] for k in ("sha256", "bytes")}):
        raise ValueError("prepared protocol differs from archived protocol identity")
    if state["rules"] != frozen_rules(read(bound["path"])):
        raise ValueError("evaluation rules differ from frozen scientific protocol")
    return records


def verify_derived_metrics(summary, comparison, selection, freeze):
    """Bind readable metrics to the authenticated comparison and frozen counts."""
    selected = selection["conditions"]
    failures = sum(c["historical_stratum"] == "common_failure" for c in selected)
    controls = len(selected) - failures
    expected_calls = freeze["expected_execution_calls"]
    for key, expected in (("matched_cases", len(selected)), ("failure_cases", failures)):
        if type(summary.get(key)) is not int or summary[key] != expected:
            raise ValueError("result denominators differ from frozen unique conditions")
    if any(type(comparison.get(k)) is not int or comparison[k] != v for k, v in
           (("unique_conditions", len(selected)), ("execution_calls", expected_calls),
            ("completed", expected_calls))):
        raise ValueError("cannot adopt: raw execution denominators differ from freeze")
    for key, maximum in (("recovered_failures", failures), ("control_regressions", controls),
                         ("hard_invariant_failures", expected_calls)):
        value = summary.get(key)
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError("cannot adopt: non-integral or out-of-range count: " + key)
    ratio = summary.get("candidate_cpu_ratio")
    if type(ratio) not in (int, float) or not math.isfinite(ratio) or ratio < 0:
        raise ValueError("cannot adopt: invalid solver CPU ratio")
    gate = comparison["gate"]
    for derived, raw in (("recovered_failures", "rescues_of17"),
                         ("control_regressions", "control_regressions"),
                         ("hard_invariant_failures", "hard_violations_or_unavailable"),
                         ("candidate_cpu_ratio", "solver_cpu_ratio"),
                         ("baseline_reproduction_pass", "narrow_on_reproduction")):
        # Missing checks remain insufficient in the existing evaluation contract.
        if derived in summary and summary[derived] != gate.get(raw):
            raise ValueError("cannot adopt: derived metric disagrees with original: " + derived)
    for group, maximum in (("success_counts", len(selected)), ("failure_stratum_success", failures)):
        values = comparison.get(group, {})
        if set(values) != {"narrow_off", "narrow_on", "wide_off", "wide_on"} or any(
                type(v) is not int or not 0 <= v <= maximum for v in values.values()):
            raise ValueError("cannot adopt: invalid four-cell counts")
    if comparison.get("candidate_id") != summary.get("candidate_id"):
        raise ValueError("cannot adopt: candidate differs between metrics and original")
    return controls


class Archive:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = CampaignStore(self.root / "research.sqlite")
        self.store.configure(CampaignSpec("archive", 0, "wall_second", TASK),
            [Endpoint("archive", "research", "archive", "count", TASK, 0, queryable=False)])
        self.journal = ResearchJournal(self.store)
        self.content = self.root / "content"

    def ref(self, rid):
        return make_ref(SID, rid, self.journal.get(rid))

    def obj(self, value, kind="evidence"):
        return self.ref(record_json(self.journal, value, self.content,
            source_kind="existing_real_artifact", bindings={"task_id": TASK, "kind": kind}))

    def file(self, path, kind="original"):
        path = Path(path).absolute()
        return self.ref(self.journal.record(Artifact(str(path), **file_identity(path),
            source_kind="existing_real_artifact", role="diagnostic",
            bindings={"task_id": TASK, "kind": kind})))

    def save(self, value):
        return self.ref(record_content(self.journal, value, self.content,
            source_id=SID, source_kind="existing_real_artifact"))

    def workspace(self, name):
        name += "-" + digest(self.store.events())[:12]
        snapshot = self.root / (name + ".sqlite")
        if not snapshot.exists():
            with self.store.connection() as src, sqlite3.connect(snapshot) as dst:
                src.backup(dst)
        path = self.root / (name + "-workspace.json")
        _save(path, {"schema": "etalon-evidence-workspace/1", "workspace_id": TASK,
            "sources": [{"source_id": SID, "kind": "journal", "location": str(snapshot),
                "identity": {"kind": "snapshot_sha256", "value": file_identity(snapshot)["sha256"]}}],
            "status_refs": [str(self.root / "AUTHORITY.json")], "location_map": []})
        return path

    def checkpoint(self, name, *, steps, methods, counters, basis, state, supersedes=(), claims=()):
        authority = self.root / "AUTHORITY.json"
        cp = self.save({"schema": CHECKPOINT, "task_id": TASK, "question": QUESTION,
            "step_refs": steps, "claim_refs": list(claims), "counter_refs": counters, "method_refs": methods,
            "branches": [{"branch_id": "torsion-range-pocket", "state": state,
                "basis_refs": basis, "next_read_refs": basis,
                "reconsider_when": "New identity-bound evidence or genuinely changed scientific conditions"},
                {"branch_id": "R4b", "state": "stopped", "basis_refs": [counters[0]],
                 "next_read_refs": [], "reconsider_when": "Only explicit user resumption of the paused R4b task"}],
            "pending_handles": [], "source_watermarks": [], "supersedes": list(supersedes),
            "status_guards": [{"location": str(authority), **file_identity(authority), "state": "active"}]})
        entry = self.root / (name + "-entry.json")
        _save(entry, {"workspace": str(self.workspace(name)), "checkpoint_ref": cp})
        return entry


def prepare(root, protocol_path):
    a = Archive(root)
    protocol_path = Path(protocol_path).absolute()
    protocol_pin = file_identity(protocol_path)
    freeze_path = protocol_path.with_name("freeze.json")
    selection_path = protocol_path.with_name("selection.json")
    freeze = read(freeze_path)
    if freeze["documents"]["protocol.json"] != protocol_pin or freeze["documents"]["selection.json"] != file_identity(selection_path):
        raise ValueError("protocol or selected conditions differ from producer freeze")
    saved = a.root / "prepared.json"
    if saved.exists():
        state = read(saved)
        if state["protocol_identity"] != protocol_pin:
            raise ValueError("prepared protocol identity changed")
        verify_preparation(a, state)
        return state
    _save(a.root / "AUTHORITY.json", {"task_id": TASK, "state": "active", "r4b": "paused",
        "automatic_scientific_retry": False, "scope": SCOPE,
        "native_execution_owner": "separate pinned scientific attempt; journal never launches work"})
    protocol = a.file(protocol_path, "frozen_scientific_protocol")
    freeze_ref = a.file(freeze_path, "frozen_execution_manifest")
    selection_ref = a.file(selection_path, "frozen_seen_conditions")
    problem = a.obj({"problem": "Seventeen paired SND1 conditions failed both original F and T0",
        "source_protocol_ref": protocol, "causal_gap": "Original F/T0 changed both freedom and objective",
        "new_question": "Separate correction range from pocket terms in a common four-torsion parameterization",
        "seen_scope": SCOPE, "r4b": "paused", "original_science_must_not_be_replayed": True})
    conditions = {"target": "SND1", "ligand_scope": "QOY/QOV", "design": "torsion_range_x_pocket",
        "evidence_role": "new_execution_seen_diagnostic", "protocol_sha256": protocol_pin["sha256"]}
    base_payload = a.obj({"protocol_ref": protocol, "variant": "narrow_on",
        "correction_bounds_degrees": [10, 10, 10, 20], "pocket_terms": True})
    candidate_payload = a.obj({"protocol_ref": protocol, "variant": "wide_on",
        "correction_bounds_degrees": [30, 30, 30, 60], "pocket_terms": True,
        "unchanged": "Same four torsions, starting coordinates, reference information, base energy and quality obligations"})
    base = a.ref(a.journal.record(MethodVersion("snd1-narrow-torsion-pocket-v1", ("domain_skill",),
        {"artifact_ref": base_payload["record_id"]}, evidence_refs=(protocol["record_id"],))))
    candidate = a.ref(a.journal.record(MethodVersion("snd1-wide-torsion-pocket-v1", ("domain_skill",),
        {"artifact_ref": candidate_payload["record_id"]}, parents=(base["record_id"],),
        evidence_refs=(protocol["record_id"],), change_summary="Widen explicit torsion correction bounds only")))
    ctx = a.save(imp.build_revision_context(a.workspace("context"), task_id=TASK, scope=SCOPE,
        conditions=conditions, target_ref=base, problem_refs=[problem], evidence_refs=[protocol]))
    opportunity = imp.register_opportunity(a.journal, ctx, a.content,
        source_id=SID, source_kind="existing_real_artifact")
    patch = a.save(imp.build_method_patch(a.journal, context_ref=ctx,
        opportunity_ref=opportunity["opportunity_ref"], base_method_ref=base,
        candidate_method_ref=candidate, expected_change="Rescue at least 3 known failures with no matched-control regression and at most 2x solver CPU",
        source_id=SID)["patch"])
    generator_payload = a.obj({"recipe": "Host proposes a controlled bounds-by-pocket comparison after inspecting real failures",
        "attribution": "Host-authored candidate; no autonomous generator quality claim"})
    generator = a.ref(a.journal.record(MethodVersion("snd1-controlled-ablation-design", ("improvement_method",),
        {"artifact_ref": generator_payload["record_id"]})))
    generator_use = a.ref(a.journal.record(MethodUse(
        ({"kind": "task", "id": TASK, "scope": str(a.store.path)},),
        ({"method_version_ref": generator["record_id"], "scope": SCOPE},),
        reason="Actual host-authored bounded candidate and frozen experiment", host_conditions=conditions)))
    revision = a.ref(record_method_revision(a.journal, build_method_revision(problem_refs=[problem, ctx],
        target_kind="S", base_method_ref=base, producer_method_ref=generator, producer_use_ref=generator_use,
        candidate_version_refs=[candidate], change_summary="Widen correction bounds under unchanged quality requirements",
        mechanism_hypothesis="Some seen failures are constrained by correction range; wider range may rescue them",
        scope=SCOPE), a.content, source_id=SID, source_kind="existing_real_artifact"))
    initial = a.obj({"conditions": conditions, "protocol_ref": protocol, "freeze_ref": freeze_ref,
        "selection_ref": selection_ref,
        "same_reference_information": True, "independent_chemical_confirmation": False})
    cases = read(selection_path)["conditions"]
    split = a.ref(a.journal.record(TaskSplit(
        tuple(c["condition_id"] for c in cases if c["seed"] <= 12),
        tuple(c["condition_id"] for c in cases if 13 <= c["seed"] <= 18),
        tuple(c["condition_id"] for c in cases if c["seed"] >= 19),
        {c["condition_id"]: c["coordinate_sha256"] for c in cases},
        "Historical seed partitions only; ALL selected conditions now seen. No independent validation or transfer claim")))
    rules = frozen_rules(read(protocol_path))
    plan = a.save({"schema": STEP, "task_id": TASK, "question": QUESTION,
        "branch_id": "torsion-range-pocket", "operation": "evaluation_plan",
        "reason": "Freeze comparison before normal scientific execution; no separate mandatory Probe",
        "input_refs": [patch, revision], "output_refs": [], "method_refs": [base, candidate],
        "accounting_refs": [], "source_watermarks": [], "supersedes": [],
        "detail": {"target": "method", "subjects": [candidate], "comparators": [base],
            "protocol_ref": protocol, "task_split_ref": split, "initial_conditions_ref": initial,
            "use_refs": [], "trigger_refs": [problem],
            "metrics": [{"name": r["name"], "family": r["family"], "unit": r["unit"],
                "denominator": "Unique frozen paired conditions; repeated chemical systems are not independent samples",
                "missing_policy": "insufficient"} for r in rules]}})
    awaiting = a.save(imp.assess_revision(a.journal, patch_ref=patch, plan_ref=plan, source_id=SID))
    entry = a.checkpoint("prepared", steps=[plan], methods=[base, candidate], counters=[problem],
        basis=[awaiting, protocol], state="waiting")
    state = {"task_id": TASK, "protocol_path": str(protocol_path), "protocol_identity": protocol_pin,
        "protocol_ref": protocol, "freeze_ref": freeze_ref, "selection_ref": selection_ref,
        "problem_ref": problem, "conditions": conditions, "base_ref": base,
        "candidate_ref": candidate, "context_ref": ctx, "opportunity_ref": opportunity["opportunity_ref"],
        "patch_ref": patch, "revision_ref": revision, "initial_ref": initial, "plan_ref": plan,
        "rules": rules, "entry": str(entry), "scientific_execution_started_by_archive": False}
    _save(saved, state)
    _save(a.root / "entry.json", read(entry))
    return state


def complete(root, summary_path, receipt_path, recovery_path, *, outcome="retain", comparison_path=None):
    a = Archive(root)
    state = read(a.root / "prepared.json")
    prepared_records = verify_preparation(a, state)
    if file_identity(state["protocol_path"]) != state["protocol_identity"]:
        raise ValueError("frozen protocol changed")
    comparison_path = Path(comparison_path or Path(summary_path).with_name("summary.json"))
    pins = {name: {"path": str(Path(path).absolute()), **file_identity(path)} for name, path in
        (("summary", summary_path), ("receipt", receipt_path), ("recovery", recovery_path), ("comparison", comparison_path))}
    saved = a.root / "completed.json"
    summary = read(summary_path)
    if summary.get("protocol_sha256") != state["protocol_identity"]["sha256"]:
        raise ValueError("result refers to another protocol")
    for path, expected in summary.get("source_artifacts", {}).items():
        if file_identity(path) != expected:
            raise ValueError("derived metric original identity changed: " + path)
    if not summary.get("source_artifacts"):
        raise ValueError("derived metrics must declare their original dependencies")
    for path in (receipt_path, comparison_path):
        declared = summary["source_artifacts"].get(str(Path(path).absolute()))
        if declared != file_identity(path):
            raise ValueError("receipt or comparison is outside the metrics' pinned dependency closure")
    native = read(receipt_path)
    frozen = {k: prepared_records["freeze_ref"][k] for k in ("sha256", "bytes")}
    if native.get("freeze") != frozen or native.get("status") != "completed":
        raise ValueError("native receipt differs from frozen execution or is incomplete")
    if native.get("artifacts", {}).get(Path(comparison_path).name) != file_identity(comparison_path):
        raise ValueError("comparison not bound by native receipt")
    for name, expected in native.get("artifacts", {}).items():
        if Path(name).name != name or file_identity(Path(receipt_path).parent / name) != expected:
            raise ValueError("native result dependency changed")
    comparison = read(comparison_path)
    controls = verify_derived_metrics(summary, comparison,
        read(prepared_records["selection_ref"]["path"]),
        read(prepared_records["freeze_ref"]["path"]))
    if saved.exists():
        prior = read(saved)
        if prior["input_pins"] != pins or prior["outcome"] != outcome:
            raise ValueError("completion replay differs; publish a new version, not overwrite")
        return prior
    dependency_refs = [a.file(path, "metric_dependency") for path in summary["source_artifacts"]]
    original_summary_ref = a.file(summary_path, "scientific_result")
    summary = {**summary, "control_cases": controls,
        "original_metrics_ref": original_summary_ref, "dependency_refs": dependency_refs}
    summary_ref = a.obj(summary, "scientific_result")
    comparison_ref = a.file(comparison_path, "factorial_scientific_result")
    receipt = a.file(receipt_path, "native_receipt")
    recovery = a.file(recovery_path, "interruption_recovery")
    cost = a.obj({"receipt_ref": receipt, "recovery_ref": recovery,
        "basis": "Actual execution receipts; no second settlement in research archive",
        "solver_cpu_ratio": summary.get("candidate_cpu_ratio"), "model_orchestration_cost": None,
        "scientific_scope": SCOPE}, "accounting")
    use = a.ref(a.journal.record(MethodUse(
        ({"kind": "task", "id": TASK, "scope": str(a.store.path)},
         {"kind": "result", "id": receipt["record_id"], "scope": str(a.store.path)}),
        ({"method_version_ref": state["candidate_ref"]["record_id"], "scope": SCOPE},),
        visible_evidence_refs=(receipt["record_id"], summary_ref["record_id"]), host_conditions=state["conditions"],
        reason="Actual candidate variant in the frozen scientific execution; not mere selection or instruction-consumption telemetry")))
    observed = a.save(node_step(task_id=TASK, question=QUESTION, branch_id="torsion-range-pocket",
        node_id="controlled-scientific-comparison", state="completed", reason="Real scientific outputs after controlled recovery",
        input_refs=[state["protocol_ref"]], output_refs=[summary_ref], method_refs=[state["candidate_ref"]],
        use_refs=[use], receipt_refs=[receipt, recovery], accounting_refs=[cost]))
    assessment = a.save(imp.assess_revision(a.journal, patch_ref=state["patch_ref"], plan_ref=state["plan_ref"],
        observed_step_refs=[observed], source_id=SID))
    selector = lambda name: {"input": "summary", "pointer": "/" + name}
    denominators = {"recovered_failures": "failure_cases", "control_regressions": "control_cases"}
    metrics = [{**r, "measure": {"operation": "value", "left": selector(r["name"]), "right": None},
        "denominator": "Unique frozen paired conditions; repeated chemical systems are not independent samples",
        "denominator_selector": selector(denominators.get(r["name"], "matched_cases")),
        "missing_policy": "insufficient"} for r in state["rules"]]
    contract = build_evaluation_contract(task_id=TASK, scope=SCOPE, conditions=state["conditions"],
        protocol_ref=state["protocol_ref"], initial_conditions_ref=state["initial_ref"],
        inputs=[{"name": "summary", "ref": summary_ref, "kind": "result"}], metrics=metrics,
        comparability=[{"check_id": "protocol", "selector": selector("protocol_sha256"),
            "expected": state["protocol_identity"]["sha256"]},
            {"check_id": "candidate", "selector": selector("candidate_id"), "expected": "snd1-wide-torsion-pocket-v1"}],
        required_checks=[{"check_id": key, "selector": selector(key), "expected": True}
            for key in ("denominator_complete", "original_pins_verified", "required_checks_complete", "baseline_reproduction_pass")],
        subjects=[state["candidate_ref"]], comparators=[state["base_ref"]], plan_ref=state["plan_ref"],
        provenance={"mode": "predeclared", "basis_refs": [state["protocol_ref"], state["plan_ref"]],
            "note": "Rules recorded before new execution of seen cases; no independent transfer; host decides adoption"})
    workspace = a.workspace("evaluation")
    report = evaluate_contract(workspace, contract)
    _save(a.root / ("evaluation-attempt-" + digest(report)[:16] + ".json"), report)
    if outcome == "adopt" and report["status"] != "criteria_passed":
        raise ValueError("cannot adopt a candidate that did not satisfy frozen criteria")
    report_ref = a.ref(record_evaluation_artifact(a.journal, report, a.content,
        source_kind="existing_real_artifact", workspace=workspace))
    comparison = read(comparison_path)
    conclusion = (summary.get("conclusion") or "Frozen seen-panel comparison") + "; " + SCOPE
    counter = a.obj({"source_ref": comparison_ref,
        "success_counts": comparison.get("success_counts"), "paired_effects": comparison.get("paired_effects"),
        "interpretation": "Pocket necessity is not established if both wide cells rescue the same conditions. Crystal-derived anchors remain available in all cells; no blind-prediction claim.",
        "cost": comparison.get("cost"), "limitations": SCOPE})
    evaluation = record_evaluation(a.journal, state["plan_ref"], directory=a.content, source_id=SID,
        source_kind="existing_real_artifact", result_refs=[summary_ref["record_id"], report_ref["record_id"]],
        accounting_refs=[cost["record_id"]], assessment_role="diagnostic", conclusion=conclusion, scope=SCOPE)
    resolution = resolve_method_revision(a.journal, state["revision_ref"], evaluation["evaluation_ref"],
        outcome=outcome, candidate_ref=state["candidate_ref"] if outcome == "adopt" else None,
        task_id=TASK, question=QUESTION, scope=SCOPE, reason=conclusion, directory=a.content,
        source_id=SID, source_kind="existing_real_artifact")
    experience = imp.record_experience(a.journal, patch_ref=state["patch_ref"], outcome=outcome,
        lesson=conclusion, invalid_when="Different ligand series, preparation, starting/reference information, bounds, checks or cost regime",
        capability_id="snd1.controlled_torsion", support_refs=[summary_ref, report_ref, assessment],
        counter_refs=[state["problem_ref"], counter, recovery], evaluation_ref=evaluation["evaluation_ref"],
        resolution_ref=resolution["step_ref"], directory=a.content, source_id=SID, source_kind="existing_real_artifact")
    terminal = a.save(build_opportunity_disposition(a.journal, opportunity_ref=state["opportunity_ref"],
        outcome=outcome, reason=conclusion, reviewer="host scientific review", support_refs=[summary_ref, report_ref],
        counter_refs=[state["problem_ref"], counter, recovery], candidate_ref=state["candidate_ref"],
        evaluation_ref=evaluation["evaluation_ref"], resolution_ref=resolution["step_ref"], source_id=SID,
        reconsider_when=[{"condition_id": "new-evidence", "condition": "New measured counterevidence or changed physical conditions; another read is not new evidence"}]))
    claim = a.save({"schema": "etalon-research-claim/1", "question": QUESTION,
        "statement": f"Frozen comparison: {summary.get('recovered_failures')} of {summary.get('failure_cases')} historical failures rescued, {summary.get('control_regressions')} control regressions, solver CPU ratio {summary.get('candidate_cpu_ratio')}; scoped host decision={outcome}",
        "epistemic_status": "interpretation", "subjects": ["QOY", "QOV"], "conditions": state["conditions"],
        "limitations": [SCOPE, "Wider bounds retain known-crystal anchors", "Pocket-term benefit must be read separately", "Solver cost is not total pipeline cost"],
        "support_refs": [summary_ref, report_ref, comparison_ref], "counter_refs": [counter],
        "method_refs": [state["base_ref"], state["candidate_ref"]], "supersedes": []})
    final_entry = a.checkpoint("completed", steps=[state["plan_ref"], observed, evaluation["step_ref"], resolution["step_ref"]],
        methods=[state["base_ref"], state["candidate_ref"]], counters=[state["problem_ref"], counter, recovery],
        basis=[experience, terminal], state="resolved", claims=[claim], supersedes=[read(state["entry"])["checkpoint_ref"]])
    result = {"input_pins": pins, "outcome": outcome, "report": report, "experience_ref": experience,
        "resolution": resolution, "terminal_ref": terminal, "entry": str(final_entry),
        "observed_step_ref": observed, "evaluation_ref": evaluation["evaluation_ref"], "claim_ref": claim,
        "counter_ref": counter}
    _save(saved, result)
    return result


def publish(root):
    a = Archive(root)
    final = read(a.root / "completed.json")
    destination = a.root / "published-entry.json"
    correction = a.root / "checkpoint-reconciliation.json"
    parent = Path(read(correction)["entry"] if correction.exists() else final["entry"])
    step = {"schema": STEP, "task_id": TASK, "question": QUESTION, "branch_id": "publication",
        "operation": "decision", "reason": "Publish already executed science and terminal RSI; no producer retry",
        "input_refs": [final["experience_ref"], final["terminal_ref"]], "output_refs": [],
        "method_refs": [], "accounting_refs": [], "source_watermarks": [], "supersedes": [],
        "detail": {"choice": "reuse", "alternatives": [],
            "basis_refs": [final["experience_ref"], final["terminal_ref"]],
            "reconsider_when": "New original scientific evidence, not chat restart"}}
    prepared_path = a.root / "publication-prepared.json"
    if prepared_path.exists():
        return read(prepared_path)
    result = prepare_publication(destination, parent_entry=parent, operation_id=TASK + "-final",
        expected_entry_identity=None, steps=[{"name": "publish_terminal", "content": step}])
    _save(prepared_path, result)
    return result


def audit(root):
    a = Archive(root)
    state = read(a.root / "prepared.json")
    final = read(a.root / "completed.json")
    entry = read(a.root / "published-entry.json")
    before = len(a.store.events())
    ctx = resume_research(entry["workspace"], entry["checkpoint_ref"])
    recovered = imp.register_opportunity(a.journal, state["context_ref"], a.content,
        source_id=SID, source_kind="existing_real_artifact")
    experience = imp.retrieve_experience(entry["workspace"], source_ids=[SID],
        capability_id="snd1.controlled_torsion", conditions=state["conditions"])
    terminal = resolve_opportunity_state(a.journal, state["opportunity_ref"], source_id=SID)
    if ctx["issues"] or not terminal["terminal"] or len(a.store.events()) != before:
        raise ValueError("continuation failed or replay mutated the journal")
    if not any(x["ref"] == final["experience_ref"] for x in experience["matches"]):
        raise ValueError("scoped experience not recovered")
    return {"entry": str(a.root / "published-entry.json"), "issues": ctx["issues"],
        "automatic_retry": ctx["automatic_retry"], "opportunity": recovered,
        "terminal": terminal, "experience": experience, "new_journal_events": len(a.store.events()) - before,
        "scientific_execution_by_archive": False, "r4b": "paused"}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=("prepare", "complete", "prepare-publication", "resume-publication", "audit"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--protocol", type=Path)
    p.add_argument("--summary", type=Path)
    p.add_argument("--receipt", type=Path)
    p.add_argument("--recovery", type=Path)
    p.add_argument("--comparison", type=Path)
    p.add_argument("--outcome", choices=("retain", "adopt"), default="retain")
    args = p.parse_args()
    if args.phase == "prepare": result = prepare(args.root, args.protocol)
    elif args.phase == "complete": result = complete(args.root, args.summary, args.receipt, args.recovery, outcome=args.outcome, comparison_path=args.comparison)
    elif args.phase == "prepare-publication": result = publish(args.root)
    elif args.phase == "resume-publication": result = resume_publication(read(args.root / "publication-prepared.json")["intent_path"])
    else: result = audit(args.root)
    print(json.dumps(result, ensure_ascii=True, indent=2))
