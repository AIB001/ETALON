"""The throughput regime, tested against the failures that produced it.

Every test here names a thing that went wrong in one real 1,056,280-molecule campaign. That is the
standard for this file: a test whose failure mode was never observed is a test of an opinion.
"""

from __future__ import annotations

import os
from pathlib import Path

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
    calibrate,
    HIGHER_STRONGER,
    MIXED,
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


def test_a_panel_with_no_tiers_at_all_fails_closed(keyed) -> None:
    """The retention read failing outright must not read as perfect recall.

    Found by pointing :func:`calibrate` at a real panel run rather than by a test. That run was
    screened without an id column, so ``measure_recall`` refused it, ``tiers`` came back empty, and
    the guard read ``any(tier.unavailable for tier in ())`` -- which is false. Recall was reported as
    1.0 and ``authorize_gate`` minted a token for a configuration nobody had measured.

    The earlier test above does not cover this: it constructs a tier that *says* it is unavailable,
    which is the case where something was read. Here nothing was.
    """

    verdict = Calibration(
        revision_id="rev-1",
        run_id="panel003",
        outcome="committed",
        panel_size=len(PANEL),
        actives=5,
        registered=0,
        tiers=(),
        finalize=(),
        separation=(),
    )
    assert verdict.recall is None, "no tiers means nothing was measured"
    assert not verdict.admissible
    assert any("never observed" in reason for reason in verdict.refusals())
    with pytest.raises(NotAuthorized):
        authorize_gate(verdict)


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


# -- separation: each engine read under its own convention -------------------
#
# The ALK2 panel, measured 2026-09-29. KarmaDock's MDN runs UP -- its gate is ``>= 40`` -- and this
# function used to compare it as if it ran down, which inverted every extreme it reported. The
# strongest active here is 67.329 and the weakest is 33.020; read upside down, ``best_active`` came
# back 33.020 and a reader comparing it against a threshold would have had the panel backwards.
KARMADOCK_ALK2 = tuple(
    {
        "parent_id": parent,
        "engine_id": "karmadock",
        "score": score,
        "score_kind": "KARMADOCK_MDN",
        "direction": "HIGHER_STRONGER",
    }
    for parent, score in (
        ("act_SARACATINIB", 67.329),
        ("act_PD-0166285", 61.530),
        ("act_CHEMBL3818173", 56.560),
        ("neg_AEE-788", 53.833),
        ("LDN-193189", 50.820),
        ("act_CHEMBL1241674", 44.700),
        ("act_CHEMBL4526828", 33.020),
        ("neg_aspirin", 17.280),
    )
)
ALK2_PANEL = (
    PanelMember("LDN-193189", True, "co-crystal 3Q4U"),
    PanelMember("act_SARACATINIB", True, "AZD0530"),
    PanelMember("act_PD-0166285", True, "Ki pChEMBL 8.80"),
    PanelMember("act_CHEMBL3818173", True, "IC50 pChEMBL 8.92"),
    PanelMember("act_CHEMBL1241674", True, "ChEMBL exact"),
    PanelMember("act_CHEMBL4526828", True, "ChEMBL exact"),
    PanelMember("neg_AEE-788", False, "EGFR/VEGFR inhibitor, selectivity control"),
    PanelMember("neg_aspirin", False, "not a kinase binder"),
)


def test_a_higher_is_better_engine_is_not_read_upside_down() -> None:
    row = separation(KARMADOCK_ALK2, ALK2_PANEL)[0]
    assert row.direction == HIGHER_STRONGER
    # In the engine's own units, and the way round a reader expects.
    assert row.best_active == 67.329
    assert row.worst_active == 33.020
    assert row.best_inactive == 53.833
    # Three actives score below AEE-788, so the score does not order this panel.
    assert row.actives_below_best_inactive == 3
    assert row.separates is False


def test_direction_falls_back_to_score_kind_when_the_row_omits_it() -> None:
    rows = tuple({k: v for k, v in row.items() if k != "direction"} for row in KARMADOCK_ALK2)
    assert separation(rows, ALK2_PANEL)[0].best_active == 67.329


