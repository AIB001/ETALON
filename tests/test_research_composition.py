"""Engineering counterexamples for optional workflow memory and evaluation links."""
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from etalon.research import MethodUse, MethodVersion, TaskSplit
from etalon.research.components import record_json
from etalon.research.composition import WORKFLOW, node_step, record_evaluation
from etalon.research.continuation import STEP, read_content, record_content, resume_research, validate_content
from etalon.research.evidence import query_evidence
from etalon.research.evidence_refs import EvidenceError
from etalon.research.method_revision import journal_ref
from test_research_continuation import case, checkpoint, publish, save


def ref(case, value):
    journal = case[0]
    return journal_ref(journal, journal.record(value), source_id="research")


def workflow(method_refs=()):
    return {"schema": WORKFLOW, "name": "fixture optional workflow", "scope": "engineering only",
            "method_refs": [], "supersedes": [], "nodes": [
                {"node_id": "read", "capability_id": "molquarry.data", "method_refs": list(method_refs),
                 "views": ["E", "J"], "purpose": "Read existing evidence", "skip_when": "Exact refs already suffice"},
                {"node_id": "expensive", "capability_id": "prism.general_fep_lab", "method_refs": [],
                 "views": ["X"], "purpose": "Proposed only", "skip_when": "No discriminating question"}],
            "edges": [{"from": "read", "to": "expensive", "kind": "control", "condition": "Only if justified"}]}


def node(case, **changes):
    _, evidence, _, _ = case
    return node_step(task_id="case", question="Does existing evidence suffice?", branch_id="available",
        node_id="read", capability_id="molquarry.data", state="reused", reason="Reuse the fixture evidence",
        input_refs=[evidence], output_refs=[evidence], receipt_refs=[evidence], **changes)


def test_workflow_partial_adoption_method_use_and_memory_filters(case):
    j, evidence, _, _ = case
    method = ref(case, MethodVersion("fixture-small-method", ("domain_skill",), {"fixture": "v1"}))
    wf = save(case, workflow([method]))
    use = ref(case, MethodUse(({"kind": "task", "id": "case", "scope": str(j.store.path)},),
        ({"method_version_ref": method["record_id"], "scope": "identity check only"},),
        visible_evidence_refs=(evidence["record_id"],), reason="Partial use, no full workflow"))
    used = save(case, node(case, method_refs=[method], workflow_ref=wf, use_refs=[use]))
    skipped = save(case, node_step(task_id="case", question="Does existing evidence suffice?", branch_id="expensive",
        node_id="expensive", capability_id="prism.general_fep_lab", workflow_ref=wf, state="skipped",
        reason="No new discrimination; capability not connected", input_refs=[evidence]))
    cp = save(case, checkpoint(case, used, step_refs=[used, skipped], method_refs=[method]))
    ws = publish(case)
    before = j.store.events()
    for filters in ({"capability_ids": ["molquarry.data"]}, {"node_ids": ["read"]},
                    {"branch_ids": ["available"]}, {"workflow_refs": [wf["record_id"]], "statuses": ["reused"]}):
        found = query_evidence(ws, filters={"source_ids": ["research"], **filters})
        assert [r["ref"] for r in found["items"]] == [used]
    context = resume_research(ws, cp)
    assert not context["issues"] and use in context["checked_refs"] and wf in context["checked_refs"]
    assert read_content(ws, skipped)["output_refs"] == []
    assert j.store.events() == before and not j.store.actions()


@pytest.mark.parametrize("edit,match", [
    (lambda w: w["nodes"].append(w["nodes"][0]), "duplicate"),
    (lambda w: w["edges"][0].update(to="missing"), "existing nodes"),
    (lambda w: w["edges"][0].update(kind="execute"), "relationship"),
    (lambda w: w["nodes"][0].update(views=["mandatory-stage"]), "views"),
])
def test_workflow_invalid_graph_rejected_before_writing(case, edit, match):
    j = case[0]
    content = workflow()
    edit(content)
    before = j.store.events()
    with pytest.raises(EvidenceError, match=match):
        save(case, content)
    assert j.store.events() == before


def test_skipped_plan_and_failed_are_not_measured_success(case):
    base = node(case)
    for state in ("skipped", "planned"):
        invalid = deepcopy(base)
        invalid["detail"]["state"] = state
        with pytest.raises(EvidenceError, match="manufacture"):
            validate_content(invalid)
    for state in ("failed", "partial", "waiting"):
        content = deepcopy(base)
        content["detail"]["state"] = state
        content["output_refs"] = []
        save(case, content)  # Actual diagnostic receipt; never a numeric negative label.
        content["detail"]["receipt_refs"] = []
        with pytest.raises(EvidenceError, match="actual receipt"):
            validate_content(content)
    assert not case[0].store.observations()


