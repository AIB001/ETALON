"""One bounded RSI trial on existing continuation methods; no science dispatch.

prepare freezes problem/methods/protocol, worker measures one predeclared read,
finalize evaluates all paired trials and records a scoped next-use decision.
All original research sources are read-only; seen cases remain diagnostic.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

from etalon import host
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.research import Artifact, MethodUse, MethodVersion, ResearchJournal, TaskSplit
from etalon.research import continuation, evidence
from etalon.research.components import record_json
from etalon.research.composition import WORKFLOW, node_step, record_evaluation
from etalon.research.continuation import CHECKPOINT, STEP, record_content, resume_research
from etalon.research.evidence_refs import file_identity
from etalon.research.method_revision import build_method_revision, journal_ref, record_method_revision, resolve_method_revision

SID, TASK = "rsi", "rsi-continuation-method"
QUESTION = "Can one frozen evidence view reduce continuation cost without changing research context?"


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def helpers(root):
    journal = ResearchJournal(CampaignStore(root / "research.sqlite"))
    ref = lambda rid: journal_ref(journal, rid, source_id=SID)
    def artifact(value, **bindings):
        return ref(record_json(journal, value, root / "memory", source_kind="new_execution", bindings=bindings))
    def save(value):
        return ref(record_content(journal, value, root / "memory", source_id=SID, source_kind="new_execution"))
    return journal, ref, artifact, save


def hook_args(root, handler):
    return dict(log_ref={"kind": "campaign", "database": str(root / "research.sqlite")},
        state_path=root / "trigger-cursor.json", handler_method_version="rsi-continuation-problem-v1",
        handler=handler, event_types=["research_recorded"], record_types=["etalon-method-use/1"], limit=100)


def prepare(root, protocol_path):
    root.mkdir(parents=True, exist_ok=False)
    store = CampaignStore(root / "research.sqlite")
    store.configure(CampaignSpec("engineering", 0, "wall_second", TASK),
                    [Endpoint("engineering", "diagnostic", "unmeasured", "count", "rsi/1", 0, queryable=False)])
    j, ref, artifact, save = helpers(root)
    protocol = json.loads(protocol_path.read_text("utf-8"))
    assert protocol["pairs_per_case"] == 8 and protocol["minimum_median_gain"] == .2
    problem = json.loads(Path(protocol["problem_path"]).read_text("utf-8"))
    assert len(problem["observed_source_reads"]) == 4 and problem["unmodified"]
    problem_ref = artifact(problem, kind="observed_method_problem")
    protocol_ref = ref(j.record(Artifact(str(protocol_path), **file_identity(protocol_path), source_kind="new_execution", role="diagnostic", bindings={"kind":"comparison_protocol"})))
    quality_path = Path(protocol["quality_receipt"])
    quality_ref = ref(j.record(Artifact(str(quality_path), **file_identity(quality_path), source_kind="new_execution", role="diagnostic", bindings={"kind":"correctness_test_receipt"})))
    code_refs = [ref(j.record(Artifact(str(Path(m.__file__).absolute()), **file_identity(m.__file__), source_kind="new_execution", role="diagnostic", bindings={"kind":"method_implementation"}))) for m in (continuation,evidence)]
    base_content = artifact({"entrypoint":"etalon.research.continuation.resume_research", "kwargs":{"read_mode":"fresh"}, "implementation_refs":code_refs})
    base = ref(j.record(MethodVersion("resume-fresh", ("domain_skill",), {"artifact_ref":base_content}, evidence_refs=tuple(r["record_id"] for r in [base_content,*code_refs]))))
    g_content = artifact({"recipe":"Use an observed repeated source read to propose one call-local frozen-view candidate; preserve all verification and the original method; compare one fixed panel; stop at adopt or retain.", "producer":"current host authored one code candidate from the observed trace", "independent_generator_benefit":"not_tested"})
    generator = ref(j.record(MethodVersion("bounded-read-revision", ("improvement_method",), {"artifact_ref":g_content}, evidence_refs=(g_content["record_id"],))))
    trigger = ref(j.record(MethodUse(({"kind":"task","id":TASK,"scope":str(j.store.path)},),
        ({"method_version_ref":base["record_id"],"scope":"Observed released R4 continuation profiling"},),
        visible_evidence_refs=(problem_ref["record_id"],), reason="Actual baseline call read each frozen source twice")))
    generated = []
    def generate(event, request_id):
        # Other MethodUse writes never become new optimization problems.
        if digest(event["body"]["record"]) != trigger["record_digest"]:
            return
        use = ref(j.record(MethodUse(({"kind":"task","id":TASK,"scope":str(j.store.path)},
            {"kind":"request","id":request_id,"scope":str(j.store.path)}),
            ({"method_version_ref":generator["record_id"],"scope":"One host-authored frozen-view modification"},),
            visible_evidence_refs=(problem_ref["record_id"],trigger["record_id"]), reason="Host-reported application of the bounded revision recipe; not native telemetry")))
        content = artifact({"entrypoint":"etalon.research.continuation.resume_research", "kwargs":{"read_mode":"frozen_view"},
            "implementation_refs":code_refs, "eligibility":"Closed complete frozen source set only; else fresh fallback; recheck bytes before returning; no cross-call cache"})
        candidate = ref(j.record(MethodVersion("resume-frozen-view", ("domain_skill",), {"artifact_ref":content},
            parents=(base["record_id"],), evidence_refs=tuple(r["record_id"] for r in [content,*code_refs]), change_summary="Reuse one closed frozen evidence view within one continuation call")))
        revision = build_method_revision(problem_refs=[problem_ref,trigger], target_kind="S", base_method_ref=base,
            producer_method_ref=generator, producer_use_ref=use, candidate_version_refs=[candidate],
            change_summary="Avoid repeated source projection within one continuation call",
            mechanism_hypothesis="Fewer repeated SQL/JSON/projection reads may reduce wall time at equal context and authority checks",
            scope=protocol["scope"], counterexamples=["Mutable or partial sources must fall back", "Small sources may have no useful time saving"])
        rev_ref = ref(record_method_revision(j, revision, root / "memory", source_id=SID, source_kind="new_execution"))
        generated.append({"candidate":candidate,"producer_use":use,"revision":rev_ref,"request_id":request_id})
    first = host.consume_events(**hook_args(root, generate))
    replay = host.consume_events(**hook_args(root, generate))
    assert len(generated) == 1
    change = generated[0]
    pinned = {str(Path(protocol["paused_status"])):file_identity(protocol["paused_status"])}
    for case in protocol["cases"]:
        entry = Path(case["entry"])
        assert file_identity(entry) == case["entry_identity"]
        pinned.update({str(p):file_identity(p) for p in entry.parent.rglob("*") if p.is_file()})
    initial = artifact({"source_pins":pinned,"python":sys.executable,"code_refs":code_refs,"protocol":protocol_ref})
    split = ref(j.record(TaskSplit(tuple(c["case_id"] for c in protocol["cases"]), ("unrun-independent",), ("unrun-native-host",),
        {**{c["case_id"]:c["entry_identity"]["sha256"] for c in protocol["cases"]}, "unrun-independent":"not-released","unrun-native-host":"not-released"}, protocol["scope"])))
    wf = save({"schema":WORKFLOW,"name":"bounded-continuation-revision","scope":protocol["scope"],"method_refs":[base,change["candidate"]],"supersedes":[],
        "nodes":[{"node_id":"next-read","capability_id":None,"method_refs":[base,change["candidate"]],"views":["E","J"],"purpose":"Use the scoped resolution on a later read","skip_when":"No later read is requested"}],"edges":[]})
    plan = save({"schema":STEP,"task_id":TASK,"question":QUESTION,"branch_id":"comparison","operation":"evaluation_plan",
        "reason":"Observed redundant reads; fixed comparison before trial results", "input_refs":[change["revision"],wf],"output_refs":[],"method_refs":[base,change["candidate"]],
        "accounting_refs":[],"source_watermarks":[],"supersedes":[],"detail":{"target":"method","subjects":[change["candidate"]],"comparators":[base],
            "protocol_ref":protocol_ref,"task_split_ref":split,"initial_conditions_ref":initial,"use_refs":[trigger,change["producer_use"]],"trigger_refs":[problem_ref,trigger],
            "metrics":[{"name":"exact_context_agreement","family":"regression","unit":"fraction","denominator":"all 16 paired comparisons; complete JSON","missing_policy":"any missing or error fails"},
                {"name":"wall_time_gain","family":"cost","unit":"fraction","denominator":"baseline wall time of each matched pair","missing_policy":"any missing or error prevents adoption"}]}})
    state={"protocol":protocol,"protocol_identity":file_identity(protocol_path),"base":base,"generator":generator,**change,
        "problem":problem_ref,"plan":plan,"workflow":wf,"initial":initial,"source_pins":pinned,"quality_ref":quality_ref,
        "quality_identity":file_identity(quality_path),"baseline_context_sha256":problem["context_sha256"],
        "hooks":{"first":first,"replay":replay},"generated_candidates":1}
    write(root / "prepared.json",state)
    (root / "trials").mkdir()
    return {"prepared":str(root / "prepared.json"),"generated_candidates":1}


def worker(entry_path, mode, output):
    entry=json.loads(entry_path.read_text("utf-8"))
    original, calls=evidence._source, []
    def traced(ws, source):
        calls.append(source["source_id"])
        return original(ws, source)
    evidence._source=traced
    start=time.perf_counter()
    try:
        warm=resume_research(entry["workspace"],entry["checkpoint_ref"],read_mode=mode)
        warm_wall=time.perf_counter()-start
        calls.clear()
        cpu=time.process_time()
        start=time.perf_counter()
        context=resume_research(entry["workspace"],entry["checkpoint_ref"],read_mode=mode)
        wall=time.perf_counter()-start
        cpu=time.process_time()-cpu
        assert context == warm
        result={"status":"ok","context":context,"wall_seconds":wall,"cpu_seconds":cpu,"warm_wall_seconds":warm_wall,"source_reads":calls}
    except Exception as exc:
        result={"status":"failed","error_type":type(exc).__name__,"message":str(exc),"elapsed_since_last_start":time.perf_counter()-start}
    finally:
        evidence._source=original
    result.update(entry=str(entry_path),entry_identity=file_identity(entry_path),read_mode=mode,python=sys.executable,
        modules={m.__name__:{"path":m.__file__,**file_identity(m.__file__)} for m in (continuation,evidence)},money=None)
    write(output,result)
    return {"status":result["status"],"output":str(output)}


def finalize(root):
    state=json.loads((root / "prepared.json").read_text("utf-8"))
    protocol=state["protocol"]
    j,ref,artifact,save=helpers(root)
    pins_ok=all(file_identity(p)==pin for p,pin in state["source_pins"].items())
    assert pins_ok, "Original research sources changed; no resolution allowed"
    quality=json.loads(Path(protocol["quality_receipt"]).read_text("utf-8"))
    assert file_identity(protocol["quality_receipt"]) == state["quality_identity"]
    quality_ok=quality["exit_code"] == 0
    trials, trial_refs, results=[],[],[]
    for case in protocol["cases"]:
        pairs=[]
        for i in range(protocol["pairs_per_case"]):
            arms={}
            for mode in ("fresh","frozen_view"):
                p=root / "trials" / f'{case["case_id"]}-{i:02}-{mode}.json'
                row=json.loads(p.read_text("utf-8"))
                assert row["entry_identity"] == case["entry_identity"] and row["read_mode"] == mode
                if row["status"] == "ok":
                    assert row["modules"] == protocol["method_modules"], "trial ran a different implementation"
                trial_refs.append(ref(j.record(Artifact(str(p),**file_identity(p),source_kind="new_execution",role="diagnostic",bindings={"kind":"paired_read_trial"}))))
                arms[mode]=row
                trials.append(row)
            a,b=arms["fresh"],arms["frozen_view"]
            equal=a["status"]==b["status"]=="ok" and a["context"]==b["context"]
            valid=equal and not a["context"]["issues"] and not a["context"]["warnings"] and a["context"]["authority"]["state"]=="paused" and a["context"]["execution_permission"]=="not_granted"
            if case == protocol["cases"][0] and a["status"] == "ok":
                valid = valid and digest(a["context"]) == state["baseline_context_sha256"]
            pairs.append({"pair":i,"correct":valid,"gain":1-b["wall_seconds"]/a["wall_seconds"] if valid else None,
                "fresh_wall":a.get("wall_seconds"),"candidate_wall":b.get("wall_seconds")})
        gains=[p["gain"] for p in pairs if p["correct"]]
        results.append({"case_id":case["case_id"],"pairs":pairs,"correct_pairs":len(gains),
            "median_gain":statistics.median(gains) if gains else None,"faster_pairs":sum(g>0 for g in gains),
            "passes":len(gains)==protocol["pairs_per_case"] and sum(g>0 for g in gains)>=6 and statistics.median(gains)>=protocol["minimum_median_gain"]})
    adopted=quality_ok and all(r["passes"] for r in results)
    outcome="adopt" if adopted else "retain"
    summary=artifact({"scope":protocol["scope"],"results":results,"outcome":outcome,"quality_passed":quality_ok,"science_benefit":"not_tested","generator_benefit":"not_tested"},kind="comparison_summary")
    process_costs=json.loads((root / "process-costs.json").read_text("utf-8"))
    costs=artifact({"process_costs":process_costs,"method_measurements_nested_in_process_costs":True,"money":None,"model_tokens":None,"science_calls":0},kind="accounting")
    conclusion=f"{outcome}: exact-context and fixed paired wall-time gates; local diagnostic scope only; no scientific or native-host gain claim"
    evaluated=record_evaluation(j,state["plan"],directory=root / "memory",source_id=SID,source_kind="new_execution",result_refs=tuple(r["record_id"] for r in [summary,state["quality_ref"],*trial_refs]),accounting_refs=(costs["record_id"],),conclusion=conclusion,scope=protocol["scope"])
    resolution=resolve_method_revision(j,state["revision"],evaluated["evaluation_ref"],outcome=outcome,
        candidate_ref=state["candidate"] if adopted else None,task_id=TASK,question=QUESTION,scope=protocol["scope"],reason=conclusion,directory=root / "memory",source_id=SID,source_kind="new_execution")
    before_events=j.store.events()
    assert resolve_method_revision(j,state["revision"],evaluated["evaluation_ref"],outcome=outcome,
        candidate_ref=state["candidate"] if adopted else None,task_id=TASK,question=QUESTION,scope=protocol["scope"],reason=conclusion,directory=root / "memory",source_id=SID,source_kind="new_execution")==resolution
    assert j.store.events()==before_events
    selected_mode="frozen_view" if adopted else "fresh"
    next_path=root / "next-use.json"
    worker(Path(protocol["cases"][0]["entry"]),selected_mode,next_path)
    next_result=json.loads(next_path.read_text("utf-8"))
    assert next_result["status"]=="ok" and next_result["context"]==trials[0]["context"]
    next_ref=ref(j.record(Artifact(str(next_path),**file_identity(next_path),source_kind="new_execution",role="diagnostic",bindings={"kind":"actual_next_method_use"})))
    next_step=save(node_step(task_id=TASK,question=QUESTION,branch_id="next-read",node_id="next-read",workflow_ref=state["workflow"],state="completed",
        reason="Actual subsequent call explicitly applied the scoped resolution",input_refs=[resolution["step_ref"]],output_refs=[next_ref],receipt_refs=[next_ref],
        method_refs=[resolution["selected_method_ref"]],use_refs=[resolution["use_ref"]],accounting_refs=[costs]))
    accidental=[]
    def no_recursive_trigger(event, request_id):
        if request_id==state["request_id"]:
            accidental.append(request_id)
    after_notes=host.consume_events(**hook_args(root,no_recursive_trigger))
    replay=host.consume_events(**hook_args(root,no_recursive_trigger))
    assert not accidental
    cp=save({"schema":CHECKPOINT,"task_id":TASK,"question":QUESTION,"step_refs":[state["plan"],evaluated["step_ref"],resolution["step_ref"],next_step],
        "claim_refs":[],"counter_refs":[state["problem"]],"method_refs":[state["base"],state["candidate"],state["generator"]],
        "branches":[{"branch_id":"comparison","state":"resolved","basis_refs":[evaluated["step_ref"],resolution["step_ref"]],"next_read_refs":[summary],"reconsider_when":"New counterevidence or a new workload; do not retune on this panel"},
            {"branch_id":"science","state":"stopped","basis_refs":[state["initial"]],"next_read_refs":[],"reconsider_when":"Explicit resumption of the paused scientific work"}],
        "pending_handles":[],"source_watermarks":[],"supersedes":[],"status_guards":[{"location":protocol["paused_status"],**file_identity(protocol["paused_status"]),"state":"paused"}]})
    ws=root / "etalon-workspace.json"
    write(ws,{"schema":"etalon-evidence-workspace/1","workspace_id":TASK,"sources":[{"source_id":SID,"kind":"journal","location":str(j.store.path),"identity":{"kind":"snapshot_sha256","value":file_identity(j.store.path)["sha256"]}}],"status_refs":[protocol["paused_status"]],"location_map":[]})
    entry={"workspace":str(ws),"checkpoint_ref":cp}
    write(root / "entry.json",entry)
    context=resume_research(ws,cp)
    assert not context["issues"] and not context["warnings"],context
    assert all(file_identity(p)==pin for p,pin in state["source_pins"].items())
    report={"outcome":outcome,"results":results,"resolution":resolution,"evaluation":evaluated,"entry":entry,"checked_refs":len(context["checked_refs"]),
        "generated_candidates":state["generated_candidates"],"after_notes":after_notes,"hook_replay":replay,"accidental_retriggers":accidental,"source_files_unchanged":len(state["source_pins"]),
        "next_use_mode":selected_mode,"default_changed":False,"scientific_balance":j.store.balance(),"source_and_trial_scope":protocol["scope"]}
    write(root / "report.json",report)
    return {"outcome":outcome,"entry":entry,"results":[{k:v for k,v in r.items() if k!="pairs"} for r in results]}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation",choices=["prepare","worker","finalize"])
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--protocol",type=Path)
    p.add_argument("--entry",type=Path)
    p.add_argument("--read-mode",choices=["fresh","frozen_view"])
    a=p.parse_args()
    result=prepare(a.output.absolute(),a.protocol.absolute()) if a.operation=="prepare" else worker(a.entry.absolute(),a.read_mode,a.output.absolute()) if a.operation=="worker" else finalize(a.output.absolute())
    print(json.dumps(result,ensure_ascii=False))