def test_one_engine_with_two_conventions_is_unevaluable() -> None:
    confused = (
        {"parent_id": "LDN-193189", "engine_id": "e", "score": 50.8, "direction": "HIGHER_STRONGER"},
        {"parent_id": "neg_aspirin", "engine_id": "e", "score": -5.0, "direction": "LOWER_STRONGER"},
    )
    row = separation(confused, ALK2_PANEL)[0]
    assert row.direction == MIXED
    # Fails closed: a comparison across two conventions is not a measurement of anything.
    assert row.separates is None


# -- calibrate: the digest-to-name join --------------------------------------


class ScoredScreen(FakeScreen):
    """A screen carrying one engine's scores, keyed the way MolCascade really keys them.

    Every downstream contract keys on a digest of the standardised molecule. The panel declares the
    library's own identifiers. ``calibrate`` used to test one against the other directly, so nothing
    matched and ``separation`` came back empty -- which reads as *the score was not shown to rank*
    rather than *nobody looked*.
    """

    def __init__(self, *, joinable: bool = True) -> None:
        super().__init__()
        self.digest = {m.parent_id: f"parent:sha256:{i:064x}" for i, m in enumerate(ALK2_PANEL)}
        self.joinable = joinable

    def recall(self, run_id: str, *, panel_size: int | None = None) -> dict[str, object]:
        return {
            "registered": len(ALK2_PANEL),
            "tiers": [
                {"tier_id": "t9_docking", "title": "Docking", "entering": 8, "surviving": 8, "lost": []}
            ],
            "finalize": [],
            "notes": [],
        }

    def parent_names(self, result) -> dict[str, str]:  # noqa: ANN001, ARG002
        return {} if not self.joinable else {v: k for k, v in self.digest.items()}

    def artifacts_carrying(self, result, contract_id: str) -> tuple[str, ...]:  # noqa: ANN001, ARG002
        return ("artifact:sha256:aa",)

    def read(self, artifact_id: str, *, contract_id: str | None = None) -> list[dict[str, object]]:  # noqa: ARG002
        return [dict(row, parent_id=self.digest[row["parent_id"]]) for row in KARMADOCK_ALK2]


def _alk2_result() -> ScreenResult:
    return ScreenResult(
        run_id="panel",
        revision_id="rev-alk2",
        status="SUCCEEDED",
        stages=(stage("docking_score_2", "SUCCEEDED", artifact="artifact:sha256:aa"),),
    )


def test_calibrate_joins_content_addressed_parents_to_panel_names() -> None:
    screen = ScoredScreen()
    measured = calibrate(screen, _alk2_result(), ALK2_PANEL)
    assert measured.separation, "the join failed and the scores were dropped"
    row = measured.separation[0]
    assert row.actives == 6 and row.inactives == 2
    assert row.best_active == 67.329
    assert measured.rankable == ()


def test_calibrate_says_so_when_the_scores_could_not_be_joined() -> None:
    measured = calibrate(ScoredScreen(joinable=False), _alk2_result(), ALK2_PANEL)
    assert measured.separation == ()
    # The note must name the cause, because "unevaluable" and "nobody looked" read alike otherwise.
    assert any("none belong to a declared panel member" in note for note in measured.notes)


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


# -- the supervisor: the loop that replaces a person checking every five minutes ----------


