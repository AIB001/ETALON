"""Dependency-guided feedback selection; fixtures are not molecular evidence."""
import copy

import pytest

from etalon.research import research_model as rm
from etalon.research.action_learning import compare_action_feedback, gather_rule_revision_context
from etalon.research.evidence_refs import EvidenceError
from test_research_continuation import case, publish
from test_research_decision import prepared
from test_action_learning import brief, output_node


def setup(case, p, *, value=False, bound=True):
    decision, basis, _ = brief(case, p)
    output, node = output_node(case, p, {"retained": value})
    feedback = p["artifact"](compare_action_feedback(publish(case), basis_ref=basis,
        execution_ref=node, observation_ref=output))
    rule = dict(rule_id="prepare", statement="Fixture preparation supplies a valid input",
        judgment_kind="diagnosis_prediction", status="active",
        applicability={"conditions": {"target": "fixture"}, "unknowns": []},
        checker_ref=None, predictor_ref=None, verdict_map=[], dependencies=[],
        support_refs=[], counter_refs=[case[1]], issue_ids=[])
    rules = [rule, {**rule, "rule_id": "retention", "dependencies": ["prepare"]},
             {**rule, "rule_id": "downstream", "dependencies": ["retention"]},
             {**rule, "rule_id": "unrelated", "dependencies": []}]
    model = p["artifact"](rm.build_research_model(publish(case), model_id="fixture-model", task_id="case",
        scope="fixture only", conditions={"target": "fixture"}, rules=rules,
        prediction_bindings=[dict(decision_ref=decision, prediction_id="p1", rule_ids=["retention"])] if bound else [],
        counter_refs=[case[1]]))
    return feedback, model


def test_failed_prediction_reads_its_assumptions_without_suppressing_other_work(case, prepared):
    feedback, model = setup(case, prepared)
    ws = publish(case)
    events = prepared["journal"].store.events()
    result = gather_rule_revision_context(ws, feedback_ref=feedback, model_ref=model)
    assert [r["rule"]["rule_id"] for r in result["rules"]] == ["prepare", "retention"]
    assert result["potentially_affected_rule_ids"] == ["downstream"]
    assert result["unselected_rule_ids"] == ["downstream", "unrelated"]
    assert result["counter_refs"] == [case[1]]
    assert not result["rsi_triggered"]
    assert prepared["journal"].store.events() == events


def test_matching_feedback_can_be_selected_for_opportunity_learning_but_is_not_automatically_reopened(case, prepared):
    feedback, model = setup(case, prepared, value=True)
    default = gather_rule_revision_context(publish(case), feedback_ref=feedback, model_ref=model)
    assert default["rules"] == []
    explicit = gather_rule_revision_context(publish(case), feedback_ref=feedback, model_ref=model, prediction_ids=["p1"])
    assert explicit["discrepancies"][0]["comparison"] == "match"
    assert explicit["scientific_benefit"] == "not_inferred"


def test_unbound_prediction_is_visible_without_inventing_a_rule_cause(case, prepared):
    feedback, model = setup(case, prepared, value=None, bound=False)
    result = gather_rule_revision_context(publish(case), feedback_ref=feedback, model_ref=model)
    assert result["unbound_prediction_ids"] == ["p1"]
    assert result["rules"] == []
    assert result["discrepancies"][0]["comparison"] == "unknown"


def test_changed_feedback_cannot_be_used_as_a_false_learning_signal(case, prepared):
    feedback, model = setup(case, prepared)
    from etalon.research.improvement import read_material
    changed = copy.deepcopy(read_material(publish(case), feedback)["content"])
    changed["comparisons"][0]["comparison"] = "match"
    forged = prepared["artifact"](changed)
    with pytest.raises(EvidenceError, match="original execution"):
        gather_rule_revision_context(publish(case), feedback_ref=forged, model_ref=model)
