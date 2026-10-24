"""Software evidence grouping counterexamples; no scientific generalization."""
import copy
import pytest

from etalon.research import Artifact
from etalon.research.capability_profile import build_capability_profile
from etalon.research.evidence_refs import EvidenceError
from etalon.research.evaluation_contract import record_evaluation_artifact
from test_evaluation_contract import setup
from test_research_continuation import case, publish
from test_research_decision import prepared, build, store


def study(case, prepared, *, contract=None, case_id="one"):
    p = prepared
    if contract is None:
        contract, _ = setup(case)
    contract = copy.deepcopy(contract)
    contract["scope"] = "fixture only"
    cr = p["ref"](record_evaluation_artifact(p["journal"], contract, p["root"] / "evaluation", source_kind="fixture"))
    identity = p["artifact"]({"case_id": case_id, "family_id": "fixture-family"})
    decision = store(p, build(case, p, support_refs=[identity], evaluation_refs=[cr]))
    return dict(decision_ref=decision, update_ref=None, evaluation_contract_ref=cr,
                case_ref=identity, case_id=case_id, family_id="fixture-family", repeat_id="seed-1"), contract


def test_grouping_is_readonly_and_unknowns_do_not_become_independence(case, prepared):
    item, _ = study(case, prepared)
    events = prepared["journal"].store.events()
    profile = build_capability_profile(publish(case), studies=[item, {**item, "repeat_id": "seed-2"}], scope="fixture")
    assert profile["distinct_result_bundles"] == 1 and len(profile["duplicate_observations"]) == 1
    assert profile["unique_observations"] is None
    assert profile["groups"][0]["independent_case_count"] is None
    assert profile["groups"][0]["declared_case_clusters"] == 1
    assert profile["rows"][0]["status"] == "criteria_passed"
    assert "model identity unknown" in profile["rows"][0]["gaps"]
    assert profile["total_cost"] is None
    assert prepared["journal"].store.events() == events


def test_same_bytes_alias_context_and_stricter_rules_are_not_new_observations(case, prepared):
    original, contract = study(case, prepared)
    record = prepared["journal"].get(contract["inputs"][0]["ref"]["record_id"])
    alias = prepared["ref"](prepared["journal"].record(Artifact(**{**record, "bindings": {"label": "alias"}})))
    contract["inputs"][0]["ref"] = alias
    contract["inputs"].append({"name": "extra", "kind": "context", "ref": case[1]})
    contract["metrics"][0]["threshold"] = 0
    renamed, _ = study(case, prepared, contract=contract, case_id="renamed")
    result = build_capability_profile(publish(case), studies=[original, renamed], scope="fixture")
    assert result["distinct_result_bundles"] == 1
    # A stricter contrary evaluation is retained in full, never silently dropped.
    assert result["rows"][result["duplicate_observations"][0]["assessment_row"]]["status"] == "criteria_failed"
    assert len(result["groups"]) == 2
    reverse=build_capability_profile(publish(case), studies=[renamed, original], scope="fixture")
    assert {g["group_id"] for g in reverse["groups"]} == {g["group_id"] for g in result["groups"]}


def test_changed_result_with_shared_provenance_is_retained_without_independence_claim(case, prepared):
    original, contract = study(case, prepared)
    original_ref = contract["inputs"][0]["ref"]
    wrapper = prepared["artifact"]({"status": "completed", "protocol": "paired/1", "attempted": 24,
        "max_error": 1, "evidence_refs": [original_ref], "label": "different measurement with common input"})
    contract["inputs"][0]["ref"] = wrapper
    wrapped, _ = study(case, prepared, contract=contract, case_id="wrapped")
    result = build_capability_profile(publish(case), studies=[original, wrapped], scope="fixture")
    assert result["distinct_result_bundles"] == 2
    assert result["unique_observations"] is None
    assert [r["status"] for r in result["rows"]] == ["criteria_passed", "criteria_failed"]


@pytest.mark.parametrize("change", ["case", "digest", "contract"])
def test_forged_identity_or_unlinked_contract_cannot_enter_profile(case, prepared, change):
    item, contract = study(case, prepared)
    if change == "case":
        item["case_id"] = "another"
    elif change == "digest":
        item["case_ref"] = {**item["case_ref"], "record_digest": "0" * 64}
    else:
        contract["metrics"][0]["threshold"] = 0
        item["evaluation_contract_ref"] = prepared["ref"](record_evaluation_artifact(prepared["journal"], contract,
            prepared["root"] / "evaluation", source_kind="fixture"))
    with pytest.raises(EvidenceError):
        build_capability_profile(publish(case), studies=[item], scope="fixture")


def test_profile_bounds_explicit_studies(case, prepared):
    item, _ = study(case, prepared)
    with pytest.raises(EvidenceError, match="1..32"):
        build_capability_profile(publish(case), studies=[item] * 33, scope="fixture")


def test_large_valid_contract_uses_complete_bounded_reader(case, prepared):
    contract, _ = setup(case)
    contract["provenance"]["note"] = "Known retrospective fixture. " * 1500
    item, _ = study(case, prepared, contract=contract)
    profile = build_capability_profile(publish(case), studies=[item], scope="fixture")
    assert profile["rows"][0]["status"] == "criteria_passed"
