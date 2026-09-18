"""The controller must learn from real feedback, preserve costs and survive process boundaries."""

from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import pytest

from etalon.active import (
    ActiveCampaign,
    BudgetExhausted,
    CampaignSpec,
    CampaignStore,
    Candidate,
    Endpoint,
    Evaluation,
    StateError,
)
from etalon.active.model import MultiEndpointGP
from etalon.active.replay import from_manifest, synthetic_manifest
from etalon.authority.grant import NotAuthorized, authorize, require
from etalon.faults.attribution import Observation


def setup_store(tmp_path, *, budget=40.0, count=16, replicas=1, batch_size=2, policy="cost_aware"):
    endpoint = Endpoint("high", "target", "score", "u", "v1", 2.0, noise=0.1,
                        requires_handoff=False, max_replicates=replicas)
    spec = CampaignSpec("high", budget, "test_units", "test-vector/1", batch_size=batch_size, policy=policy)
    store = CampaignStore(tmp_path / "journal.sqlite")
    store.configure(spec, [endpoint])
    store.add_candidates([Candidate(f"p{i:02d}", "CCO", (float(i), float(i % 3)),
                                    scaffold=str(i % 4)) for i in range(count)])
    return store


def exact_executor(action, candidate, endpoint, grant):
    return Evaluation(candidate.id, endpoint.id, -candidate.features[0], endpoint.units, endpoint.cost)


@pytest.mark.parametrize("field,value", [("cost", 0), ("cost", float("nan")), ("noise", -1),
                                        ("noise", float("inf")), ("max_replicates", 0),
                                        ("prior_scale", 0), ("direction", "wrong"),
                                        ("quantity", "rbfe")])
def test_endpoint_contract_rejects_ambiguous_or_nonfinite_labels(field, value):
    fields = {"id": "a", "target": "t", "quantity": "score", "units": "u", "protocol": "v1", "cost": 1}
    fields[field] = value
    with pytest.raises(ValueError):
        Endpoint(**fields)


def test_campaign_target_and_identity_are_immutable(tmp_path):
    store = setup_store(tmp_path)
    spec, endpoints = store.configuration()
    with pytest.raises(StateError, match="configuration differs"):
        store.configure(replace(spec, budget=100), list(endpoints.values()))
    with pytest.raises(ValueError, match="one target"):
        store.configure(spec, [endpoints["high"], replace(endpoints["high"], id="other", target="other")])
    candidate = store.candidates()["p00"]
    assert store.add_candidates([candidate]) == 0
    with pytest.raises(StateError, match="changed"):
        store.add_candidates([replace(candidate, smiles="CCC")])
    with pytest.raises(ValueError, match="width"):
        store.add_candidates([Candidate("new", "CO", (0.0,))])


def test_historical_import_is_idempotent_costed_and_versioned(tmp_path):
    store = setup_store(tmp_path)
    result = Evaluation("p00", "high", -2, "u", 2)
    store.import_evaluation(result, source_id="file:hash:row0")
    store.import_evaluation(result, source_id="file:hash:row0")
    assert len(store.observations()) == 1
    assert store.balance()["spent"] == 2
    with pytest.raises(StateError, match="source changed"):
        store.import_evaluation(replace(result, value=-3), source_id="file:hash:row0")
    with pytest.raises(ValueError, match="units"):
        store.import_evaluation(replace(result, units="pIC50"), source_id="other")


def test_reservations_are_atomic_across_two_connections(tmp_path):
    store = setup_store(tmp_path, budget=2)
    round_id = store.start_round({})

    def reserve(identifier):
        try:
            return CampaignStore(store.path).reserve(round_id, identifier, "high", {})
        except BudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ["p00", "p01"]))
    assert sum(r is not None for r in results) == 1
    assert store.balance() == {"budget": 2, "spent": 0, "reserved": 2, "remaining": 0}


