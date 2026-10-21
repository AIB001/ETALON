"""Optional legacy advice, using synthetic chemistry labels and real local functions.

These are engineering capability checks, not an independent ranking experiment or
an execution/recovery benchmark. No PRISM task or campaign round is run.
"""

from dataclasses import replace
from pathlib import Path
import json
import sqlite3

import pytest

from etalon.boundary.screen import Screen
from etalon.authority.grant import NotAuthorized
from etalon.campaign.ledger import Ledger
from etalon.campaign.loop import Campaign
from etalon.campaign.pipeline import Pipeline
from etalon.campaign.propose import Acquisition, Panel, ParameterChange, as_loop_acquirer
from etalon.campaign.supervisor import Supervisor
from etalon.campaign.sweep import Sweep
from etalon.economics.stage import BY_ID
from etalon.judgment.proposal import Act
from etalon.learn.calibrate import Split, Verdict


def _forbidden(*args, **kwargs):
    raise AssertionError("an unrelated campaign execution was invoked")


def _panel() -> Panel:
    # The relationship is intentionally synthetic and visible. This is not test data
    # for a scientific generalization claim, nor a new undisclosed affinity panel.
    rings = ("c1ccccc1", "c1ccncc1", "C1CCCCC1", "C1CCNCC1")
    return Panel(
        smiles=tuple(("N" if i % 2 else "C") + rings[(i // 2) % 4] for i in range(40)),
        affinity_nM=tuple(10.0 if i % 2 else 1000.0 for i in range(40)),
    )


def test_standalone_parameter_advice_changes_method_after_feedback_without_a_round(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Campaign, "__init__", _forbidden)
    monkeypatch.setattr(Screen, "__init__", _forbidden)
    monkeypatch.setattr(Supervisor, "tick", _forbidden)
    judge = ParameterChange(_panel())
    unchanged = {"ranker": "constant"}
    first, feedback = judge.judge(unchanged, lambda _: 0.0, lambda _: 0.0)
    original_first = first.as_dict()
    assert feedback.verdict is Verdict.WITHIN_NOISE
    assert feedback.before.auc == feedback.after.auc == 0.5

    # The caller chooses a different illustrative method after reading the result;
    # judge neither schedules it nor changes any persisted configuration.
    second, verdict = judge.judge(
        {"ranker": "synthetic-prefix"}, lambda _: 0.0,
        lambda smiles: float(smiles.startswith("N")),
    )
    assert second.act is Act.PARAMETER_CHANGE and verdict.accepted
    assert verdict.after.auc == 1.0
    assert verdict.after.split is Split.WHOLE_PANEL
    assert "nothing held out" in verdict.note
    assert first.as_dict() == original_first and unchanged == {"ranker": "constant"}
    # Caller stops here, including after an accepted descriptive comparison.
    assert list(tmp_path.iterdir()) == []


def test_empty_acquisition_stops_before_fitting_or_legacy_orchestration(monkeypatch):
    from etalon.campaign import propose as module
    monkeypatch.setattr(module.Surrogate, "fit", _forbidden)
    monkeypatch.setattr(Campaign, "__init__", _forbidden)
    method = Acquisition(_panel(), budget=1)
    with pytest.raises(ValueError, match="no candidates"):
        method.propose({})
    assert method.last_calibration is None
    # The optional legacy adapter keeps its existing empty-pool convention.
    assert as_loop_acquirer(method)({}) is None


def test_nonempty_acquisition_fits_local_synthetic_panel_and_only_returns_advice(tmp_path, monkeypatch):
    pytest.importorskip("sklearn", reason="requires the declared optional learning dependency")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Campaign, "__init__", _forbidden)
    monkeypatch.setattr(Supervisor, "__init__", _forbidden)
    monkeypatch.setattr(Screen, "__init__", _forbidden)
    monkeypatch.setattr(Ledger, "__init__", _forbidden)
    rings = ("c1ccccc1", "c1ccncc1", "c1ccsc1", "c1cc[nH]c1", "C1CCCCC1", "C1CCNCC1")
    panel = Panel(
        smiles=tuple("C" * (i // 6 + 1) + rings[i % 6] for i in range(36)),
        affinity_nM=tuple(float(10 + ((i * 37) % 31) * 20) for i in range(36)),
    )
    assert len(set(panel.groups)) == 6
    candidates = {"fixture-a": "CCOc1ccccc1", "fixture-b": "CCNc1ccncc1",
                  "fixture-c": "CCC1CCCCC1", "fixture-d": "CCc1ccsc1"}
    original_candidates = dict(candidates)
    method = Acquisition(panel, budget=2, seed=17)
    result = method.propose(candidates)
    assert result.act is Act.SPEND
    chosen = result.payload["parent_ids"]
    assert len(chosen) == len(set(chosen)) == 2
    assert set(chosen) <= set(candidates)
    assert result.payload["unparsed_candidates"] == []
    assert method.last_calibration is not None
    assert method.last_calibration.calibration_count == 36
    assert method.last_calibration.groups_measured == 6
    assert candidates == original_candidates and list(tmp_path.iterdir()) == []
    # This advice can be discarded. Act.SPEND is a proposal tag, not an execution.
    print(json.dumps({"engineering_fixture": "synthetic-36-row-six-scaffold-panel",
                      "proposal": result.as_dict(), "next_action": "stop-without-dispatch"}))


def test_one_custom_stage_plan_skips_all_default_stages_and_can_be_abandoned(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Campaign, "__init__", _forbidden)
    monkeypatch.setattr(Supervisor, "__init__", _forbidden)
    stage = replace(BY_ID["lilly_demerits"], id="fixture-validity", label="Synthetic local filter")
    method = Pipeline(pool=40, budget_gpu_hours=1, stages=(stage.id,), final_count=10)
    proposal = method.dry_run(overrides={stage.id: stage})
    assert [tier.stage.id for tier in proposal.funnel.tiers] == [stage.id]
    assert proposal.generators is None
    with pytest.raises(KeyError, match="not in this plan"):
        method.expensive_budget(proposal)
    assert list(tmp_path.iterdir()) == []


def _pool_rows(path: Path):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("molecule", "batch")}


def test_existing_supervisor_status_needs_no_tick_generator_or_gate_and_preserves_remainder(tmp_path, monkeypatch):
    screen = Screen(tmp_path / "screen")
    ledger = Ledger(tmp_path / "ledger.jsonl")
    sweep = Sweep(screen, ledger, tmp_path / "pool.sqlite", batch_size=2)
    sweep.admit([("fixture-ethanol", "CCO", "engineering-fixture")])
    supervisor = Supervisor(sweep, revision_id="fixture-revision", workspace=tmp_path / "libraries",
                            generators=(), screen_devices=("cpu",))
    before = _pool_rows(sweep.pool)
    monkeypatch.setattr(supervisor, "tick", _forbidden)
    monkeypatch.setattr(screen, "run", _forbidden)
    reading = supervisor.state()
    assert reading["complete"] is True and reading["unscreened_remainder"] == 1
    assert reading["running"] == [] and reading["generators"] == {}
    assert supervisor.productivity()["note"] == "no finished chunks yet"
    # Optional observation does not waive emission's genuine calibration gate.
    with pytest.raises(NotAuthorized, match="authoriz"):
        sweep.emit(revision_id="fixture-revision", flush=True)
    assert _pool_rows(sweep.pool) == before and not ledger.path.exists()
