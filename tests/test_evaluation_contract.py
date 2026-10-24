"""Rule replay counterexamples; fixtures establish software behavior only."""
from pathlib import Path

import pytest

from etalon.active.schema import digest
from etalon.research.components import record_json
from etalon.research.evaluation_contract import (
    CONTRACT, build_evaluation_contract, evaluate_contract, record_evaluation_artifact,
    validate_evaluation_contract,
)
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.method_revision import journal_ref
from test_research_continuation import case, publish


def selector(pointer, name="observed"):
    return dict(input=name, pointer=pointer)


def metric(name="error", family="regression", pointer="/max_error", threshold=0.01):
    return dict(name=name, family=family, measure=dict(operation="value", left=selector(pointer), right=None),
                operator="le", threshold=threshold, unit="angstrom", denominator="all attempted pairs, including failures",
                denominator_selector=selector("/attempted"), missing_policy="insufficient")


def setup(case, **changes):
    journal, _, _, root = case
    observed = dict(status="completed", protocol="paired/1", attempted=24, max_error=0.001,
                    retained=20, baseline_retained=20, candidate_seconds=10, baseline_seconds=20)
    observed.update(changes)
    rid = record_json(journal, observed, root / "inputs", source_kind="fixture")
    ref = journal_ref(journal, rid, source_id="research")
    contract = build_evaluation_contract(task_id="case", scope="synthetic paired observations",
        conditions={"target": "fixture"}, protocol_ref=ref, initial_conditions_ref=ref,
        inputs=[dict(name="observed", kind="result", ref=ref)], metrics=[metric()],
        comparability=[dict(check_id="protocol", selector=selector("/protocol"), expected="paired/1")],
        required_checks=[dict(check_id="execution", selector=selector("/status"), expected="completed")],
        provenance=dict(mode="retrospective", note="Rules authored after fixture outcomes", basis_refs=[]))
    return contract, publish(case)


def test_pass_is_scoped_read_only_and_archive_reuses_ordinary_artifact(case):
    contract, workspace = setup(case)
    journal, _, _, root = case
    before = file_identity(journal.store.path)
    result = evaluate_contract(workspace, contract)
    assert result["status"] == "criteria_passed"
    assert result["families"]["cost"]["status"] == "not_declared"
    assert result["freeze_status"] == "retrospective_not_prospective"
    assert result["scientific_truth"] == "not_certified"
    assert result["adoption"] == result["execution"] == "not_performed"
    assert file_identity(journal.store.path) == before and not journal.store.actions()
    rid = record_evaluation_artifact(journal, result, root / "evaluation", source_kind="fixture", workspace=workspace)
    workspace = publish(case)
    events = journal.store.events()
    assert record_evaluation_artifact(journal, result, root / "evaluation", source_kind="fixture", workspace=workspace) == rid
    assert journal.store.events() == events
    crid = record_evaluation_artifact(journal, contract, root / "evaluation", source_kind="fixture")
    result2 = evaluate_contract(publish(case), contract_ref=journal_ref(journal, crid, source_id="research"))
    assert result2["status"] == result["status"]
    assert result2["contract_digest"] == digest(contract)


@pytest.mark.parametrize("changes,status", [
    ({"max_error": 1}, "criteria_failed"), ({"max_error": None}, "insufficient"),
    ({"max_error": True}, "insufficient"), ({"attempted": 0}, "insufficient"),
    ({"status": "failed", "max_error": 0}, "criteria_failed"),
    ({"status": None}, "insufficient"), ({"protocol": "other/2"}, "incomparable"),
])
def test_no_missing_failed_or_incomparable_pass(case, changes, status):
    contract, workspace = setup(case, **changes)
    assert evaluate_contract(workspace, contract)["status"] == status


def test_absent_member_and_zero_divisor_are_not_imputed(case):
    contract, workspace = setup(case, baseline_seconds=0)
    contract["metrics"][0]["measure"] = dict(operation="ratio", left=selector("/candidate_seconds"), right=selector("/baseline_seconds"))
    assert evaluate_contract(workspace, contract)["metrics"][0]["reason"] == "zero ratio divisor"
    contract["metrics"][0]["measure"] = dict(operation="value", left=selector("/absent"), right=None)
    assert evaluate_contract(workspace, contract)["status"] == "insufficient"


def test_cost_pass_cannot_erase_quality_failure_and_all_families_stay_separate(case):
    contract, workspace = setup(case, max_error=1)
    cost = metric("cost", "cost", "/candidate_seconds", 0.8)
    cost["unit"] = "ratio"
    cost["measure"] = dict(operation="ratio", left=selector("/candidate_seconds"), right=selector("/baseline_seconds"))
    contract["metrics"] += [cost, metric("retention", "retention", "/retained", 24), metric("funnel", "funnel", "/attempted", 24)]
    out = evaluate_contract(workspace, contract)
    assert out["status"] == "criteria_failed"
    assert out["families"]["regression"]["status"] == "criteria_failed"
    assert out["families"]["cost"]["status"] == "criteria_passed"
    assert set(out["families"]) == {"regression", "retention", "funnel", "cost"}


@pytest.mark.parametrize("change", [
    lambda c: c["metrics"][0].update(missing_policy="drop"),
    lambda c: c["metrics"][0]["measure"].update(operation="eval"),
    lambda c: c["metrics"][0].update(threshold=float("nan")),
    lambda c: c["metrics"][0].update(threshold=True),
    lambda c: c["metrics"][0]["measure"]["left"].update(input="unknown"),
    lambda c: c.update(required_checks=[]),
    lambda c: c.update(code="__import__('os').system('anything')"),
    lambda c: c["provenance"].update(mode="prospective_frozen"),
])
def test_declarative_allowlist_and_nonempty_obligations(case, change):
    contract, _ = setup(case)
    change(contract)
    with pytest.raises(EvidenceError):
        validate_evaluation_contract(contract)