def _campaign(
    tmp_path,
    keyed,
    *,
    chunk=8,
    total=24,
    batch_size=10,
    generator_devices=("cuda:0",),
    migrate=False,
):
    """A whole campaign with fake drivers, wired the way a real one is.

    ``generator_devices`` takes one entry per generation loop, so naming a device twice builds the
    two-loops-one-card configuration that ``tag`` exists to distinguish.
    """

    import json

    from etalon.campaign.generation import ChunkResult, Generator, Pocket
    from etalon.campaign.supervisor import Supervisor
    import etalon.campaign.generation as generation_module

    screen = FakeScreen()
    sweep = Sweep(
        screen,
        Ledger(tmp_path / "ledger.jsonl"),
        tmp_path / "pool.sqlite",
        batch_size=batch_size,
        gate=authorize_gate(calibration(), provenance=screen.provenance()),
    )
    generators = [
        Generator(
            tag=f"flowr_{chr(ord('a') + n)}",
            model="flowr",
            pocket=Pocket("A22", "reference", tmp_path / "ref.sdf"),
            device=device,
            protein=tmp_path / "p.pdb",
            output_root=tmp_path / "gen",
            generation_config=tmp_path / "g.yaml",
            chunk=chunk,
            total=total,
        )
        for n, device in enumerate(generator_devices)
    ]

    produced = {"n": 0}

    def generate(gen, index):
        path = gen.chunk_path(index)
        path.mkdir(parents=True, exist_ok=True)
        produced["n"] += 1
        smiles = [f"M{produced['n']}_{i}" for i in range(gen.chunk)]
        (path / "manifest.json").write_text(json.dumps({"candidate_count": gen.chunk}))
        (path / "_smiles.json").write_text(json.dumps(smiles))
        return ChunkResult(gen.tag, index, path, gen.chunk, gen.chunk, 1.0, 0)

    def read(path, tag=None):
        source = path.parent.name
        payload = path / "_smiles.json"
        if not payload.is_file():
            return []
        return [(s, s, source) for s in json.loads(payload.read_text())]

    generation_module.read_chunk = read
    generation_module.Ingest.consume = lambda self, chunk: (
        self.consumed.add(str(chunk)),
        self.state.parent.mkdir(parents=True, exist_ok=True),
        self.state.write_text(json.dumps(sorted(self.consumed))),
        read(chunk),
    )[-1]

    def screen_batch(batch_id, library, device):
        result = ScreenResult(
            batch_id, "rev-1", "SUCCEEDED", (stage("shortlist", "SUCCEEDED", artifact="a"),)
        )
        screen.runs[batch_id] = result
        return result

    supervisor = Supervisor(
        sweep,
        revision_id="rev-1",
        workspace=tmp_path / "ws",
        generators=generators,
        screen_devices=("cuda:5", "cuda:6"),
        generation=generate,
        screen=screen_batch,
        migrate_retired_devices=migrate,
    )
    return supervisor, sweep, screen


def _drain(supervisor, limit=40):
    import time

    for _ in range(limit):
        supervisor.tick()
        if supervisor.complete():
            return True
        time.sleep(0.05)
    return supervisor.complete()


def test_a_campaign_runs_itself_to_completion(tmp_path, keyed) -> None:
    """Generation, ingest, emission, screening and recording, with nobody watching."""

    supervisor, sweep, _ = _campaign(tmp_path, keyed)
    assert _drain(supervisor), "the campaign did not terminate"
    state = sweep.state()
    assert state["batches"]["recorded"] == 2
    assert state["batches"]["outstanding"] == 0
    assert state["pool"]["unique"] == 24
    assert supervisor.state()["generators"]["flowr_a"]["stopped"] is True


def test_most_passes_are_quiet_and_say_so(tmp_path, keyed) -> None:
    """The report a supervisor returns when nothing happened must be distinguishable.

    490 of one campaign's 500 readings changed nothing. A loop whose every pass looks eventful
    trains its reader to stop looking.
    """

    supervisor, _, _ = _campaign(tmp_path, keyed)
    _drain(supervisor)
    assert supervisor.tick().quiet


def test_a_supervisor_resumes_from_disk(tmp_path, keyed) -> None:
    """Kill it mid-campaign and the next one picks up from the pool, the ledger and the manifests."""

    first, sweep, screen = _campaign(tmp_path, keyed)
    first.tick()  # start generation
    import time

    time.sleep(0.2)
    first.tick()  # ingest chunk 1
    assert sweep.state()["pool"]["unique"] == 8

    second, sweep2, _ = _campaign(tmp_path, keyed)  # a new process, same paths
    assert sweep2.state()["pool"]["unique"] == 8, "the pool survived"
    assert _drain(second), "the resumed supervisor did not finish the campaign"


