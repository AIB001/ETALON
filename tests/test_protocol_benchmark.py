import importlib
import json
from copy import deepcopy

import pytest

from etalon.active.protocol_benchmark import (
    GROUPS,
    SCENARIOS,
    _select,
    main,
    protocol_benchmark,
)


def test_deterministic_equal_information_equal_cost_benchmark():
    arguments = {"seeds": (0, 3), "budget": 3.5, "max_trials": 8}
    first = protocol_benchmark(**arguments)
    assert first == protocol_benchmark(**arguments)
    assert first["synthetic"] is True
    assert "no real protocol execution" in first["claim"]
    assert len(first["runs"]) == len(GROUPS) * len(SCENARIOS) * 2
    json.dumps(first, allow_nan=False)
    for seed in arguments["seeds"]:
        for scenario in SCENARIOS:
            rows = [row for row in first["runs"] if row["seed"] == seed and row["scenario"] == scenario]
            assert {row["group"] for row in rows} == set(GROUPS)
            assert len({row["controls_hash"] for row in rows}) == 1
            assert len({row["public_catalog_hash"] for row in rows}) == 1
            ucb = next(row for row in rows if row["group"] == "linear_ucb")
            no_transfer = next(row for row in rows if row["group"] == "linear_ucb_no_transfer")
            assert ucb["trace"][0]["chosen"]["std"] == pytest.approx(no_transfer["trace"][0]["chosen"]["std"])
            for row in rows:
                assert row["spent"] == 3
                assert row["remaining"] == 0.5
                assert row["queries"] == 3
                assert row["stop_reason"] == "budget_exhausted"
                assert len({item["id"] for item in row["observed"]}) == 3
                assert all(item["cost"] == 1 for item in row["trace"])
                for index, step in enumerate(row["trace"]):
                    assert step["training_size"] == index
                    assert step["snapshot"]["observed"] == row["observed"][:index]
                    assert step["chosen"]["id"] in step["snapshot"]["eligible"]
                    assert step["chosen"]["id"] not in {item["id"] for item in step["snapshot"]["observed"]}
                    assert all(item["id"] in step["snapshot"]["features"]
                               for item in step["snapshot"]["observed"])
                    assert step["cost"] <= row["budget"] - (step["spent"] - step["cost"])


@pytest.mark.parametrize("budget, trials, count, reason", [
    (0.5, 3, 0, "budget_exhausted"),
    (20, 2, 2, "trial_limit"),
    (20, 30, 16, "catalog_exhausted"),
])
def test_all_policy_budget_trial_and_catalog_limits(budget, trials, count, reason):
    report = protocol_benchmark(seeds=(1,), budget=budget, max_trials=trials)
    for row in report["runs"]:
        assert row["spent"] <= budget
        assert row["queries"] == count
        assert row["queries"] <= trials
        assert row["stop_reason"] == reason
        assert len({item["id"] for item in row["observed"]}) == count
        assert row["final"]["simple_regret"] >= 0
        if count == 16:
            assert row["final"]["simple_regret"] == 0
        if count == 0:
            assert row["final"]["recommendation"] is None
            assert row["final"]["best_revealed_positive_utility"] == 0


def test_negative_utilities_are_charged_and_do_not_force_bad_recommendation():
    report = protocol_benchmark(seeds=(2,), budget=4)
    for row in report["runs"]:
        if row["scenario"] == "negative_utility":
            assert all(item["reward"] < 0 for item in row["observed"])
            assert row["spent"] == 4
            assert row["final"]["simple_regret"] == 0
            assert row["final"]["opportunity_recall"] is None
            assert row["final"]["recommendation"] is None


def test_changing_hidden_oracle_cannot_change_first_decision(monkeypatch):
    module = importlib.import_module("etalon.active.protocol_benchmark")
    original = module._scenario
    first = protocol_benchmark(seeds=(7,), budget=1, max_trials=1)

    def changed(seed, name):
        catalog, rewards = original(seed, name)
        return catalog, {key: -value for key, value in rewards.items()}

    monkeypatch.setattr(module, "_scenario", changed)
    second = protocol_benchmark(seeds=(7,), budget=1, max_trials=1)
    for before, after in zip(first["runs"], second["runs"], strict=True):
        assert before["controls_hash"] != after["controls_hash"]
        assert before["public_catalog_hash"] == after["public_catalog_hash"]
        for key in ("chosen", "training_hash", "training_size", "snapshot"):
            assert before["trace"][0][key] == after["trace"][0][key]


