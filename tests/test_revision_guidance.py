"""Synthetic mechanism counterexamples; no scientific benefit or independent data."""
import copy

import pytest

from etalon.research import MethodUse, MethodVersion, TaskSplit
from etalon.research import candidate_brief as cb, research_decision as rd
from etalon.research import revision_guidance as rg
from etalon.research.composition import node_step, record_evaluation
from etalon.research.evidence_refs import EvidenceError
from test_candidate_brief import prepared as brief_fixture
from test_research_continuation import case, publish, save


@pytest.fixture
def prepared(case, brief_fixture):
    p = brief_fixture
    diagnostic = p["artifact"]({"before": {"errors": 3}, "after": {"errors": 1}, "null": None, "zero": 0, "false": False})
    candidate = p["ref"](p["journal"].record(MethodVersion("fixture reader candidate", ("usage_method",),
        {"artifact_ref": p["after"]["record_id"]}, parents=(p["base"]["record_id"],))))
    methods = [p["base"], candidate]
    planned = save(case, node_step(task_id="fixture-case", question="Read failure attribution", branch_id="normal",
        node_id="read", state="planned", reason="Existing normal use", method_refs=methods, input_refs=[case[1]]))
    split = p["ref"](p["journal"].record(TaskSplit(("fixture-case",), ("validation",), ("transfer",),
        {"fixture-case": "a", "validation": "b", "transfer": "c"}, "fixture comparison")))
    protocol = p["artifact"]({"fixed": "preserve originals and nulls; same denominator"})
    plan = save(case, {"schema": "etalon-research-step/1", "task_id": "fixture-case", "question": "Same-condition comparison",
        "branch_id": "compare", "operation": "evaluation_plan", "reason": "Fixed fixture checks", "input_refs": [case[1]],
        "output_refs": [], "method_refs": methods, "accounting_refs": [], "source_watermarks": [], "supersedes": [],
        "detail": {"target": "branch_selection", "subjects": [candidate], "comparators": [p["base"]],
            "protocol_ref": protocol, "task_split_ref": split, "initial_conditions_ref": case[1], "use_refs": [],
            "trigger_refs": [case[1]], "metrics": [{"name": "read-errors", "family": "regression", "unit": "count",
                "denominator": "all identical originals", "missing_policy": "unknown remains unknown"}]}})
    args = dict(task_id="fixture-case", scope="fixture readers", branch_id="normal", question="Which reader change is testable?",
        problem_refs=[case[1]], support_refs=[diagnostic], counter_refs=[case[1]], method_refs=methods,
        structure={"task_family": "reader", "objective_kind": "errors", "subject": "originals", "intervention": "reader choice",
            "readout": "errors", "constraints": ["Keep all original checks"], "unknowns": ["Cause not uniquely identified"]},
        attribution={"actor": "fixture host", "basis": "host_judgment", "model_ref": None, "skill_refs": [], "verifier_refs": []},
        options=[{"option_id": "routing", "kind": "hypothesis", "statement": "Wrong schema routing", "claim_refs": []},
                 {"option_id": "unknown", "kind": "unknown", "statement": "Availability or another cause", "claim_refs": []}],
        predictions=[{"prediction_id": "p", "action_id": "read", "option_ids": ["routing", "unknown"], "observable": "read-errors",
            "expected": "Fewer errors while preserving all originals", "distinguishes": "Compare same inputs and fixed checks",
            "status": "testable", "basis_refs": [diagnostic], "unknown_reason": None}],
        actions=[{"action_id": "read", "description": "Use normal reader", "disposition": "selected",
            "reason": "Existing normal comparison", "execution_ref": planned, "prediction_ids": ["p"]}], evaluation_refs=[plan])
    decision = save(case, rd.build_research_decision(publish(case), **args))
    difference = {"difference_id": "errors", "left": {"ref": diagnostic, "pointer": "/before/errors"},
        "right": {"ref": diagnostic, "pointer": "/after/errors"},
        "option_links": [{"option_id": "routing", "relation": "compatible", "reason": "Fixture count difference alone cannot identify cause"},
                         {"option_id": "unknown", "relation": "unknown", "reason": "Availability was not controlled"}],
        "uncontrolled": ["Availability"]}
    return {**p, "decision": decision, "decision_args": args, "diagnostic": diagnostic, "difference": difference,
            "planned": planned, "plan": plan, "methods": methods, "candidate": candidate}


