"""Synthetic integration of existing decisions, node receipts and normal output.

These tests exercise no molecular tool and claim no scientific validation.
"""
import copy

import pytest

from etalon.research.action_learning import gather_action_basis, compare_action_feedback
from etalon.research.composition import node_step
from etalon.research.evidence_refs import EvidenceError
from test_research_continuation import case, publish, save
from test_research_decision import prepared, build, store, observed


def brief(case, p, *, checks=None, predictions=None, **kwargs):
    checks = checks or [{"pointer": "/retained", "expected": True}]
    expectation = dict(observable="retained count", expected_statement="All fixture evidence preserved",
        checks=checks, next_if_match="Reuse the retained route; inspect reaction conditions next",
        next_if_different="Inspect the exact missing precursor before considering more search",
        next_if_unknown="Acquire the missing precursor evidence; retain the uncertainty")
    expectation_ref = p["artifact"](expectation)
    args = copy.deepcopy(p["args"])
    args["scientific_inputs"] = [{"ref": expectation_ref, "use": "conditioning", "reason": "Pre-execution structured expectation"}]
    args["predictions"][0]["basis_refs"].append(expectation_ref)
    if predictions:
        args["predictions"][0].update(predictions)
        expectation["observable"] = args["predictions"][0]["observable"]
        expectation["expected_statement"] = args["predictions"][0]["expected"]
        expectation_ref = p["artifact"](expectation)
        args["scientific_inputs"][0]["ref"] = expectation_ref
        args["predictions"][0]["basis_refs"][-1] = expectation_ref
    decision_ref = store(p, build(case, p, **args))
    basis = gather_action_basis(publish(case), decision_ref=decision_ref, action_id="read",
        conditions={"target": "fixture", "stock": "fixed"},
        expectations=[dict(prediction_id="p1", ref=expectation_ref, pointer="")], **kwargs)
    return decision_ref, p["artifact"](basis), basis


def output_node(case, p, output, *, state="completed", **changes):
    output_ref = p["artifact"](output)
    node = save(case, node_step(task_id="case", question="Fixture discrimination", branch_id="normal", node_id="read",
        state=state, reason="Fixture ordinary output and receipt", input_refs=[case[1]], output_refs=[output_ref],
        method_refs=p["args"]["method_refs"], receipt_refs=[output_ref], supersedes=[p["planned"]], **changes))
    return output_ref, node


def test_normal_node_feedback_is_useful_readonly_and_replay_is_not_a_new_execution(case, prepared):
    p = prepared
    decision, basis_ref, basis = brief(case, p)
    _, output, node, _ = observed(case, p, decision)
    ws = publish(case)
    events = p["journal"].store.events()
    result = compare_action_feedback(ws, basis_ref=basis_ref, execution_ref=node, observation_ref=output)
    assert result["comparisons"][0]["comparison"] == "match"
    assert "reaction conditions" in result["next_choice_basis"]["hints"][0]["consider"]
    assert result["scientific_success"] == "not_inferred_from_value_match"
    assert compare_action_feedback(ws, basis_ref=basis_ref, execution_ref=node, observation_ref=output) == result
    assert p["journal"].store.events() == events
    assert not p["journal"].store.actions()
    assert basis["prospective_ignorance"] == "not_established"


@pytest.mark.parametrize("output,presence,status", [
    ({}, "missing", "unknown"), ({"retained": None}, "null", "unknown"),
    ({"retained": False}, "observed", "different"), ({"retained": 0}, "observed", "different"),
    ({"retained": True}, "observed", "match"),
])
def test_unknown_is_not_false_and_false_is_not_zero(case, prepared, output, presence, status):
    _, basis_ref, _ = brief(case, prepared)
    output_ref, node = output_node(case, prepared, output)
    result = compare_action_feedback(publish(case), basis_ref=basis_ref, execution_ref=node, observation_ref=output_ref)
    comparison = result["comparisons"][0]
    assert comparison["comparison"] == status
    assert comparison["checks"][0]["observation"]["presence"] == presence