def test_only_changed_acquired_reward_can_change_later_selection(monkeypatch):
    module = importlib.import_module("etalon.active.protocol_benchmark")
    reward = [0.8]

    def fixture(seed, name):
        return ({"features": {"a": [1, 1], "b": [1, 1], "c": [1, -2]},
                 "costs": {"a": 1.0, "b": 1.0, "c": 1.0}},
                {"a": reward[0], "b": 0.4, "c": 0.5})

    monkeypatch.setattr(module, "_scenario", fixture)
    first = protocol_benchmark(seeds=(0,), budget=2, max_trials=2)
    reward[0] = -0.8
    second = protocol_benchmark(seeds=(0,), budget=2, max_trials=2)
    for before, after in zip(first["runs"], second["runs"], strict=True):
        if before["group"] == "linear_greedy":
            assert before["trace"][0]["chosen"]["id"] == after["trace"][0]["chosen"]["id"] == "a"
            assert before["trace"][1]["chosen"]["id"] == "b"
            assert after["trace"][1]["chosen"]["id"] == "c"
            assert before["trace"][1]["training_hash"] != after["trace"][1]["training_hash"]


def test_affordability_filter_retains_training_ids_and_fixed_feature_vocabulary():
    features = {"a": [1, 1], "b": [1, -1], "c": [1, 0]}
    observed = [{"id": "a", "reward": 0.7, "evidence_hash": "synthetic-test-a"}]
    costs = {"a": 1, "b": 5, "c": 1}
    decision = _select(features, costs, observed, ["c"], group="linear_ucb_no_transfer", seed=0)
    assert decision["chosen"]["id"] == "c"
    assert set(decision["snapshot"]["features"]) == {"a", "c"}
    assert decision["snapshot"]["features"] == {"a": [1, 0, 0], "c": [0, 0, 1]}
    assert decision["training_size"] == 1
    assert observed == [{"id": "a", "reward": 0.7, "evidence_hash": "synthetic-test-a"}]


def test_training_snapshot_is_detached_and_replayable():
    module = importlib.import_module("etalon.active.protocol_benchmark")
    row = protocol_benchmark(seeds=(0,), budget=3)["runs"][2]
    for step in row["trace"]:
        snap = step["snapshot"]
        before = deepcopy(snap)
        result = module.rank_variants(snap["features"], snap["observed"], snap["costs"], **step["parameters"])
        assert result["training_hash"] == step["training_hash"]
        assert result["ranking"][0] == step["chosen"]
        assert snap == before
    row["observed"][0]["reward"] = 999
    assert row["trace"][1]["snapshot"]["observed"][0]["reward"] != 999


@pytest.mark.parametrize("overrides, match", [
    ({"seeds": ()}, "distinct integers"),
    ({"seeds": (0, 0)}, "distinct integers"),
    ({"seeds": (True,)}, "distinct integers"),
    ({"seeds": (-1,)}, "distinct integers"),
    ({"seeds": (2**32,)}, "distinct integers"),
    ({"seeds": "1,2"}, "distinct integers"),
    ({"seeds": None}, "distinct integers"),
    ({"seeds": ([],)}, "distinct integers"),
    ({"budget": 0}, "positive finite"),
    ({"budget": -1}, "positive finite"),
    ({"budget": float("nan")}, "positive finite"),
    ({"budget": float("inf")}, "positive finite"),
    ({"budget": 10**400}, "positive finite"),
    ({"budget": True}, "positive finite"),
    ({"budget": "3"}, "positive finite"),
    ({"max_trials": 0}, "positive integer"),
    ({"max_trials": 1.5}, "positive integer"),
    ({"max_trials": True}, "positive integer"),
])
def test_invalid_input_refused_before_any_scoring(monkeypatch, overrides, match):
    module = importlib.import_module("etalon.active.protocol_benchmark")

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid input reached synthetic oracle construction")

    monkeypatch.setattr(module, "_scenario", forbidden)
    with pytest.raises(ValueError, match=match):
        protocol_benchmark(**overrides)


def test_main_emits_json_and_rejects_invalid_seeds(capsys):
    assert main(["--seeds", "2", "--budget", "0.5", "--max-trials", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["synthetic"] is True
    assert result["seeds"] == [2]
    with pytest.raises(SystemExit) as error:
        main(["--seeds", "bad"])
    assert error.value.code == 2