def test_an_already_ingested_chunk_is_not_ingested_twice(tmp_path, keyed) -> None:
    supervisor, sweep, _ = _campaign(tmp_path, keyed)
    _drain(supervisor)
    before = sweep.state()["pool"]["unique"]
    supervisor.tick()
    assert sweep.state()["pool"]["unique"] == before


def test_consecutive_barren_chunks_stop_a_loop_but_one_does_not(tmp_path, keyed) -> None:
    """One empty chunk is a sampler discarding reconstruction failures; five is the model."""

    import json

    from etalon.campaign.generation import BARREN_LIMIT, ChunkResult, Generator, Pocket
    from etalon.campaign.supervisor import Supervisor
    import etalon.campaign.generation as generation_module

    screen = FakeScreen()
    sweep = Sweep(
        screen,
        Ledger(tmp_path / "l.jsonl"),
        tmp_path / "p.sqlite",
        batch_size=10,
        gate=authorize_gate(calibration(), provenance=screen.provenance()),
    )
    generator = Generator(
        tag="empty",
        model="m",
        pocket=Pocket("A", "reference", tmp_path / "r.sdf"),
        device="cuda:0",
        protein=tmp_path / "p.pdb",
        output_root=tmp_path / "gen",
        generation_config=tmp_path / "g.yaml",
        chunk=5,
        total=1000,
    )

    def generate(gen, index):
        path = gen.chunk_path(index)
        path.mkdir(parents=True, exist_ok=True)
        (path / "manifest.json").write_text(json.dumps({"candidate_count": 0}))
        return ChunkResult(gen.tag, index, path, gen.chunk, 0, 1.0, 0, barren="model returned nothing")

    generation_module.read_chunk = lambda path, tag=None: []
    generation_module.Ingest.consume = lambda self, chunk: (
        self.consumed.add(str(chunk)),
        self.state.parent.mkdir(parents=True, exist_ok=True),
        self.state.write_text(json.dumps(sorted(self.consumed))),
        [],
    )[-1]

    supervisor = Supervisor(
        sweep,
        revision_id="rev-1",
        workspace=tmp_path / "ws",
        generators=[generator],
        screen_devices=("cuda:5",),
        generation=generate,
        screen=lambda *_: None,
    )
    import time

    for index in range(BARREN_LIMIT):
        supervisor.tick()
        time.sleep(0.05)
        supervisor.tick()
        if index < BARREN_LIMIT - 1:
            assert "empty" not in supervisor.stopped, f"stopped after {index + 1} barren chunks"
    for _ in range(3):
        supervisor.tick()
        time.sleep(0.05)
    assert "empty" in supervisor.stopped
    assert supervisor.state()["generators"]["empty"]["consecutive_barren"] >= BARREN_LIMIT


def test_the_screen_driver_refuses_an_edited_cascade() -> None:
    """A configuration edited mid-campaign is caught before it produces an incomparable batch."""

    from etalon.campaign.drivers import MolCascadeScreening, RevisionChanged

    class Compiles:
        def plan(self, *_a, **_k):
            return type("P", (), {"revision_id": "rev-EDITED"})()

    driver = MolCascadeScreening(
        screen=Compiles(), config_path=Path("cascade.json"), revision_id="rev-1"
    )
    with pytest.raises(RevisionChanged, match="not comparable"):
        driver("batch_0001", Path("batch.csv"), "cuda:0")


def test_a_chunk_is_finished_only_when_its_manifest_exists(tmp_path) -> None:
    """Per-molecule SDFs on disk with no manifest is a chunk mid-consolidation, not a finished one."""

    from etalon.campaign.generation import finished_chunks

    root = tmp_path / "gen"
    (root / "flowr" / "chunk_001" / "models").mkdir(parents=True)
    assert finished_chunks(root) == []
    (root / "flowr" / "chunk_001" / "manifest.json").write_text("{}")
    assert [p.name for p in finished_chunks(root)] == ["chunk_001"]