def test_supplier_listing_alone_cannot_satisfy_conjunctive_identity_and_reaction_role(case, prepared):
    checks = [{"pointer": "/observation/" + name, "expected": True}
              for name in ("identity_match", "catalog_listing_supported", "reaction_role_supported")]
    _, basis_ref, _ = brief(case, prepared, checks=checks)
    output, node = output_node(case, prepared, {"observation": {
        "identity_match": True, "catalog_listing_supported": True, "reaction_role_supported": None}})
    result = compare_action_feedback(publish(case), basis_ref=basis_ref, execution_ref=node, observation_ref=output)
    assert result["comparisons"][0]["comparison"] == "unknown"
    assert "missing precursor evidence" in result["next_choice_basis"]["hints"][0]["consider"]


def test_failed_node_partial_values_do_not_produce_success_hint(case, prepared):
    _, basis_ref, _ = brief(case, prepared)
    output, node = output_node(case, prepared, {"retained": True}, state="failed")
    result = compare_action_feedback(publish(case), basis_ref=basis_ref, execution_ref=node, observation_ref=output)
    assert result["comparisons"][0]["checks"][0]["comparison"] == "match"
    assert result["comparisons"][0]["comparison"] == "unknown"


def test_output_from_unrelated_normal_action_is_rejected(case, prepared):
    _, basis_ref, _ = brief(case, prepared)
    output, node = output_node(case, prepared, {"retained": True})
    other = prepared["artifact"]({"retained": True, "unrelated": True})
    with pytest.raises(EvidenceError, match="exact normal node output"):
        compare_action_feedback(publish(case), basis_ref=basis_ref, execution_ref=node, observation_ref=other)
    with pytest.raises(EvidenceError, match="unexecuted"):
        compare_action_feedback(publish(case), basis_ref=basis_ref, execution_ref=prepared["planned"])


def test_saved_brief_cannot_replace_the_predictions_structured_expectation(case, prepared):
    _, basis_ref, basis = brief(case, prepared)
    output, node = output_node(case, prepared, {"retained": False})
    basis["expectations"][0]["expectation"]["checks"][0]["expected"] = False
    tampered = prepared["artifact"](basis)
    with pytest.raises(EvidenceError, match="original prediction basis"):
        compare_action_feedback(publish(case), basis_ref=tampered, execution_ref=node, observation_ref=output)


def test_expectation_is_linked_to_actual_prediction_and_exact_semantics(case, prepared):
    decision, _, basis = brief(case, prepared)
    selector = {k: basis["expectations"][0][k] for k in ("prediction_id", "ref", "pointer")}
    other = prepared["artifact"]({"arbitrary": "comparison, not a declared prediction"})
    with pytest.raises(EvidenceError, match="declared prediction basis"):
        gather_action_basis(publish(case), decision_ref=decision, action_id="read", conditions={"stock": "fixed"},
                            expectations=[{**selector, "ref": other}])
    changed = copy.deepcopy(prepared["args"])
    changed["predictions"][0]["basis_refs"].append(selector["ref"])
    changed["scientific_inputs"] = [{"ref": selector["ref"], "use": "conditioning", "reason": "Fixture expected readout"}]
    changed["predictions"][0]["expected"] = "A different scientific expectation"
    changed_ref = store(prepared, build(case, prepared, **changed))
    with pytest.raises(EvidenceError, match="exact decision prediction"):
        gather_action_basis(publish(case), decision_ref=changed_ref, action_id="read", conditions={"stock": "fixed"},
                            expectations=[selector])


def test_selected_rule_scope_and_negative_unknown_experience_are_preserved(case, prepared):
    p = prepared
    rule = p["artifact"]({"scope": "retained fixture routes only", "conditions": {"stock": "fixed"},
        "counter_refs": [case[1]], "rules": [{"statement": "A catalog listing is not a lab synthesis"}]})
    experience = dict(schema="etalon-method-experience/1", task_id="case", scope="fixture only",
        conditions={"stock": "older", "runtime": "fixed"}, method_refs=p["args"]["method_refs"], supersedes=[],
        patch_ref=case[1], evaluation_ref=None, resolution_ref=None, outcome="unresolved",
        lesson="More iterations did not resolve missing stock in this fixture", invalid_when="A new reaction branch is available",
        support_refs=[], counter_refs=[case[1]], capability_id="fixture")
    experience_ref = p["artifact"](experience)
    _, _, basis = brief(case, p, rule_selectors=[dict(ref=rule, pointer="/rules/0")], experience_refs=[experience_ref])
    assert basis["rules"][0]["source_context"]["scope"] == "retained fixture routes only"
    assert basis["rules"][0]["source_context"]["counter_refs"] == [case[1]]
    selected = basis["experiences"][0]
    assert selected["experience"]["counter_refs"] == [case[1]]
    assert selected["condition_comparison"] == {"status": "different", "different": ["stock"], "unknown": ["runtime"]}
    assert selected["invalid_when_check"] == "host_review_required"