def test_digest_tamper_hidden_and_unknown_source_cannot_pass(case):
    contract, workspace = setup(case)
    contract["inputs"][0]["ref"]["record_digest"] = "0" * 64
    result = evaluate_contract(workspace, contract)
    assert result["status"] == "insufficient" and result["issues"]
    contract["inputs"][0]["ref"]["source_id"] = "undeclared"
    assert evaluate_contract(workspace, contract)["status"] == "insufficient"


def test_changed_original_bytes_rejected(case):
    contract, workspace = setup(case)
    path = Path(case[0].get(contract["inputs"][0]["ref"]["record_id"])["path"])
    path.write_text('{"status":"completed","max_error":0}', encoding="utf-8")
    result = evaluate_contract(workspace, contract)
    assert result["status"] == "insufficient"


def test_report_status_cannot_be_forged_for_archival(case):
    contract, workspace = setup(case, max_error=1)
    result = evaluate_contract(workspace, contract)
    result["status"] = "criteria_passed"
    with pytest.raises(EvidenceError, match="differs"):
        record_evaluation_artifact(case[0], result, case[3] / "evaluation", source_kind="fixture", workspace=workspace)


def test_predeclaration_claim_does_not_certify_chronology(case):
    contract, workspace = setup(case)
    contract["provenance"] = dict(mode="predeclared", note="Caller claim only", basis_refs=[contract["protocol_ref"]])
    result = evaluate_contract(workspace, contract)
    assert result["freeze_status"] == "predeclaration_claim_chronology_not_verified"
    assert result["independent_validation"] == "not_established_by_replay"


def test_wrong_method_evaluation_type_is_incomparable(case):
    contract, workspace = setup(case)
    contract["evaluation_ref"] = contract["protocol_ref"]
    assert evaluate_contract(workspace, contract)["status"] == "incomparable"


def test_large_input_remains_an_explicit_gap(case):
    contract, workspace = setup(case, padding="x" * (1024 * 1024))
    assert evaluate_contract(workspace, contract)["status"] == "insufficient"


def test_paged_original_and_explicit_array_denominator(case):
    contract, workspace = setup(case, padding="x" * 40000, cohort=list(range(24)))
    contract["metrics"][0]["denominator_selector"] = dict(input="observed", pointer="/cohort", reducer="count")
    result = evaluate_contract(workspace, contract)
    assert result["status"] == "criteria_passed"
    assert result["metrics"][0]["denominator_observed"] == 24


def test_sealed_original_is_not_read_through_explicit_reference(case):
    from dataclasses import replace
    from etalon.research.schema import Artifact
    contract, _ = setup(case)
    original = Artifact(**case[0].get(contract["inputs"][0]["ref"]["record_id"]))
    rid = case[0].record(replace(original, role="sealed_holdout"))
    contract["inputs"][0]["ref"] = journal_ref(case[0], rid, source_id="research")
    report = evaluate_contract(publish(case), contract)
    assert report["status"] == "insufficient"
    assert report["metrics"][0]["observed"] is None


def test_composition_and_method_evaluation_binding_checks_exact_inputs(case):
    from test_research_composition import evaluation_plan
    from test_research_continuation import save
    from etalon.research.composition import record_evaluation
    contract, _ = setup(case)
    plan = evaluation_plan(case)
    d = plan["detail"]
    d["protocol_ref"] = contract["protocol_ref"]
    d["initial_conditions_ref"] = contract["initial_conditions_ref"]
    d["metrics"] = [{k: m[k] for k in ("name", "family", "unit", "denominator", "missing_policy")} for m in contract["metrics"]]
    plan_ref = save(case, plan)
    out = record_evaluation(case[0], plan_ref, directory=case[3] / "content", source_id="research", source_kind="fixture",
        result_refs=[contract["inputs"][0]["ref"]["record_id"]], conclusion="fixture only", scope="fixture")
    contract.update(plan_ref=plan_ref, evaluation_ref=out["evaluation_ref"], subjects=d["subjects"], comparators=d["comparators"])
    workspace = publish(case)
    assert evaluate_contract(workspace, contract)["status"] == "criteria_passed"
    contract["metrics"][0]["unit"] = "nanometer"
    result = evaluate_contract(workspace, contract)
    assert result["status"] == "incomparable"
    assert any("metric declarations differ" in row.get("reason", "") for row in result["bindings"])


def test_method_external_rule_digest_binding(case):
    from etalon.research.method_contract import CONTRACT as METHOD_CONTRACT
    from etalon.research.evaluation_contract import evaluation_rules_digest
    contract, _ = setup(case)
    method = dict(schema=METHOD_CONTRACT, name="fixture declared method", parameters=[], required_checks=["execution"],
        evaluation={"schema": CONTRACT, "rules_digest": evaluation_rules_digest(contract)}, supersedes=[])
    rid = record_json(case[0], method, case[3] / "method", source_kind="fixture")
    contract["method_contract_ref"] = journal_ref(case[0], rid, source_id="research")
    workspace = publish(case)
    assert evaluate_contract(workspace, contract)["status"] == "criteria_passed"
    contract["metrics"][0]["threshold"] = 10
    result = evaluate_contract(workspace, contract)
    assert result["status"] == "incomparable"
    assert any("rules differ" in row.get("reason", "") for row in result["bindings"])