def guide(case, p, **changes):
    return rg.build_revision_input(publish(case), **{**dict(decision_ref=p["decision"], prediction_id="p",
        context_ref=p["args"]["context_ref"], differences=[p["difference"]]), **changes})


def candidate(case, p, **changes):
    args = {k: v for k, v in p["args"].items() if k not in {"base_method_ref", "normal_execution", "quality_criteria", "expected_effect"}}
    return rg.build_candidate_from_decision(publish(case), **{**args, "decision_ref": p["decision"], "prediction_id": "p",
        "differences": [p["difference"]], "object_kind": "content", "falsifier": "Equal or more errors, or any lost original",
        "expected_effect": {"metric": "read-errors", "unit": "count", "direction": "decrease"}, **changes})


def test_differences_bind_inputs_explanations_and_fixed_checks_without_mutation(case, prepared):
    p = prepared
    before = p["journal"].store.events()
    result = guide(case, p)
    assert result["diagnostic_differences"][0]["left"]["value"] == 3
    assert result["diagnostic_differences"][0]["right"]["value"] == 1
    assert result["diagnostic_differences"][0]["comparison"] == "different"
    assert result["diagnostic_differences"][0]["causal_identification"] == "not_established"
    assert result["alternatives"][1]["kind"] == "unknown"
    assert result["fixed_evaluations"][0]["ref"] == p["plan"]
    assert result["evidence_timing"]["mode"] is None and result["evaluation_followup"] is None
    assert p["journal"].store.events() == before


@pytest.mark.parametrize("pointer,presence,value", [("/absent", "missing", None), ("/null", "null", None),
                                                     ("/zero", "observed", 0), ("/false", "observed", False)])
def test_missing_null_zero_and_false_are_not_semantically_lifted(case, prepared, pointer, presence, value):
    d = copy.deepcopy(prepared["difference"])
    d["right"]["pointer"] = pointer
    result = guide(case, prepared, differences=[d])
    assert result["diagnostic_differences"][0]["right"] == {**d["right"], "presence": presence, "value": value}
    assert (result["diagnostic_differences"][0]["comparison"] == "unknown") == (presence != "observed")
    assert bool(result["missing"]) == (presence != "observed")


def test_fake_and_wrong_scope_references_are_rejected(case, prepared):
    d = copy.deepcopy(prepared["difference"])
    d["left"]["ref"]["record_digest"] = "0" * 64
    with pytest.raises(EvidenceError):
        guide(case, prepared, differences=[d])
    other = prepared["artifact"]({"task_id": "another task", "scope": "fixture readers", "errors": 8})
    decision = save(case, rd.build_research_decision(publish(case), **{**prepared["decision_args"], "support_refs": [other, prepared["diagnostic"]]}))
    d = copy.deepcopy(prepared["difference"])
    d["left"] = {"ref": other, "pointer": "/errors"}
    with pytest.raises(EvidenceError, match="task/scope"):
        guide(case, prepared, decision_ref=decision, differences=[d])
    wrong_context, _, _ = prepared["episode"](scope="other scope")
    with pytest.raises(EvidenceError, match="task/scope"):
        guide(case, prepared, context_ref=wrong_context)


def test_evaluation_answers_cannot_enter_original_diagnostic_differences(case, prepared, monkeypatch):
    output = prepared["artifact"]({"errors": 0, "role": "later normal execution answer"})
    d = copy.deepcopy(prepared["difference"])
    d["right"] = {"ref": output, "pointer": "/errors"}
    original_read = rg.read_material
    def checked_read(workspace, ref):
        assert ref != output, "excluded answer was opened before rejection"
        return original_read(workspace, ref)
    monkeypatch.setattr(rg, "read_material", checked_read)
    with pytest.raises(EvidenceError, match="not evaluation answers"):
        guide(case, prepared, differences=[d])
    with pytest.raises(EvidenceError, match="already be declared"):
        candidate(case, prepared, support_refs=[output])