def test_a_failed_chunk_reads_as_empty_rather_than_raising(tmp_path) -> None:
    """A manifest beside a zero-byte candidates file took one collector down for twelve hours."""

    from etalon.campaign.generation import read_chunk

    chunk = tmp_path / "flowr" / "chunk_001"
    chunk.mkdir(parents=True)
    (chunk / "manifest.json").write_text("{}")
    (chunk / "candidates.sdf").write_bytes(b"")
    assert read_chunk(chunk) == []


def test_a_generator_resumes_at_the_first_unwritten_chunk(tmp_path) -> None:
    from etalon.campaign.generation import Generator, Pocket

    generator = Generator(
        tag="flowr",
        model="flowr",
        pocket=Pocket("A", "reference", tmp_path / "r.sdf"),
        device="cuda:0",
        protein=tmp_path / "p.pdb",
        output_root=tmp_path / "gen",
        generation_config=tmp_path / "g.yaml",
    )
    assert generator.next_index() == 1
    for index in (1, 2):
        generator.chunk_path(index).mkdir(parents=True)
    assert generator.next_index() == 3


def test_the_generation_command_sets_no_cuda_mask() -> None:
    """PRISM's wrappers disagree about the mask, so the only correct choice is to set neither."""

    from etalon.campaign.generation import Generator, Pocket

    generator = Generator(
        tag="flowr",
        model="flowr",
        pocket=Pocket("A", "reference", Path("/r.sdf")),
        device="cuda:3",
        protein=Path("/p.pdb"),
        output_root=Path("/gen"),
        generation_config=Path("/g.yaml"),
    )
    command = generator.command(1)
    assert "--device" in command and command[command.index("--device") + 1] == "cuda:3"
    assert not any("CUDA_VISIBLE_DEVICES" in part for part in command)


# -- a retired generator's device, which used to sit idle for the rest of the campaign -------


def test_a_retired_generator_hands_its_device_to_screening(tmp_path, keyed) -> None:
    """The move an operator made by hand eighteen hours in, made when the loop ends instead.

    ``screen_devices`` was fixed at construction, so a campaign that retired its generators finished
    on the screeners it started with. One campaign's hand-made version of this move -- three devices
    -- produced half that campaign's hits.
    """

    supervisor, _, _ = _campaign(tmp_path, keyed, migrate=True)
    assert "cuda:0" not in supervisor.screen_devices

    _drain(supervisor)

    assert supervisor.state()["generators"]["flowr_a"]["stopped"] is True
    assert "cuda:0" in supervisor.screen_devices, "the retired generator's card never joined"
    assert supervisor.state()["screen_devices"] == ["cuda:5", "cuda:6", "cuda:0"]


def test_migration_is_reported_and_is_not_a_quiet_pass(tmp_path, keyed) -> None:
    """A device changing hands is an event. 490 of 500 passes are quiet and this is not one of them."""

    import time

    supervisor, _, _ = _campaign(tmp_path, keyed, migrate=True)
    reports = []
    for _ in range(40):
        report = supervisor.tick()
        reports.append(report)
        if supervisor.complete():
            break
        time.sleep(0.05)

    moved = [report for report in reports if report.migrated]
    assert len(moved) == 1, "the device should change hands exactly once"
    assert moved[0].migrated == ("cuda:0",)
    assert not moved[0].quiet
    assert "cuda:0" in moved[0].as_dict()["migrated"]
    assert any("joined the screening pool" in note for note in moved[0].notes)


