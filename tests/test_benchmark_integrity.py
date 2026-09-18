"""A comparison cannot mix implementations or execute only its valid input prefix."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from etalon.active.benchmark import benchmark
from etalon.active.replay import from_manifest, synthetic_manifest
from etalon.active.store import CampaignStore, StateError


@pytest.mark.parametrize("overrides", [
    {"seeds": ()}, {"seeds": (0, 0)}, {"seeds": (0, True)}, {"seeds": (0, "1")},
    {"seeds": (0, 1.5)}, {"policies": ()}, {"policies": ("random", "random")},
    {"policies": ("random", "typo")}, {"policies": ("random", 2)},
    {"rounds": True}, {"rounds": False}, {"rounds": 1.5}, {"rounds": 0},
    {"size": True}, {"size": 8.0}, {"size": 7}, {"size": 5001},
    {"budget": float("nan")}, {"budget": 0},
    {"size": 513, "policies": ("random", "mf_kg")},
    {"size": 513, "policies": ("random", "decision_aware")},
])
def test_invalid_comparison_inputs_fail_before_any_journal(tmp_path, overrides):
    arguments = {"seeds": (0,), "policies": ("random",), "rounds": 1, "size": 8, "budget": 48}
    arguments.update(overrides)
    workspace = tmp_path / "uncreated"
    with pytest.raises(ValueError):
        benchmark(workspace, **arguments)
    assert not workspace.exists()


def test_legacy_comparison_fingerprints_environment_and_rejects_changed_resume(tmp_path, monkeypatch):
    module = importlib.import_module("etalon.active.benchmark")
    arguments = {"seeds": (0,), "policies": ("random",), "rounds": 1, "size": 8, "budget": 48}
    first = benchmark(tmp_path, **arguments)
    assert first["round_limit"] == 1
    assert set(first["implementation"]["environment"]) == {"numpy", "scipy"}
    assert "model" in first["implementation"]["modules"]
    assert len(first["runs"][0]["controls_hash"]) == 64
    store = CampaignStore(first["runs"][0]["database"], read_only=True)
    before = store.actions()
    monkeypatch.setattr(module, "version", lambda name: "changed-environment")
    with pytest.raises(StateError, match="benchmark_implementation.*changed"):
        benchmark(tmp_path, **{**arguments, "rounds": 2})
    assert store.actions() == before


@pytest.mark.parametrize("comparison", ["legacy", "decision"])
def test_cannot_retroactively_certify_rounds_that_ran_outside_benchmark(tmp_path, comparison):
    if comparison == "legacy":
        manifest = synthetic_manifest(seed=0, size=8, budget=48, policy="random")
        arguments = {"policies": ("random",)}
        run_comparison = benchmark
    else:
        module = importlib.import_module("etalon.active.decision_benchmark")
        manifest = module._manifest(synthetic_manifest(seed=0, size=8, budget=48, batch_size=1), "random")
        arguments = {}
        run_comparison = module.decision_benchmark
    path = tmp_path / "seed-0" / "random" / "campaign.sqlite"
    campaign = from_manifest(manifest, path)
    campaign.run(max_rounds=1)
    before = campaign.store.actions()
    with pytest.raises(StateError, match="cannot retroactively bind"):
        run_comparison(tmp_path, seeds=(0,), size=8, budget=48, rounds=2, **arguments)
    assert campaign.store.actions() == before
    assert all(key == "replay_manifest" for key in (
        event["body"]["name"] for event in campaign.store.events() if event["kind"] == "resource_bound"))


def test_legacy_comparison_resumes_without_spending_its_round_quota_twice(tmp_path):
    arguments = {"seeds": (0,), "policies": ("random", "cost_only"), "rounds": 1, "size": 8, "budget": 64}
    first = benchmark(tmp_path, **arguments)
    before = {run["policy"]: Path(run["database"]).read_bytes() for run in first["runs"]}
    second = benchmark(tmp_path, **arguments)
    assert first == second
    assert {run["policy"]: Path(run["database"]).read_bytes() for run in second["runs"]} == before
    assert len({run["controls_hash"] for run in first["runs"]}) == 1


@pytest.mark.parametrize("comparison", ["legacy", "decision"])
def test_qc_implementation_changes_cannot_silently_change_a_resumed_benchmark(tmp_path, monkeypatch, comparison):
    module = importlib.import_module("etalon.active.decision_benchmark")
    run_comparison = benchmark if comparison == "legacy" else module.decision_benchmark
    extra = {"policies": ("random",)} if comparison == "legacy" else {}
    arguments = {"seeds": (0,), "size": 8, "budget": 48, "rounds": 1, **extra}
    first = run_comparison(tmp_path, **arguments)
    assert "learn/admissible" in first["implementation"]["admission_modules"]
    assert {"protocols", "proposer"} <= set(first["implementation"]["modules"])
    store = CampaignStore(first["runs"][0]["database"], read_only=True)
    before = store.actions()
    original = Path.read_bytes

    def changed_source(path):
        return original(path) + b"\n# changed QC implementation\n" if path.name == "admissible.py" else original(path)

    monkeypatch.setattr(Path, "read_bytes", changed_source)
    with pytest.raises(StateError, match="implementation.*changed"):
        run_comparison(tmp_path, **{**arguments, "rounds": 2})
    assert store.actions() == before
