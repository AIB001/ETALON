from copy import deepcopy

import pytest

from etalon.active.decision_benchmark import GROUPS, _posthoc, decision_benchmark
from etalon.active.store import CampaignStore, StateError


def _without_timing(report):
    result = deepcopy(report)
    for row in result["runs"]:
        row.pop("timing")
    return result


def test_equal_budget_ablations_and_idempotent_round_quota(tmp_path):
    arguments = {"seeds": (3,), "budget": 48, "size": 8, "rounds": 2}
    first = decision_benchmark(tmp_path, **arguments)
    second = decision_benchmark(tmp_path, **arguments)
    assert _without_timing(first) == _without_timing(second)
    assert [row["group"] for row in first["runs"]] == list(GROUPS)
    assert "neither chemical efficacy" in first["claim"]
    assert len({row["controls_hash"] for row in first["runs"]}) == 1
    assert all(row["rounds"] <= 2 for row in first["runs"])
    for row in first["runs"]:
        assert row["balance"]["budget"] == 48
        assert row["balance"]["spent"] <= 48
        assert row["balance"]["reserved"] == 0
        assert row["charged_warmstart_cost"] == 32
        assert row["spec"]["batch_size"] == 1
        assert row["spec"]["calibration_fraction"] == 0.15
        assert row["spec"]["max_kg_candidates"] == 512
        assert row["timing"]["persisted_round_planning_seconds"] >= 0
        assert row["timing"]["current_invocation_run_wall_seconds"] >= 0
        store = CampaignStore(row["database"], read_only=True)
        assert all(len(r["actions"]) == 1 for r in store.rounds())
        assert row["recommendations"]["evidence_backed"]["has_objective_evidence"]
        assert row["final"]["evidence_backed_oracle_regret"] is not None
        assert row["final"]["legacy_best_observed_regret"] is not None

    specs = {row["group"]: row["spec"] for row in first["runs"]}
    assert specs["mf_kg"]["policy"] == "mf_kg"
    assert specs["mf_kg"]["validity_mode"] == "none"
    assert specs["mf_kg"]["confirmation_reserve"] == 0
    for group in ("decision_no_validity", "decision_no_guard", "decision_global", "decision_aware"):
        assert specs[group]["policy"] == "decision_aware"
    assert specs["decision_no_validity"]["validity_mode"] == "none"
    assert specs["decision_no_validity"]["confirmation_reserve"] == 1
    assert specs["decision_no_guard"]["validity_mode"] == "local"
    assert specs["decision_no_guard"]["confirmation_reserve"] == 0
    assert specs["decision_global"]["validity_mode"] == "global"
    assert specs["decision_global"]["confirmation_reserve"] == 1
    assert specs["decision_aware"]["validity_mode"] == "local"
    assert specs["decision_aware"]["confirmation_reserve"] == 1
    paired = first["paired_differences"]
    assert "no significance" in paired["inference"]
    for comparator in paired["comparators"].values():
        assert comparator["provisional_oracle_regret"]["n"] == 1
        assert comparator["provisional_oracle_regret"]["sd_difference"] is None