def test_actual_overrun_is_not_hidden_or_followed_by_more_spending(tmp_path):
    store = setup_store(tmp_path, budget=4, batch_size=2)

    def expensive(action, candidate, endpoint, grant):
        return Evaluation(candidate.id, endpoint.id, -1, "u", 5)

    result = ActiveCampaign(store, expensive).run(max_rounds=2)
    assert len(store.actions()) == 1
    assert store.balance()["remaining"] == -1
    assert result["stop_reason"] == "budget_exhausted"


def test_runtime_evidence_updates_future_reservations(tmp_path):
    store = setup_store(tmp_path)
    store.import_evaluation(Evaluation("p00", "high", -1, "u", 5), source_id="measured-cost")
    campaign = ActiveCampaign(store, exact_executor)
    _, choices, _ = campaign.plan()
    assert all(c.evidence["quoted_cost"] == 5 for c in choices)
    campaign.run_round()
    assert all(a["reserved_cost"] == 5 for a in store.actions() if a["round_id"])


def test_crash_keeps_reservation_and_resume_cannot_repeat_the_job(tmp_path):
    store = setup_store(tmp_path)

    def interrupted(action, candidate, endpoint, grant):
        raise KeyboardInterrupt("external process may still be running")

    with pytest.raises(KeyboardInterrupt):
        ActiveCampaign(store, interrupted).run_round()
    resumed = CampaignStore(store.path)
    pending = resumed.status()["pending"]
    assert len(pending) == 1
    assert resumed.balance()["reserved"] == 2
    with pytest.raises(StateError, match="unresolved"):
        ActiveCampaign(resumed, exact_executor).run_round()
    action = pending[0]
    resumed.resolve(action["id"], Evaluation(action["candidate_id"], "high", None, "u", 2,
                                           status="failed", provenance={"operator": "job confirmed stopped"}))
    with pytest.raises(StateError, match="unfinished round"):
        ActiveCampaign(resumed, exact_executor).run_round()
    resumed.recover_idle_rounds(reason="operator confirmed the old worker stopped and reconciled its outcome")
    ActiveCampaign(resumed, exact_executor).run_round()
    assert resumed.rounds()[0]["status"] == "interrupted"
    assert len([a for a in resumed.actions() if a["candidate_id"] == action["candidate_id"]]) == 1


def test_failures_and_rejected_quality_results_are_costed_but_never_trained(tmp_path):
    store = setup_store(tmp_path)

    def bad_quality(action, candidate, endpoint, grant):
        return Evaluation(candidate.id, endpoint.id, -999, "u", 2,
                          checks=(Observation("F_BUILD_INCOMPLETE", True, "failed parameterization"),))

    campaign = ActiveCampaign(store, bad_quality)
    campaign.run_round()
    model, _, _ = campaign.plan()
    assert model.snapshot()["training_size"] == 0
    assert len(store.observations()) == 2
    assert store.balance()["spent"] == 4
    assert all(o["result"]["value"] == -999 and not o["admitted"] for o in store.observations())


def test_executor_exception_is_a_persistent_charged_failure(tmp_path):
    store = setup_store(tmp_path, batch_size=1)

    def broken(action, candidate, endpoint, grant):
        raise RuntimeError("tool exited before providing a meter")

    ActiveCampaign(store, broken).run_round()
    result = store.observations()[0]
    assert not result["admitted"]
    assert result["result"]["status"] == "failed"
    assert result["result"]["cost"] == 2
    assert "actual cost unavailable" in result["result"]["provenance"]["cost_basis"]


@pytest.mark.parametrize("changes", [{"candidate_id": "wrong"}, {"endpoint_id": "wrong"}, {"units": "wrong"}])
def test_wrong_result_identity_is_quarantined_not_silently_relabelled(tmp_path, changes):
    store = setup_store(tmp_path, batch_size=1)

    def wrong(action, candidate, endpoint, grant):
        return replace(exact_executor(action, candidate, endpoint, grant), **changes)

    ActiveCampaign(store, wrong).run_round()
    entry = store.observations()[0]
    assert not entry["admitted"]
    assert entry["result"]["status"] == "invalid"
    assert "raw_result" in entry["result"]["provenance"]