def test_node_rejects_wrong_workflow_and_wrong_task_use(case):
    j = case[0]
    wf = save(case, workflow())
    invalid = node(case, workflow_ref=wf)
    invalid["detail"]["capability_id"] = "prism.design"
    before = j.store.events()
    with pytest.raises(EvidenceError, match="capability"):
        save(case, invalid)
    assert j.store.events() == before
    use = ref(case, MethodUse(({"kind": "task", "id": "different", "scope": str(j.store.path)},), reason="No methods"))
    with pytest.raises(EvidenceError, match="same task"):
        save(case, node(case, use_refs=[use]))
    disconnected = node(case)
    disconnected["detail"]["capability_id"] = "prism.general_fep_lab"
    with pytest.raises(EvidenceError, match="not-connected"):
        save(case, disconnected)


def evaluation_plan(case, target="method"):
    j, evidence, _, root = case
    role = "usage_method" if target in {"branch_selection", "workflow_composition"} else "domain_skill"
    subjects = [ref(case, MethodVersion("candidate-" + target, (role,), {"fixture": "candidate"}))]
    comparators = [ref(case, MethodVersion("baseline-" + target, (role,), {"fixture": "baseline"}))]
    split = ref(case, TaskSplit(("case",), ("validation",), ("transfer",),
        {"case": "a", "validation": "b", "transfer": "c"}, "fixture correctness"))
    return {"schema": STEP, "task_id": "case", "question": "What evidence would distinguish the methods?",
        "branch_id": "compare", "operation": "evaluation_plan", "reason": "Concrete counterexample",
        "input_refs": [evidence], "output_refs": [], "method_refs": subjects + comparators,
        "accounting_refs": [], "source_watermarks": [], "supersedes": [],
        "detail": {"target": target, "subjects": subjects, "comparators": comparators,
            "protocol_ref": evidence, "task_split_ref": split, "initial_conditions_ref": evidence,
            "use_refs": [], "trigger_refs": [evidence], "metrics": [
                {"name": "correct", "family": "regression", "unit": "fraction",
                 "denominator": "completed comparable cases", "missing_policy": "report unknown separately"},
                {"name": "positive_retained", "family": "retention", "unit": "fraction",
                 "denominator": "complete reference positives", "missing_policy": "incomplete panel remains unknown"}]}}


@pytest.mark.parametrize("target", ["method", "branch_selection", "workflow", "workflow_composition"])
def test_each_rsi_target_reuses_existing_evaluation_and_is_idempotent(case, target):
    j, evidence, _, root = case
    plan = save(case, evaluation_plan(case, target))
    kwargs = dict(directory=root / "content", source_id="research", source_kind="fixture",
                  result_refs=(evidence["record_id"],), conclusion="Diagnostic only; no adoption", scope="fixture")
    result = record_evaluation(j, plan, **kwargs)
    before = j.store.events()
    assert record_evaluation(j, plan, **kwargs) == result and j.store.events() == before
    assert result["promotion"] == "not_performed" and result["optimization"] == "not_triggered"
    cp = save(case, checkpoint(case, plan, step_refs=[plan, result["step_ref"]]))
    ws = publish(case)
    context = resume_research(ws, cp)
    assert not context["issues"] and result["evaluation_ref"] in context["checked_refs"]
    assert [v["ref"] for v in query_evidence(ws, filters={"evaluation_targets": [target]})["items"]] == [plan]
    assert j.store.balance()["spent"] == 0 and not j.store.actions()


def test_new_comparison_entry_does_not_relax_independent_validation(case):
    j, evidence, _, root = case
    plan = save(case, evaluation_plan(case))
    costs = record_json(j, {"money": None}, root / "content", source_kind="fixture", bindings={"kind": "accounting"})
    before = j.store.events()
    with pytest.raises(Exception, match="frozen-policy validation receipt"):
        record_evaluation(j, plan, directory=root / "content", source_id="research", source_kind="fixture",
            result_refs=(evidence["record_id"],), accounting_refs=(costs,), assessment_role="independent_validation",
            conclusion="Should fail", scope="fixture")
    assert j.store.events() == before


def test_comparison_plan_rejects_self_baseline_and_unversioned_targets(case):
    value = evaluation_plan(case)
    value["detail"]["comparators"] = value["detail"]["subjects"]
    with pytest.raises(EvidenceError, match="distinct"):
        save(case, value)
    value = evaluation_plan(case, "branch_selection")
    value["detail"]["subjects"] = [case[1]]
    with pytest.raises(EvidenceError, match="record type"):
        save(case, value)


def test_scientific_subject_names_do_not_become_method_record_dependencies():
    from etalon.research.evidence import _references
    claim = {"schema": "etalon-research-claim/1", "subjects": ["phi", "phi0"], "support_refs": []}
    assert _references(claim) == []
    evaluation = {"schema": "etalon-method-evaluation/1", "subjects": ["methodversion-a"], "comparators": ["methodversion-b"]}
    assert _references(evaluation) == ["methodversion-a", "methodversion-b"]
