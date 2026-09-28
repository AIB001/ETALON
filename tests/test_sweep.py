"""The throughput regime, tested against the failures that produced it.

Every test here names a thing that went wrong in one real 1,056,280-molecule campaign. That is the
standard for this file: a test whose failure mode was never observed is a test of an opinion.
"""

from __future__ import annotations

import os

import pytest

from etalon.authority.gate import (
    GateAuthorization,
    authorize_gate,
    calibration_digest,
    require_gate,
    unauthorized_gate,
)
from etalon.authority.grant import NotAuthorized
from etalon.boundary.screen import ScreenResult, StageOutcome
from etalon.campaign.calibrate import (
    Calibration,
    PanelMember,
    Separation,
    TierVerdict,
    separation,
)
from etalon.campaign.ledger import Ledger
from etalon.campaign.sweep import Sweep, SweepError, library_rows
from etalon.generate.productivity import Chunk, Productivity, allocate, window

# -- the real SND1 calibration panel, measured 2026-09-25 ---------------------
#
# Uni-Dock scores, lower is better. The two co-crystal ligands score worse than imatinib, which does
# not bind SND1 at all -- which is why the separation verdict matters as much as the recall one.
PANEL = (
    PanelMember("bdb_570nM", True, "Kd 570 nM"),
    PanelMember("bdb_23uM", True, "Kd 23.6 uM"),
    PanelMember("bdb_279uM", True, "Kd 279 uM"),
    PanelMember("C-26-A2", True, "co-crystal 7KNW"),
    PanelMember("C-26-A6", True, "co-crystal 7KNX"),
    PanelMember("imatinib", False, "negative control"),
    PanelMember("aspirin", False, "negative control"),
    PanelMember("caffeine", False, "negative control"),
)
SCORES = tuple(
    {"parent_id": parent, "engine_id": "unidock", "score": score}
    for parent, score in (
        ("bdb_23uM", -7.78),
        ("bdb_570nM", -7.76),
        ("bdb_279uM", -7.21),
        ("imatinib", -6.57),
        ("C-26-A6", -6.05),
        ("C-26-A2", -5.90),
        ("aspirin", -5.07),
        ("caffeine", -4.85),
    )
)


def stage(stage_id: str, status: str, *, artifact: str | None = None, code: str | None = None):
    return StageOutcome(
        stage_id=stage_id,
        plugin="plugin@1",
        status=status,
        attempts=1,
        artifact_id=artifact,
        error=None if code is None else {"code": code, "message": "no rows"},
    )


class FakeScreen:
    """Only ``state`` and ``provenance`` are reached from :class:`Sweep`; nothing starts a run."""

    def __init__(self) -> None:
        self.runs: dict[str, ScreenResult] = {}

    def state(self, run_id: str) -> ScreenResult | None:
        return self.runs.get(run_id)

    def provenance(self) -> dict[str, object]:
        return {"name": "molcascade", "tree_sha256": "deadbeef", "pinned": True}


def calibration(*, deleted: tuple[str, ...] = (), revision: str = "rev-1") -> Calibration:
    survived = len([m for m in PANEL if m.known_active]) - len(deleted)
    return Calibration(
        revision_id=revision,
        run_id="panel-run",
        outcome="committed",
        panel_size=len(PANEL),
        actives=sum(1 for m in PANEL if m.known_active),
        registered=len(PANEL),
        tiers=(
            TierVerdict(
                "t9_docking",
                "docking",
                len(PANEL),
                survived,
                deleted,
                ("imatinib", "aspirin", "caffeine"),
            ),
        ),
        finalize=(),
        separation=separation(SCORES, PANEL),
    )