def test_bridge_preserves_prediction_checks_counterevidence_and_candidate_identity(case, prepared):
    p = prepared
    result = candidate(case, p)
    brief = result["candidate_brief"]
    assert brief["normal_execution"]["execution_ref"] == p["planned"]
    assert brief["normal_execution"]["success_criterion"] == p["decision_args"]["predictions"][0]["expected"]
    assert "Keep all original checks" in brief["frozen_invariants"]
    assert p["plan"] in brief["support_refs"] and case[1] in brief["counter_refs"]
    saved = save(case, brief)
    assert cb.read_candidate_brief(publish(case), saved) == brief
    repeated = candidate(case, p, title="Renamed same hypothesis", duplicate_refs=[saved])["candidate_brief"]
    assert repeated["same_candidate_refs"] == [saved] and repeated["candidate_key"] == brief["candidate_key"]
    assert repeated["adoption"] == "not_performed"
    with pytest.raises(EvidenceError, match="cannot be overridden"):
        candidate(case, p, quality_criteria=["Relax the original check"])
    with pytest.raises(EvidenceError, match="exact decision observable"):
        candidate(case, p, expected_effect={"metric": "easier metric", "unit": "count", "direction": "decrease"})


def test_unknown_and_no_change_do_not_manufacture_candidates(case, prepared):
    args = copy.deepcopy(prepared["decision_args"])
    args["predictions"][0].update(status="unknown", expected=None, distinguishes=None, unknown_reason="No prediction available")
    decision = save(case, rd.build_research_decision(publish(case), **args))
    assert guide(case, prepared, decision_ref=decision)["prediction"]["expected"] is None
    with pytest.raises(EvidenceError, match="unknown prediction"):
        candidate(case, prepared, decision_ref=decision)
    with pytest.raises(EvidenceError, match="no content change"):
        candidate(case, prepared, change={**prepared["args"]["change"], "after_ref": prepared["before"]})


def test_absent_checks_and_observations_leave_candidate_incomplete(case, prepared):
    decision = save(case, rd.build_research_decision(publish(case), **{**prepared["decision_args"], "evaluation_refs": []}))
    brief = candidate(case, prepared, decision_ref=decision)["candidate_brief"]
    assert brief["status"] == "incomplete" and "no fixed evaluation" in str(brief["missing"])
    d = copy.deepcopy(prepared["difference"])
    d["left"]["pointer"] = "/unmeasured"
    assert candidate(case, prepared, differences=[d])["candidate_brief"]["status"] == "incomplete"


def test_different_after_cannot_borrow_old_candidate_execution_or_plan(case, prepared):
    p = prepared
    normal = candidate(case, p)
    different_after = p["artifact"]({"read": "unrelated_new_algorithm", "preserve_negative": True})
    events = p["journal"].store.events()
    changed = candidate(case, p, change={**p["args"]["change"], "after_ref": different_after})
    assert normal["candidate_brief"]["status"] == "ready_for_normal_execution"
    assert changed["candidate_brief"]["candidate_key"] != normal["candidate_brief"]["candidate_key"]
    assert changed["candidate_brief"]["status"] == "incomplete"
    assert changed["candidate_brief"]["normal_execution"]["execution_ref"] is None
    assert "actual candidate" in str(changed["candidate_brief"]["missing"])
    assert changed["revision_input"]["action"]["execution_ref"] == p["planned"]
    assert changed["revision_input"]["candidate_binding"]["action_candidate_method_refs"] == []
    assert p["journal"].store.events() == events
    saved = save(case, changed["candidate_brief"])
    assert cb.read_candidate_brief(publish(case), saved)["status"] == "incomplete"


@pytest.mark.parametrize("bind_action,bind_plan,ready", [(True, False, False), (False, True, False), (True, True, True)])
def test_actual_after_must_bind_the_same_method_in_action_and_comparison(case, prepared, bind_action, bind_plan, ready):
    from etalon.research.improvement import read_material
    p = prepared
    after = p["artifact"]({"read": "a_second_real_candidate", "preserve_negative": True})
    method = p["ref"](p["journal"].record(MethodVersion("second candidate", ("usage_method",),
        {"artifact_ref": after["record_id"]}, parents=(p["base"]["record_id"],))))
    args = copy.deepcopy(p["decision_args"])
    args["method_refs"].append(method)
    if bind_action:
        node = read_material(publish(case), p["planned"])["content"]
        node["method_refs"] = [p["base"], method]
        args["actions"][0]["execution_ref"] = save(case, node)
    if bind_plan:
        plan = read_material(publish(case), p["plan"])["content"]
        plan["detail"]["subjects"] = [method]
        plan["method_refs"] = [p["base"], method]
        args["evaluation_refs"] = [save(case, plan)]
    decision = save(case, rd.build_research_decision(publish(case), **args))
    result = candidate(case, p, decision_ref=decision, change={**p["args"]["change"], "after_ref": after})
    assert (result["candidate_brief"]["status"] == "ready_for_normal_execution") is ready
    assert bool(result["revision_input"]["candidate_binding"]["applicable_evaluation_refs"]) is ready
    assert (result["candidate_brief"]["normal_execution"]["execution_ref"] is not None) is bind_action


