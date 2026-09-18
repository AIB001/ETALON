"""Evidence-aware decisions must be costed, acquired-data-only and independently auditable."""

from __future__ import annotations

import copy
import json

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
)
from etalon.active.model import MultiEndpointGP
from etalon.active.reliability import admission_estimate
from etalon.active.replay import from_manifest, synthetic_manifest


def campaign(tmp_path, **settings):
    spec = CampaignSpec("high", 20, "quote", "test/1", policy="decision_aware", **settings)
    high = Endpoint("high", "t", "reference", "u", "h1", 5, requires_handoff=False)
    low = Endpoint("low", "t", "proxy", "v", "l1", 1, requires_handoff=False)
    store = CampaignStore(tmp_path / "journal.sqlite")
    store.configure(spec, [high, low])
    store.add_candidates([Candidate(f"m{i}", "CCO", (float(i),)) for i in range(8)])
    return ActiveCampaign(store, lambda a, c, e, g: Evaluation(c.id, e.id, c.features[0], e.units, e.cost))


@pytest.mark.parametrize("field,value", [("calibration_fraction", -0.1), ("calibration_fraction", float("nan")),
                                         ("confirmation_reserve", -1), ("confirmation_reserve", 0.5),
                                         ("validity_mode", "calibrated"), ("max_kg_candidates", 0)])
def test_invalid_new_settings_rejected(field, value):
    with pytest.raises(ValueError):
        CampaignSpec("h", 10, "u", "r", **{field: value})


def test_new_settings_do_not_invalidate_legacy_journal_defaults(tmp_path):
    run = campaign(tmp_path)
    spec, endpoints = run.store.configuration()
    with run.store.connection(write=True) as db:
        old = json.loads(db.execute("SELECT body FROM metadata WHERE key='configuration'").fetchone()[0])
        for key in ("calibration_fraction", "confirmation_reserve", "validity_mode", "max_kg_candidates"):
            old["spec"].pop(key)
        body = json.dumps(old)
        db.execute("UPDATE metadata SET body=? WHERE key='configuration'", (body,))
    run.store.configure(spec, list(endpoints.values()))
    with run.store.connection() as db:
        assert db.execute("SELECT body FROM metadata WHERE key='configuration'").fetchone()[0] == body


def test_new_policy_returns_one_action_and_replans_real_feedback(tmp_path):
    run = campaign(tmp_path, batch_size=4)
    _, choices, _ = run.plan()
    assert len(choices) == 1
    assert choices[0].evidence["requested_batch_size"] == 4
    result = run.run(max_rounds=3)
    assert len(result["rounds"]) == 3
    assert [r["model"]["training_size"] for r in run.store.rounds()] == [0, 1, 2]
    assert all(r["planning_seconds"] >= 0 for r in run.store.rounds())


def test_terminal_budget_is_spent_on_objective_not_proxy(tmp_path):
    run = campaign(tmp_path)
    for i in range(3):
        run.store.import_evaluation(Evaluation(f"m{i}", "high", float(i), "u", 5), source_id=str(i))
    _, choices, _ = run.plan()
    assert len(choices) == 1
    assert choices[0].endpoint_id == "high"
    assert choices[0].evidence["reason"] == "terminal-confirmation"
    run.run_round()
    assert run.store.balance()["remaining"] == 0


def test_proxy_cannot_use_insufficient_confirmation_budget_even_via_direct_reserve(tmp_path):
    run = campaign(tmp_path)
    run.store.import_evaluation(Evaluation("m0", "high", 1, "u", 15), source_id="cost-overrun")
    # The updated quote is now 15, not the original 5. A stale plan cannot reserve a proxy.
    round_id = run.store.start_round({})
    with pytest.raises(BudgetExhausted, match="confirmation"):
        run.store.reserve(round_id, "m1", "low", {"confirmation_budget_guard": 0})
    assert run.store.balance()["reserved"] == 0


def test_insufficient_confirmation_budget_has_explicit_stop_reason(tmp_path):
    run = campaign(tmp_path)
    run.store.import_evaluation(Evaluation("m0", "high", 1, "u", 17), source_id="overrun")
    _, choices, reason = run.plan()
    assert not choices
    assert reason == "confirmation_unaffordable"


