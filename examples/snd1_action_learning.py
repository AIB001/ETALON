"""Prospective SND1 action learning using the existing research journal.

Prepare a decision before native execution, attach normal results afterwards,
and publish a resumable current choice. Native searches remain owned by the
component runner. No scheduler or automatic scientific adoption is introduced.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical
from etalon.research import Artifact, MethodVersion, MethodUse, ResearchJournal, TaskSplit
from etalon.research import research_decision as rd
from etalon.research import improvement as imp
from etalon.research.action_learning import gather_action_basis, compare_action_feedback
from etalon.research.components import record_json
from etalon.research.composition import node_step, record_evaluation
from etalon.research.continuation import CHECKPOINT, STEP, record_content
from etalon.research.evidence_refs import file_identity
from etalon.research.method_revision import journal_ref
from etalon.research.opportunity_state import build_opportunity_disposition
from etalon.research.publication import _save

TASK = "snd1-source-qualified-route-learning"
SID = "snd1-action-learning"
BRANCH = "stock-coverage-versus-search"
SCOPE = "QOY/QOV, pinned AiZynthFinder models, explicit catalog stock; computational route closure"
CONDITIONS = {"target": "SND1", "endpoint": "catalog-supported-route-closure", "family": "QOY-QOV-sulfonamide"}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class Session:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = CampaignStore(self.root / "research.sqlite")
        self.store.configure(CampaignSpec("learning", 0, "wall_second", TASK),
            [Endpoint("learning", "diagnostic", "unmeasured", "count", "learning/1", 0, queryable=False)])
        self.journal = ResearchJournal(self.store)
        prior = load(self.root.parent / "prior-read.json")
        self.prior_sources = load(prior["workspace"])["sources"]

    def ref(self, rid):
        return journal_ref(self.journal, rid, source_id=SID)

    def artifact(self, obj):
        return self.ref(record_json(self.journal, obj, self.root / "content", source_kind="new_execution",
            bindings={"schema": obj["schema"]} if "schema" in obj else {}))

    def original(self, path, purpose, *, kind=None):
        path = Path(path).absolute()
        return self.ref(self.journal.record(Artifact(str(path), **file_identity(path),
            source_kind="existing_real_artifact", role="diagnostic",
            bindings={"purpose": purpose, **({"kind": kind} if kind else {})})))

    def workspace(self):
        path = self.root / "workspace.json"
        _save(path, dict(schema="etalon-evidence-workspace/1", workspace_id=TASK,
            sources=[*self.prior_sources, dict(source_id=SID, kind="journal", location=str(self.store.path),
                identity=dict(kind="snapshot_sha256", value=file_identity(self.store.path)["sha256"]))],
            status_refs=[], location_map=[]), replace=True)
        return path

    def save(self, content):
        return self.ref(record_content(self.journal, content, self.root / "content", source_id=SID,
            source_kind="new_execution", external_workspace=self.workspace()))

    def publish(self, phase, *, steps, basis, methods, state, reason, previous=(), extra_branches=()):
        pause_ref = self.original(Path(__file__).parents[2] / "integration/round4b/STATUS.md", "R4b pause")
        checkpoint = self.save(dict(schema=CHECKPOINT, task_id=TASK,
            question="Which intervention can advance the retained SND1 routes?", step_refs=steps,
            claim_refs=[], counter_refs=[], method_refs=methods,
            branches=[dict(branch_id=BRANCH, state=state, basis_refs=basis, next_read_refs=basis,
                reconsider_when=reason), dict(branch_id="R4b", state="stopped", basis_refs=[pause_ref],
                next_read_refs=[pause_ref], reconsider_when="Explicit user resumption"), *extra_branches],
            pending_handles=[], source_watermarks=[], status_guards=[], supersedes=list(previous)))
        directory = self.root / phase
        directory.mkdir(exist_ok=True)
        if (directory / "entry.json").exists():
            existing = load(directory / "entry.json")
            if existing["checkpoint_ref"] != checkpoint:
                raise ValueError("Phase content differs; create an explicit successor")
            return existing
        snapshot = directory / "research.sqlite"
        if snapshot.exists():
            raise ValueError("Phase already published; use saved phase entry")
        with sqlite3.connect(self.store.path) as src, sqlite3.connect(snapshot) as dst:
            src.backup(dst)
        ws = dict(schema="etalon-evidence-workspace/1", workspace_id=TASK,
            sources=[*self.prior_sources, dict(source_id=SID, kind="journal", location=str(snapshot),
                identity=dict(kind="snapshot_sha256", value=file_identity(snapshot)["sha256"]))],
            status_refs=[], location_map=[])
        _save(directory / "workspace.json", ws)
        entry = dict(workspace=str(directory / "workspace.json"), checkpoint_ref=checkpoint)
        _save(directory / "entry.json", entry)
        _save(self.root.parent / "entry.json", entry, replace=True)
        return entry


def prepare(root, plan_path, *, repair_lineage=False):
    s = Session(root)
    done = s.root / "prepared.json"
    previous = None
    if done.exists():
        result = load(done)
        if result["plan_identity"] != file_identity(plan_path):
            raise ValueError("Prepared plan changed; create an explicit new decision")
        if not repair_lineage or result.get("lineage_repaired"):
            return result
        previous = result
        _save(s.root / "prepared-v1.json", previous)
    plan = load(plan_path)
    prior = load(s.root.parent / "prior-route-basis.json")
    prior_ref = prior["items"][0]["ref"]
    protocol = s.original(plan_path, "prospective scientific comparison")
    problem = s.artifact(dict(statement=plan["question"], support_refs=[prior_ref],
        missing_mechanism="A missing purchasable reagent can prevent terminal closure without making a route impossible.",
        alternatives=["catalog coverage", "insufficient search", "reaction or intermediate mismatch"]))
    methods = []
    for arm in plan["arms"]:
        body = s.artifact(dict(arm=arm, fixed=plan["fixed"], seed=plan["seed"],
            overlay=plan["overlay_eligibility"] if arm["id"].startswith("C_") else None))
        methods.append(s.ref(s.journal.record(MethodVersion(arm["id"], ("scientific_method",),
            {"artifact_ref": body["record_id"]}, parents=(methods[0]["record_id"],) if methods else (),
            change_summary="Explicit route search workflow configuration"))))
    expected = {}
    predictions = []
    for case in plan["cases"]:
        cid = case["id"]
        expected[cid] = dict(observable=cid + " route stock closure across three arms",
            expected_statement="A and B remain nonclosed; catalog-supported C closes",
            checks=[dict(pointer=f"/cases/{cid}/{arm}/stock_closed", expected=value) for arm, value in
                    [("A_original_100", False), ("B_original_400", False), ("C_catalog_100", True)]],
            next_if_match="Use source-qualified route continuation; investigate reaction conditions instead of more blind search",
            next_if_different="Inspect which arm closes and remaining leaves; revise the catalog-gap explanation",
            next_if_unknown="Continue only the missing result/source; reuse completed attempts")
    expectation_ref = s.artifact(expected)
    inputs = [protocol, prior_ref, problem, expectation_ref]
    planned = s.save(node_step(task_id=TASK, question=plan["question"], branch_id=BRANCH, node_id="compare",
        state="planned", reason="Normal route research selected to distinguish stock coverage from search budget",
        input_refs=inputs, method_refs=methods))
    for cid, spec in expected.items():
        predictions.append(dict(prediction_id=cid, action_id="compare", option_ids=["catalog_gap"],
            observable=spec["observable"], expected=spec["expected_statement"],
            distinguishes="Different closures distinguish catalog coverage from search stochasticity/budget",
            status="testable", basis_refs=[expectation_ref, prior_ref], unknown_reason=None))
    decision = rd.build_research_decision(s.workspace(), task_id=TASK, scope=SCOPE, branch_id=BRANCH,
        question=plan["question"], problem_refs=[problem],
        structure=dict(task_family="retrosynthesis", objective_kind="candidate_progress", subject="QOY/QOV retained routes",
            intervention="Catalog-qualified terminal stock versus increased search", readout="stock closure and complete cost",
            constraints=["unchanged expansion/filter models", "explicit catalog listing, not inventory"],
            unknowns=["reaction conditions and experimental yield"]),
        attribution=dict(actor="research lead", basis="host_judgment", model_ref=None, skill_refs=[], verifier_refs=[]),
        options=[dict(option_id=k, kind="hypothesis", statement=v, claim_refs=[]) for k,v in [
            ("catalog_gap", "A missing terminal reagent is the bottleneck"),
            ("search_gap", "Additional search can find a route in existing stock"),
            ("reaction_gap", "Reaction choice or conditions remain unresolved")]],
        predictions=predictions,
        actions=[dict(action_id="compare", description="Run the frozen three-arm route comparison",
            disposition="selected", reason="Different interventions change the proposed bottleneck", execution_ref=planned,
            prediction_ids=list(expected))], method_refs=methods,
        scientific_inputs=[dict(ref=r, use="conditioning", reason="Pre-execution basis") for r in inputs],
        support_refs=[prior_ref])
    decision_ref = s.artifact(decision)
    basis = gather_action_basis(s.workspace(), decision_ref=decision_ref, action_id="compare", conditions=CONDITIONS,
        expectations=[dict(prediction_id=k, ref=expectation_ref, pointer="/"+k) for k in expected],
        rule_selectors=[dict(ref=prior_ref, pointer="/routes"), dict(ref=prior_ref, pointer="/targeted_followup")])
    basis_ref = s.artifact(basis)
    entry = s.publish("prepared-v2" if previous else "prepared", steps=[planned], basis=[decision_ref,basis_ref], methods=methods,
        state="waiting", reason="Frozen prospective comparison; resume from actual component receipts",
        previous=[previous["entry"]["checkpoint_ref"]] if previous else [])
    result = dict(plan_identity=file_identity(plan_path), protocol_ref=protocol, prior_ref=prior_ref,
        decision_ref=decision_ref, basis_ref=basis_ref, planned_ref=planned, methods=methods, inputs=inputs, entry=entry)
    if previous:
        result.update(lineage_repaired=True, original_decision_ref=previous["decision_ref"],
            supersedes_preparation=previous["entry"])
    _save(done, result, replace=bool(previous))
    return result


def observe(root, observation_path, receipt_path, cost_path):
    s = Session(root)
    prepared = load(s.root / "prepared.json")
    request = dict(observation=file_identity(observation_path), receipt=file_identity(receipt_path), cost=file_identity(cost_path))
    done = s.root / "observed.json"
    if done.exists():
        result=load(done)
        if result["request"] != request:
            raise ValueError("Different observation requires an explicit successor")
        return result
    output = s.original(observation_path, "normal scientific result")
    receipt = s.original(receipt_path, "actual component receipt")
    cost = s.original(cost_path, "complete measured cost and unknown cost dimensions", kind="cost_receipt")
    use = s.ref(s.journal.record(MethodUse((dict(kind="task", id=TASK, scope=str(s.store.path)),
        dict(kind="result", id=receipt["record_id"], scope=str(s.store.path))),
        tuple(dict(method_version_ref=r["record_id"], scope=SCOPE) for r in prepared["methods"]),
        host_conditions=CONDITIONS, reason="Actual three-arm native execution; exact attempts in component receipts")))
    node = s.save(node_step(task_id=TASK, question="Which intervention advances QOY/QOV routes?", branch_id=BRANCH,
        node_id="compare", state="completed", reason="Completed normal route comparison", input_refs=prepared["inputs"],
        output_refs=[output], receipt_refs=[receipt], method_refs=prepared["methods"], use_refs=[use],
        accounting_refs=[cost],
        supersedes=[prepared["planned_ref"]]))
    feedback=compare_action_feedback(s.workspace(), basis_ref=prepared["basis_ref"], execution_ref=node, observation_ref=output)
    feedback_ref=s.artifact(feedback)
    entry=s.publish("observed", steps=[node], basis=[feedback_ref,output], methods=prepared["methods"],
        state="waiting", reason="Results saved; choose the next research action and publish scoped RSI experience",
        previous=[prepared["entry"]["checkpoint_ref"]])
    result=dict(request=request, output_ref=output, receipt_ref=receipt, cost_ref=cost, use_ref=use, node_ref=node,
        feedback_ref=feedback_ref, feedback=feedback, entry=entry)
    _save(done,result)
    return result


def finish(root, judgment_path):
    """Append the host's actual scoped evaluation and selected next research action.

    The judgment is authored after reading normal results. Replaying a completed
    publication uses saved identities and never invokes a scientific executor.
    """
    s=Session(root)
    done=s.root / "finished.json"
    if done.exists():
        result=load(done)
        if result["judgment_identity"] != file_identity(judgment_path):
            raise ValueError("Changed judgment needs an explicit successor")
        return result
    p=load(s.root / "prepared.json")
    o=load(s.root / "observed.json")
    r=load(s.root / "revision.json")
    j=load(judgment_path)
    if j["outcome"] not in {"adopt","retain"}:
        raise ValueError("This example accepts an explicit scoped adoption or retention")
    judgment=s.original(judgment_path,"host scientific interpretation and next action")
    next_product=s.original(j["next_product_path"],"actual conditions-aware route continuation")
    reaction=s.original(j["reaction_evidence_path"],"exact-fragment reaction literature and negative result")
    assessment=s.save(imp.assess_revision(s.journal,patch_ref=r["patch_ref"],
        plan_ref=r["evaluation_plan_ref"],observed_step_refs=[o["node_ref"]],source_id=SID))
    evaluation=record_evaluation(s.journal,r["evaluation_plan_ref"],directory=s.root / "content",
        source_id=SID,source_kind="new_execution",result_refs=[o["output_ref"]["record_id"],o["node_ref"]["record_id"],
            o["feedback_ref"]["record_id"],judgment["record_id"]],accounting_refs=[o["cost_ref"]["record_id"]],
        assessment_role="diagnostic",conclusion=j["conclusion"],scope=SCOPE)
    selected=p["methods"][2 if j["outcome"]=="adopt" else 0]
    selection_use=s.ref(s.journal.record(MethodUse(
        (dict(kind="task",id=TASK,scope=str(s.store.path)),),
        (dict(method_version_ref=selected["record_id"],scope=SCOPE),),
        visible_evidence_refs=(evaluation["evaluation_ref"]["record_id"],o["feedback_ref"]["record_id"]),
        host_conditions=CONDITIONS,reason="Host adopts the measured workflow for local continuation; no new scientific execution")))
    resolution=s.save(dict(schema=STEP,task_id=TASK,question="How should the retained SND1 routes progress?",
        branch_id=BRANCH,operation="decision",reason=j["conclusion"],input_refs=[evaluation["evaluation_ref"],o["feedback_ref"],judgment],
        output_refs=[selection_use],method_refs=[selected],accounting_refs=[o["cost_ref"]],source_watermarks=[],supersedes=[],
        detail=dict(choice="proceed",alternatives=[dict(branch_id=BRANCH,disposition=j["outcome"],reason=j["lesson"])],
            basis_refs=[evaluation["evaluation_ref"],o["feedback_ref"],reaction],reconsider_when=j["reconsider_when"])))
    predecessors_path=s.root / "experience-predecessors.json"
    if predecessors_path.exists():
        predecessors=load(predecessors_path)
    else:
        prior_experience=imp.retrieve_experience(s.workspace(),source_ids=[SID],capability_id="molcascade.retrosynthesis",
            conditions=CONDITIONS,limit=10)
        predecessors=[row["ref"] for row in prior_experience["matches"] if row["experience"]["patch_ref"]==r["patch_ref"]]
        _save(predecessors_path,predecessors)
    experience=imp.record_experience(s.journal,patch_ref=r["patch_ref"],outcome=j["outcome"],lesson=j["lesson"],
        invalid_when=" ".join(j["invalid_when"]),capability_id="molcascade.retrosynthesis",support_refs=[o["output_ref"],o["feedback_ref"]],
        counter_refs=[reaction,o["feedback_ref"]],evaluation_ref=evaluation["evaluation_ref"],resolution_ref=resolution,
        supersedes=predecessors,directory=s.root / "content",source_id=SID,source_kind="new_execution")
    disposition=s.save(build_opportunity_disposition(s.journal,opportunity_ref=r["opportunity_ref"],
        outcome=j["outcome"],reason=j["conclusion"],reviewer="host lead with actual normal results",
        support_refs=[o["output_ref"],evaluation["evaluation_ref"]],counter_refs=[reaction,o["feedback_ref"]],
        reconsider_when=[dict(condition_id="changed-route-bottleneck",condition=j["reconsider_when"])],
        source_id=SID,candidate_ref=p["methods"][2],evaluation_ref=evaluation["evaluation_ref"],
        resolution_ref=resolution,external_workspace=s.workspace()))
    readback=imp.retrieve_experience(s.workspace(),source_ids=[SID],capability_id="molcascade.retrosynthesis",
        conditions=CONDITIONS,limit=10)
    readback_ref=s.artifact(readback)
    next_choice=s.save(dict(schema=STEP,task_id=TASK,question=j["next_question"],branch_id="conditions-aware-route",
        operation="decision",reason=j["next_reason"],input_refs=[readback_ref,o["feedback_ref"],reaction,next_product],
        output_refs=[next_product],method_refs=[selected],accounting_refs=[],source_watermarks=[],supersedes=[],
        detail=dict(choice="fork",alternatives=[dict(branch_id="conditions-aware-route",disposition="proceed",
            reason=j["next_reason"]),dict(branch_id="more-blind-search",disposition="defer",reason=j["defer_reason"])],
            basis_refs=[experience,o["feedback_ref"],reaction],reconsider_when=j["reconsider_when"])))
    brief=s.artifact(dict(task_id=TASK,scope=SCOPE,conditions=CONDITIONS,
        current_conclusion=j["conclusion"],scoped_outcome=j["outcome"],
        next_question=j["next_question"],next_action=j["next_reason"],remaining_unknowns=j["remaining_unknowns"],
        completed_refs=[o["node_ref"],evaluation["evaluation_ref"],disposition],experience_ref=experience,
        local_rules_ref=judgment,case_lessons=j["case_lessons"],
        next_read_refs=[next_product,reaction,o["feedback_ref"]],reuse_completed="Six native attempts are complete; reuse pinned receipts. Reopen only for changed evidence/conditions.",
        paused=["R4b"],cost_ref=o["cost_ref"]))
    completed=o["node_ref"]
    if p.get("lineage_repaired"):
        old=load(s.root / "prepared-v1.json")
        completed=s.save(node_step(task_id=TASK,question="Which intervention advances QOY/QOV routes?",branch_id=BRANCH,
            node_id="compare",state="reused",reason="Reuse six completed receipts; retire the superseded metadata-only preparation",
            input_refs=p["inputs"],output_refs=[o["output_ref"]],receipt_refs=[o["receipt_ref"]],method_refs=p["methods"],
            use_refs=[o["use_ref"]],accounting_refs=[o["cost_ref"]],
            supersedes=[o["node_ref"],p["planned_ref"],old["planned_ref"]]))
    entry=s.publish("finished",steps=[completed,evaluation["step_ref"],resolution,next_choice],
        basis=[brief,experience,disposition],methods=p["methods"],state="resolved",
        reason=j["reconsider_when"],previous=[o["entry"]["checkpoint_ref"]],
        extra_branches=[dict(branch_id="conditions-aware-route",state="waiting",basis_refs=[next_choice,experience],
            next_read_refs=[brief,next_product,reaction],reconsider_when=j["next_question"])])
    result=dict(judgment_identity=file_identity(judgment_path),evaluation=evaluation,assessment_ref=assessment,
        resolution_ref=resolution,experience_ref=experience,disposition_ref=disposition,readback_ref=readback_ref,
        next_choice_ref=next_choice,brief_ref=brief,entry=entry)
    _save(done,result)
    return result


def start_revision(root):
    s=Session(root)
    done=s.root / "revision.json"
    if done.exists():
        return load(done)
    p=load(s.root / "prepared.json")
    context=s.save(imp.build_revision_context(s.workspace(), task_id=TASK, scope=SCOPE, conditions=CONDITIONS,
        target_ref=p["methods"][0], problem_refs=[p.get("original_decision_ref",p["decision_ref"])], evidence_refs=[p["protocol_ref"],p["prior_ref"]]))
    opportunity=imp.register_opportunity(s.journal,context,s.root / "content",source_id=SID,
        source_kind="new_execution",external_workspace=s.workspace())
    mechanism="Use exact catalog-supported missing leaves as explicit stock overlay; distinguish missing terminal coverage from more search"
    patch=s.save(imp.build_method_patch(s.journal,context_ref=context,opportunity_ref=opportunity["opportunity_ref"],
        base_method_ref=p["methods"][0],candidate_method_ref=p["methods"][2],source_id=SID,expected_change=mechanism)["patch"])
    initial=s.artifact(dict(conditions=CONDITIONS, protocol_ref=p["protocol_ref"],
        note="Prospective comparison of known QOY/QOV anchors; new seed, no independent chemistry claim"))
    split=s.ref(s.journal.record(TaskSplit(("prior-route-seed",),("new-route-seed",),(),
        {"prior-route-seed":p["prior_ref"]["record_digest"],"new-route-seed":p["protocol_ref"]["record_digest"]},
        "Prospective new-seed comparison on known molecules; no cross-target or experimental validation")))
    evaluation_plan=s.save(dict(schema=STEP,task_id=TASK,question="Does explicit sourcing advance retained routes?",
        branch_id=BRANCH,operation="evaluation_plan",reason="Evaluate the normal three-arm comparison",
        input_refs=[p["protocol_ref"],context],output_refs=[],method_refs=[p["methods"][2],p["methods"][0]],
        accounting_refs=[],source_watermarks=[],supersedes=[],detail=dict(target="workflow",
            subjects=[p["methods"][2]],comparators=[p["methods"][0]],protocol_ref=p["protocol_ref"],
            task_split_ref=split,initial_conditions_ref=initial,use_refs=[],trigger_refs=[context],
            metrics=[dict(name="catalog_closure",family="funnel",unit="molecule",denominator="two known molecules",
                missing_policy="unknown remains unknown"),dict(name="complete_cost",family="cost",unit="seconds",
                denominator="all six attempts plus source and preparation work",missing_policy="unmeasured remains unknown")])))
    result=dict(context_ref=context,opportunity_ref=opportunity["opportunity_ref"],patch_ref=patch,
        evaluation_plan_ref=evaluation_plan)
    _save(done,result)
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare","observe","revise","finish"])
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--plan",type=Path)
    parser.add_argument("--repair-lineage",action="store_true")
    parser.add_argument("--observation",type=Path)
    parser.add_argument("--receipt",type=Path)
    parser.add_argument("--cost",type=Path)
    parser.add_argument("--judgment",type=Path)
    args=parser.parse_args()
    if args.phase=="prepare":
        value=prepare(args.root,args.plan,repair_lineage=args.repair_lineage)
    elif args.phase=="revise":
        value=start_revision(args.root)
    elif args.phase=="finish":
        value=finish(args.root,args.judgment)
    else:
        value=observe(args.root,args.observation,args.receipt,args.cost)
    print(canonical({k:value[k] for k in ("entry", "basis_ref", "feedback_ref", "opportunity_ref") if k in value}))
