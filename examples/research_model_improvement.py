"""Archive a real, already executed pose-endpoint revision as a scoped RSI chain.

No docking, MD or verifier is invoked. The caller supplies the actual frozen
reassessment receipt and independent comparison audit. Baseline/candidate here
are same-environment reanalyses, never invented historical executions. Adoption
concerns endpoint availability and preserved eligibility boundaries, not improved
poses, affinity, independent transfer or lower total research cost.

Run with --root NEW_BATCH_DIR --receipt ACTUAL_RECEIPT --audit ACTUAL_AUDIT.
--stop-after evaluation publishes an interruption checkpoint; replay without that
flag continues deterministic records. Replaying a completed request verifies its
originals and terminal state without new proposals, evaluations or computations.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical, digest
from etalon.research import Artifact, MethodVersion, MethodUse, ResearchJournal, TaskSplit
from etalon.research import improvement as imp, research_model as rm
from etalon.research.components import record_json
from etalon.research.composition import node_step, record_evaluation
from etalon.research.continuation import STEP, CHECKPOINT, record_content
from etalon.research.evaluation_contract import build_evaluation_contract, evaluate_contract
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.method_revision import journal_ref, build_method_revision, record_method_revision, resolve_method_revision
from etalon.research.opportunity_state import build_opportunity_disposition, resolve_opportunity_state

SID = "endpoint-model-rsi"
TASK = "pose-endpoint-representation-revision"
SCOPE = "Seen 30 archived pose slots: endpoint availability and eligibility boundaries, not pose or affinity benefit"
QUESTION = "Can independent endpoint reporting recover physical information while leaving unknown RMSD/joint unqualified?"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def immutable_json(path, value):
    path = Path(path)
    raw = (canonical(value) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != raw:
            raise EvidenceError("immutable batch content differs: " + str(path))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(raw)


def verify_pin(path, pin):
    if file_identity(Path(path)) != {k: pin[k] for k in ("sha256", "bytes")}:
        raise EvidenceError("original identity changed: " + str(path))


def verify_inputs(receipt_path, audit_path):
    receipt_path, audit_path = Path(receipt_path).absolute(), Path(audit_path).absolute()
    receipt, audit = read_json(receipt_path), read_json(audit_path)
    if receipt.get("schema") != "etalon-pose-endpoint-reassessment/1" or audit.get("schema") != "etalon-pose-endpoint-comparison-audit/1":
        raise EvidenceError("actual reassessment receipt and comparison audit are required")
    verify_pin(receipt_path, audit["receipt"])
    request = read_json(receipt_path.parent / "request.json")
    if digest(request) != receipt["request_digest"] or request["modules"] != receipt["modules"]:
        raise EvidenceError("receipt differs from the frozen reassessment request")
    for path, pin in receipt["originals"].items():
        verify_pin(path, pin)
    for rel, pin in receipt["outputs"].items():
        verify_pin(receipt_path.parent / rel, pin)
    for pin in receipt["modules"].values():
        verify_pin(pin["path"], pin)
    for case in audit["cases"].values():
        for key in ("old_report", "new_report", "historical_raw", "workspace"):
            verify_pin(case[key]["path"], case[key])
    verify_pin(audit["check_contract"]["path"], audit["check_contract"])
    if not receipt["originals_unchanged"] or not audit["all_originals_and_outputs_verified"]:
        raise EvidenceError("unchanged original coverage not established")
    if receipt["cost"]["new_docking_calls"] != 0 or receipt["cost"]["new_md_calls"] != 0:
        raise EvidenceError("this example is limited to existing-pose reassessment")
    if set(receipt["cases"]) != set(audit["cases"]):
        raise EvidenceError("receipt and audit panels differ")
    return receipt, audit


def summarize(receipt, audit):
    """Recompute narrow engineering comparisons from all recorded pose slots."""
    required = set(read_json(audit["check_contract"]["path"])["required_checks"])
    old_count = new_count = total = unknown_count = known_count = 0
    unchanged, unknown_safe, same_inputs, checks_covered = True, True, True, True
    details = {}
    for name, case in sorted(audit["cases"].items()):
        old, new = read_json(case["old_report"]["path"]), read_json(case["new_report"]["path"])
        count = receipt["cases"][name]["slot_count"]
        if len(old["rows"]) != count or len(new["rows"]) != count or not new["slot_coverage_complete"]:
            raise EvidenceError("missing slots cannot be dropped from endpoint comparison")
        same_inputs &= old["inputs"] == new["inputs"] == case["inputs"]
        same_inputs &= old["threshold_A"] == new["threshold_A"] == 2.0
        counts = {"baseline_physical": 0, "candidate_physical": 0, "unknown_reference": 0}
        for index, (left, right) in enumerate(zip(old["rows"], new["rows"])):
            if left["pose_index"] != index or right["pose_index"] != index:
                raise EvidenceError("pose slot identity mismatch")
            old_ok = required <= set(left["checks"]) and all(type(left["checks"][k]) is bool for k in required)
            new_ok = right["physical"]["status"] == "observed"
            if new_ok:
                checks_covered &= required <= set(right["checks"]) and all(type(right["checks"][k]) is bool for k in required)
            old_count += old_ok; new_count += new_ok; total += 1
            counts["baseline_physical"] += old_ok; counts["candidate_physical"] += new_ok
            qualified = right["qualification"]["eligibility"]["status"] == "identity_comparable_under_declared_policy"
            if qualified:
                known_count += 1
                unchanged &= (left["checks"] == right["checks"] and left["physical_valid"] == right["physical"]["value"]
                    and left["rmsd_A"] == right["rmsd"]["value"] and left["success"] == right["joint"]["value"])
            else:
                unknown_count += 1; counts["unknown_reference"] += 1
                unknown_safe &= all(right[k]["status"] == "unknown" and right[k]["value"] is None for k in ("rmsd", "joint"))
        if counts["baseline_physical"] != case["legacy_complete_physical_endpoint_count"] or counts["candidate_physical"] != case["new_complete_physical_endpoint_count"]:
            raise EvidenceError("independent comparison counts differ from report bytes")
        details[name] = counts
    return dict(schema="etalon-endpoint-rsi-comparison/1", task_id=TASK, scope=SCOPE,
        protocol="endpoint-availability-retrospective/1", complete=True, slots=total, cases=details,
        baseline_physical_available=old_count, candidate_physical_available=new_count, availability_gain=new_count-old_count,
        known_reference_slots=known_count, unresolved_reference_slots=unknown_count,
        inputs_and_threshold_unchanged=bool(same_inputs), required_physical_checks_covered=bool(checks_covered),
        qualified_results_unchanged=bool(unchanged), unknown_not_promoted=bool(unknown_safe),
        original_files_unchanged=receipt["originals_unchanged"], historical_raw_information_already_existed=True,
        independent_validation="not_established", pose_affinity_benefit="not_established", cost_saving="not_claimed")


def run(root, receipt_path, audit_path, *, stop_after=None):
    root = Path(root).absolute()
    receipt_path, audit_path = Path(receipt_path).absolute(), Path(audit_path).absolute()
    receipt, audit = verify_inputs(receipt_path, audit_path)
    request = dict(schema="etalon-endpoint-rsi-publication-request/1", receipt={"path": str(receipt_path), **file_identity(receipt_path)},
        audit={"path": str(audit_path), **file_identity(audit_path)}, driver={"path": str(Path(__file__).absolute()), **file_identity(Path(__file__))})
    root.mkdir(parents=True, exist_ok=True)
    immutable_json(root / "request.json", request)
    store = CampaignStore(root / "research.sqlite")
    store.configure(CampaignSpec("engineering", 0, "wall_second", TASK), [
        Endpoint("engineering", "diagnostic", "unmeasured", "count", "endpoint-rsi/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    report_path = root / "report.json"
    if report_path.exists():
        report = read_json(report_path)
        if report["request_digest"] != digest(request):
            raise EvidenceError("completed batch has a different exact request")
        before = len(store.events())
        state = resolve_opportunity_state(journal, report["opportunity_ref"], source_id=SID,
                                           external_workspace=root / "workspace.json")
        if not state["terminal"] or state["head_ref"] != report["terminal_ref"]:
            raise EvidenceError("completed batch lost its exact terminal")
        rm.read_model_revision(root / "workspace.json", report["model_review_ref"])
        if len(store.events()) != before:
            raise EvidenceError("read-only replay unexpectedly wrote journal events")
        return {**report, "replay": "reused_verified", "new_evaluations": 0, "new_proposals": 0,
                "new_verifier_calls": 0, "new_scientific_calls": 0}
    ref = lambda rid: journal_ref(journal, rid, source_id=SID)
    def artifact(value, **bindings):
        return ref(record_json(journal, value, root / "memory", source_kind="new_execution", bindings=bindings))
    def existing(path, kind):
        path = Path(path).absolute()
        return ref(journal.record(Artifact(str(path), **file_identity(path), source_kind="existing_real_artifact",
            role="diagnostic", bindings={"kind": kind})))
    def save(value):
        return ref(record_content(journal, value, root / "memory", source_id=SID, source_kind="new_execution",
                                  external_workspace=publish()))
    sources = []
    for case in audit["cases"].values():
        sources.extend(read_json(case["workspace"]["path"])["sources"])
    authority = root / "AUTHORITY.txt"
    content = "Local existing-result reassessment archival only. R4b remains paused. No docking or MD authorization.\n"
    if authority.exists() and authority.read_text("utf-8") != content:
        raise EvidenceError("batch authority changed")
    if not authority.exists():
        authority.write_text(content, encoding="utf-8")
    def publish():
        path = root / "workspace.json"
        value = dict(schema="etalon-evidence-workspace/1", workspace_id=TASK, sources=[*sources,
            dict(source_id=SID, kind="journal", location=str(store.path),
                 identity={"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]})],
            status_refs=[str(authority)], location_map=[])
        path.write_text(canonical(value)+"\n", encoding="utf-8")
        return path
    authority_ref = existing(authority, "batch_scope_and_paused_R4b")
    receipt_ref, audit_ref = existing(receipt_path, "execution_receipt"), existing(audit_path, "independent_comparison_audit")
    protocol_ref = existing(audit["check_contract"]["path"], "frozen_check_contract")
    source_pin = audit["source_functions"]["verify_poses"]
    source_path = Path(source_pin["module"])
    verify_pin(source_path, source_pin)
    snapshot = root / "source" / (source_pin["sha256"] + ".py")
    snapshot.parent.mkdir(exist_ok=True)
    if snapshot.exists():
        verify_pin(snapshot, source_pin)
    else:
        with snapshot.open("xb") as stream:
            stream.write(source_path.read_bytes())
    source_ref = existing(snapshot, "actual_executed_source_snapshot")
    contents = {}
    methods = {}
    for kind, function in (("baseline", "verify_poses"), ("candidate", "verify_pose_endpoints")):
        contents[kind] = artifact(dict(entrypoint="etalon.research.pose_verifier."+function,
            function_source_sha256=audit["source_functions"][function]["function_source_sha256"], source_ref=source_ref,
            runtime=receipt["runtime"], protocol_ref=protocol_ref,
            provenance="Actual same-environment reassessment API; not a new invocation and not historical runtime equivalence"))
        methods[kind] = ref(journal.record(MethodVersion("pose-endpoint-"+kind, ("domain_skill", "usage_method"),
            {"artifact_ref": contents[kind]["record_id"]}, parents=(methods["baseline"]["record_id"],) if kind == "candidate" else (),
            entrypoint="etalon.research.pose_verifier."+function, verification_basis="host_reported")))
    conditions = dict(task_family="pose_endpoint_qualification", panel=digest(sorted(audit["cases"])),
                      protocol="endpoint-availability-retrospective/1", runtime=receipt["runtime"]["rdkit"])
    case_inputs, reports = {}, {}
    for name, case in sorted(audit["cases"].items()):
        paths = {key: next(Path(p) for p, identity in receipt["originals"].items() if identity == case["inputs"][key])
                 for key in ("predicted", "reference", "receptor")}
        case_inputs[name] = {k: existing(p, "frozen_"+k) for k, p in paths.items()}
        reports[name] = {kind: existing(case[field]["path"], "actual_"+kind+"_report")
                         for kind, field in (("baseline", "old_report"), ("candidate", "new_report"))}
    problem = artifact(dict(problem="A pooled reference identity failure suppresses independently evaluable physical endpoints",
        evidence_refs=[reports[n]["baseline"] for n in sorted(reports)],
        boundary="Legacy false combines missing qualification and quality; do not infer scientific failure"))
    context = imp.build_revision_context(publish(), task_id=TASK, scope=SCOPE, conditions=conditions,
        target_ref=methods["baseline"], problem_refs=[problem], evidence_refs=[case_inputs[n]["predicted"] for n in sorted(case_inputs)])
    context_ref = save(context)
    opportunity = imp.register_opportunity(journal, context_ref, root / "memory", source_id=SID,
        source_kind="new_execution", external_workspace=publish())
    patch = save(imp.build_method_patch(journal, context_ref=context_ref, opportunity_ref=opportunity["opportunity_ref"],
        base_method_ref=methods["baseline"], candidate_method_ref=methods["candidate"], source_id=SID,
        expected_change="Separate physical, reference RMSD and joint eligibility without promoting unknown or weakening physical checks")["patch"])
    generator_content = artifact(dict(procedure="Host-authored endpoint decomposition, assessed against already recorded real outputs",
        evidence_refs=[problem, protocol_ref], scope=SCOPE, cost="Model/development cost unmeasured; no net cost-benefit claim"))
    generator = ref(journal.record(MethodVersion("endpoint-representation-revision", ("improvement_method",),
        {"artifact_ref": generator_content["record_id"]})))
    generator_use = ref(journal.record(MethodUse(({"kind": "task", "id": TASK, "scope": str(store.path)},),
        ({"method_version_ref": generator["record_id"], "scope": SCOPE},), reason="Actual host-authored revision archival")))
    revision = ref(record_method_revision(journal, build_method_revision(problem_refs=[problem, context_ref], target_kind="S",
        base_method_ref=methods["baseline"], producer_method_ref=generator, producer_use_ref=generator_use,
        candidate_version_refs=[methods["candidate"]], change_summary="Decompose pose evaluation into independently qualified endpoints",
        mechanism_hypothesis="Reference comparability need not suppress reference-independent physical checks", scope=SCOPE),
        root / "memory", source_id=SID, source_kind="new_execution"))
    initial = artifact(dict(conditions=conditions, input_refs=[r for item in case_inputs.values() for r in item.values()],
                           role="seen diagnostic cases; no independent test"))
    split = ref(journal.record(TaskSplit(tuple(sorted(case_inputs)), ("not_used_validation",), ("not_used_transfer",),
        {**{n: "seen" for n in case_inputs}, "not_used_validation": "unused", "not_used_transfer": "unused"},
        "All cases previously seen; this comparison is retrospective engineering evidence")))
    comparison = artifact(summarize(receipt, audit), evidence_refs=[receipt_ref, audit_ref], kind="recorded_report_comparison")
    cost = artifact(dict(kind="resource_accounting", scope="Actual previously executed paired reassessment; not charged twice",
        **receipt["cost"], baseline_wall_seconds=sum(r["legacy_seconds"] for r in receipt["cases"].values()),
        candidate_wall_seconds=sum(r["endpoint_seconds"] for r in receipt["cases"].values()),
        development_model_tokens=None, archival_overhead_seconds=None, receipt_ref=receipt_ref), kind="accounting")
    nodes, uses = {}, []
    for name in sorted(case_inputs):
        nodes[name] = {}
        for kind in ("baseline", "candidate"):
            use = ref(journal.record(MethodUse(({"kind": "task", "id": TASK, "scope": str(store.path)},),
                ({"method_version_ref": methods[kind]["record_id"], "scope": SCOPE},), host_conditions=conditions,
                reason="Attribution of the actual frozen reassessment receipt; no repeated execution")))
            uses.append(use)
            nodes[name][kind] = save(node_step(task_id=TASK, question=QUESTION, branch_id="endpoint-reassessment", node_id=name+"-"+kind,
                state="completed", reason="Existing real reassessment result and receipt, archived after execution",
                input_refs=list(case_inputs[name].values()), output_refs=[reports[name][kind]], method_refs=[methods[kind]],
                use_refs=[use], receipt_refs=[receipt_ref], accounting_refs=[cost]))
    select = lambda pointer: {"input": "comparison", "pointer": pointer}
    contract = build_evaluation_contract(task_id=TASK, scope=SCOPE, conditions=conditions, protocol_ref=protocol_ref,
        initial_conditions_ref=initial, subjects=[methods["candidate"]], comparators=[methods["baseline"]],
        inputs=[dict(name="comparison", kind="result", ref=comparison)],
        comparability=[dict(check_id="protocol", selector=select("/protocol"), expected=conditions["protocol"]),
                       dict(check_id="same_inputs", selector=select("/inputs_and_threshold_unchanged"), expected=True)],
        required_checks=[dict(check_id=k, selector=select("/"+k), expected=True) for k in (
            "complete", "required_physical_checks_covered", "qualified_results_unchanged", "unknown_not_promoted", "original_files_unchanged")],
        metrics=[dict(name="additional_physical_availability", family="retention", measure={"operation": "value", "left": select("/availability_gain"), "right": None},
            operator="gt", threshold=0, unit="pose_slot", denominator="All 30 archived slots including unknown reference endpoints",
            denominator_selector=select("/slots"), missing_policy="insufficient")],
        provenance=dict(mode="retrospective", note="Fixed replay rules declared after inspecting outcomes, not prospective validation", basis_refs=[protocol_ref]))
    contract_ref = artifact(contract, schema=contract["schema"])
    evaluated = evaluate_contract(publish(), contract_ref=contract_ref)
    evaluated_ref = artifact(evaluated, schema=evaluated["schema"])
    plan = save(dict(schema=STEP, task_id=TASK, question=QUESTION, branch_id="endpoint-reassessment", operation="evaluation_plan",
        reason="Versioned retrospective comparison; not a claim of pre-execution freezing", input_refs=[patch, contract_ref], output_refs=[],
        method_refs=list(methods.values()), accounting_refs=[], source_watermarks=[], supersedes=[], detail=dict(target="method",
        subjects=[methods["candidate"]], comparators=[methods["baseline"]], protocol_ref=protocol_ref, task_split_ref=split,
        initial_conditions_ref=initial, use_refs=[], trigger_refs=[problem], metrics=[dict(name="additional_physical_availability",
        family="retention", unit="pose_slot", denominator="All 30 archived slots", missing_policy="insufficient")])) )
    evaluation = record_evaluation(journal, plan, directory=root / "memory", source_id=SID, source_kind="new_execution",
        result_refs=[comparison["record_id"], evaluated_ref["record_id"], *[reports[n][k]["record_id"] for n in sorted(reports) for k in ("baseline", "candidate")]],
        accounting_refs=[cost["record_id"]], conclusion="Scoped endpoint information recovery; unknown reference and scientific benefit remain unresolved",
        scope=SCOPE, use_refs=imp._unique(uses), node_refs=[r for item in nodes.values() for r in item.values()])
    def checkpoint(state, steps, basis, *, supersedes=()):
        return save(dict(schema=CHECKPOINT, task_id=TASK, question=QUESTION, step_refs=steps, claim_refs=[],
            counter_refs=[problem], method_refs=[*methods.values(), generator], branches=[
                dict(branch_id="endpoint-reassessment", state=state, basis_refs=basis, next_read_refs=basis,
                     reconsider_when="New exact evidence of check regression, new input regime or newly resolved reference identity"),
                dict(branch_id="R4b", state="stopped", basis_refs=[authority_ref], next_read_refs=[authority_ref], reconsider_when="User-paused; explicit user resumption required")],
            pending_handles=[], source_watermarks=[], supersedes=list(supersedes),
            status_guards=[dict(location=str(authority), **file_identity(authority), state="active")]))
    pending = checkpoint("waiting", [plan, evaluation["step_ref"]], [patch, comparison, evaluated_ref])
    if stop_after == "evaluation":
        result = dict(status="interrupted_after_evaluation", workspace=str(publish()), checkpoint_ref=pending,
                      opportunity_ref=opportunity["opportunity_ref"], evaluation=evaluation, new_scientific_calls=0)
        immutable_json(root / "interruption.json", result)
        return result

    all_cases = [case_inputs[n]["predicted"] for n in sorted(case_inputs)]
    unknown_cases = [case_inputs[n]["predicted"] for n in sorted(case_inputs) if audit["cases"][n]["reference_unknown_count"]]
    issues = [dict(issue_id="reference-qualification-"+n, statement="Reference stereochemistry remains unresolved; joint and RMSD are unknown",
        witness_refs=[case_inputs[n]["predicted"]], status="unresolved", resolution_refs=[])
        for n in sorted(case_inputs) if audit["cases"][n]["reference_unknown_count"]]
    def rule(rid, checker, statement, judgment, mapping, dependencies=()):
        return dict(rule_id=rid, statement=statement, judgment_kind=judgment, status="active",
            applicability=dict(conditions=conditions, unknowns=[]), checker_ref=checker, predictor_ref=None,
            verdict_map=mapping, dependencies=list(dependencies), support_refs=all_cases, counter_refs=unknown_cases,
            issue_ids=[i["issue_id"] for i in issues])
    old_rule = rule("combined-pose-result", methods["baseline"], "Legacy reported combined success conflates quality and reference qualification",
        "method_quality", [dict(value=True, verdict="TRUE"), dict(value=False, verdict="FALSE")])
    model_args = dict(model_id="pose-endpoint-decomposition", task_id=TASK, scope=SCOPE, conditions=conditions,
                      issues=issues, support_refs=all_cases, counter_refs=unknown_cases)
    before_model = artifact(rm.build_research_model(publish(), **model_args, rules=[old_rule], method_refs=[methods["baseline"]]))
    mapping = [dict(value="observed", verdict="TRUE"), dict(value="unknown", verdict="UNKNOWN")]
    new_rules = [rule("physical-availability", methods["candidate"], "Physical endpoint availability is separate from reference comparability", "integrity", mapping),
        rule("rmsd-availability", methods["candidate"], "RMSD availability requires qualified reference identity", "integrity", mapping),
        rule("joint-availability", methods["candidate"], "Joint endpoint requires physical and qualified RMSD availability", "integrity", mapping,
             dependencies=("physical-availability", "rmsd-availability"))]
    after_model = artifact(rm.build_research_model(publish(), **model_args, rules=new_rules, method_refs=[methods["candidate"]], supersedes=[before_model]))
    assessments = []
    for name in sorted(case_inputs):
        common = dict(case_ref=case_inputs[name]["predicted"])
        left = rm.recorded_rule_assessment(publish(), **common, model_ref=before_model, rule_id="combined-pose-result",
            observation_ref=reports[name]["baseline"], pointer="/top1_success", execution_ref=nodes[name]["baseline"])
        right = [rm.recorded_rule_assessment(publish(), **common, model_ref=after_model, rule_id=rid,
            observation_ref=reports[name]["candidate"], pointer="/rows/0/"+endpoint+"/status", execution_ref=nodes[name]["candidate"])
            for rid, endpoint in (("physical-availability", "physical"), ("rmsd-availability", "rmsd"), ("joint-availability", "joint"))]
        assessments.append(dict(case_id=name+"-top1-diagnostic", **common,
            role="counterexample" if audit["cases"][name]["reference_unknown_count"] else "positive", before=[left], after=right))
    model_review = rm.review_model_revision(publish(), before_ref=before_model, after_ref=after_model, revision_kind="knowledge_revision",
        mappings=[dict(before_rule_ids=["combined-pose-result"], after_rule_ids=[r["rule_id"] for r in new_rules], operation="split",
            reason="Separate quality/availability and reference eligibility; historical false remains an unresolved witness")], assessments=assessments)
    model_review_ref = artifact(model_review)
    outcome = "adopt" if evaluated["status"] == "criteria_passed" else "retain"
    resolution = resolve_method_revision(journal, revision, evaluation["evaluation_ref"], outcome=outcome, candidate_ref=methods["candidate"],
        task_id=TASK, question=QUESTION, scope=SCOPE,
        reason="Use separated endpoint reporting within the checked scope; preserve unresolved reference and all scientific limits",
        directory=root / "memory", source_id=SID, source_kind="new_execution")
    experience = imp.record_experience(journal, patch_ref=patch, outcome=outcome,
        lesson="Separate independently evaluable physical endpoints from reference-qualified RMSD/joint. This restores availability on this seen panel; historical raw physical information already existed.",
        invalid_when="Different checks/parser/chemical state or reference regime; unknowns must not become successes; no affinity/pose improvement or transfer established",
        capability_id="etalon.pose_endpoint_qualification", support_refs=[comparison, evaluated_ref, model_review_ref], counter_refs=[problem, *unknown_cases],
        evaluation_ref=evaluation["evaluation_ref"], resolution_ref=resolution["step_ref"], directory=root / "memory", source_id=SID,
        source_kind="new_execution")
    state = resolve_opportunity_state(journal, opportunity["opportunity_ref"], source_id=SID, external_workspace=publish())
    if state["terminal"]:
        terminal = state["head_ref"]
    else:
        terminal = save(build_opportunity_disposition(journal, opportunity_ref=opportunity["opportunity_ref"], outcome=outcome,
            reason="Scoped engineering availability result only; same-evidence model revision did not reopen any historical opportunity",
            reviewer="current host", support_refs=[experience, evaluated_ref], counter_refs=[problem, *unknown_cases],
            reconsider_when=[dict(condition_id="new-qualification-or-regression", condition="New original evidence resolves reference qualification or shows a check regression under a changed regime")],
            source_id=SID, candidate_ref=methods["candidate"], evaluation_ref=evaluation["evaluation_ref"], resolution_ref=resolution["step_ref"],
            external_workspace=publish()))
    final = checkpoint("resolved", [plan, *[r for item in nodes.values() for r in item.values()], evaluation["step_ref"], resolution["step_ref"]],
                       [experience, terminal, model_review_ref], supersedes=[pending])
    duplicate = imp.register_opportunity(journal, context_ref, root / "memory", source_id=SID,
                                         source_kind="new_execution", external_workspace=publish())
    if duplicate["state"] != "terminal":
        raise EvidenceError("repeated exact problem is not terminal")
    result = dict(schema="etalon-endpoint-model-rsi-result/1", request_digest=digest(request), status="complete", outcome=outcome,
        workspace=str(publish()), checkpoint_ref=final, interruption_checkpoint_ref=pending,
        opportunity_ref=opportunity["opportunity_ref"], context_ref=context_ref, patch_ref=patch, revision_ref=revision,
        evaluation=evaluation, evaluation_report_ref=evaluated_ref, resolution_ref=resolution["step_ref"], experience_ref=experience,
        terminal_ref=terminal, model_before_ref=before_model, model_after_ref=after_model, model_review_ref=model_review_ref,
        comparison_ref=comparison, cost_ref=cost, receipt_ref=receipt_ref, audit_ref=audit_ref, methods=methods,
        normal_node_refs=[r for item in nodes.values() for r in item.values()], conditions=conditions,
        exact_problem_replay_state=duplicate["state"], source_id=SID, scientific_calls=0, archival_verifier_calls=0,
        new_physical_observations="not_claimed; historical raw existed", benefit_scope=audit["benefit_scope"],
        independence="seen retrospective panel; no prospective RSI or scientific transfer claim",
        model_review_scope="Three top1 diagnostic witnesses; numerical endpoint evaluation covers all 30 slots", r4b="paused")
    immutable_json(root / "entry.json", dict(workspace=str(publish()), checkpoint_ref=final))
    immutable_json(report_path, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "receipt", "audit"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--stop-after", choices=["evaluation"])
    args = parser.parse_args()
    result = run(args.root, args.receipt, args.audit, stop_after=args.stop_after)
    print(json.dumps({k: result.get(k) for k in ("status", "outcome", "workspace", "checkpoint_ref", "replay", "new_scientific_calls")}, ensure_ascii=False))