def test_guard_ablation_releases_proxy_budget(tmp_path):
    run = campaign(tmp_path, confirmation_reserve=0)
    run.store.import_evaluation(Evaluation("m0", "high", 1, "u", 15), source_id="cost-overrun")
    round_id = run.store.start_round({})
    action = run.store.reserve(round_id, "m1", "low", {})
    assert action.reserved_cost == 1


def test_protocol_calibration_spending_is_limited_across_new_protocols(tmp_path):
    manifest = synthetic_manifest(seed=0, size=16, policy="decision_aware", batch_size=1)
    manifest["spec"]["calibration_fraction"] = 0.025  # 3 units shared by ALL protocols.
    run = from_manifest(manifest, tmp_path / "journal.sqlite")
    # Real executions here still use the sealed replay's original endpoint table.
    run.run(max_rounds=100)
    scouts = [a for a in run.store.actions() if a["decision"].get("reason") == "protocol-calibration"]
    assert sum(a["cost"] for a in scouts) <= 3
    assert run.store.balance()["remaining"] >= 0
    assert all(a["decision"]["calibration_budget_cap"] == 3 for a in scouts)


def test_registering_protocols_does_not_reset_the_total_scouting_allowance(tmp_path):
    run = campaign(tmp_path)
    for i in range(4):
        run.store.import_evaluation(Evaluation(f"m{i}", "high", float(i), "u", 0), source_id=str(i))
    for i in range(5):
        endpoint = Endpoint(f"redesigned-{i}", "t", "proxy", "v", f"recipe-{i}", 1, requires_handoff=False)
        run.store.register_endpoints([endpoint], rationale="a new independently composable protocol")
        run.run_round()
    scouts = [a for a in run.store.actions() if a["decision"].get("reason") == "protocol-calibration"]
    assert len(scouts) == 3
    assert sum(a["cost"] for a in scouts) == 3
    assert len(run.store.configuration()[1]) == 7


def test_exhausted_failed_objective_is_not_a_terminal_decision(tmp_path):
    run = campaign(tmp_path, bootstrap=2, calibration_fraction=0)
    for i in range(2):
        run.store.import_evaluation(Evaluation(f"m{i}", "high", float(i), "u", 0), source_id=str(i))
    run.store.import_evaluation(Evaluation("m2", "high", None, "u", 0, status="failed"), source_id="failed")
    _, choices, _ = run.plan()
    assert choices[0].evidence["decision_pool_size"] == 7
    assert choices[0].evidence["excluded_unconfirmable_decisions"] == 1


def test_unconfirmable_prediction_is_separate_from_attainable_recommendation(tmp_path, monkeypatch):
    run = campaign(tmp_path)
    run.store.import_evaluation(Evaluation("m2", "high", None, "u", 0, status="failed"), source_id="failed")

    def predict(self, ids, endpoint):
        return np.array([-99.0 if key == "m2" else 0.0 for key in ids]), np.ones(len(ids))

    monkeypatch.setattr(MultiEndpointGP, "predict", predict)
    result = run.recommend()
    assert result["provisional"]["candidate_id"] == "m2"
    assert not result["provisional"]["confirmation_eligible"]
    assert result["attainable"]["candidate_id"] != "m2"
    assert result["evidence_backed"] is None


def test_planning_limit_is_explicit_not_silent_candidate_subsampling(tmp_path):
    run = campaign(tmp_path, max_kg_candidates=4)
    with pytest.raises(ValueError, match="global KG pilot limit"):
        run.plan()
    assert not run.store.actions()
    # Reporting a recommendation does not need a quadratic acquisition calculation.
    assert run.recommend()["provisional"] is not None


def test_unobserved_oracle_values_do_not_change_decision_or_recommendation(tmp_path):
    first = synthetic_manifest(seed=2, size=16, policy="decision_aware")
    second = copy.deepcopy(first)
    acquired = {(r["result"]["candidate_id"], r["result"]["endpoint_id"]) for r in first["initial"]}
    for row in second["oracle"]:
        if (row["candidate_id"], row["endpoint_id"]) not in acquired:
            row["value"] = 99999
    one = from_manifest(first, tmp_path / "one.sqlite")
    two = from_manifest(second, tmp_path / "two.sqlite")
    assert one.plan()[1:] == two.plan()[1:]
    # Imported evidence action identities are source-stable, unlike generated action UUIDs.
    assert one.recommend() == two.recommend()