def test_without_the_flag_the_idle_device_is_named_once_and_not_taken(tmp_path, keyed) -> None:
    """Off by default, because on a shared host those cards may be owed back to the machine.

    An idle GPU nobody mentions is the failure this exists to stop, so the refusal to take it still
    has to say it is there -- once, not every sixty seconds for forty-four hours.
    """

    import time

    supervisor, _, _ = _campaign(tmp_path, keyed, migrate=False)
    reports = []
    for _ in range(40):
        reports.append(supervisor.tick())
        if supervisor.complete():
            break
        time.sleep(0.05)

    assert "cuda:0" not in supervisor.screen_devices, "the device was taken without being asked"
    assert all(report.migrated == () for report in reports)
    mentions = [
        note
        for report in reports
        for note in report.notes
        if "cuda:0" in note and "idle" in note
    ]
    assert len(mentions) == 1, f"said it {len(mentions)} times, not once: {mentions}"
    assert "migrate_retired_devices=True" in mentions[0]


def test_a_card_shared_by_two_loops_is_not_taken_until_both_stop(tmp_path, keyed) -> None:
    """One model across two loops on one card is a normal configuration.

    ``tag`` rather than the model is the generator's identity precisely because of this shape, and a
    card handed to screening while one of its loops still generates would contend for its memory.
    """

    supervisor, _, _ = _campaign(
        tmp_path, keyed, generator_devices=("cuda:0", "cuda:0"), total=8, chunk=8, migrate=True
    )
    assert set(supervisor.generators) == {"flowr_a", "flowr_b"}

    # Stop one loop by hand and tick: one of two is not enough.
    supervisor.stopped.add("flowr_a")
    supervisor.tick()
    assert "cuda:0" not in supervisor.screen_devices, "taken while flowr_b could still generate"

    _drain(supervisor)
    assert supervisor.state()["generators"]["flowr_b"]["stopped"] is True

    # One more pass, and the reason is the tick order: a generator stopped in `_start_generation` is
    # migrated at the top of the *next* pass, so the stop decision stays in one place rather than
    # being made twice per tick. On a sixty-second interval that is one interval of latency; here it
    # is one explicit call, and it is asserted rather than hidden because a reader draining to
    # completion would otherwise conclude migration was broken.
    assert supervisor.tick().migrated == ("cuda:0",)
    assert "cuda:0" in supervisor.screen_devices


def test_a_device_named_in_both_lists_is_not_offered_two_batches(tmp_path, keyed) -> None:
    """A duplicate lane would be claimed twice and refused by the sweep rather than by anything
    that could explain it."""

    supervisor, _, _ = _campaign(tmp_path, keyed, generator_devices=("cuda:5",), migrate=True)
    assert supervisor.screen_devices == ("cuda:5", "cuda:6")

    _drain(supervisor)
    assert supervisor.screen_devices.count("cuda:5") == 1


# -- sizing a campaign against a machine that actually exists --------------------------------


def _sweep_tool(name, **arguments):
    """Call one registered sweep tool and parse its result.

    Parsing here rather than in each test because a tool returns a JSON string: an assertion written
    against the string would pass on a substring that appeared in an error message.
    """

    import json

    from etalon.mcp import sweep

    class Collector:
        def __init__(self):
            self.tools = {}

        def tool(self, *_args, **_kwargs):
            def register(function):
                self.tools[function.__name__] = function
                return function

            return register

    collector = Collector()
    sweep.register(collector)
    return json.loads(collector.tools[name](**arguments))


def test_the_planner_reproduces_the_campaign_it_was_measured_on() -> None:
    """5 generation loops against 6 screen workers, which sat generation-limited by 3.7x."""

    plan = _sweep_tool(
        "etalon_campaign_plan", pool_size=1_056_280, screen_devices=6, generation_devices=5
    )
    assert plan["ok"]
    assert plan["capacity_ratio"] == 3.7
    assert plan["binding_constraint"] == "generation"


