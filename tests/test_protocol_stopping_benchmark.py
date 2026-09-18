import importlib
import json
from copy import deepcopy

import pytest

from etalon.active.protocol_stopping_benchmark import (
    ECONOMIC_STOP,
    GROUPS,
    OPPORTUNITY_COSTS,
    SCENARIOS,
    _decision,
    _posthoc,
    main,
    protocol_stopping_benchmark,
)


def test_all_prespecified_costs_and_groups_are_reported_deterministically():
    arguments = {"seeds": (0, 2), "budget": 3.5, "max_trials": 8}
    report = protocol_stopping_benchmark(**arguments)
    assert report == protocol_stopping_benchmark(**arguments)
    assert report["opportunity_costs"] == [0.02, 0.1, 0.3]
    assert report["synthetic"] is True
    assert len(report["runs"]) == 2 * len(SCENARIOS) * len(OPPORTUNITY_COSTS) * len(GROUPS)
    assert len(report["summary"]) == len(SCENARIOS) * len(OPPORTUNITY_COSTS)
    json.dumps(report, allow_nan=False)
    for seed in arguments["seeds"]:
        for scenario in SCENARIOS:
            runs = [row for row in report["runs"] if row["seed"] == seed and row["scenario"] == scenario]
            assert len({row["controls_hash"] for row in runs}) == 1
            assert len({row["public_catalog_hash"] for row in runs}) == 1
            for row in runs:
                assert row["spent"] <= row["budget"]
                assert row["queries"] <= arguments["max_trials"]
                assert row["queries"] == len(row["trace"]) == len(row["observed"])
                assert len({item["id"] for item in row["observed"]}) == row["queries"]
                assert row["final"]["net_utility"] == pytest.approx(row["final"]["best_revealed_positive_utility"] - row["opportunity_cost"] * row["spent"])
                assert row["final"]["cost_adjusted_regret"] == pytest.approx(row["final"]["simple_regret"] + row["opportunity_cost"] * row["spent"])
                assert row["final"]["cost_adjusted_regret"] + row["final"]["net_utility"] == pytest.approx(row["final"]["oracle_best_positive_utility"])
                selected = row["final"]["research_best_completed_variant"]
                assert selected is None or selected in {item["id"] for item in row["observed"]}
                assert "recommendation" not in row["final"]
                for index, step in enumerate(row["trace"]):
                    assert step["training_size"] == index
                    assert step["snapshot"]["observed"] == row["observed"][:index]
                    assert step["chosen"]["id"] in step["snapshot"]["eligible"]
                    assert step["cost"] == 1
                    assert len(step["snapshot"]["features"]) == 16
                if row["group"] == "no_search":
                    assert row["queries"] == row["spent"] == row["final"]["net_utility"] == 0
                    assert row["stop_reason"] == "no_search"
                elif row["group"] in {"linear_ucb", "audit_ei_no_stop"}:
                    assert row["queries"] == row["spent"] == 3
                    assert row["stop_reason"] == "budget_exhausted"


def test_explicit_alternative_grid_is_reported_completely_not_selected_by_results():
    report = protocol_stopping_benchmark(seeds=(0, 2), opportunity_costs=(0.07, 0.2), budget=2)
    assert report["opportunity_costs"] == [0.07, 0.2]
    assert len(report["runs"]) == 80
    assert len(report["summary"]) == 8
    assert {row["opportunity_cost"] for row in report["runs"]} == {0.07, 0.2}


def test_stop_is_explicit_replayable_and_no_stop_is_same_acquisition_prefix():
    module = importlib.import_module("etalon.active.protocol_stopping_benchmark")
    report = protocol_stopping_benchmark(seeds=(0, 1), budget=6)
    count = 0
    for row in report["runs"]:
        if row["group"] not in {"audit_ei", "audit_ei_no_transfer"}:
            continue
        if row["stop_reason"] != ECONOMIC_STOP:
            continue
        count += 1
        stop = row["stop_snapshot"]
        assert stop["chosen"] is None
        assert stop["ranking"]
        assert stop["max_affordable_net_value"] <= 0
        assert all(item["score"] <= 0 for item in stop["ranking"])
        assert stop["snapshot"]["observed"] == row["observed"]
        assert stop["training_size"] == row["queries"]
        snapshot = stop["snapshot"]
        ranked = module.rank_variants(snapshot["features"], snapshot["observed"], snapshot["costs"], **stop["parameters"])
        assert ranked["training_hash"] == stop["training_hash"]
        if row["group"] == "audit_ei":
            reference = next(r for r in report["runs"] if r["group"] == "audit_ei_no_stop"
                             and (r["seed"], r["scenario"], r["opportunity_cost"])
                             == (row["seed"], row["scenario"], row["opportunity_cost"]))
            assert reference["observed"][:row["queries"]] == row["observed"]
            assert reference["queries"] > row["queries"]
            next_step = reference["trace"][row["queries"]]
            assert next_step["training_hash"] == stop["training_hash"]
            assert next_step["chosen"]["score"] <= 0
            assert row["paired_no_stop"]["spent_saved"] > 0
    assert count > 0