@pytest.mark.parametrize("seed", range(5))
def test_objective_unit_scaling_preserves_action_not_an_absolute_kg_epsilon(tmp_path, seed):
    original = synthetic_manifest(seed=seed, size=16, policy="decision_aware")
    original["spec"]["calibration_fraction"] = 0
    changed = copy.deepcopy(original)
    scale = 1e-16
    for endpoint in changed["endpoints"]:
        if endpoint["id"] == "reference":
            for field in ("prior_scale", "prior_mean", "noise"):
                endpoint[field] *= scale
    # synthetic_manifest intentionally shares warm-start dicts with oracle rows; deepcopy
    # preserves that alias. Scale each physical row once, not twice for acquired entries.
    rows = {id(row): row for row in changed["oracle"] + [r["result"] for r in changed["initial"]]}
    for row in rows.values():
        if row["endpoint_id"] == "reference":
            row["value"] *= scale
    first = from_manifest(original, tmp_path / "first.sqlite").plan()[1][0]
    second = from_manifest(changed, tmp_path / "second.sqlite").plan()[1][0]
    assert (first.candidate_id, first.endpoint_id) == (second.candidate_id, second.endpoint_id)
    assert first.evidence["reason"] == second.evidence["reason"] == "global-knowledge-gradient"


def test_recommendation_distinguishes_unobserved_predictions_and_objective_evidence(tmp_path):
    run = campaign(tmp_path)
    before = run.store.path.read_bytes()
    empty = run.recommend()
    assert run.store.path.read_bytes() == before
    assert empty["provisional"] is not None
    assert not empty["provisional"]["has_objective_evidence"]
    assert empty["evidence_backed"] is None
    run.store.import_evaluation(Evaluation("m0", "low", -100, "v", 1), source_id="proxy")
    assert run.recommend()["evidence_backed"] is None
    run.store.import_evaluation(Evaluation("m1", "high", 3, "u", 5), source_id="objective")
    evidence = run.recommend()["evidence_backed"]
    assert evidence["candidate_id"] == "m1"
    assert evidence["protocol"] == "h1"
    assert evidence["observed_values"] == [3]
    assert evidence["evidence_action_ids"][0].startswith("import-")


def test_reliability_is_local_not_hidden_numerical_labels_or_replica_count(tmp_path):
    run = campaign(tmp_path)
    spec, endpoints = run.store.configuration()
    candidates = run.store.candidates()
    model = MultiEndpointGP(candidates, endpoints, spec.objective, [])
    # Set a narrow, explicit kernel just for this numerical locality unit test.
    model.lengthscale2 = 0.01
    good = {"admitted": True, "result": Evaluation("m0", "low", 9, "v", 1).as_dict()}
    bad = {"admitted": False, "result": Evaluation("m7", "low", -999, "v", 1, status="invalid").as_dict()}
    p, support = admission_estimate(model, ["m0", "m7"], "low", [good, bad])
    assert p[0] > p[1]
    assert np.all(support >= 0)
    repeated, _ = admission_estimate(model, ["m0", "m7"], "low", [good] * 20 + [bad])
    np.testing.assert_allclose(p, repeated)
    changed = copy.deepcopy(bad)
    changed["result"]["value"] = 999
    np.testing.assert_allclose(p, admission_estimate(model, ["m0", "m7"], "low", [good, changed])[0])
    global_p, _ = admission_estimate(model, ["m0", "m7"], "low", [good, bad], mode="global")
    np.testing.assert_allclose(global_p, [0.5, 0.5])
    np.testing.assert_allclose(admission_estimate(model, ["m0"], "low", [bad], mode="none")[0], [1])
    blocked = copy.deepcopy(bad)
    blocked["result"]["status"] = "blocked"
    assert admission_estimate(model, ["m7"], "low", [blocked])[0][0] == 0.5


def test_failed_confirmation_is_paid_but_not_claimed_as_evidence(tmp_path):
    run = campaign(tmp_path)
    run.executor = lambda a, c, e, g: Evaluation(c.id, e.id, None, e.units, e.cost, status="failed")
    run.run_round()
    assert run.store.balance()["spent"] == 5
    assert run.recommend()["evidence_backed"] is None


def test_cli_recommend_is_read_only_and_json_serializable(tmp_path, capsys):
    from etalon.__main__ import main

    run = campaign(tmp_path)
    assert main(["active", "recommend", "--database", str(run.store.path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["evidence_backed"] is None
    assert not run.store.actions()