def test_the_advised_move_is_the_one_the_operator_actually_made() -> None:
    """The shipped formula said four; the balance point says three, and three is what that campaign's
    operator moved eighteen hours in.

    Four is not absurd -- it lands at 0.68, inside the tolerated band by 0.01 -- but it is 1.47x
    imbalanced the other way where three is 1.16x. The assertion is therefore that three sits closer
    to parity, not that four trips a threshold: it does not, and an earlier version of this test
    claimed it did.
    """

    advised = _sweep_tool(
        "etalon_campaign_plan", pool_size=1_056_280, screen_devices=6, generation_devices=5
    )
    assert advised["recommended_split"] == {
        "total_devices": 11, "generation": 8, "screening": 3, "basis": "declared",
    }
    assert "Move 3 device(s)" in advised["advice"][0]

    # The split it names is balanced; the move the old formula named is not.
    balanced = _sweep_tool(
        "etalon_campaign_plan", pool_size=1_056_280, screen_devices=3, generation_devices=8
    )
    overshot = _sweep_tool(
        "etalon_campaign_plan", pool_size=1_056_280, screen_devices=2, generation_devices=9
    )
    assert 0.67 <= balanced["capacity_ratio"] <= 1.5, balanced["capacity_ratio"]
    assert abs(balanced["capacity_ratio"] - 1.0) < abs(overshot["capacity_ratio"] - 1.0), (
        f"three ({balanced['capacity_ratio']}) should sit closer to parity than four "
        f"({overshot['capacity_ratio']})"
    )


def test_a_plan_may_not_ask_for_more_devices_than_the_host_has() -> None:
    """The one arithmetic error no care in the rates can catch: a plan for a machine that is not there.

    The refusal has to name both numbers, because "8 requested, 1 detected" is the whole diagnosis.
    """

    detected = _sweep_tool("etalon_devices")
    assert detected["ok"]
    asked_screen = detected["total"] + 4
    result = _sweep_tool(
        "etalon_campaign_plan",
        pool_size=1000,
        screen_devices=asked_screen,
        generation_devices=4,
        detect=True,
    )
    assert not result["ok"]
    assert result["error"]["retryable"] is False, "a machine does not grow on retry"
    message = result["error"]["message"]
    assert str(asked_screen + 4) in message
    assert f"{detected['total']} were detected" in message


def test_detection_names_the_pinned_commit_it_measured_through() -> None:
    """A reading that cannot say which MolCascade produced it cannot be cited later.

    An editable install shadowing the vendored tree is silent, so the commit is reported beside the
    measurement rather than assumed from the manifest.
    """

    from etalon.boundary.infra import load

    reading = _sweep_tool("etalon_devices")
    assert reading["ok"]
    assert reading["measured_by"]["source_commit"] == load("molcascade").source_commit
    assert reading["measured_by"]["function"] == "molcascade.environment.detect_environment"
    # Detection must not invent an accelerator, and must not omit the count.
    assert reading["total"] == len(reading["devices"])
    assert (reading["accelerator"] == "none") == (reading["total"] == 0)


def test_the_plan_says_which_of_its_numbers_are_borrowed() -> None:
    """Both shipped rates were measured on one campaign and neither records its device.

    A plan built entirely from defaults is a plan for somebody else's machine, and the result has to
    say so rather than leaving it in a docstring the caller did not read.
    """

    borrowed = _sweep_tool("etalon_campaign_plan", pool_size=1000)
    assert "default" in borrowed["basis"]["minutes_per_batch"]
    assert "no device recorded" in borrowed["basis"]["minutes_per_batch"]
    assert "no device recorded" in borrowed["basis"]["unique_per_generator_hour"]
    assert borrowed["basis"]["devices"] == "declared by the caller"

    supplied = _sweep_tool(
        "etalon_campaign_plan", pool_size=1000, minutes_per_batch=42.0, unique_per_generator_hour=900.0
    )
    assert supplied["basis"]["minutes_per_batch"] == "caller"
    assert supplied["basis"]["unique_per_generator_hour"] == "caller"


def test_one_device_is_reported_as_unsplittable_rather_than_split() -> None:
    """Half a GPU is not a lane. Generating and screening on one card is sequential, not a ratio."""

    plan = _sweep_tool(
        "etalon_campaign_plan", pool_size=1000, screen_devices=1, generation_devices=0
    )
    assert plan["ok"]
    assert plan["recommended_split"]["total_devices"] == 1
    assert "cannot be split" in plan["recommended_split"]["note"]
