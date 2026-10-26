"""Explore a local improvement-procedure revision using existing QOV feedback.

This is explicitly retrospective learning from completed MD, not a prospective
MD prediction or proof of third-layer benefit. It joins real feedback, dependent
rules, a changed improvement skill and conditional experience in existing APIs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical
from etalon.research import Artifact, MethodVersion, MethodUse, ResearchJournal
from etalon.research import research_decision as rd, research_model as rm, improvement as imp
from etalon.research.action_learning import gather_action_basis, compare_action_feedback, gather_rule_revision_context
from etalon.research.components import record_json
from etalon.research.composition import node_step
from etalon.research.continuation import record_content
from etalon.research.evidence_refs import file_identity
from etalon.research.method_revision import journal_ref, build_method_revision, record_method_revision
from etalon.research.publication import _save

TASK = "snd1-mechanism-improvement"
SCOPE = "QOV seen three-pair short relaxation; task-local improvement procedure trial"
SID = "mechanism-rsi"
CONDITIONS = {"target": "SND1", "molecule": "QOV", "endpoint": "conditioned-local-anchor-retention"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def skill_bundle(directory, *, old_entry=None):
    directory = Path(directory)
    files = {p.relative_to(directory).as_posix(): p.read_text(encoding="utf-8")
             for p in directory.rglob("*") if p.is_file() and p.suffix in {".md", ".yaml", ".py"}}
    if old_entry:
        files["SKILL.md"] = Path(old_entry).read_text(encoding="utf-8")
        files.pop("references/mechanism-revision.md", None)
    return {"files": files, "origin": str(directory.absolute()), "snapshot_kind": "skill_text_and_code"}


def run(root, primary_path, old_skill, skill_directory, creator_directory):
    root = Path(root).absolute()
    root.mkdir(parents=True, exist_ok=True)
    primary_path = Path(primary_path).absolute()
    request = {"primary": {"path": str(primary_path), **file_identity(primary_path)},
        "base": skill_bundle(skill_directory, old_entry=old_skill),
        "candidate": skill_bundle(skill_directory), "producer": skill_bundle(creator_directory),
        "source": file_identity(__file__), "adapters": {name: file_identity(Path(__file__).parents[1] /
            "src/etalon/research" / (name + ".py")) for name in ("action_learning", "research_model", "research_decision", "improvement")}}
    if (root / "DONE.json").exists():
        if read(root / "request.json") != request:
            raise ValueError("revision inputs changed; use a new explicit trial")
        saved = read(root / "DONE.json")
        verified = imp.read_material(saved["workspace"], saved["result_ref"])["content"]
        if verified != {k: v for k, v in saved.items() if k != "result_ref"}:
            raise ValueError("saved summary differs from its original")
        return saved
    if (root / "request.json").exists() and read(root / "request.json") != request:
        raise ValueError("pending revision inputs changed")
    _save(root / "request.json", request, replace=(root / "request.json").exists())
    started = time.perf_counter()
    store = CampaignStore(root / "research.sqlite")
    store.configure(CampaignSpec("learning", 0, "wall_second", TASK), [
        Endpoint("learning", "diagnostic", "unmeasured", "count", "mechanism/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    ref = lambda rid: journal_ref(journal, rid, source_id=SID)
    artifact = lambda value: ref(record_json(journal, value, root / "content", source_kind="new_execution"))
    original = ref(journal.record(Artifact(str(primary_path), **file_identity(primary_path),
        source_kind="existing_real_artifact", role="diagnostic", bindings={"kind": "completed_md_summary"})))
    def workspace():
        path = root / "workspace.json"
        _save(path, {"schema": "etalon-evidence-workspace/1", "workspace_id": TASK,
            "sources": [{"source_id": SID, "kind": "journal", "location": str(store.path),
                "identity": {"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]}}],
            "status_refs": [], "location_map": []}, replace=True)
        return path
    def save(body):
        return ref(record_content(journal, body, root / "content", source_id=SID,
            source_kind="new_execution", external_workspace=workspace()))
    base_content, candidate_content, creator_content = (artifact(request[k]) for k in ("base", "candidate", "producer"))
    def method(name, content, parents=()):
        return ref(journal.record(MethodVersion(name, ("improvement_method",),
            {"artifact_ref": content["record_id"]}, parents=parents,
            change_summary="Task-local procedure, not scientific adoption")))
    base = method("etalon-skill-improvement-before-mechanism-revision", base_content)
    candidate = method("etalon-skill-improvement-mechanism-revision", candidate_content, (base["record_id"],))
    creator = method("rsi-skill-creator-used-by-lead", creator_content)
    window = artifact({"timing": "retrospective", "prior_results_already_seen": True,
        "interpretation": "Re-examine the rejected geometry-to-retention inference; no historical prediction fabricated"})
    expectation = artifact(dict(observable="candidate retained-anchor superiority", expected_statement=
        "The static geometric intervention improves retained-anchor count over the exact inputs",
        checks=[dict(pointer="/retained_superiority", expected=True)],
        next_if_match="Investigate whether benefit persists with comparable preparation and independent seeds",
        next_if_different="Locate the first lost contact and competing preparation/strain/environment explanations",
        next_if_unknown="Read the missing normal results without restarting completed work"))
    inputs = [original, expectation, window]
    planned = save(node_step(task_id=TASK, question="What assumption should the next improvement test?",
        branch_id="rule-revision", node_id="reanalyse-completed-md", state="planned",
        reason="Reinterpret existing results with known timing", input_refs=inputs, method_refs=[candidate]))
    decision = artifact(rd.build_research_decision(workspace(), task_id=TASK, scope=SCOPE,
        branch_id="rule-revision", question="Does static geometric success explain retained contact?", problem_refs=[original],
        structure=dict(task_family="mechanism_learning", objective_kind="local_retention", subject="QOV",
            intervention="geometric proposal", readout="existing paired MD", constraints=["seen results", "no new MD"], unknowns=[]),
        attribution=dict(actor="ETALON lead", basis="host_judgment", model_ref=None, skill_refs=[candidate], verifier_refs=[]),
        options=[dict(option_id="geometry", kind="hypothesis", statement="Static contact construction predicts retention", claim_refs=[]),
                 dict(option_id="other", kind="unknown", statement="Preparation, environment or strain matters", claim_refs=[])],
        predictions=[dict(prediction_id="retention", action_id="read", option_ids=["geometry"],
            observable="candidate retained-anchor superiority",
            expected="The static geometric intervention improves retained-anchor count over the exact inputs",
            distinguishes="Separates geometric objective from the observed downstream endpoint", status="testable",
            basis_refs=[expectation, original], unknown_reason=None)],
        actions=[dict(action_id="read", description="Reanalyse completed paired outcomes", disposition="selected",
            reason="Existing observations answer the current inference", execution_ref=planned, prediction_ids=["retention"])],
        method_refs=[candidate], counter_refs=[original],
        scientific_inputs=[dict(ref=window, use="conditioning", reason="Known retrospective observation window")]))
    basis = artifact(gather_action_basis(workspace(), decision_ref=decision, action_id="read", conditions=CONDITIONS,
        expectations=[dict(prediction_id="retention", ref=expectation, pointer="")]))
    rows = [r for r in read(primary_path)["rows"] if r["phase"] == "primary"]
    counts = {arm: sum(bool(r["retained_local_anchor"]) for r in rows if r["arm"] == arm)
              for arm in ("candidate", "input")}
    denominators = {arm: sum(r["arm"] == arm for r in rows) for arm in counts}
    if not denominators["candidate"] or denominators["candidate"] != denominators["input"]:
        raise ValueError("paired completed primary outcomes required")
    observation = artifact({"retained_counts": counts, "denominators": denominators,
        "retained_superiority": counts["candidate"] > counts["input"], "new_md_calls": 0,
        "original_ref": original, "scope": SCOPE})
    node = save(node_step(task_id=TASK, question="What assumption should the next improvement test?",
        branch_id="rule-revision", node_id="reanalyse-completed-md", state="completed",
        reason="Actual local reanalysis of completed primary outcomes", input_refs=inputs,
        output_refs=[observation], receipt_refs=[observation], method_refs=[candidate], supersedes=[planned]))
    feedback = artifact(compare_action_feedback(workspace(), basis_ref=basis, execution_ref=node, observation_ref=observation))
    def rule(rid, statement, dependencies=()):
        return dict(rule_id=rid, statement=statement, judgment_kind="diagnosis_prediction", status="active",
            applicability={"conditions": CONDITIONS, "unknowns": []}, checker_ref=None, predictor_ref=None,
            verdict_map=[], dependencies=list(dependencies), support_refs=[], counter_refs=[original], issue_ids=[])
    model = artifact(rm.build_research_model(workspace(), model_id="qov-local-premises", task_id=TASK, scope=SCOPE,
        conditions=CONDITIONS, rules=[rule("environment", "Hypothesis: inserted coordinates have adequately adapted to the local environment"),
            rule("geometry_retention", "Hypothesis under re-examination: single-contact geometric improvement predicts retention", ["environment"]),
            rule("next_restraint", "Prior strategy: challenge the largest paired advantage at weaker protein restraint", ["geometry_retention"]),
            rule("route_feasibility", "Route chemistry remains a separate unresolved question")],
        prediction_bindings=[dict(decision_ref=decision, prediction_id="retention", rule_ids=["geometry_retention"])],
        method_refs=[candidate], counter_refs=[original]))
    context = gather_rule_revision_context(workspace(), feedback_ref=feedback, model_ref=model)
    context_ref = artifact(context)
    _save(root / "RULE_CONTEXT.json", context)
    revised_rules = rm.read_research_model(workspace(), model)["rules"]
    for item in revised_rules:
        if item["rule_id"] == "geometry_retention":
            item["statement"] = ("In this seen QOV panel, static single-contact improvement did not improve retention. "
                "Treat contact construction and conditioned retention as separate objectives; preparation remains a competing explanation.")
        elif item["rule_id"] == "next_restraint":
            item["statement"] = ("Use loss stage and preparation/contact compatibility to choose a discriminating next method; "
                "a largest observed occupancy advantage alone does not identify the relevant intervention.")
    revised_model = artifact(rm.build_research_model(workspace(), model_id="qov-local-premises", task_id=TASK, scope=SCOPE,
        conditions=CONDITIONS, rules=revised_rules, method_refs=[candidate], counter_refs=[original],
        support_refs=[observation], supersedes=[model],
        prediction_bindings=[dict(decision_ref=decision, prediction_id="retention", rule_ids=["geometry_retention"])]))
    generation = artifact({"actor": "Codex lead", "basis": "host_reported",
        "operation": "Apply rsi-skill-creator to revise the method-improvement skill's local mechanism strategy",
        "consumed_parts": ["task-specific improvement mechanism", "normal-feedback adaptation", "downstream benefit boundary"],
        "outputs": [candidate_content], "inputs": [base_content, creator_content, original],
        "token_cost": None, "claim": "Generation occurred in this lead turn; not an independent G0/G1 benefit trial"})
    use = ref(journal.record(MethodUse(({"kind": "task", "id": TASK, "scope": str(store.path)},),
        ({"method_version_ref": creator["record_id"], "adoption": "partial",
          "used_parts": ["problem-specific improvement procedure", "normal feedback and lineage"],
          "omitted_parts": ["independent generator superiority evaluation"]},),
        visible_evidence_refs=(original["record_id"], generation["record_id"]),
        basis="host_reported", reason="Actual lead skill revision using creator guidance")))
    revision = ref(record_method_revision(journal, build_method_revision(problem_refs=[original], target_kind="G",
        base_method_ref=base, producer_method_ref=creator, producer_use_ref=use, candidate_version_refs=[candidate],
        change_summary="Use discrepant outcomes to revise local premises and choose an informative method family",
        mechanism_hypothesis="May avoid further optimizing the same geometric proxy or replaying uninformative MD",
        scope=SCOPE, trial_refs=[context_ref, generation], counterexamples=["Earliest loss need not identify a unique physical cause"]),
        root / "content", source_id=SID, source_kind="new_execution"))
    revision_context = save(imp.build_revision_context(workspace(), task_id=TASK, scope=SCOPE, conditions=CONDITIONS,
        target_ref=base, problem_refs=[original], evidence_refs=[context_ref]))
    opportunity = imp.register_opportunity(journal, revision_context, root / "content", source_id=SID,
        source_kind="new_execution", external_workspace=workspace())
    patch = save(imp.build_method_patch(journal, context_ref=revision_context, opportunity_ref=opportunity["opportunity_ref"],
        base_method_ref=base, candidate_method_ref=candidate, source_id=SID,
        expected_change="Select mechanism-discriminating work from normal feedback; generation benefit remains to be measured")["patch"])
    experience = imp.record_experience(journal, patch_ref=patch, outcome="unresolved",
        lesson="Use the observed geometry-to-retention mismatch to examine preparation and contact compatibility before selecting another method intervention",
        invalid_when="Different endpoint, molecule, preparation or evidence may require a different improvement strategy",
        capability_id="task-local-mechanism-improvement", support_refs=[context_ref, revision], counter_refs=[original],
        directory=root / "content", source_id=SID, source_kind="new_execution")
    result = dict(task_id=TASK, scope=SCOPE, workspace=str(workspace()), original_ref=original,
        model_ref=model, revised_model_ref=revised_model, feedback_ref=feedback, revision_context_ref=context_ref, revision_ref=revision,
        candidate_ref=candidate, experience_ref=experience, retained_counts=counts,
        denominator=denominators, timing="retrospective seen MD; new local rule/procedure representation",
        new_md_calls=0, third_layer_benefit="not_established; candidate under task-local use",
        wall_seconds=time.perf_counter()-started,
        next_action="Existing insertion/EM/early-frame analysis and compatible-contact method; use actual findings to revise this procedure")
    result["result_ref"] = artifact(result)
    workspace()
    _save(root / "DONE.json", result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "primary", "old-skill", "skill-directory", "creator-directory"):
        p.add_argument("--" + name, required=True, type=Path)
    a = p.parse_args()
    result = run(a.root, a.primary, a.old_skill, a.skill_directory, a.creator_directory)
    print(canonical({k: result[k] for k in ("workspace", "retained_counts", "third_layer_benefit")}))