def test_local_rule_fragment_retains_ancestor_limits_without_overriding_source_conditions(case, prepared):
    rule = prepared["artifact"]({"conditions": {"stock": "older"}, "rules": [{
        "scope": "only a retained route", "counter_refs": [case[1]],
        "applicability": {"conditions": {"target": "fixture"}, "unknowns": []},
        "statement": "A catalog listing does not establish reaction feasibility"}]})
    _, _, basis = brief(case, prepared, rule_selectors=[dict(ref=rule, pointer="/rules/0/statement")])
    selected = basis["rules"][0]
    assert selected["value"].startswith("A catalog")
    assert selected["condition_comparison"]["status"] == "different"
    assert selected["condition_comparison"]["different"] == ["stock"]
    assert selected["context_chain"][1]["context"]["counter_refs"] == [case[1]]
    assert selected["context_chain"][1]["context"]["scope"] == "only a retained route"
    assert len(selected["condition_comparison"]["by_context"]) == 2


@pytest.mark.parametrize("pointer,presence", [("/rules/absent", "missing"), ("/rules/null", "null")])
def test_unavailable_local_rule_cannot_acquire_applicability_from_parent(case, prepared, pointer, presence):
    rule = prepared["artifact"]({"conditions": {"stock": "fixed"}, "rules": {"null": None}})
    _, _, basis = brief(case, prepared, rule_selectors=[dict(ref=rule, pointer=pointer)])
    assert basis["rules"][0]["presence"] == presence
    assert basis["rules"][0]["condition_comparison"]["status"] == "unknown"


@pytest.mark.parametrize("changed", ["input_refs", "method_refs", "capability_id", "node_id", "branch_id", "supersedes"])
def test_same_looking_result_cannot_be_attributed_to_a_changed_normal_action(case, prepared, changed):
    _, basis_ref, _ = brief(case, prepared)
    output = prepared["artifact"]({"retained": True})
    args = dict(task_id="case", question="Fixture discrimination", branch_id="normal", node_id="read",
        state="completed", reason="Fixture changed execution", input_refs=[case[1]], output_refs=[output],
        method_refs=prepared["args"]["method_refs"], receipt_refs=[output], supersedes=[prepared["planned"]])
    args[changed] = {"input_refs": [output], "method_refs": [], "capability_id": "another-capability",
                     "node_id": "another-node", "branch_id": "another-branch", "supersedes": []}[changed]
    node = save(case, node_step(**args))
    with pytest.raises(EvidenceError, match="selected normal action"):
        compare_action_feedback(publish(case), basis_ref=basis_ref, execution_ref=node, observation_ref=output)


def test_missing_output_and_missing_expectation_remain_unknown(case, prepared):
    _, basis_ref, _ = brief(case, prepared, checks=[{"pointer": "/retained", "expected": None}])
    output, node = output_node(case, prepared, {"retained": True})
    ws = publish(case)
    for observation in (None, output):
        result = compare_action_feedback(ws, basis_ref=basis_ref, execution_ref=node, observation_ref=observation)
        assert result["comparisons"][0]["comparison"] == "unknown"
        assert result["unresolved_prediction_ids"] == ["p1"]


@pytest.mark.parametrize("field", ["task_id", "scope"])
def test_saved_basis_cannot_relabel_the_real_execution_scope(case, prepared, field):
    _, _, basis = brief(case, prepared)
    output, node = output_node(case, prepared, {"retained": True})
    basis[field] = "different scientific task or applicability scope"
    changed = prepared["artifact"](basis)
    with pytest.raises(EvidenceError, match="task/scope mismatch"):
        compare_action_feedback(publish(case), basis_ref=changed, execution_ref=node, observation_ref=output)