@pytest.fixture()
def keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixed signing key, so a token minted in one test verifies in the same test."""

    monkeypatch.setenv("ETALON_AUTHORITY_KEY", "test-key")


@pytest.fixture()
def sweep(tmp_path, keyed) -> Sweep:
    screen = FakeScreen()
    made = Sweep(
        screen,
        Ledger(tmp_path / "ledger.jsonl"),
        tmp_path / "pool.sqlite",
        batch_size=5,
        gate=authorize_gate(calibration(), provenance=screen.provenance()),
    )
    made.screen = screen  # type: ignore[assignment] -- the test drives the fake directly
    return made


# -- exhaustion is an outcome, not a failure ---------------------------------


@pytest.mark.parametrize(
    "code",
    [
        # All three appeared in one campaign. Two were misfiled as failures before the family was
        # recognised, which is why this is parametrised rather than asserted once.
        "EVIDENCE_GATE_EMPTY_PARENT_INPUT",
        "FEATURE_EMPTY_INPUT",
        "POSE_STRAIN_EMPTY_PARENT_INPUT",
    ],
)
def test_every_emptiness_code_reads_as_exhausted(code: str) -> None:
    result = ScreenResult(
        "batch_0053",
        "rev-1",
        "FAILED",
        (stage("docking", "SUCCEEDED", artifact="artifact:sha256:aa"), stage("gate", "FAILED", code=code)),
    )
    assert result.exhausted
    assert result.outcome == "exhausted"
    assert result.exhaustion is not None and result.exhaustion.stage_id == "gate"


def test_a_real_failure_is_not_read_as_exhaustion() -> None:
    result = ScreenResult(
        "batch_0007", "rev-1", "FAILED", (stage("docking", "FAILED", code="UNIDOCK_REQUIRES_CUDA"),)
    )
    assert not result.exhausted
    assert result.outcome == "failed"


def test_a_code_merely_containing_empty_input_is_not_exhaustion() -> None:
    # The family is anchored at both ends deliberately: a code about a missing input *file* is a
    # defect to fix, not a gate that emptied.
    result = ScreenResult(
        "b", "rev-1", "FAILED", (stage("load", "FAILED", code="EMPTY_INPUT_FILE_MISSING"),)
    )
    assert result.outcome == "failed"


def test_exhausted_run_still_reports_its_committed_stages() -> None:
    # The whole reason exhaustion is not a failure: the scores are there. One such batch held 7,545.
    result = ScreenResult(
        "b",
        "rev-1",
        "FAILED",
        (
            stage("docking", "SUCCEEDED", artifact="artifact:sha256:aa"),
            stage("docking_2", "SUCCEEDED", artifact="artifact:sha256:bb"),
            stage("pose_strain", "FAILED", code="POSE_STRAIN_EMPTY_PARENT_INPUT"),
        ),
    )
    assert result.outcome == "exhausted"
    assert len(result.committed) == 2


# -- gate calibration --------------------------------------------------------


def test_shipped_defaults_are_refused_on_the_real_panel() -> None:
    # MolCascade's defaults delete all five known actives including both co-crystal ligands.
    everything = tuple(m.parent_id for m in PANEL if m.known_active)
    verdict = calibration(deleted=everything)
    assert not verdict.admissible
    assert verdict.recall == 0.0
    assert set(verdict.deleted_actives) == set(everything)
    assert any("deleted known actives" in reason for reason in verdict.refusals())


def test_a_gate_that_keeps_every_active_is_admissible() -> None:
    verdict = calibration()
    assert verdict.admissible
    assert verdict.recall == 1.0
    assert verdict.refusals() == ()


def test_unreadable_panel_fails_closed() -> None:
    # "We could not tell" must not read the same as "they kept everything".
    verdict = Calibration(
        revision_id="rev-1",
        run_id="r",
        outcome="committed",
        panel_size=len(PANEL),
        actives=5,
        registered=0,
        tiers=(TierVerdict("t", "t", None, None, (), (), unavailable="no id column recorded"),),
        finalize=(),
        separation=(),
    )
    assert verdict.recall is None
    assert not verdict.admissible
    assert any("could not be read" in reason for reason in verdict.refusals())


def test_a_panel_with_no_declared_actives_measures_nothing() -> None:
    verdict = Calibration("rev", "r", "committed", 3, 0, 3, (), (), ())
    assert not verdict.admissible
    assert any("no known actives" in reason for reason in verdict.refusals())


def test_post_tier_stages_count_toward_recall() -> None:
    # The shortlist selector caps molecules per Murcko scaffold, and a calibration panel is a
    # congeneric series -- so it can delete more than every gate combined while each tier reports
    # keeping everything.
    verdict = Calibration(
        revision_id="rev-1",
        run_id="r",
        outcome="committed",
        panel_size=len(PANEL),
        actives=5,
        registered=len(PANEL),
        tiers=(TierVerdict("t9", "docking", 8, 8, (), ()),),
        finalize=(TierVerdict("shortlist", "scaffold cap", 8, 2, ("C-26-A2", "C-26-A6"), ()),),
        separation=(),
    )
    assert verdict.deleted_actives == ("C-26-A2", "C-26-A6")
    assert not verdict.admissible


def test_known_active_must_be_an_explicit_boolean() -> None:
    with pytest.raises(ValueError, match="explicit boolean"):
        PanelMember("x", 1)  # type: ignore[arg-type]


# -- separation: the finding a table nobody computed --------------------------


def test_the_real_panel_does_not_separate() -> None:
    row = separation(SCORES, PANEL)[0]
    assert row.separates is False
    # imatinib at -6.57 outscores both co-crystal ligands.
    assert row.actives_below_best_inactive == 2
    # ...while still not beating the best active, which is why the weaker test was wrong.
    assert row.inactives_above_best_active == 0


def test_a_separating_score_is_reported_as_rankable() -> None:
    clean = tuple(
        {"parent_id": p, "engine_id": "e", "score": s}
        for p, s in (("a1", -9.0), ("a2", -8.8), ("i1", -6.0), ("i2", -5.0))
    )
    panel = (PanelMember("a1", True), PanelMember("a2", True), PanelMember("i1", False), PanelMember("i2", False))
    assert separation(clean, panel)[0].separates is True


def test_separation_is_unevaluable_with_one_class() -> None:
    only_actives = (PanelMember("a1", True), PanelMember("a2", True))
    rows = separation(
        ({"parent_id": "a1", "engine_id": "e", "score": -9.0}, {"parent_id": "a2", "engine_id": "e", "score": -8.0}),
        only_actives,
    )
    assert rows[0].separates is None


def test_best_pose_per_molecule_is_used() -> None:
    panel = (PanelMember("a1", True), PanelMember("i1", False))
    many = (
        {"parent_id": "a1", "engine_id": "e", "score": -5.0},
        {"parent_id": "a1", "engine_id": "e", "score": -9.0},
        {"parent_id": "i1", "engine_id": "e", "score": -6.0},
    )
    assert separation(many, panel)[0].best_active == -9.0


# -- gate authorization ------------------------------------------------------


def test_an_inadmissible_configuration_cannot_be_authorized(keyed) -> None:
    with pytest.raises(NotAuthorized, match="deleted known actives"):
        authorize_gate(calibration(deleted=("C-26-A2",)))


def test_a_token_is_bound_to_its_revision(keyed) -> None:
    token = authorize_gate(calibration(revision="rev-1"))
    assert require_gate(token, "rev-1") is token
    with pytest.raises(NotAuthorized, match="configuration changed"):
        require_gate(token, "rev-2")


def test_no_token_is_refused_with_the_remedy(keyed) -> None:
    with pytest.raises(NotAuthorized, match="known-active panel"):
        require_gate(None, "rev-1")


def test_an_edited_token_does_not_verify(keyed) -> None:
    import dataclasses

    token = authorize_gate(calibration())
    assert dataclasses.replace(token, recall=0.1).valid() is False


def test_an_unrecorded_engine_is_reported_as_unchecked(keyed) -> None:
    token = authorize_gate(calibration())
    assert "ENGINE_IDENTITY_UNRECORDED" in token.unchecked


def test_a_score_that_cannot_rank_is_flagged_not_refused(keyed) -> None:
    # The real panel: admissible once the gate is widened, and still not a ranking.
    token = authorize_gate(calibration(), provenance={"tree_sha256": "abc"})
    assert token.rankable_engines == ()
    assert "SCORE_NOT_SHOWN_TO_RANK" in token.unchecked


def test_calibration_digest_ignores_the_score_table(keyed) -> None:
    left = calibration()
    right = Calibration(**{**{f.name: getattr(left, f.name) for f in left.__dataclass_fields__.values()},
                           "scores": ({"parent_id": "x", "engine_id": "e", "score": -1.0},)})
    assert calibration_digest(left) == calibration_digest(right)


def test_unauthorized_gate_reports_without_raising() -> None:
    report = unauthorized_gate(calibration(deleted=("C-26-A2", "C-26-A6")))
    assert report["admissible"] is False
    assert set(report["deleted_actives"]) == {"C-26-A2", "C-26-A6"}


# -- the sweep ---------------------------------------------------------------


def test_deduplication_is_global_and_permanent(sweep: Sweep) -> None:
    first = sweep.admit([(f"K{i}", f"C{i}", "flowr") for i in range(8)])
    second = sweep.admit([(f"K{i}", f"C{i}", "molcraft") for i in range(4)])
    assert (first.accepted, first.duplicates) == (8, 0)
    assert (second.accepted, second.duplicates) == (0, 4)
    # The first proposer keeps the attribution, which is what makes a shortlist traceable.
    assert sweep.state()["pool"]["by_source"] == {"flowr": 8}


def test_a_batch_is_carved_only_when_the_watermark_is_reached(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", "C", "flowr") for i in range(8)])
    carved = sweep.emit(revision_id="rev-1")
    assert [b.batch_id for b in carved] == ["batch_0001"]
    assert sweep.ready() == 3


def test_flush_carves_the_short_remainder_and_stops(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", "C", "flowr") for i in range(7)])
    carved = sweep.emit(revision_id="rev-1", flush=True)
    assert [b.size for b in carved] == [5, 2]
    assert sweep.ready() == 0


def test_emitting_without_a_gate_authorization_is_refused(tmp_path, keyed) -> None:
    bare = Sweep(FakeScreen(), Ledger(tmp_path / "l.jsonl"), tmp_path / "p.sqlite", batch_size=2)
    bare.admit([("K1", "C", "f"), ("K2", "C", "f")])
    with pytest.raises(NotAuthorized, match="known-active panel"):
        bare.emit(revision_id="rev-1")


def test_emitting_with_a_token_for_another_funnel_is_refused(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", "C", "f") for i in range(5)])
    with pytest.raises(NotAuthorized, match="configuration changed"):
        sweep.emit(revision_id="rev-EDITED")


def test_two_screeners_cannot_claim_one_batch(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", "C", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    with pytest.raises(SweepError, match="not claimable"):
        sweep.claim("batch_0001", by="gpu6")


def test_an_anonymous_claim_is_refused(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", "C", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    with pytest.raises(ValueError, match="name its claimer"):
        sweep.claim("batch_0001", by="  ")


def test_an_outcome_is_append_only(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", "C", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    done = ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("shortlist", "SUCCEEDED", artifact="a"),))
    sweep.record("batch_0001", done)
    with pytest.raises(SweepError, match="already recorded"):
        sweep.record("batch_0001", done)


def test_recording_under_a_different_revision_is_refused(sweep: Sweep) -> None:
    # The provenance hole 56 hand-recorded batches had.
    sweep.admit([(f"K{i}", "C", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    with pytest.raises(SweepError, match="not comparable"):
        sweep.record(
            "batch_0001",
            ScreenResult("batch_0001", "rev-EDITED", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)),
        )


def test_an_exhausted_batch_is_recorded_as_a_measurement(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", "C", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    recorded = sweep.record(
        "batch_0001",
        ScreenResult(
            "batch_0001",
            "rev-1",
            "FAILED",
            (
                stage("docking", "SUCCEEDED", artifact="artifact:sha256:aa"),
                stage("pose_strain", "FAILED", code="POSE_STRAIN_EMPTY_PARENT_INPUT"),
            ),
        ),
    )
    assert recorded.outcome == "exhausted"
    entry = sweep.ledger.entries()[-1]
    assert entry.kind == "batch"
    assert entry.body["exhausted_at"] == "pose_strain"
    assert entry.body["committed_stages"] == 1


def test_the_ledger_line_carries_the_revision_and_the_gate(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", "C", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    sweep.record("batch_0001", ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)))
    body = sweep.ledger.entries()[-1].body
    assert body["revision_id"] == "rev-1"
    assert body["gate"] == sweep.gate.calibration_sha256  # type: ignore[union-attr]
    assert body["infrastructure"]["tree_sha256"] == "deadbeef"


# -- recovery: the six-hour orphan -------------------------------------------


def _three_claimed(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", "C", "f") for i in range(15)])  # batch_size=5, so exactly three batches
    sweep.emit(revision_id="rev-1")
    for batch, who in zip(sweep.pending(), ("gpu5", "gpu6", "gpu7"), strict=True):
        sweep.claim(batch.batch_id, by=who)


def test_a_finished_run_is_recorded_whatever_became_of_its_claimer(sweep: Sweep) -> None:
    # Measured: a screen succeeded at 17:30 and its batch sat claimed until 21:16.
    _three_claimed(sweep)
    sweep.screen.runs["batch_0001"] = ScreenResult(  # type: ignore[attr-defined]
        "batch_0001", "rev-1", "SUCCEEDED", (stage("shortlist", "SUCCEEDED", artifact="a"),)
    )
    actions = {a.batch_id: a.action for a in sweep.recover()}
    assert actions["batch_0001"] == "recorded"
    assert sweep.batch("batch_0001").outcome == "committed"  # type: ignore[union-attr]


def test_a_running_run_is_left_alone(sweep: Sweep) -> None:
    # A dead supervisor does not stop a committing screen; requeueing here discards real work.
    _three_claimed(sweep)
    sweep.screen.runs["batch_0002"] = ScreenResult(  # type: ignore[attr-defined]
        "batch_0002", "rev-1", "RUNNING", (stage("docking", "RUNNING"),)
    )
    actions = {a.batch_id: a.action for a in sweep.recover()}
    assert actions["batch_0002"] == "running"
    assert sweep.batch("batch_0002").claimed  # type: ignore[union-attr]


def test_a_claim_that_never_started_a_run_is_requeued(sweep: Sweep) -> None:
    _three_claimed(sweep)
    actions = {a.batch_id: a.action for a in sweep.recover()}
    assert actions["batch_0003"] == "requeued"
    assert "batch_0003" in {b.batch_id for b in sweep.pending()}


def test_a_running_run_is_requeued_only_on_an_operator_assertion(sweep: Sweep) -> None:
    _three_claimed(sweep)
    sweep.screen.runs["batch_0002"] = ScreenResult(  # type: ignore[attr-defined]
        "batch_0002", "rev-1", "RUNNING", (stage("docking", "RUNNING"),)
    )
    assert {a.batch_id: a.action for a in sweep.recover()}["batch_0002"] == "running"
    after = {a.batch_id: a.action for a in sweep.recover(abandoned=("batch_0002",))}
    assert after["batch_0002"] == "requeued"


def test_recovery_never_consults_the_process_table(sweep: Sweep, monkeypatch) -> None:
    # Asserted structurally: recovery must work with no way to look at processes at all.
    import subprocess

    def refuse(*_a, **_k):
        raise AssertionError("recovery must not shell out to inspect processes")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(os, "kill", refuse)
    _three_claimed(sweep)
    assert len(sweep.recover()) == 3


# -- status ------------------------------------------------------------------


def test_more_than_one_revision_marks_the_batches_incomparable(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", "C", "f") for i in range(10)])
    sweep.emit(revision_id="rev-1")
    for batch in sweep.pending():
        sweep.claim(batch.batch_id, by="gpu5")
    sweep.record("batch_0001", ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)))
    assert sweep.state()["comparable"] is True
    # A second revision can only arrive by re-emitting under a retuned funnel; simulate the record.
    object.__setattr__(sweep, "gate", authorize_gate(calibration(revision="rev-2"), provenance={"t": "1"}))
    sweep.admit([(f"M{i}", "C", "f") for i in range(5)])
    third = sweep.emit(revision_id="rev-2")[0]
    sweep.claim(third.batch_id, by="gpu6")
    sweep.record(third.batch_id, ScreenResult(third.batch_id, "rev-2", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)))
    assert sweep.state()["comparable"] is False


def test_library_rows_always_write_an_id_column(sweep: Sweep, tmp_path) -> None:
    # Without it a run records no names, and recall, explanation and a named shortlist all fail.
    sweep.admit([(f"K{i}", f"C{i}", "flowr") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    written = library_rows(sweep, "batch_0001", tmp_path / "batch.csv")
    header, *rows = written.read_text(encoding="utf-8").splitlines()
    assert header == "id,smiles,source"
    assert len(rows) == 5


def test_molecules_of_an_unemitted_batch_is_refused(sweep: Sweep) -> None:
    with pytest.raises(SweepError, match="never emitted"):
        sweep.molecules("batch_9999")


# -- generation productivity -------------------------------------------------


def test_flowr_is_kept_and_molcraft_is_retired() -> None:
    # The real measurement: FLOWR flat at 87% for 39 chunks, MolCRAFT falling from 32% to 24%.
    flowr = Productivity("flowr", "flowr", "pocketA_22A", tuple(Chunk(5000, 4840, 4210, 3700) for _ in range(6)))
    molcraft = Productivity(
        "molcraft",
        "molcraft",
        "pocketA_22A",
        tuple(Chunk(20000, 19950, unique, 5450) for unique in (6400, 6100, 5700, 5200, 4900, 4790)),
    )
    assert not flowr.exhausted and flowr.retire() == ()
    assert molcraft.exhausted
    assert any("covered the space" in reason for reason in molcraft.retire())
    assert molcraft.trend is not None and molcraft.trend < 0


def test_a_large_box_is_reported_as_unviable_not_as_a_bad_model() -> None:
    # TargetDiff: 2 of 100 at 28 A, 92 of 100 at 22 A. The remedy is the box.
    big = Productivity("targetdiff", "targetdiff", "pocketB_28A", tuple(Chunk(100, 2, 2, 600) for _ in range(3)))
    small = Productivity("targetdiff", "targetdiff", "pocketA_22A", tuple(Chunk(100, 92, 88, 600) for _ in range(3)))
    assert not big.viable
    assert any("shrink the box" in reason for reason in big.retire())
    assert small.viable and small.retire() == ()


def test_one_bad_chunk_does_not_retire_a_generator() -> None:
    # A sampler discarding its own reconstruction failures produces an empty chunk routinely; one
    # campaign's loop aborted on exactly that and killed a working model.
    single = Productivity("flowr", "flowr", "p", (Chunk(5000, 0, 0, 3700),))
    assert not single.exhausted
    assert single.trend is None


def test_zero_uniqueness_over_enough_chunks_is_exhaustion() -> None:
    spent = Productivity("m", "m", "p", tuple(Chunk(1000, 900, 0, 100) for _ in range(3)))
    assert spent.exhausted
    assert spent.cost_multiplier is None


def test_the_window_is_the_tail_not_the_lifetime() -> None:
    falling = tuple(Chunk(20000, 19950, u, 5450) for u in (6400, 6100, 5700, 5200, 4900, 4790))
    lifetime = Productivity("m", "m", "p", falling).uniqueness[0]
    tail = Productivity("m", "m", "p", window(falling, keep=3)).uniqueness[0]
    assert tail < lifetime


def test_allocate_ranks_on_throughput_and_names_who_should_stop() -> None:
    fast = Productivity("flowr", "flowr", "p", tuple(Chunk(5000, 4840, 4210, 3700) for _ in range(6)))
    spent = Productivity("molcraft", "molcraft", "p", tuple(Chunk(20000, 19950, 4790, 5450) for _ in range(6)))
    report = allocate([spent, fast])
    assert [row["tag"] for row in report["loops"]][0] == "flowr"
    assert "molcraft" in report["retire"]


def test_a_chunk_cannot_be_more_unique_than_delivered() -> None:
    with pytest.raises(ValueError, match="new to the library"):
        Chunk(100, 10, 11, 60)