def test_unaffordable_positive_net_arm_cannot_prevent_economic_stop():
    module = importlib.import_module("etalon.active.protocol_stopping_benchmark")
    features = {"seen": [1.0], "expensive": [10.0], "affordable": [0.0]}
    costs = {"seen": 1.0, "expensive": 1.01, "affordable": 1.0}
    observed = [{"id": "seen", "reward": 0.4, "evidence_hash": "only-acquired"}]
    result = _decision(features, costs, observed, ["affordable"], group="audit_ei", seed=0, opportunity_cost=0.3)
    raw = module.rank_variants(features, observed, costs, policy="audit_ei", opportunity_cost=0.3)
    assert raw["ranking"][0]["id"] == "expensive"
    assert raw["ranking"][0]["score"] > 0
    assert result["chosen"] is None
    assert result["stop_reason"] == ECONOMIC_STOP
    assert [row["id"] for row in result["ranking"]] == ["affordable"]
    assert set(result["snapshot"]["features"]) == set(features)


def test_changed_observed_incumbent_can_change_stop_without_any_future_label():
    features = {"seen": [1, 0], "new": [0, 1]}
    costs = {"seen": 1.0, "new": 1.0}
    history = [{"id": "seen", "reward": 0.0, "evidence_hash": "acquired-low"}]
    first = _decision(features, costs, history, ["new"], group="audit_ei", seed=0, opportunity_cost=0.1)
    history[0] = {"id": "seen", "reward": 0.9, "evidence_hash": "acquired-high"}
    second = _decision(features, costs, history, ["new"], group="audit_ei", seed=0, opportunity_cost=0.1)
    assert first["chosen"]["id"] == "new"
    assert second["chosen"] is None
    assert first["training_hash"] != second["training_hash"]


def test_future_oracle_does_not_change_first_choice_or_cold_stop(monkeypatch):
    module = importlib.import_module("etalon.active.protocol_stopping_benchmark")
    original = module._scenario

    def costly(seed, name):
        catalog, oracle = original(seed, name)
        catalog["costs"] = dict.fromkeys(catalog["costs"], 2.0)
        return catalog, oracle

    monkeypatch.setattr(module, "_scenario", costly)
    first = protocol_stopping_benchmark(seeds=(4,), budget=4, max_trials=1)

    def changed(seed, name):
        catalog, oracle = costly(seed, name)
        return catalog, {key: -value for key, value in oracle.items()}

    monkeypatch.setattr(module, "_scenario", changed)
    second = protocol_stopping_benchmark(seeds=(4,), budget=4, max_trials=1)
    cold_stops = 0
    for before, after in zip(first["runs"], second["runs"], strict=True):
        assert before["controls_hash"] != after["controls_hash"]
        if before["queries"]:
            assert before["trace"][0]["chosen"] == after["trace"][0]["chosen"]
            assert before["trace"][0]["training_hash"] == after["trace"][0]["training_hash"]
        else:
            assert before["stop_snapshot"] == after["stop_snapshot"]
            cold_stops += before["stop_reason"] == ECONOMIC_STOP
    assert cold_stops > 0


def test_no_transfer_has_matched_prior_variance_and_stable_vocabulary():
    module = importlib.import_module("etalon.active.protocol_stopping_benchmark")
    catalog, _ = module._scenario(0, SCENARIOS[0])
    features, costs = catalog["features"], catalog["costs"]
    first = _decision(features, costs, [], list(features), group="audit_ei", seed=0, opportunity_cost=0.1)
    second = _decision(features, costs, [], list(features), group="audit_ei_no_transfer", seed=0, opportunity_cost=0.1)
    assert first["ranking"][0]["std"] == pytest.approx(second["ranking"][0]["std"])
    assert first["ranking"][0]["predictive_std"] == pytest.approx(second["ranking"][0]["predictive_std"])
    assert first["ranking"][0]["expected_improvement"] == pytest.approx(second["ranking"][0]["expected_improvement"])
    ids = sorted(features)
    observed = [{"id": ids[0], "reward": 0.5, "evidence_hash": "acquired"}]
    filtered = _decision(features, costs, observed, [ids[-1]], group="audit_ei_no_transfer", seed=0, opportunity_cost=0.1)
    assert len(filtered["snapshot"]["features"]) == len(ids)
    assert all(len(vector) == len(ids) for vector in filtered["snapshot"]["features"].values())
    assert filtered["snapshot"]["features"][ids[0]][0] == 1
    assert filtered["snapshot"]["features"][ids[-1]][-1] == 1