def test_posthoc_does_not_conflate_recommendation_and_observed_regret():
    oracle = [{"candidate_id": key, "endpoint_id": "reference", "value": value}
              for key, value in (("a", -5.0), ("b", -1.0), ("c", -3.0))]
    observations = [
        {"admitted": True, "result": {"candidate_id": "a", "endpoint_id": "reference", "value": -5.0, "cost": 8}},
        {"admitted": True, "result": {"candidate_id": "b", "endpoint_id": "reference", "value": -1.0, "cost": 8}},
        {"admitted": False, "result": {"candidate_id": "c", "endpoint_id": "proxy", "value": -20.0, "cost": 1}},
    ]
    actions = [{"endpoint_id": "reference", "round_id": 0, "cost": 8, "decision": {}},
               {"endpoint_id": "reference", "round_id": 1, "cost": 8, "decision": {}},
               {"endpoint_id": "proxy", "round_id": 2, "cost": 1,
                "decision": {"reason": "protocol-calibration"}}]
    recommendations = {"provisional": {"candidate_id": "c"}, "evidence_backed": {"candidate_id": "b"}}
    result = _posthoc(oracle, "reference", recommendations, observations, actions)
    assert result["provisional_oracle_regret"] == 2.0
    assert result["evidence_backed_oracle_regret"] == 4.0
    assert result["legacy_best_observed_regret"] == 0.0
    assert result["provisional_unconfirmed"] is True
    assert result["invalid_spent"] == 1
    assert result["calibration_spent"] == 1
    assert result["objective_queries_including_warmstart"] == 2
    assert result["objective_queries_after_warmstart"] == 1
    with pytest.raises(ValueError, match="lacks admitted"):
        _posthoc(oracle, "reference", {"provisional": None, "evidence_backed": {"candidate_id": "c"}},
                 observations, actions)
    empty = _posthoc(oracle, "reference", {"provisional": None, "evidence_backed": None}, [], [])
    assert empty["provisional_oracle_regret"] is None
    assert empty["evidence_backed_oracle_regret"] is None
    assert empty["legacy_best_observed_regret"] is None
    assert empty["provisional_unconfirmed"] is None


def test_resume_refuses_changed_implementation_environment_before_more_queries(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("etalon.active.decision_benchmark")
    first = decision_benchmark(tmp_path, seeds=(0,), size=8, budget=48, rounds=1)
    path = first["runs"][0]["database"]
    before = CampaignStore(path, read_only=True).actions()
    monkeypatch.setattr(module, "version", lambda name: "different-environment")
    with pytest.raises(StateError, match="decision_benchmark_implementation.*changed"):
        decision_benchmark(tmp_path, seeds=(0,), size=8, budget=48, rounds=2)
    assert CampaignStore(path, read_only=True).actions() == before


def test_changing_hidden_oracle_cannot_change_first_action(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("etalon.active.decision_benchmark")
    original = module.synthetic_manifest
    tamper = [False]

    def manifest(**kwargs):
        result = original(**kwargs)
        if tamper[0]:
            warm = {r["result"]["candidate_id"] for r in result["initial"]}
            for row in result["oracle"]:
                if row["endpoint_id"] == "reference" and row["candidate_id"] not in warm:
                    row["value"] = -1000.0 - int(row["candidate_id"].split("-")[1])
        return result

    monkeypatch.setattr(module, "synthetic_manifest", manifest)
    arguments = {"seeds": (7,), "budget": 48, "size": 8, "rounds": 1}
    first = module.decision_benchmark(tmp_path / "original", **arguments)
    tamper[0] = True
    changed = module.decision_benchmark(tmp_path / "changed", **arguments)
    assert first["runs"][0]["controls_hash"] != changed["runs"][0]["controls_hash"]
    for before, after in zip(first["runs"], changed["runs"], strict=True):
        assert before["group"] == after["group"]
        assert before["last_action"]["candidate_id"] == after["last_action"]["candidate_id"]
        assert before["last_action"]["endpoint_id"] == after["last_action"]["endpoint_id"]
        assert before["last_action"]["reason"] == after["last_action"]["reason"]


@pytest.mark.parametrize("overrides, match", [
    ({"seeds": ()}, "distinct integer"),
    ({"seeds": (1, 1)}, "distinct integer"),
    ({"seeds": (True,)}, "distinct integer"),
    ({"rounds": 0}, "positive integer"),
    ({"rounds": True}, "positive integer"),
    ({"size": 513}, "8 to 512"),
    ({"size": 7}, "8 to 512"),
])
def test_invalid_benchmark_inputs_fail_before_writing(tmp_path, overrides, match):
    workspace = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match=match):
        decision_benchmark(workspace, **overrides)
    assert not workspace.exists()