def test_three_rounds_retrain_and_execute_only_the_selected_batch(tmp_path):
    store = setup_store(tmp_path)
    campaign = ActiveCampaign(store, exact_executor)
    for _ in range(3):
        _, choices, _ = campaign.plan()
        result = campaign.run_round()
        executed = [a for a in store.actions() if a["id"] in result["actions"]]
        assert [(a["candidate_id"], a["endpoint_id"]) for a in executed] == [(c.candidate_id, c.endpoint_id) for c in choices]
    rounds = store.rounds()
    assert [r["model"]["training_size"] for r in rounds] == [0, 2, 4]
    assert len({r["model"]["training_hash"] for r in rounds}) == 3
    assert len({a["candidate_id"] for a in store.actions()}) == 6
    assert store.balance()["spent"] == 12
    assert store.balance()["reserved"] == 0
    assert all(a["decision"]["model_hash"] for a in store.actions())


def test_changed_feedback_changes_next_choices_not_just_the_model_hash(tmp_path):
    one = setup_store(tmp_path / "one", batch_size=4, policy="greedy")
    two = setup_store(tmp_path / "two", batch_size=4, policy="greedy")

    def opposite(action, candidate, endpoint, grant):
        return replace(exact_executor(action, candidate, endpoint, grant), value=candidate.features[0])

    first, second = ActiveCampaign(one, exact_executor), ActiveCampaign(two, opposite)
    assert first.plan()[1] == second.plan()[1]
    first.run_round()
    second.run_round()
    assert {c.candidate_id for c in first.plan()[1]} != {c.candidate_id for c in second.plan()[1]}


def test_training_provenance_cannot_include_a_label_arriving_after_the_fit(tmp_path, monkeypatch):
    store = setup_store(tmp_path)
    campaign = ActiveCampaign(store, exact_executor)
    original_plan = campaign.plan

    def plan_then_receive_external_label():
        model, choices, reason = original_plan()
        selected = {choice.candidate_id for choice in choices}
        identifier = next(key for key in store.candidates() if key not in selected)
        store.import_evaluation(Evaluation(identifier, "high", -10, "u", 0), source_id="late-assay")
        return model, choices, reason

    monkeypatch.setattr(campaign, "plan", plan_then_receive_external_label)
    with pytest.raises(StateError, match="stale"):
        campaign.run_round()
    assert not store.rounds()  # No tool or stale-evidence decision was started.
    assert len(store.actions()) == 1 and store.actions()[0]["round_id"] == 0
    monkeypatch.setattr(campaign, "plan", original_plan)
    campaign.run_round()
    round_record = store.rounds()[0]
    assert round_record["model"]["training_size"] == 1
    assert round_record["training_actions"] == [store.actions()[0]["id"]]


def test_restart_reproduces_uninterrupted_decisions_and_training_hashes(tmp_path):
    manifest = synthetic_manifest(seed=7, size=24)
    full = from_manifest(manifest, tmp_path / "full.sqlite")
    full.run(max_rounds=5)
    partial = from_manifest(manifest, tmp_path / "partial.sqlite")
    partial.run(max_rounds=2)
    resumed = from_manifest(manifest, partial.store.path)
    resumed.run(max_rounds=3)
    def projection(store):
        return [(a["candidate_id"], a["endpoint_id"], a["replicate"]) for a in store.actions()]
    assert projection(full.store) == projection(resumed.store)
    assert full.store.balance() == resumed.store.balance()
    assert [r["model"] for r in full.store.rounds()] == [r["model"] for r in resumed.store.rounds()]