def test_verified_after_content_alias_preserves_existing_candidate_identity(case, prepared):
    from etalon.research.components import record_json
    p = prepared
    alias = p["ref"](record_json(p["journal"], {"preserve_negative": True, "read": "generic"},
        p["root"] / "alias", source_kind="fixture"))
    assert alias["record_id"] != p["after"]["record_id"]
    normal = candidate(case, p)["candidate_brief"]
    same = candidate(case, p, change={**p["args"]["change"], "after_ref": alias})["candidate_brief"]
    assert same["candidate_key"] == normal["candidate_key"]
    assert same["status"] == "ready_for_normal_execution"


def test_fixed_plan_also_needs_the_exact_base_comparator(case, prepared):
    from etalon.research.improvement import read_material
    p = prepared
    other = p["ref"](p["journal"].record(MethodVersion("other baseline", ("usage_method",),
        {"artifact_ref": p["before"]["record_id"]})))
    plan = read_material(publish(case), p["plan"])["content"]
    plan["detail"]["comparators"] = [other]
    plan["method_refs"] = [other, p["candidate"]]
    args = {**p["decision_args"], "evaluation_refs": [save(case, plan)], "method_refs": p["methods"] + [other]}
    decision = save(case, rd.build_research_decision(publish(case), **args))
    result = candidate(case, p, decision_ref=decision)
    assert result["candidate_brief"]["status"] == "incomplete"
    assert "exact base as comparator" in str(result["candidate_brief"]["missing"])


def test_workflow_edit_needs_exact_patch_and_selected_after_workflow(case, prepared):
    from etalon.research import improvement as imp
    from etalon.research.composition import WORKFLOW
    p = prepared
    before = {"schema": WORKFLOW, "name": "reader selection", "scope": "fixture readers", "method_refs": p["methods"],
        "nodes": [{"node_id": "read", "capability_id": None, "method_refs": p["methods"], "views": ["E"],
                   "purpose": "Read exact originals", "skip_when": "Already read"}], "edges": [], "supersedes": []}
    before_ref = save(case, before)
    def workflow_patch(skip):
        built = imp.build_method_patch(p["journal"], context_ref=p["args"]["context_ref"],
            opportunity_ref=p["args"]["opportunity_ref"], base_method_ref=p["base"], candidate_method_ref=p["candidate"],
            expected_change="Use exact reader when needed", source_id="research", workflow_before_ref=before_ref,
            workflow_edits=[{"collection": "nodes", "before": before["nodes"][0], "after": {**before["nodes"][0], "skip_when": skip}}])
        after_ref = save(case, built["workflow"])
        patch_ref = save(case, {**built["patch"], "workflow_after_ref": after_ref})
        return after_ref, patch_ref
    after_ref, patch_ref = workflow_patch("Already read and bytes still verified")
    kwargs = dict(object_kind="selection", target_part="nodes/read/skip_when", patch_ref=patch_ref,
                  change={"kind": "workflow", "before_ref": before_ref, "after_ref": after_ref})
    assert candidate(case, p, **kwargs)["candidate_brief"]["status"] == "incomplete"
    node = save(case, node_step(task_id="fixture-case", question="Read failure attribution", branch_id="normal", node_id="read",
        state="planned", reason="Selected exact workflow", workflow_ref=after_ref, method_refs=p["methods"], input_refs=[case[1]]))
    args = copy.deepcopy(p["decision_args"])
    args["actions"][0]["execution_ref"] = node
    decision = save(case, rd.build_research_decision(publish(case), **args))
    assert candidate(case, p, decision_ref=decision, **kwargs)["candidate_brief"]["status"] == "ready_for_normal_execution"
    other_after, other_patch = workflow_patch("A different untested policy")
    changed = candidate(case, p, decision_ref=decision, **{**kwargs, "patch_ref": other_patch,
        "change": {**kwargs["change"], "after_ref": other_after}})
    assert changed["candidate_brief"]["status"] == "incomplete"
    assert changed["candidate_brief"]["normal_execution"]["execution_ref"] is None