def test_posthoc_discloses_premature_stop_and_negative_net_without_deployment_claim():
    oracle = {"seen": 0.2, "missed": 0.9, "bad": -0.7}
    costs = dict.fromkeys(oracle, 1.0)
    observed = [{"id": "seen", "reward": 0.2, "evidence_hash": "synthetic"}]
    result = _posthoc(oracle, costs, observed, budget=3, spent=1, opportunity_cost=0.3, stop_reason=ECONOMIC_STOP)
    assert result["net_utility"] == pytest.approx(-0.1)
    assert result["cost_adjusted_regret"] == pytest.approx(1.0)
    assert result["economically_premature_stop"] is True
    assert result["model_stop_left_raw_improvement"] is True
    assert result["oracle_remaining_affordable_beneficial_queries"] == 1
    assert result["research_best_completed_variant"] == "seen"
    assert result["missed_positive_opportunities"] == 1
    expensive = _posthoc(oracle, costs, observed, budget=3, spent=1, opportunity_cost=0.8, stop_reason=ECONOMIC_STOP)
    assert expensive["model_stop_left_raw_improvement"] is True
    assert expensive["economically_premature_stop"] is False


@pytest.mark.parametrize("budget, trials, reason", [(0.5, 2, "budget_exhausted"), (20, 1, "trial_limit"), (20, 20, "catalog_exhausted")])
def test_budget_trial_and_catalog_guards(budget, trials, reason):
    report = protocol_stopping_benchmark(seeds=(0,), budget=budget, max_trials=trials)
    for row in report["runs"]:
        assert row["spent"] <= budget
        assert row["queries"] <= min(16, trials)
        assert row["stop_snapshot"]["chosen"] is None
        if row["group"] in {"linear_ucb", "audit_ei_no_stop"}:
            assert row["stop_reason"] == reason
        if row["scenario"] == "negative_utility" and row["spent"]:
            assert row["final"]["net_utility"] < 0
            assert row["final"]["opportunity_recall"] is None


def test_snapshots_are_detached_from_mutable_observation_output():
    row = next(row for row in protocol_stopping_benchmark(seeds=(0,), budget=3)["runs"]
               if row["group"] == "audit_ei_no_stop")
    snapshot = deepcopy(row["trace"][1]["snapshot"])
    row["observed"][0]["reward"] = 999
    assert row["trace"][1]["snapshot"] == snapshot
    assert row["stop_snapshot"]["snapshot"]["observed"][0]["reward"] != 999


@pytest.mark.parametrize("overrides, match", [
    ({"seeds": ()}, "distinct integers"), ({"seeds": (0, 0)}, "distinct integers"),
    ({"seeds": (True,)}, "distinct integers"), ({"seeds": (-1,)}, "distinct integers"),
    ({"seeds": (2**32,)}, "distinct integers"), ({"seeds": "0"}, "distinct integers"),
    ({"seeds": None}, "distinct integers"), ({"seeds": ([],)}, "distinct integers"),
    ({"budget": 0}, "positive finite"), ({"budget": -1}, "positive finite"),
    ({"budget": float("nan")}, "positive finite"), ({"budget": float("inf")}, "positive finite"),
    ({"budget": 10**400}, "positive finite"), ({"budget": True}, "positive finite"),
    ({"budget": "3"}, "positive finite"), ({"max_trials": 0}, "positive integer"),
    ({"max_trials": 1.5}, "positive integer"), ({"max_trials": True}, "positive integer"),
    ({"opportunity_costs": ()}, "opportunity_costs"),
    ({"opportunity_costs": None}, "opportunity_costs"),
    ({"opportunity_costs": "0.1"}, "opportunity_costs"),
    ({"opportunity_costs": (0.1, 0.1)}, "opportunity_costs"),
    ({"opportunity_costs": (0,)}, "opportunity_costs"),
    ({"opportunity_costs": (-1,)}, "opportunity_costs"),
    ({"opportunity_costs": (float("nan"),)}, "opportunity_costs"),
    ({"opportunity_costs": (float("inf"),)}, "opportunity_costs"),
    ({"opportunity_costs": (10**400,)}, "opportunity_costs"),
    ({"opportunity_costs": (1e308,)}, "opportunity_costs"),
    ({"opportunity_costs": (True,)}, "opportunity_costs"),
])
def test_invalid_inputs_rejected_before_constructing_oracle(monkeypatch, overrides, match):
    module = importlib.import_module("etalon.active.protocol_stopping_benchmark")

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid inputs reached oracle construction")

    monkeypatch.setattr(module, "_scenario", forbidden)
    with pytest.raises(ValueError, match=match):
        protocol_stopping_benchmark(**overrides)


def test_main_reports_whole_grid_and_rejects_invalid_input(capsys):
    assert main(["--seeds", "0", "--budget", "0.5", "--max-trials", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["opportunity_costs"] == list(OPPORTUNITY_COSTS)
    assert len(report["runs"]) == len(OPPORTUNITY_COSTS) * len(SCENARIOS) * len(GROUPS)
    with pytest.raises(SystemExit) as error:
        main(["--seeds", "bad"])
    assert error.value.code == 2