def test_unqueried_labels_cannot_affect_next_decision(tmp_path):
    manifest = synthetic_manifest(seed=3, size=24)
    changed = copy.deepcopy(manifest)
    warm = {i["result"]["candidate_id"] for i in changed["initial"]}
    for row in changed["oracle"]:
        if row["candidate_id"] not in warm:
            row["value"] = -10000.0
    one = from_manifest(manifest, tmp_path / "one.sqlite")
    two = from_manifest(changed, tmp_path / "two.sqlite")
    assert one.plan()[0].snapshot() == two.plan()[0].snapshot()
    assert one.plan()[1] == two.plan()[1]
    with pytest.raises(StateError, match="resource"):
        from_manifest(changed, one.store.path)


def test_missing_or_duplicate_oracle_rows_refuse_before_creating_database(tmp_path):
    manifest = synthetic_manifest(size=8)
    manifest["oracle"].pop()
    with pytest.raises(ValueError, match="incomplete oracle"):
        from_manifest(manifest, tmp_path / "missing.sqlite")
    assert not (tmp_path / "missing.sqlite").exists()
    manifest["oracle"].append(manifest["oracle"][0])
    with pytest.raises(ValueError, match="duplicate oracle"):
        from_manifest(manifest, tmp_path / "duplicate.sqlite")


def test_live_preflight_refusal_never_calls_executor(tmp_path):
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text("receptor", encoding="utf-8")
    endpoint = Endpoint("md", "t", "mmpbsa", "kcal/mol", "v1", 10)
    store = CampaignStore(tmp_path / "live.sqlite")
    store.configure(CampaignSpec("md", 20, "GPU-h", "test"), [endpoint])
    row = {"parent_id": "p", "parent_smiles": "CCO", "coordinate_source": "TWO_D_DEPICTION",
           "hydrogens": "IMPLICIT", "status": "OK", "protonation_state_id": "INHERITED_FROM_STANDARDIZER"}
    store.add_candidates([Candidate("p", "CCO", (0.0,), handoff=row)])
    called = []
    campaign = ActiveCampaign(store, lambda *args: called.append(args), receptor_path=receptor)
    campaign.run_round()
    assert called == []
    assert store.balance()["spent"] == 0
    assert store.actions()[0]["status"] == "blocked"
    assert store.observations()[0]["result"]["checks"]


def clean_row(receptor):
    return {"parent_id": "p", "parent_smiles": "CCO", "coordinate_source": "DOCKED_POSE",
            "hydrogens": "EXPLICIT_ALL", "hydrogen_count": 6, "heavy_atom_count": 3,
            "formal_charge": 0, "stereo_smiles": "CCO", "protonation_state_id": "explicit:ph7.4",
            "receptor_id": "sha256:" + hashlib.sha256(receptor.read_bytes()).hexdigest(), "status": "OK"}


def test_receptor_changed_after_authorization_is_refused(tmp_path):
    receptor = tmp_path / "receptor.pdb"
    receptor.write_text("v1", encoding="utf-8")
    row = clean_row(receptor)
    grants = authorize([row], receptor_path=receptor, toolchain_active=True).grants
    require(row, grants, receptor_path=receptor)
    receptor.write_text("v2", encoding="utf-8")
    with pytest.raises(NotAuthorized, match="receptor"):
        require(row, grants, receptor_path=receptor)