def test_normal_use_update_attributes_negative_to_fixed_plan_without_reproposing(case, prepared):
    p = prepared
    output = p["artifact"]({"errors": 4, "result": "fixture regression"})
    use = p["ref"](p["journal"].record(MethodUse(({"kind": "task", "id": "fixture-case", "scope": str(p["journal"].store.path)},),
        tuple({"method_version_ref": r["record_id"]} for r in p["methods"]), reason="Fixture normal use")))
    node = save(case, node_step(task_id="fixture-case", question="Read failure attribution", branch_id="normal",
        node_id="read", state="completed", reason="Synthetic normal-use receipt", method_refs=p["methods"], input_refs=[case[1]],
        output_refs=[output], receipt_refs=[output], use_refs=[use], supersedes=[p["planned"]]))
    evaluation = record_evaluation(p["journal"], p["plan"], directory=p["root"] / "content", source_id="research", source_kind="fixture",
        result_refs=[output["record_id"]], conclusion="Fixture regression; retain baseline", scope="fixture readers", use_refs=[use], node_refs=[node])
    args = dict(decision_ref=p["decision"], execution_refs=[node], evaluation_refs=[evaluation["step_ref"]],
        judgments=[{"prediction_id": "p", "status": "contradicted", "observation_refs": [output],
            "evaluation_refs": [evaluation["step_ref"]], "reason": "Same-check normal use regressed"}],
        disposition="no_change", summary="Retain baseline; no new proposal")
    update = save(case, rd.build_decision_update(publish(case), **args))
    before = p["journal"].store.events()
    guidance = guide(case, p, update_ref=update)
    assert guidance["evaluation_followup"]["disposition"] == "no_change"
    assert output in guidance["counter_refs"]
    assert output not in [d[k]["ref"] for d in guidance["diagnostic_differences"] for k in ("left", "right")]
    assert p["journal"].store.events() == before
    # Normal-use attribution can connect to the existing patch/evaluation/
    # experience chain without creating an adoption or a second workflow.
    from etalon.research import improvement as imp
    patch = imp.build_method_patch(p["journal"], context_ref=p["args"]["context_ref"],
        opportunity_ref=p["args"]["opportunity_ref"], base_method_ref=p["base"], candidate_method_ref=p["candidate"],
        expected_change="Fewer reader failures", source_id="research")["patch"]
    patch_ref = save(case, patch)
    raw_eval = evaluation["evaluation_ref"]
    experience = imp.record_experience(p["journal"], patch_ref=patch_ref, outcome="unresolved",
        lesson="Fixture failed comparison; keep original evidence", invalid_when="Other protocols", capability_id="reader",
        support_refs=[output], counter_refs=[output], evaluation_ref=raw_eval,
        directory=p["root"] / "content", source_id="research", source_kind="fixture")
    attributed = save(case, rd.build_decision_update(publish(case), **args, experience_refs=[experience]))
    connected = candidate(case, p, update_ref=attributed, patch_ref=patch_ref, evaluation_refs=[raw_eval],
                          experience_refs=[experience])["candidate_brief"]
    assert connected["evaluation_refs"] == [raw_eval] and connected["experience_refs"] == [experience]
    assert connected["adoption"] == "not_performed" and output in connected["counter_refs"]
    with pytest.raises(EvidenceError, match="exact decision update"):
        candidate(case, p, patch_ref=patch_ref, evaluation_refs=[raw_eval])
    with pytest.raises(EvidenceError, match="attributed update"):
        candidate(case, p, update_ref=update, patch_ref=patch_ref, experience_refs=[experience])
    # A changed denominator/check plan cannot be silently substituted after seeing the result.
    changed = copy.deepcopy(p["decision_args"])
    from etalon.research.improvement import read_material
    plan = read_material(publish(case), p["plan"])["content"]
    plan["detail"]["metrics"][0]["denominator"] = "successful cases only"
    changed["evaluation_refs"] = [save(case, plan)]
    other = save(case, rd.build_research_decision(publish(case), **changed))
    with pytest.raises(EvidenceError, match="exact evaluation plan"):
        rd.build_decision_update(publish(case), **{**args, "decision_ref": other})
    with pytest.raises(EvidenceError, match="different exact decision"):
        guide(case, p, decision_ref=other, update_ref=update)


@pytest.mark.parametrize("pointer", ["not/a/pointer", "/before/~2", "/before/~"])
def test_ambiguous_pointer_is_rejected(case, prepared, pointer):
    d = copy.deepcopy(prepared["difference"])
    d["left"]["pointer"] = pointer
    with pytest.raises(EvidenceError, match="pointer"):
        guide(case, prepared, differences=[d])