def test_multi_endpoint_model_learns_signed_transfer_without_mixing_units():
    candidates = {str(i): Candidate(str(i), "C", (float(i),)) for i in range(6)}
    endpoints = {"high": Endpoint("high", "t", "pIC50", "pIC50", "assay-v1", 10, direction="maximize", requires_handoff=False),
                 "low": Endpoint("low", "t", "dock", "kcal/mol", "dock-v1", 1, requires_handoff=False)}
    observations = [{"admitted": True, "result": Evaluation(str(i), task, (i if task == "high" else -10*i),
                      endpoint.units, endpoint.cost).as_dict()}
                    for i in range(4) for task, endpoint in endpoints.items()]
    model = MultiEndpointGP(candidates, endpoints, "high", observations)
    assert model.snapshot()["objective_correlations"]["low"] < 0
    assert np.linalg.eigvalsh(model.task_cov).min() > 0
    high, _ = model.predict(["0", "3"], "high")
    low, _ = model.predict(["0", "3"], "low")
    assert high[1] > high[0] and low[1] < low[0]
    assert model.objective_reduction(["5"], "low")[0] > 0
    independent = MultiEndpointGP(candidates, endpoints, "high", observations[:2])
    assert independent.objective_reduction(["5"], "low")[0] == 0


def test_replication_reduces_uncertainty_and_respects_attempt_limit(tmp_path):
    store = setup_store(tmp_path, count=1, replicas=2, batch_size=1)
    campaign = ActiveCampaign(store, exact_executor)
    campaign.run_round()
    sd_before = campaign.plan()[0].predict(["p00"], "high")[1][0]
    campaign.run_round()
    sd_after = campaign.plan()[0].predict(["p00"], "high")[1][0]
    assert sd_after < sd_before
    assert campaign.run_round()["stop_reason"] == "no_eligible_actions"
    assert [a["replicate"] for a in store.actions()] == [0, 1]


def test_full_pool_featurization_keeps_ids_aligned_with_invalid_smiles():
    from etalon.active.adapters import molecular_candidates

    candidates, rejected = molecular_candidates({"a": "CCO", "b": "not-a-smiles", "c": "c1ccccc1"})
    assert [c.id for c in candidates] == ["a", "c"]
    assert rejected == ["b"]
    assert len(candidates[0].features) == len(candidates[1].features) > 1024


def test_read_only_inspection_neither_creates_nor_mutates_journals(tmp_path):
    with pytest.raises(FileNotFoundError):
        CampaignStore(tmp_path / "absent.sqlite", read_only=True)
    store = setup_store(tmp_path)
    before = store.path.read_bytes()
    read_only = CampaignStore(store.path, read_only=True)
    ActiveCampaign(read_only).plan()
    read_only.status()
    assert before == store.path.read_bytes()
    with pytest.raises(StateError, match="read-only"):
        read_only.start_round({})


def test_cli_demo_and_status(tmp_path, capsys):
    from etalon.__main__ import main

    assert main(["active", "demo", "--workspace", str(tmp_path), "--size", "16", "--rounds", "3"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "offline_replay" and result["new_rounds"] == 3
    assert main(["active", "status", "--database", str(tmp_path / "campaign.sqlite")]) == 0
    assert json.loads(capsys.readouterr().out)["pending"] == []
    assert main(["active", "status", "--database", str(tmp_path / "absent.sqlite")]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_cli_existing_output_refuses_before_executing_more_actions(tmp_path, capsys):
    from etalon.__main__ import main

    output = tmp_path / "report.json"
    output.write_text("existing evidence", encoding="utf-8")
    assert main(["active", "demo", "--workspace", str(tmp_path), "--output", str(output)]) == 2
    assert "overwrite" in json.loads(capsys.readouterr().out)["error"]
    assert output.read_text(encoding="utf-8") == "existing evidence"
    assert not (tmp_path / "campaign.sqlite").exists()


def test_benchmark_controls_and_reproducibility(tmp_path):
    from etalon.active.benchmark import benchmark

    arguments = {"seeds": (0, 1), "budget": 64, "size": 16, "rounds": 8,
                 "policies": ("random", "cost_only", "cost_aware")}
    first = benchmark(tmp_path, **arguments)
    second = benchmark(tmp_path, **arguments)
    assert first == second
    assert len(first["runs"]) == 6
    assert "neither CADD efficacy" in first["claim"]
    assert all(r["balance"]["spent"] <= 64 and r["balance"]["reserved"] == 0 for r in first["runs"])
