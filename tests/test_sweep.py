"""The throughput regime, tested against the failures that produced it.

Every test here names a thing that went wrong in one real 1,056,280-molecule campaign. That is the
standard for this file: a test whose failure mode was never observed is a test of an opinion.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from dataclasses import replace
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
from etalon.boundary.infra import etalon_revision
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


# -- one SMILES, one row -----------------------------------------------------


def test_a_second_key_on_one_smiles_is_a_duplicate_not_a_second_molecule(sweep) -> None:
    """The pool deduplicates on the caller's key, and nothing checked the key against the SMILES.

    Measured on the v7 pool: 540,883 rows over 540,851 distinct SMILES. The thirty-two SMILES with
    two keys each were counted as sixty-four molecules, thirty-one pairs were carved into two
    different batches, and `AdmitReport.duplicates` reported zero of them.
    """

    one = "c1ccccc1"
    first = sweep.admit([("AAAAAAAAAAAAAA-UHFFFAOYSA-N", one, "g0")])
    assert (first.accepted, first.duplicates) == (1, 0)

    second = sweep.admit([("AAAAAAAAAAAAAA-INIZCTEOSA-N", one, "g1")])
    assert (second.accepted, second.duplicates) == (0, 1)
    assert sweep.ready() == 1

    pool = sweep.state()["pool"]
    assert pool["unique"] == 1
    assert pool["one_row_per_smiles"] is True
    assert pool["smiles_conflicts"] == 0
    # And the first proposer keeps the attribution, which is what a source breakdown rests on.
    assert pool["by_source"] == {"g0": 1}


def test_a_pool_that_already_holds_one_smiles_twice_opens_and_says_so(tmp_path, keyed) -> None:
    """A finished campaign's pool cannot be rewritten to make a new rule fit.

    The v7 pool is such a pool. Imposing the unique index on it would raise, and deleting the
    conflicting rows would destroy the record of what was actually screened -- those molecules were
    docked, twice. So the constraint goes unenforced there and the reading has to admit it, rather
    than a caller inferring from the code that one SMILES means one row everywhere.
    """

    import sqlite3

    path = tmp_path / "legacy.sqlite"
    gate = authorize_gate(calibration(), provenance=FakeScreen().provenance())
    Sweep(FakeScreen(), Ledger(tmp_path / "l.jsonl"), path, batch_size=5, gate=gate)

    # Reach past `admit` to plant the violation the old ingest path produced.
    db = sqlite3.connect(path)
    db.execute("DROP INDEX molecule_smiles")
    db.executemany(
        "INSERT INTO molecule(key, smiles, source, seen_at) VALUES (?,?,?,?)",
        [("K1-UHFFFAOYSA-N", "c1ccccc1", "g0", 1.0), ("K1-INIZCTEOSA-N", "c1ccccc1", "g0", 2.0)],
    )
    db.commit()
    db.close()

    reopened = Sweep(FakeScreen(), Ledger(tmp_path / "l.jsonl"), path, batch_size=5, gate=gate)
    pool = reopened.state()["pool"]
    assert pool["unique"] == 2
    assert pool["one_row_per_smiles"] is False
    assert pool["smiles_conflicts"] == 1


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
    sweep.admit([(f"K{i}", f"C{i}", "flowr") for i in range(8)])
    carved = sweep.emit(revision_id="rev-1")
    assert [b.batch_id for b in carved] == ["batch_0001"]
    assert sweep.ready() == 3


def test_flush_carves_the_short_remainder_and_stops(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", f"C{i}", "flowr") for i in range(7)])
    carved = sweep.emit(revision_id="rev-1", flush=True)
    assert [b.size for b in carved] == [5, 2]
    assert sweep.ready() == 0


def test_emitting_without_a_gate_authorization_is_refused(tmp_path, keyed) -> None:
    bare = Sweep(FakeScreen(), Ledger(tmp_path / "l.jsonl"), tmp_path / "p.sqlite", batch_size=2)
    bare.admit([("K1", "C1", "f"), ("K2", "C2", "f")])
    with pytest.raises(NotAuthorized, match="known-active panel"):
        bare.emit(revision_id="rev-1")


def test_emitting_with_a_token_for_another_funnel_is_refused(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    with pytest.raises(NotAuthorized, match="configuration changed"):
        sweep.emit(revision_id="rev-EDITED")


def test_two_screeners_cannot_claim_one_batch(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    with pytest.raises(SweepError, match="not claimable"):
        sweep.claim("batch_0001", by="gpu6")


def test_an_anonymous_claim_is_refused(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    with pytest.raises(ValueError, match="name its claimer"):
        sweep.claim("batch_0001", by="  ")


def test_an_outcome_is_append_only(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    done = ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("shortlist", "SUCCEEDED", artifact="a"),))
    sweep.record("batch_0001", done)
    with pytest.raises(SweepError, match="already recorded"):
        sweep.record("batch_0001", done)


def test_a_batch_keeps_the_revision_it_was_emitted_under(sweep: Sweep) -> None:
    """The ledger's ``revision_id`` is the funnel, and a differing run digest is provenance.

    This used to raise on any difference, guarding the provenance hole 56 hand-recorded batches
    had. The guard was unusable where it stood: a compiled revision covers the library as well as
    the funnel, so a run's own digest is a function of which molecules were in it, and five batches
    of one ALK2 campaign compiled to five digests from one unchanged config file. The check refused
    all five -- from inside ``recover``, so the exception left the supervisor's tick and killed the
    process driving the campaign.

    The guard did not go away; it moved to the only place it can be performed. A screen driver
    compiles the current config against a fixed reference library before every batch and refuses to
    run one whose funnel is not the campaign's, so a configuration changed mid-campaign never
    produces a result for ``record`` to judge. See
    ``test_a_drifted_configuration_is_refused_before_the_batch_runs``.
    """

    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    recorded = sweep.record(
        "batch_0001",
        ScreenResult(
            "batch_0001", "rev-per-library", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)
        ),
    )
    # The funnel, not the funnel-and-these-molecules digest -- which is what `comparable` reads.
    assert recorded.revision_id == "rev-1"
    assert sweep.state()["comparable"] is True


def test_a_drifted_configuration_is_refused_before_the_batch_runs(tmp_path) -> None:
    """Where the revision guard lives now: in front of the screen, not behind it."""

    from etalon.campaign.drivers import MolCascadeScreening, RevisionChanged

    class Compiles:
        def __init__(self, revision: str) -> None:
            self.revision = revision

        def plan(self, config_path, library=None, *, target=None):  # noqa: ANN001, ARG002
            return SimpleNamespace(revision_id=self.revision)

        def state(self, run_id: str):  # noqa: ARG002
            return None

    driver = MolCascadeScreening(
        screen=Compiles("rev-EDITED"),
        config_path=tmp_path / "c.json",
        revision_id="rev-1",
        reference_library=tmp_path / "panel.csv",
    )
    with pytest.raises(RevisionChanged, match="not comparable"):
        driver("batch_0001", tmp_path / "b.csv", "cuda:0")


def test_an_exhausted_batch_is_recorded_as_a_measurement(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
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
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    sweep.record("batch_0001", ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)))
    body = sweep.ledger.entries()[-1].body
    assert body["revision_id"] == "rev-1"
    assert body["gate"] == sweep.gate.calibration_sha256  # type: ignore[union-attr]
    assert body["infrastructure"]["tree_sha256"] == "deadbeef"


def test_an_unfinished_run_is_not_recorded_as_a_measurement(sweep: Sweep) -> None:
    """A screen killed mid-funnel files as ``committed``, because ``outcome`` reads the stages.

    Measured shape: a supervisor was restarted while a 20,000-molecule batch was at stage 6 of 43.
    The run record stayed RUNNING with six committed stages and none failed, and
    ``ScreenResult.outcome`` is derived from the stages -- so it returned ``"committed"``, the same
    string a finished screen produces. Recording that is a complete measurement of 20,000 molecules
    with 37 stages that never ran, and nothing downstream can tell it apart: the ledger entry
    carries ``committed_stages`` but no reader compares it against the funnel's length.
    """

    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    midway = ScreenResult(
        "batch_0001",
        "rev-1",
        "RUNNING",
        tuple(stage(f"s{i}", "SUCCEEDED", artifact=f"a{i}") for i in range(6)),
    )
    # The thing being guarded against: this is not a failure that would be caught some other way.
    assert midway.outcome == "committed"
    with pytest.raises(SweepError, match="not a terminal"):
        sweep.record("batch_0001", midway)
    still = sweep.batch("batch_0001")
    assert still is not None and still.outcome is None
    assert [e.kind for e in sweep.ledger.entries()] == []


def test_two_recorders_cannot_both_file_an_outcome(sweep: Sweep) -> None:
    """The append-only guard is a read, the write is a separate statement, and that is a window.

    Both a supervisor's ``recover`` and an operator's MCP ``etalon_sweep_record`` reach the same
    pool, and both check ``recorded`` before writing. Two that interleave both pass the check, and
    the second overwrote the first's measurement -- including overwriting ``committed`` with
    ``failed`` from a retry that should never have been dispatched. ``claim`` already took the
    conditional-update-plus-rowcount route; the terminal write did not.
    """

    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    stale = sweep.batch("batch_0001")  # the row as BOTH recorders read it: claimed, unrecorded
    assert stale is not None and not stale.recorded

    sweep.record(
        "batch_0001",
        ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)),
    )

    # The loser's pre-read happened before that write. ``record`` re-reads the row, so replaying
    # the window means handing it the row it actually had -- once.
    real = sweep.batch
    pre_read = iter([stale])
    sweep.batch = lambda bid: next(pre_read, None) or real(bid)  # type: ignore[assignment]
    try:
        with pytest.raises(SweepError, match="recorded by someone else"):
            sweep.record(
                "batch_0001",
                ScreenResult("batch_0001", "rev-1", "FAILED", (stage("s", "FAILED", code="BOOM"),)),
            )
    finally:
        sweep.batch = real  # type: ignore[assignment]

    landed = sweep.batch("batch_0001")
    assert landed is not None and landed.outcome == "committed"
    assert [e.body["outcome"] for e in sweep.ledger.entries() if e.kind == "batch"] == ["committed"]


def test_the_ledger_records_the_gate_the_batch_was_emitted_under(tmp_path, keyed) -> None:
    """Not the gate of whichever sweep happened to record it.

    A supervisor restarted mid-campaign re-authorizes its gate before carving anything, and
    ``record`` read ``self.gate`` -- so every batch it recovered, including ones carved hours
    earlier under a different calibration, was filed under the live authorization. A ledger read
    back afterwards therefore attributes the whole campaign to one gate regardless of what was
    actually screened under what.
    """

    screen = FakeScreen()
    emitting = authorize_gate(calibration(), provenance=screen.provenance())
    carver = Sweep(
        screen,
        Ledger(tmp_path / "ledger.jsonl"),
        tmp_path / "pool.sqlite",
        batch_size=5,
        gate=emitting,
    )
    carver.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    carver.emit(revision_id="rev-1")
    carved = carver.batch("batch_0001")
    assert carved is not None and carved.gate == emitting.calibration_sha256

    # What a restart mints: the same funnel, a differently-digested calibration verdict.
    restarted = authorize_gate(
        replace(calibration(), run_id="panel-run-after-restart"), provenance=screen.provenance()
    )
    assert restarted.calibration_sha256 != emitting.calibration_sha256

    recorder = Sweep(
        screen,
        Ledger(tmp_path / "ledger.jsonl"),
        tmp_path / "pool.sqlite",
        batch_size=5,
        gate=restarted,
    )
    recorder.screen = screen  # type: ignore[assignment]
    recorder.claim("batch_0001", by="gpu5")
    recorder.record(
        "batch_0001",
        ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)),
    )
    body = recorder.ledger.entries()[-1].body
    assert body["gate"] == emitting.calibration_sha256
    assert body["gate"] != restarted.calibration_sha256


def test_a_pool_carved_before_the_gate_column_still_records(tmp_path, keyed) -> None:
    """``CREATE TABLE IF NOT EXISTS`` is a no-op, so a new column needs an explicit migration.

    The campaign already on disk is the one with batches in it, and without the ``ALTER TABLE`` a
    column added to the schema reaches only pools created after the change -- so the first thing
    the new code does against a running campaign is raise ``no such column: gate`` out of
    ``recover``, inside the supervisor tick, which kills the process driving it.
    """

    import sqlite3

    pool = tmp_path / "pool.sqlite"
    db = sqlite3.connect(pool)
    db.executescript(
        """
        CREATE TABLE molecule (
            key TEXT PRIMARY KEY, smiles TEXT NOT NULL, source TEXT NOT NULL,
            seen_at REAL NOT NULL, batch TEXT
        );
        CREATE TABLE batch (
            batch_id TEXT PRIMARY KEY, size INTEGER NOT NULL, emitted_at TEXT NOT NULL,
            claimed_by TEXT, claimed_at TEXT, run_id TEXT, revision_id TEXT,
            outcome TEXT, recorded_at TEXT
        );
        INSERT INTO batch(batch_id, size, emitted_at, revision_id)
        VALUES ('batch_0001', 5, '2026-01-01T00:00:00Z', 'rev-1');
        """
    )
    for i in range(5):
        db.execute(
            "INSERT INTO molecule(key, smiles, source, seen_at, batch) VALUES (?,?,?,?,?)",
            (f"K{i}", f"C{i}", "f", float(i), "batch_0001"),
        )
    db.commit()
    db.close()

    screen = FakeScreen()
    sweep = Sweep(
        screen,
        Ledger(tmp_path / "ledger.jsonl"),
        pool,
        batch_size=5,
        gate=authorize_gate(calibration(), provenance=screen.provenance()),
    )
    sweep.screen = screen  # type: ignore[assignment]
    before = sweep.batch("batch_0001")
    assert before is not None and before.gate is None  # migrated, and honest about what it holds
    sweep.claim("batch_0001", by="gpu5")
    sweep.record(
        "batch_0001",
        ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)),
    )
    # No emitting gate on the row, so the ledger falls back to the live one rather than a null.
    assert sweep.ledger.entries()[-1].body["gate"] == sweep.gate.calibration_sha256  # type: ignore[union-attr]


# -- recovery: the six-hour orphan -------------------------------------------


def _three_claimed(sweep: Sweep) -> None:
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(15)])  # batch_size=5, so exactly three batches
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
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(10)])
    sweep.emit(revision_id="rev-1")
    for batch in sweep.pending():
        sweep.claim(batch.batch_id, by="gpu5")
    sweep.record("batch_0001", ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)))
    assert sweep.state()["comparable"] is True
    # A second revision can only arrive by re-emitting under a retuned funnel; simulate the record.
    object.__setattr__(sweep, "gate", authorize_gate(calibration(revision="rev-2"), provenance={"t": "1"}))
    sweep.admit([(f"M{i}", f"M{i}", "f") for i in range(5)])
    third = sweep.emit(revision_id="rev-2")[0]
    sweep.claim(third.batch_id, by="gpu6")
    sweep.record(third.batch_id, ScreenResult(third.batch_id, "rev-2", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)))
    assert sweep.state()["comparable"] is False


def test_a_claimed_batch_carries_the_etalon_revision_that_will_screen_it(sweep: Sweep) -> None:
    """Every provenance block named the three packages ETALON drives and never ETALON.

    Measured: ``grep -c etalon ledger_v7.json`` returns 0 across 27 batch lines, whose single shared
    ``infrastructure`` block names ``molcascade`` and nothing else. Establishing which ETALON had
    screened them meant cross-checking 27 ledger timestamps against ``git log`` by hand, and the next
    commit landed 9m50s after the last batch was recorded.
    """

    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(10)])
    sweep.emit(revision_id="rev-1")
    assert sweep.batch("batch_0001").etalon is None, "nothing claimed it yet"

    claimed = sweep.claim("batch_0001", by="gpu5")
    assert claimed.etalon == etalon_revision()
    assert claimed.etalon, "this checkout is a git repository, so the revision is evaluable here"

    sweep.record(
        "batch_0001",
        ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)),
    )
    line = [e for e in sweep.ledger.entries() if e.kind == "batch"][-1]
    assert line.body["etalon"] == claimed.etalon
    assert sweep.state()["etalon_revisions"] == [claimed.etalon]


def test_a_requeued_batch_is_attributed_to_the_etalon_that_rescreened_it(sweep: Sweep) -> None:
    # The gate has this shape already: the recorder is not always the claimer. A batch requeued by
    # recovery and taken by a newer process was screened by the newer code, and says so.
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(10)])
    sweep.emit(revision_id="rev-1")
    sweep.claim("batch_0001", by="gpu5")
    assert sweep.recover()[0].action == "requeued", "no run record, so the claim never started one"

    sweep.claim("batch_0001", by="gpu6")
    sweep.record(
        "batch_0001",
        ScreenResult("batch_0001", "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)),
    )
    assert sweep.batch("batch_0001").etalon == etalon_revision()


def test_two_etalon_revisions_mark_one_funnel_s_batches_incomparable(sweep: Sweep) -> None:
    """The funnel can be byte-identical while the code driving it is not.

    This is the case ``comparable`` could not see. ``revision_id`` is ``rev-1`` throughout, so the
    old reading called these batches comparable, and the enrichment computed across them was
    attributed to a funnel that two different ETALONs had fed and read back.
    """

    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(20)])
    sweep.emit(revision_id="rev-1")
    for batch in sweep.pending():
        sweep.claim(batch.batch_id, by="gpu5")
        sweep.record(
            batch.batch_id,
            ScreenResult(batch.batch_id, "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)),
        )
    assert sweep.state()["comparable"] is True
    assert sweep.state()["revisions"] == ["rev-1"]

    # Rewrite one row's ETALON revision: a batch screened by a different commit of this repo, which
    # is what a campaign restarted after a fix really looks like.
    import sqlite3

    with sqlite3.connect(sweep.pool) as db:
        db.execute("UPDATE batch SET etalon = ? WHERE batch_id = ?", ("deadbeefcafe", "batch_0001"))
    reading = sweep.state()
    assert reading["revisions"] == ["rev-1"], "one funnel"
    assert len(reading["etalon_revisions"]) == 2
    assert reading["comparable"] is False


def test_a_pool_carved_before_the_etalon_column_opens_and_migrates(tmp_path, keyed) -> None:
    # `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so a column added to the schema
    # never reaches a campaign already on disk -- and the campaign already on disk is the one with
    # batches in it. Measured on the gate column, which is why `_MIGRATIONS` exists at all.
    import sqlite3

    pool = tmp_path / "old.sqlite"
    with sqlite3.connect(pool) as db:
        db.executescript(
            """
            CREATE TABLE molecule (
                key TEXT PRIMARY KEY, smiles TEXT NOT NULL, source TEXT NOT NULL,
                seen_at REAL NOT NULL, batch TEXT
            );
            CREATE TABLE batch (
                batch_id TEXT PRIMARY KEY, size INTEGER NOT NULL, emitted_at TEXT NOT NULL,
                claimed_by TEXT, claimed_at TEXT, run_id TEXT, revision_id TEXT, outcome TEXT,
                recorded_at TEXT
            );
            """
        )
        db.execute(
            "INSERT INTO batch VALUES ('batch_0001', 7, 'then', NULL, NULL, NULL, NULL, NULL, NULL)"
        )
    screen = FakeScreen()
    sweep = Sweep(
        screen,
        Ledger(tmp_path / "l.jsonl"),
        pool,
        batch_size=7,
        gate=authorize_gate(calibration(), provenance=screen.provenance()),
    )
    old = sweep.batch("batch_0001")
    assert old is not None and old.etalon is None and old.gate is None
    assert sweep.state()["etalon_revisions"] == [], "an unrecorded batch contributes no revision"
    assert sweep.claim("batch_0001", by="gpu5").etalon == etalon_revision()


def test_a_tree_without_git_says_so_instead_of_reporting_a_clean_commit(tmp_path, monkeypatch) -> None:
    # A wheel install has no .git. "Could not tell" and "told you it was clean" are different
    # claims, and the one this project refuses to make is the second.
    import etalon.boundary.infra as infra

    monkeypatch.setattr(infra, "__file__", str(tmp_path / "a" / "b" / "c" / "infra.py"))
    infra.etalon_provenance.cache_clear()
    try:
        record = infra.etalon_provenance()
        assert "problem" in record and "unevaluable" in record["problem"]
        assert "commit" not in record and "dirty" not in record
        assert infra.etalon_revision() == "", "empty, not a plausible-looking digest"
    finally:
        infra.etalon_provenance.cache_clear()


def test_library_rows_always_write_an_id_column(sweep: Sweep, tmp_path) -> None:
    # Without it a run records no names, and recall, explanation and a named shortlist all fail.
    sweep.admit([(f"K{i}", f"C{i}", "flowr") for i in range(5)])
    sweep.emit(revision_id="rev-1")
    written = library_rows(sweep, "batch_0001", tmp_path / "batch.csv")
    header, *rows = written.read_text(encoding="utf-8").splitlines()
    assert header == "id,smiles,source"
    assert len(rows) == 5


def test_two_campaigns_over_the_same_molecules_write_one_library_file(tmp_path, keyed) -> None:
    """The library's *name* is in the screen's cache key, so a name per batch is a cache miss per batch.

    MolCascade's source stage carries the library's absolute path in its stage config and
    `stage_cache_key` hashes that config. A caller-chosen name therefore makes the entry stage's key
    unique, republishes its output under a new digest, and changes every downstream stage's input
    digest with it -- the whole funnel re-runs.

    Measured on ALK2: a second campaign over the same pool wrote the same 20,000 molecules to
    `batch_0001.csv` and `v7_batch_0001.csv`. Byte-identical files, same md5. Of 43 stages the 10
    whose config had genuinely changed were the gates meant to change, and `docking_score`'s own
    config was identical -- yet all 43 re-ran, because the 11th differing config was the source
    stage's and the only thing in it that differed was the file name.
    """

    screen = FakeScreen()
    gate = authorize_gate(calibration(), provenance=screen.provenance())
    molecules = [(f"K{i}", f"C{i}", "flowr") for i in range(5)]

    old = Sweep(screen, Ledger(tmp_path / "a.jsonl"), tmp_path / "a.sqlite", batch_size=5, gate=gate)
    old.admit(molecules)
    old.emit(revision_id="rev-1")
    new = Sweep(
        screen, Ledger(tmp_path / "b.jsonl"), tmp_path / "b.sqlite",
        batch_size=5, gate=gate, prefix="v7_",
    )
    new.admit(molecules)
    new.emit(revision_id="rev-1")

    root = tmp_path / "libraries"
    first = library_rows(old, "batch_0001", root, content_addressed=True)
    second = library_rows(new, "v7_batch_0001", root, content_addressed=True)

    assert first == second, "same molecules, same bytes -- so the same path, or the cache cannot hit"
    assert first.name.startswith("lib-") and first.name.endswith(".csv")
    assert "batch_0001" not in first.name, "a batch id in the name is what defeats the cache"
    assert len(list(root.glob("*.csv"))) == 1

    # Different molecules must still get a different file, or two batches would alias.
    other = Sweep(screen, Ledger(tmp_path / "c.jsonl"), tmp_path / "c.sqlite", batch_size=5, gate=gate)
    other.admit([(f"J{i}", f"N{i}", "flowr") for i in range(5)])
    other.emit(revision_id="rev-1")
    assert library_rows(other, "batch_0001", root, content_addressed=True) != first

    # The explicit-path form is unchanged: tests and manual runs still name their own file.
    explicit = library_rows(old, "batch_0001", tmp_path / "named.csv")
    assert explicit.name == "named.csv"
    assert explicit.read_bytes() == first.read_bytes()


def test_a_library_already_on_disk_keeps_the_name_it_has(tmp_path, keyed) -> None:
    """One path per content -- not a particular spelling of it.

    A campaign that ran before content-addressing wrote its libraries under its own scheme, and the
    screen's cache entries are keyed on *those* paths. Insisting on a fresh `lib-<digest>.csv` would
    be content-addressing that still recomputes everything it was introduced to avoid.

    Measured on ALK2: 27 batches of 20,000 molecules, every one byte-identical to the previous
    campaign's batch of the same number, 3.9 GB of artifacts and 1,633 cache entries already on
    disk, ~250 minutes a batch.
    """

    screen = FakeScreen()
    gate = authorize_gate(calibration(), provenance=screen.provenance())
    molecules = [(f"K{i}", f"C{i}", "flowr") for i in range(5)]
    root = tmp_path / "libraries"

    older = Sweep(screen, Ledger(tmp_path / "a.jsonl"), tmp_path / "a.sqlite", batch_size=5, gate=gate)
    older.admit(molecules)
    older.emit(revision_id="rev-1")
    legacy = library_rows(older, "batch_0001", root / "batch_0001.csv")  # the old naming scheme

    newer = Sweep(
        screen, Ledger(tmp_path / "b.jsonl"), tmp_path / "b.sqlite",
        batch_size=5, gate=gate, prefix="v7_",
    )
    newer.admit(molecules)
    newer.emit(revision_id="rev-1")
    found = library_rows(newer, "v7_batch_0001", root, content_addressed=True)

    assert found == legacy, "the bytes are already on disk; a second name is a second cache miss"
    assert len(list(root.glob("*.csv"))) == 1

    # And when one content already sits under several names, the oldest wins -- that is the one the
    # cache entries were written against. Measured on ALK2: 54 files, 27 distinct contents, every v6
    # name shadowed by a v7 twin, and indexing the later name yields a tidy index and no cache hit.
    import os
    import time

    shadow = root / "zz_later_name.csv"
    shadow.write_bytes(legacy.read_bytes())
    os.utime(shadow, (time.time() + 60, time.time() + 60))
    (root / "by-content.json").unlink()
    assert library_rows(newer, "v7_batch_0001", root, content_addressed=True) == legacy

    # A library that is genuinely new still gets a digest name.
    other = Sweep(screen, Ledger(tmp_path / "c.jsonl"), tmp_path / "c.sqlite", batch_size=5, gate=gate)
    other.admit([(f"J{i}", f"N{i}", "flowr") for i in range(5)])
    other.emit(revision_id="rev-1")
    fresh = library_rows(other, "batch_0001", root, content_addressed=True)
    assert fresh.name.startswith("lib-")
    # And is found again by content on the next pass, without rescanning.
    assert library_rows(other, "batch_0001", root, content_addressed=True) == fresh


def test_an_indexed_library_whose_bytes_changed_is_not_handed_out(tmp_path, keyed) -> None:
    """The index is a claim about file contents, so the file has to be asked, not the index.

    Measured: replacing an indexed `lib-<digest>.csv` with a 31-byte wrong-content file -- wrong
    length, so a size guard would have caught it -- still returned that file untouched, because the
    index hit returned before any guard ran. The original observation was a request for one batch's
    library returning a path whose first data row read `J0,N0,flowr`: another batch's molecules,
    under this batch's id, about to be docked and recorded as this batch's measurement.
    """

    screen = FakeScreen()
    gate = authorize_gate(calibration(), provenance=screen.provenance())
    root = tmp_path / "libraries"

    mine = Sweep(screen, Ledger(tmp_path / "a.jsonl"), tmp_path / "a.sqlite", batch_size=5, gate=gate)
    mine.admit([(f"K{i}", f"C{i}", "flowr") for i in range(5)])
    mine.emit(revision_id="rev-1")
    wanted = library_rows(mine, "batch_0001", root, content_addressed=True)
    payload = wanted.read_bytes()

    # Another batch's molecules, written over the indexed name. Shorter, so the old size guard
    # would have fired -- and did not, because the index hit returned first.
    other = Sweep(screen, Ledger(tmp_path / "b.jsonl"), tmp_path / "b.sqlite", batch_size=5, gate=gate)
    other.admit([(f"J{i}", f"N{i}", "flowr") for i in range(5)])
    other.emit(revision_id="rev-1")
    foreign = library_rows(other, "batch_0001", root, content_addressed=True).read_bytes()
    assert foreign != payload
    wanted.write_bytes(foreign)

    again = library_rows(mine, "batch_0001", root, content_addressed=True)
    assert again.read_bytes() == payload, "a library must hold the molecules of the batch asking for it"
    assert "J0,N0,flowr" not in again.read_text(encoding="utf-8")

    # Truncation is caught the same way, and by content rather than by length.
    again.write_bytes(payload[: len(payload) // 2])
    assert library_rows(mine, "batch_0001", root, content_addressed=True).read_bytes() == payload

    # Same length, different bytes -- the case no size check can see.
    swapped = bytearray(payload)
    swapped[-2:] = b"zz"
    again.write_bytes(bytes(swapped))
    assert len(bytes(swapped)) == len(payload)
    assert library_rows(mine, "batch_0001", root, content_addressed=True).read_bytes() == payload


def test_a_stale_content_index_entry_is_repaired_rather_than_followed(tmp_path, keyed) -> None:
    """An index naming a file that holds something else must fall through to the write path."""

    import json

    screen = FakeScreen()
    gate = authorize_gate(calibration(), provenance=screen.provenance())
    root = tmp_path / "libraries"
    sweep = Sweep(screen, Ledger(tmp_path / "a.jsonl"), tmp_path / "a.sqlite", batch_size=5, gate=gate)
    sweep.admit([(f"K{i}", f"C{i}", "flowr") for i in range(5)])
    sweep.emit(revision_id="rev-1")

    first = library_rows(sweep, "batch_0001", root, content_addressed=True)
    payload = first.read_bytes()
    digest = next(iter(json.loads((root / "by-content.json").read_text(encoding="utf-8"))))

    # Point the index at a file that exists and holds the wrong thing.
    decoy = root / "decoy.csv"
    decoy.write_text("id,smiles,source\nQ0,X0,flowr\n", encoding="utf-8")
    (root / "by-content.json").write_text(json.dumps({digest: decoy.name}), encoding="utf-8")

    repaired = library_rows(sweep, "batch_0001", root, content_addressed=True)
    assert repaired.read_bytes() == payload
    assert repaired != decoy
    # And the index now names the file that really holds these bytes.
    index = json.loads((root / "by-content.json").read_text(encoding="utf-8"))
    assert index[digest] == repaired.name


def test_one_unreadable_csv_does_not_abort_library_writing(tmp_path, keyed) -> None:
    """Measured: a dangling CSV raised out of index construction, hence out of `library_rows`.

    The supervisor calls `library_rows` *after* the batch is claimed, so the batch stayed claimed
    with no screen running and nothing retried it. The stat lived inside the sort key, which is the
    one place the loop body's own `OSError` guard could not reach.
    """

    screen = FakeScreen()
    gate = authorize_gate(calibration(), provenance=screen.provenance())
    root = tmp_path / "libraries"
    root.mkdir(parents=True)
    (root / "dangling.csv").symlink_to(root / "nowhere.csv")

    sweep = Sweep(screen, Ledger(tmp_path / "a.jsonl"), tmp_path / "a.sqlite", batch_size=5, gate=gate)
    sweep.admit([(f"K{i}", f"C{i}", "flowr") for i in range(5)])
    sweep.emit(revision_id="rev-1")

    written = library_rows(sweep, "batch_0001", root, content_addressed=True)
    assert written.name.startswith("lib-")
    assert written.read_text(encoding="utf-8").startswith("id,smiles,source")


def _claim_tool():  # noqa: ANN202 -- mirrors the SDK's untyped decorator factory
    from etalon.mcp import sweep as mcp_sweep

    class Collector:
        def __init__(self) -> None:
            self.tools: dict[str, object] = {}

        def tool(self):  # noqa: ANN202
            def decorate(function):  # noqa: ANN001, ANN202
                self.tools[function.__name__] = function
                return function

            return decorate

        def resource(self, _uri: str):  # noqa: ANN202
            def decorate(function):  # noqa: ANN001, ANN202
                return function

            return decorate

    collector = Collector()
    mcp_sweep.register(collector)
    return collector.tools["etalon_sweep_claim"]


def test_the_mcp_claim_tool_can_write_a_content_addressed_library(tmp_path, keyed) -> None:
    """The content-addressed form existed but only the in-process supervisor could reach it.

    An MCP-driven campaign got a file named after the batch, which is the exact cache defeat the
    content-addressed form was written for: MolCascade's source stage carries the library path in
    its stage config and `stage_cache_key` hashes that config, so every downstream key changes with
    the name. Measured on ALK2: byte-identical 20,000-molecule libraries under `batch_0001.csv` and
    `v7_batch_0001.csv`, same md5, all 43 stages re-run, docking included, for identical scores.
    """

    import json as _json

    screen = FakeScreen()
    gate = authorize_gate(calibration(), provenance=screen.provenance())
    molecules = [(f"K{i}", f"C{i}", "flowr") for i in range(5)]
    pool, ledger = tmp_path / "pool.sqlite", tmp_path / "ledger.jsonl"
    workspace = tmp_path / "ws"
    workspace.mkdir()

    sweep = Sweep(screen, Ledger(ledger), pool, batch_size=5, gate=gate)
    sweep.admit(molecules)
    sweep.emit(revision_id="rev-1")

    claim = _claim_tool()
    root = tmp_path / "libraries"
    answer = _json.loads(
        claim(
            workspace=str(workspace),
            pool=str(pool),
            ledger=str(ledger),
            batch_id="batch_0001",
            by="cuda:0",
            library_dir=str(root),
        )
    )
    assert answer["ok"] is True
    written = Path(answer["library"])
    assert written.name.startswith("lib-") and written.name.endswith(".csv")
    assert "batch_0001" not in written.name, "a batch id in the name is what defeats the cache"
    assert written.read_text(encoding="utf-8").startswith("id,smiles,source")

    # The same molecules under another campaign's batch id land on the same path, which is the
    # whole point: one path per content, so the screen's cache can hit across campaigns.
    other_pool, other_ledger = tmp_path / "p2.sqlite", tmp_path / "l2.jsonl"
    twin = Sweep(screen, Ledger(other_ledger), other_pool, batch_size=5, gate=gate, prefix="v7_")
    twin.admit(molecules)
    twin.emit(revision_id="rev-1")
    second = _json.loads(
        claim(
            workspace=str(workspace),
            pool=str(other_pool),
            ledger=str(other_ledger),
            batch_id="v7_batch_0001",
            by="cuda:1",
            library_dir=str(root),
        )
    )
    assert Path(second["library"]) == written
    assert len(list(root.glob("*.csv"))) == 1


def test_the_mcp_claim_tool_refuses_two_names_for_one_library(tmp_path, keyed) -> None:
    """Two paths for one batch's molecules is a question about which the screen will cache on."""

    import json as _json

    screen = FakeScreen()
    gate = authorize_gate(calibration(), provenance=screen.provenance())
    pool, ledger = tmp_path / "pool.sqlite", tmp_path / "ledger.jsonl"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sweep = Sweep(screen, Ledger(ledger), pool, batch_size=5, gate=gate)
    sweep.admit([(f"K{i}", f"C{i}", "flowr") for i in range(5)])
    sweep.emit(revision_id="rev-1")

    answer = _json.loads(
        _claim_tool()(
            workspace=str(workspace),
            pool=str(pool),
            ledger=str(ledger),
            batch_id="batch_0001",
            by="cuda:0",
            library_dir=str(tmp_path / "libraries"),
            library_path=str(tmp_path / "named.csv"),
        )
    )
    assert answer["ok"] is False
    assert answer["error"]["code"] == "AmbiguousLibrary"
    # And nothing was written under either name.
    assert not (tmp_path / "named.csv").exists()
    assert not (tmp_path / "libraries").exists()


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
    monkeypatch,
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

    def consume(self, chunk):
        self.consumed.add(str(chunk))
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(json.dumps(sorted(self.consumed)))
        return read(chunk)

    # Through ``monkeypatch`` rather than by assignment: a bare assignment here is never undone, so
    # every later test in the process read chunks through this fake and saw no rows.
    monkeypatch.setattr(generation_module, "read_chunk", read)
    monkeypatch.setattr(generation_module.Ingest, "consume", consume)

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


def _settle(supervisor, seconds=15.0):
    """Wait for every in-flight job to finish, then reap it with one tick.

    The alternative -- tick, sleep a fixed 50 ms, tick -- assumes a generation thread completes in
    50 ms. Observed: this file passing three runs in a row alone and failing once when run beside
    tests/test_mcp.py, which starts servers and takes the CPU away. A test that depends on how busy
    the machine is reports the machine, not the supervisor.
    """

    import time

    deadline = time.monotonic() + seconds
    while any(job.process.poll() is None for job in supervisor.jobs):
        if time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    supervisor.tick()


def _drain(supervisor, seconds=30.0):
    """Tick until the campaign completes, bounded by wall clock rather than by tick count.

    The bound used to be 40 ticks of 50 ms, which is two seconds of *this* process's time and says
    nothing about how long the worker threads it is waiting on need. Observed: the whole file
    passing four runs in a row on an idle machine and this helper's caller failing once while the
    host sat at load 232 -- a false failure that says the machine was busy, not that a supervisor
    stopped working.
    """

    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        supervisor.tick()
        if supervisor.complete():
            return True
        time.sleep(0.05)
    return supervisor.complete()


def test_generators_with_separate_output_roots_are_refused(tmp_path, keyed, monkeypatch) -> None:
    """Per-generator roots ingest one generator and silently drop the rest.

    Only one Ingest is kept for the campaign and it discovers chunks by path, so distinct roots
    mean every loop but one contributes nothing to the pool. Measured on an ALK2 campaign: six
    loops generating, five of them invisible to the sweep, and the only symptom was a pool that
    looked slow. Refused at construction rather than diagnosed sixteen hours in.
    """


    from etalon.campaign.supervisor import Supervisor

    supervisor, sweep, _ = _campaign(tmp_path, keyed, monkeypatch, generator_devices=("cuda:0", "cuda:1"))
    generators = list(supervisor.generators.values())
    assert len(generators) == 2, "one generator cannot exhibit a disagreement about roots"
    split = [
        replace(generator, output_root=tmp_path / "gen" / generator.tag)
        for generator in generators
    ]
    with pytest.raises(ValueError, match="share one output_root"):
        Supervisor(
            sweep,
            revision_id="rev-1",
            workspace=tmp_path / "ws2",
            generators=split,
            screen_devices=("cuda:5",),
        )


def test_a_campaign_runs_itself_to_completion(tmp_path, keyed, monkeypatch) -> None:
    """Generation, ingest, emission, screening and recording, with nobody watching."""

    supervisor, sweep, _ = _campaign(tmp_path, keyed, monkeypatch)
    assert _drain(supervisor), "the campaign did not terminate"
    state = sweep.state()
    assert state["batches"]["recorded"] == 2
    assert state["batches"]["outstanding"] == 0
    assert state["pool"]["unique"] == 24
    assert supervisor.state()["generators"]["flowr_a"]["stopped"] is True


def test_most_passes_are_quiet_and_say_so(tmp_path, keyed, monkeypatch) -> None:
    """The report a supervisor returns when nothing happened must be distinguishable.

    490 of one campaign's 500 readings changed nothing. A loop whose every pass looks eventful
    trains its reader to stop looking.
    """

    supervisor, _, _ = _campaign(tmp_path, keyed, monkeypatch)
    _drain(supervisor)
    assert supervisor.tick().quiet


def test_a_pass_whose_only_product_is_a_note_is_not_quiet() -> None:
    """Notes are where a screen that raised ends up, and `quiet` decides whether a pass is printed.

    One ALK2 campaign spent 14 hours screening nothing: every batch was claimed, the screen raised
    on its first line, and the supervisor ticked on with no output. The fix put those failures in
    `notes` -- but `quiet` did not look at `notes`, so a pass whose one product was a failure still
    reported itself as having changed nothing, and a printer that skips quiet passes still showed
    nothing. The channel was repaired; the gate in front of it was not.
    """

    from etalon.campaign.supervisor import TickReport

    assert not TickReport(notes=("v7_batch_0003: screen raised FileNotFoundError",)).quiet
    assert TickReport().quiet


def test_recovery_that_left_a_batch_alone_is_a_quiet_pass() -> None:
    """`running` is `Sweep.recover`'s documented no-op: the one branch of four that mutates nothing.

    Counting it meant a campaign with batches in flight had no quiet pass at all. Measured on ALK2:
    27 consecutive ticks, each printing the same 1,200-character line listing 16 batches as running,
    and that line is the channel a failure note arrives on. A log where every pass looks eventful
    hides a note exactly as well as a log that prints nothing.
    """

    from etalon.campaign.supervisor import TickReport

    left_alone = TickReport(
        recovered=({"batch_id": "v7_batch_0001", "action": "running"},) * 16
    )
    assert left_alone.quiet

    requeued = TickReport(recovered=({"batch_id": "v7_batch_0001", "action": "requeued"},))
    assert not requeued.quiet, "requeued releases the claim -- that is a change"


def test_a_supervisor_resumes_from_disk(tmp_path, keyed, monkeypatch) -> None:
    """Kill it mid-campaign and the next one picks up from the pool, the ledger and the manifests."""

    first, sweep, screen = _campaign(tmp_path, keyed, monkeypatch)
    first.tick()  # start generation
    import time

    time.sleep(0.2)
    first.tick()  # ingest chunk 1
    assert sweep.state()["pool"]["unique"] == 8

    second, sweep2, _ = _campaign(tmp_path, keyed, monkeypatch)  # a new process, same paths
    assert sweep2.state()["pool"]["unique"] == 8, "the pool survived"
    assert _drain(second), "the resumed supervisor did not finish the campaign"


def test_an_already_ingested_chunk_is_not_ingested_twice(tmp_path, keyed, monkeypatch) -> None:
    supervisor, sweep, _ = _campaign(tmp_path, keyed, monkeypatch)
    _drain(supervisor)
    before = sweep.state()["pool"]["unique"]
    supervisor.tick()
    assert sweep.state()["pool"]["unique"] == before


def test_consecutive_barren_chunks_stop_a_loop_but_one_does_not(tmp_path, keyed, monkeypatch) -> None:
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

    def consume(self, chunk):
        self.consumed.add(str(chunk))
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(json.dumps(sorted(self.consumed)))
        return []

    monkeypatch.setattr(generation_module, "read_chunk", lambda path, tag=None: [])
    monkeypatch.setattr(generation_module.Ingest, "consume", consume)

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
        _settle(supervisor)
        if index < BARREN_LIMIT - 1:
            assert "empty" not in supervisor.stopped, f"stopped after {index + 1} barren chunks"
    for _ in range(3):
        supervisor.tick()
        time.sleep(0.05)
    assert "empty" in supervisor.stopped
    assert supervisor.state()["generators"]["empty"]["consecutive_barren"] >= BARREN_LIMIT


def test_a_library_that_cannot_be_written_does_not_strand_the_claimed_batch(tmp_path, keyed) -> None:
    """The claim is committed before the library exists, so a failure there reserves and abandons.

    Measured: one dangling CSV in the library directory raised out of index construction, the batch
    stayed claimed with nothing running against it, and the supervisor driving the campaign exited
    before any other device was offered work. `Sweep.recover` releases a claimed batch whose run
    record does not exist, so containment is enough -- but only if the tick survives to reach it.
    """

    import etalon.campaign.supervisor as supervisor_module
    from etalon.campaign.supervisor import Supervisor

    screen = FakeScreen()
    sweep = Sweep(
        screen,
        Ledger(tmp_path / "l.jsonl"),
        tmp_path / "p.sqlite",
        batch_size=5,
        gate=authorize_gate(calibration(), provenance=screen.provenance()),
    )
    sweep.admit([(f"K{i}", f"C{i}", "flowr") for i in range(10)])
    sweep.emit(revision_id="rev-1")
    assert len(sweep.pending()) == 2

    launched: list[str] = []
    original = supervisor_module.library_rows

    def refuses(*args, **kwargs):
        raise OSError("dangling library")

    supervisor_module.library_rows = refuses
    try:
        supervisor = Supervisor(
            sweep,
            revision_id="rev-1",
            workspace=tmp_path / "ws",
            generators=[],
            screen_devices=("cuda:0",),
            screen=lambda batch_id, *_: launched.append(batch_id),
        )
        report = supervisor.tick()
        assert report.screens_started == ()
        assert launched == []
        assert any("could not be given a library" in note for note in report.notes)
        # The batch is claimed, which is what recovery looks for.
        assert len(sweep.outstanding()) == 1
    finally:
        supervisor_module.library_rows = original

    # Next tick: recovery releases it (no run record exists) and the batch screens normally.
    report = supervisor.tick()
    assert any(r["action"] == "requeued" for r in report.recovered), report.recovered
    _settle(supervisor)
    report = supervisor.tick()
    assert report.screens_started, report.notes


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


# One real PocketXMol candidate, 24 atoms, whose two stereo perceivers disagree: RDKit assigns a
# chiral tag from the signed volume and writes `[C@H]` into the canonical SMILES, while the InChI
# library reads the same coordinates, declares the centre undefined, and returns the flat
# `UHFFFAOYSA` block. Kept as a molblock rather than built in code because the disagreement is a
# property of this geometry.
_MARGINAL_STEREO_MOLBLOCK = """\

     RDKit          3D

 24 27  0  0  0  0  0  0  0  0999 V2000
   22.9521  -19.3510    5.3527 C   0  0  0  0  0  0  0  0  0  0  0  0
   19.2912  -27.3646    8.0099 C   0  0  0  0  0  0  0  0  0  0  0  0
   24.2273  -16.6098    7.4187 C   0  0  0  0  0  0  0  0  0  0  0  0
   22.4362  -19.6355    3.1056 C   0  0  0  0  0  0  0  0  0  0  0  0
   19.6924  -29.3658    6.9094 N   0  0  0  0  0  0  0  0  0  0  0  0
   20.0882  -25.8517    6.2671 C   0  0  0  0  0  0  0  0  0  0  0  0
   19.6294  -25.5116    5.0348 C   0  0  0  0  0  0  0  0  0  0  0  0
   19.8925  -27.1228    6.7350 C   0  0  2  0  0  0  0  0  0  0  0  0
   23.5848  -18.6368    6.4120 C   0  0  0  0  0  0  0  0  0  0  0  0
   21.6429  -21.1161    4.6114 C   0  0  0  0  0  0  0  0  0  0  0  0
   21.7425  -20.7066    3.3939 N   0  0  0  0  0  0  0  0  0  0  0  0
   23.6690  -17.2747    6.3866 C   0  0  0  0  0  0  0  0  0  0  0  0
   24.7541  -18.6317    8.4498 C   0  0  0  0  0  0  0  0  0  0  0  0
   20.0999  -28.3586    6.0281 C   0  0  0  0  0  0  0  0  0  0  0  0
   20.7450  -24.9537    7.0733 C   0  0  0  0  0  0  0  0  0  0  0  0
   19.8682  -24.2691    4.5891 C   0  0  0  0  0  0  0  0  0  0  0  0
   20.8563  -22.1703    4.8327 N   0  0  0  0  0  0  0  0  0  0  0  0
   19.1292  -28.8360    8.0992 C   0  0  0  0  0  0  0  0  0  0  0  0
   24.7695  -17.2806    8.4069 N   0  0  0  0  0  0  0  0  0  0  0  0
   22.2791  -20.5022    5.5659 N   0  0  0  0  0  0  0  0  0  0  0  0
   24.0997  -19.3431    7.4509 C   0  0  0  0  0  0  0  0  0  0  0  0
   22.9955  -18.9046    4.0589 C   0  0  0  0  0  0  0  0  0  0  0  0
   21.0122  -23.7403    6.5861 C   0  0  0  0  0  0  0  0  0  0  0  0
   20.5873  -23.4118    5.3363 C   0  0  0  0  0  0  0  0  0  0  0  0
  1  9  1  0
  1 20  2  0
  1 22  1  0
  2  8  1  0
  2 18  1  0
  3 12  2  0
  3 19  1  0
  4 11  1  0
  4 22  2  0
  5 14  1  0
  5 18  1  0
  6  7  1  0
  8  6  1  6
  6 15  2  0
  7 16  2  0
  8 14  1  0
  9 12  1  0
  9 21  2  0
 10 11  2  0
 10 17  1  0
 10 20  1  0
 13 19  2  0
 13 21  1  0
 15 23  1  0
 16 24  1  0
 17 24  1  0
 23 24  2  0
M  END
M  END
"""


def _write_chunk(chunk, molblock: str) -> None:
    chunk.mkdir(parents=True, exist_ok=True)
    (chunk / "manifest.json").write_text("{}")
    (chunk / "candidates.sdf").write_text(molblock.rstrip("\n") + "\n$$$$\n")


def test_a_chunk_row_is_keyed_on_the_smiles_it_ships(tmp_path) -> None:
    """A key read off the 3D molecule is not a function of the string the screen docks.

    Thirty-two SMILES in the v7 pool carried two InChIKeys each; thirty-one of those pairs were
    carved into two different batches, docked twice, and counted as two molecules.
    """

    pytest.importorskip("rdkit")
    from rdkit import Chem, RDLogger

    from etalon.campaign.generation import read_chunk

    RDLogger.DisableLog("rdApp.*")
    chunk = tmp_path / "pocketxmol" / "chunk_001"
    _write_chunk(chunk, _MARGINAL_STEREO_MOLBLOCK)

    rows = read_chunk(chunk)
    assert len(rows) == 1
    key, smiles, _ = rows[0]

    # The property, which is what has to hold for every molecule rather than just this one.
    assert key == Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))

    # And this particular geometry is one where the old policy gave a different answer, so the
    # test would fail against it rather than merely passing for a different reason.
    read = Chem.MolFromMolBlock(_MARGINAL_STEREO_MOLBLOCK, removeHs=True, sanitize=True)
    assert Chem.MolToInchiKey(read) != key


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


def test_a_retired_generator_hands_its_device_to_screening(tmp_path, keyed, monkeypatch) -> None:
    """The move an operator made by hand eighteen hours in, made when the loop ends instead.

    ``screen_devices`` was fixed at construction, so a campaign that retired its generators finished
    on the screeners it started with. One campaign's hand-made version of this move -- three devices
    -- produced half that campaign's hits.
    """

    supervisor, _, _ = _campaign(tmp_path, keyed, monkeypatch, migrate=True)
    assert "cuda:0" not in supervisor.screen_devices

    _drain(supervisor)

    assert supervisor.state()["generators"]["flowr_a"]["stopped"] is True
    assert "cuda:0" in supervisor.screen_devices, "the retired generator's card never joined"
    assert supervisor.state()["screen_devices"] == ["cuda:5", "cuda:6", "cuda:0"]


def test_migration_is_reported_and_is_not_a_quiet_pass(tmp_path, keyed, monkeypatch) -> None:
    """A device changing hands is an event. 490 of 500 passes are quiet and this is not one of them."""

    import time

    supervisor, _, _ = _campaign(tmp_path, keyed, monkeypatch, migrate=True)
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


def test_without_the_flag_the_idle_device_is_named_once_and_not_taken(tmp_path, keyed, monkeypatch) -> None:
    """Off by default, because on a shared host those cards may be owed back to the machine.

    An idle GPU nobody mentions is the failure this exists to stop, so the refusal to take it still
    has to say it is there -- once, not every sixty seconds for forty-four hours.
    """

    import time

    supervisor, _, _ = _campaign(tmp_path, keyed, monkeypatch, migrate=False)
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


def test_a_card_shared_by_two_loops_is_not_taken_until_both_stop(tmp_path, keyed, monkeypatch) -> None:
    """One model across two loops on one card is a normal configuration.

    ``tag`` rather than the model is the generator's identity precisely because of this shape, and a
    card handed to screening while one of its loops still generates would contend for its memory.
    """

    supervisor, _, _ = _campaign(
        tmp_path,
        keyed,
        monkeypatch,
        generator_devices=("cuda:0", "cuda:0"),
        total=8,
        chunk=8,
        migrate=True,
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


def test_a_device_named_in_both_lists_is_not_offered_two_batches(tmp_path, keyed, monkeypatch) -> None:
    """A duplicate lane would be claimed twice and refused by the sweep rather than by anything
    that could explain it."""

    supervisor, _, _ = _campaign(tmp_path, keyed, monkeypatch, generator_devices=("cuda:5",), migrate=True)
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


def test_a_batch_is_not_resumed_before_it_exists(tmp_path) -> None:
    """``--resume`` on a run that never started is MolCascade's RUN_NOT_FOUND, not a no-op.

    Both screen drivers had this, and it is the same one line in each: the committed-stage cache is
    always safe to reuse, but the run record it is keyed on has to exist first. Measured on ALK2:
    every batch of a fresh campaign failed on its first line, twice -- once through the in-process
    driver and once again through the CLI after the first fix, because the flag was hardcoded.
    """

    from etalon.campaign.drivers import MolCascadeProcessScreening

    class Records:
        def __init__(self, known: set[str]) -> None:
            self.known = known

        def state(self, run_id: str):
            return object() if run_id in self.known else None

    driver = MolCascadeProcessScreening(
        screen=Records({"batch_old"}),
        config_path=tmp_path / "c.json",
        workspace=tmp_path / "ws",
        revision_id="rev-1",
        target_args=("--receptor", str(tmp_path / "r.pdb")),
        reference_library=tmp_path / "panel.csv",
    )
    assert "--resume" not in driver._argv(tmp_path / "b.csv", "batch_new", dry_run=False)
    assert "--resume" in driver._argv(tmp_path / "b.csv", "batch_old", dry_run=False)
    # A dry run never resumes: it compiles and stops before any run record is consulted.
    assert "--resume" not in driver._argv(tmp_path / "panel.csv", "batch_old", dry_run=True)


def test_the_identity_probe_compiles_the_reference_library(tmp_path) -> None:
    """The revision check must compile the library the campaign's revision was computed from.

    A compiled revision covers the library, so probing with the batch's own library asks a question
    whose answer is different for every batch and equal to the campaign's for none of them.
    """

    from etalon.campaign.drivers import MolCascadeProcessScreening

    driver = MolCascadeProcessScreening(
        screen=None,
        config_path=tmp_path / "c.json",
        workspace=tmp_path / "ws",
        revision_id="rev-1",
        target_args=(),
        reference_library=tmp_path / "panel.csv",
    )
    probe = driver._argv(driver.reference_library, "batch_0001__identity", dry_run=True)
    assert str(tmp_path / "panel.csv") in probe
    assert "--dry-run" in probe and "--json" in probe


def test_progress_counts_a_cached_stage_as_reached(tmp_path) -> None:
    """A resumed batch must not read as wedged at the first stage it recomputed.

    ``CACHED`` is as terminal as ``SUCCEEDED`` -- more so, in the sense that the campaign already
    holds the artifact. Measured on ALK2: five batches resumed after an out-of-memory failure came
    back with eighteen stages CACHED, and ``progress`` called them stage 8 of 41 while every one was
    running stage 26. Two readings ten minutes apart both said 8, which is what an operator reads
    when deciding whether to intervene -- and the opposite of the truth.
    """

    from etalon.boundary.screen import Screen

    result = ScreenResult(
        run_id="batch_0001",
        revision_id="rev-1",
        status="RUNNING",
        stages=(
            stage("library", "SUCCEEDED", artifact="a"),
            *(stage(f"cheap_{i}", "CACHED", artifact=f"c{i}") for i in range(18)),
            stage("admet", "SUCCEEDED", artifact="b"),
            stage("conformers", "RUNNING"),
            stage("docking", "PENDING"),
        ),
    )

    class OneRun(Screen):
        def __init__(self) -> None:  # noqa: D107 -- no workspace is touched
            pass

        def state(self, run_id: str):  # noqa: ARG002
            return result

    reading = OneRun().progress("batch_0001")
    # 1 library + 18 cached + 1 admet, and the one in flight.
    assert reading["stage"] == 21
    assert reading["stages"] == 22
    assert reading["current"] == "conformers"


def test_the_device_goes_in_the_argv_not_the_environment(tmp_path) -> None:
    """CUDA_VISIBLE_DEVICES does not compose, so setting it here would defeat itself.

    MolCascade pins each shard with ``os.environ["CUDA_VISIBLE_DEVICES"] = device[5:]``, and that
    value indexes the machine's devices rather than the subset the process can see. A screen
    launched under ``CUDA_VISIBLE_DEVICES=3`` sees one card, names it ``cuda:0``, and pins its
    shards to "0" -- physical card zero. Measured on ALK2: eight screens with eight distinct values,
    and every Uni-Dock child on the same physical GPU, 63 GB on card 0 while seven cards idled.
    """

    from etalon.campaign.drivers import MolCascadeProcessScreening

    class NoRuns:
        def state(self, run_id: str):  # noqa: ARG002
            return None

    driver = MolCascadeProcessScreening(
        screen=NoRuns(),
        config_path=tmp_path / "c.json",
        workspace=tmp_path / "ws",
        revision_id="rev-1",
        target_args=(),
        reference_library=tmp_path / "panel.csv",
    )
    argv = driver._argv(tmp_path / "b.csv", "batch_0004", dry_run=False, device="cuda:3")
    assert argv[argv.index("--device") + 1] == "cuda:3"
    # The identity probe compiles and stops; it reaches no engine and names no card.
    assert "--device" not in driver._argv(
        driver.reference_library, "batch_0004__identity", dry_run=True
    )


def test_one_batch_per_device_unless_told_otherwise(tmp_path, keyed, monkeypatch) -> None:
    """The default rations devices; ``batches_per_device`` says when that is the wrong resource.

    Measured on ALK2: the docking tier's wall clock went to PoseBusters in Python rather than to the
    engine, so eight batches on eight cards held 1.3 cores each of a 96-core machine and 25 GB of
    each 96 GB card. One per device was rationing the resource that was spare.
    """

    from etalon.campaign.supervisor import Supervisor

    supervisor, sweep, _ = _campaign(tmp_path, keyed, monkeypatch)
    devices = ("cuda:5", "cuda:6")

    def build(per_device: int) -> Supervisor:
        return Supervisor(
            sweep,
            revision_id="rev-1",
            workspace=tmp_path / f"ws{per_device}",
            screen_devices=devices,
            batches_per_device=per_device,
        )

    assert build(1)._free_slots() == ["cuda:5", "cuda:6"]
    assert build(3)._free_slots() == ["cuda:5"] * 3 + ["cuda:6"] * 3
    assert build(2).state()["batches_per_device"] == 2
    with pytest.raises(ValueError, match="at least 1"):
        build(0)


def test_a_device_with_a_batch_on_it_offers_one_fewer_slot(tmp_path, keyed, monkeypatch) -> None:
    """Occupancy is counted per device, not treated as a boolean."""

    from etalon.campaign.supervisor import Supervisor, _Job

    supervisor, sweep, _ = _campaign(tmp_path, keyed, monkeypatch)
    two = Supervisor(
        sweep,
        revision_id="rev-1",
        workspace=tmp_path / "ws2",
        screen_devices=("cuda:5", "cuda:6"),
        batches_per_device=2,
    )

    class Forever:
        def poll(self):
            return None

    two.jobs.append(_Job(kind="screen", name="b1", device="cuda:5", process=Forever(), started=0.0))
    assert two._free_slots() == ["cuda:5", "cuda:6", "cuda:6"]
    two.jobs.append(_Job(kind="screen", name="b2", device="cuda:5", process=Forever(), started=0.0))
    assert two._free_slots() == ["cuda:6", "cuda:6"]


def test_a_batch_already_screening_here_is_not_handed_to_a_second_device(tmp_path, keyed, monkeypatch) -> None:
    """``recover`` releases a claim with no run record, and a screen that just started has none.

    A screen subprocess has to start, import MolCascade and compile the funnel before it writes a
    run record -- seconds on a loaded host. ``Sweep.recover`` runs at the top of every tick and
    releases the claim of any claimed batch whose run record does not exist, which is right for a
    crash and wrong for a launch: the batch is back in ``pending()`` on the very next tick while
    its screen is still starting. Handed to a second device, two subprocesses commit stages into
    one run record, and because a batch id *is* a run id nothing afterwards can say that record had
    two writers.
    """

    import threading

    supervisor, sweep, _ = _campaign(tmp_path, keyed, monkeypatch, batch_size=5)
    held = threading.Event()
    dispatched: list[str] = []

    def slow_screen(batch_id, library, device):  # noqa: ANN001, ARG001
        dispatched.append(f"{batch_id}@{device}")
        held.wait(10.0)  # the window: started, no run record written yet

    supervisor.screen = slow_screen  # type: ignore[assignment]
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    sweep.emit(revision_id="rev-1")

    try:
        assert supervisor._start_screens([]) == ("batch_0001@cuda:5",)
        # The release recovery performs, verbatim: claimed, and no run record to ask.
        assert [r.action for r in sweep.recover()] == ["requeued"]
        assert [b.batch_id for b in sweep.pending()] == ["batch_0001"]

        # Next pass. cuda:6 is free, batch_0001 is pending again, and its screen is still live here.
        assert supervisor._start_screens([]) == ()
        assert dispatched == ["batch_0001@cuda:5"]
    finally:
        held.set()


def test_a_restarted_supervisor_does_not_dispatch_on_top_of_live_screens(tmp_path, keyed, monkeypatch) -> None:
    """``self.jobs`` is this process's memory; a supervisor is advertised as restartable.

    The previous process's screens keep committing stages after it dies -- measured, a worker whose
    shell had died left a child committing stages for another hour -- and ``Sweep.recover``
    deliberately leaves a running run alone, because the claim is exactly the durable record of it.
    Counting occupancy from memory alone therefore reported every card free, and the restart
    dispatched a second full set of batches on top of the first.
    """

    from etalon.campaign.supervisor import Supervisor

    _, sweep, screen = _campaign(tmp_path, keyed, monkeypatch, batch_size=5)
    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(15)])
    sweep.emit(revision_id="rev-1")
    for batch_id, device in (("batch_0001", "cuda:5"), ("batch_0002", "cuda:6")):
        sweep.claim(batch_id, by=device)
        screen.runs[batch_id] = ScreenResult(
            batch_id, "rev-1", "RUNNING", (stage("conformers", "SUCCEEDED", artifact="a"),)
        )
    assert {r.action for r in sweep.recover()} == {"running"}

    def build(per_device: int) -> Supervisor:
        return Supervisor(
            sweep,
            revision_id="rev-1",
            workspace=tmp_path / f"restart{per_device}",
            screen_devices=("cuda:5", "cuda:6"),
            batches_per_device=per_device,
            screen=lambda *args: None,
        )

    fresh = build(1)
    assert fresh.jobs == []  # a restart genuinely remembers nothing
    assert fresh._free_slots() == []
    assert fresh._start_screens([]) == ()
    # The ration is still per device, not a blanket refusal: two per card leaves one each.
    assert build(2)._free_slots() == ["cuda:5", "cuda:6"]


# -- enrichment: a threshold is a percentile you have not measured -----------
#
# The ALK2 campaign's Uni-Dock gate was set to -8.0 because the weakest known active scored -8.497.
# Measured against the 274,097 molecules the campaign actually screened, -8.356 sits at the 81st
# percentile -- so that gate was a "keep the best 81%" gate, and nobody knew it. The same number on
# SND1's shallow groove sat *above* every known binder and kept 2 of 5,822. One threshold, a
# 2,000-fold difference in what it does.
_POPULATION = tuple(-6.0 - i * 0.004 for i in range(1000))  # -6.000 down to -9.996, strongest last


def test_a_score_threshold_is_a_percentile_nobody_measured(tmp_path) -> None:  # noqa: ARG001
    from etalon.campaign.calibrate import enrichment

    panel = (
        PanelMember("strong", True, "co-crystal"),
        PanelMember("weak", True, "measured, micromolar"),
        PanelMember("decoy", False, "negative control"),
    )
    scores = tuple(
        {"parent_id": p, "engine_id": "unidock", "score": v, "direction": "LOWER_STRONGER"}
        for p, v in (("strong", -9.99), ("weak", -7.0), ("decoy", -6.5))
    )
    row = enrichment(scores, panel, {"unidock": _POPULATION})[0]

    assert row.population == 1000
    # -9.99 is at the strong end of a -6.0 to -10.0 ramp; -7.0 is three quarters of the way in.
    assert row.best is not None and row.best < 0.01
    assert 0.70 < row.worst < 0.80
    # Keeping the best 1% retains the co-crystal ligand and deletes the micromolar one.
    assert row.recall_at(0.01) == 0.5
    # Full recall costs whatever percentile the weakest active sits at -- which is the number the
    # absolute threshold was choosing without saying so.
    assert row.keep_for(1.0) == row.worst
    assert row.informative is True


def test_the_percentile_becomes_the_number_a_cascade_gate_takes() -> None:
    from etalon.campaign.calibrate import HIGHER_STRONGER, percentile_threshold

    # Lower-is-stronger: the best 10% of a descending ramp.
    assert percentile_threshold(_POPULATION, 0.10) == pytest.approx(-9.6, abs=0.01)
    # Higher-is-stronger reverses which end is kept, and the helper must not read one as the other.
    rising = tuple(-v for v in _POPULATION)
    assert percentile_threshold(rising, 0.10, HIGHER_STRONGER) == pytest.approx(9.6, abs=0.01)
    assert percentile_threshold((), 0.1) is None
    assert percentile_threshold(_POPULATION, 0.0) is None


def test_a_score_that_orders_neither_class_is_not_informative() -> None:
    from etalon.campaign.calibrate import enrichment

    panel = (
        PanelMember("a1", True),
        PanelMember("a2", True),
        PanelMember("i1", False),
        PanelMember("i2", False),
    )
    # Actives bracket the inactives symmetrically: the median active is no better than the
    # median inactive, which is what a score with no enrichment on this panel looks like.
    scores = tuple(
        {"parent_id": p, "engine_id": "e", "score": v, "direction": "LOWER_STRONGER"}
        for p, v in (("a1", -9.0), ("i1", -8.9), ("a2", -6.9), ("i2", -7.0))
    )
    row = enrichment(scores, panel, {"e": _POPULATION})[0]
    assert row.informative is False


def test_a_pose_quality_tier_is_not_offered_as_droppable() -> None:
    """"Widen it or drop the tier" is wrong advice for a check on the coordinates.

    A known active failing `t9_redock` has not been shown to be a poor binder; it has been shown
    that the docking placed it badly, and every number computed from those coordinates inherits
    that. Measured on ALK2, this exact wording is what the campaign cited when it disabled the
    redock tier after one known active redocked at 5.834 A -- trading a pose-reproducibility check
    for a molecule whose pose was, by that very measurement, not reproducible.
    """

    structural = Calibration(
        revision_id="rev-1",
        run_id="panel",
        outcome="committed",
        panel_size=len(PANEL),
        actives=sum(1 for m in PANEL if m.known_active),
        registered=len(PANEL),
        tiers=(TierVerdict("t9_redock", "redock", 8, 7, ("C-26-A2",), ()),),
        finalize=(),
        separation=(),
    )
    reason = " ".join(structural.refusals())
    assert "do NOT drop it" in reason
    assert "judges the pose, not the molecule" in reason

    selective = Calibration(
        revision_id="rev-1",
        run_id="panel",
        outcome="committed",
        panel_size=len(PANEL),
        actives=sum(1 for m in PANEL if m.known_active),
        registered=len(PANEL),
        tiers=(TierVerdict("t9_docking", "docking", 8, 7, ("C-26-A2",), ()),),
        finalize=(),
        separation=(),
    )
    assert "widen it or drop the tier" in " ".join(selective.refusals())


def test_a_batch_id_that_already_names_a_run_is_refused(sweep: Sweep) -> None:
    """A batch id is a run id, and recovery reads run records by id without knowing whose they are.

    Measured on ALK2: a second sweep over the same pool, with its own fresh ledger and its own
    gate, carved batch_0001..0027 into a workspace that still held the first sweep's finished runs
    under those names. `recover()` found them SUCCEEDED and recorded all 27 as committed within 100
    seconds -- results produced by a different cascade, under a different gate, filed as the new
    campaign's own output. Nothing failed and nothing warned.
    """

    sweep.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    sweep.screen.runs["batch_0001"] = ScreenResult(
        "batch_0001", "someone-elses-revision", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)
    )
    with pytest.raises(SweepError, match="already names a run"):
        sweep.emit(revision_id="rev-1")


def test_a_prefix_scopes_batch_ids_to_one_campaign(tmp_path, keyed) -> None:
    """The remedy the refusal names: two campaigns in one workspace need distinct run ids."""

    screen = FakeScreen()
    gate = authorize_gate(calibration(), provenance=screen.provenance())
    first = Sweep(screen, Ledger(tmp_path / "a.jsonl"), tmp_path / "a.sqlite", batch_size=5, gate=gate)
    first.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    assert [b.batch_id for b in first.emit(revision_id="rev-1")] == ["batch_0001"]
    screen.runs["batch_0001"] = ScreenResult(
        "batch_0001", "rev-1", "SUCCEEDED", (stage("s", "SUCCEEDED", artifact="a"),)
    )

    second = Sweep(
        screen,
        Ledger(tmp_path / "b.jsonl"),
        tmp_path / "b.sqlite",
        batch_size=5,
        gate=gate,
        prefix="v7_",
    )
    second.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    assert [b.batch_id for b in second.emit(revision_id="rev-1")] == ["v7_batch_0001"]


def test_a_sweep_that_lost_its_prefix_is_refused_by_its_own_pool(tmp_path, keyed) -> None:
    """The dangerous direction, and the one `startswith` would wave through.

    The prefix is a constructor argument and is not stored, so a supervisor restarted from a script
    that lost it carves into a different id family in the same pool. The run-collision guard does
    not fire -- `batch_0028` beside `v7_batch_0027` is a free name -- and the campaign ends up
    holding two families. Measured downstream on ALK2: a harvest selecting runs by id prefix then
    collected the *other* family, 27 batches from a different cascade revision, and reported their
    198,511 hits under this campaign's name.
    """

    screen = FakeScreen()
    gate = authorize_gate(calibration(), provenance=screen.provenance())
    pool = tmp_path / "pool.sqlite"

    scoped = Sweep(screen, Ledger(tmp_path / "a.jsonl"), pool, batch_size=5, gate=gate, prefix="v7_")
    scoped.admit([(f"K{i}", f"C{i}", "f") for i in range(5)])
    assert [b.batch_id for b in scoped.emit(revision_id="rev-1")] == ["v7_batch_0001"]

    lost = Sweep(screen, Ledger(tmp_path / "a.jsonl"), pool, batch_size=5, gate=gate)
    lost.admit([(f"J{i}", f"J{i}", "f") for i in range(5)])
    with pytest.raises(SweepError, match="two id families") as refusal:
        lost.emit(revision_id="rev-1")
    # The refusal names the prefix that would make it work, read out of the pool itself.
    assert "'v7_'" in str(refusal.value)

    # And the other direction: a prefix pointed at a pool carved without one.
    bare = Sweep(screen, Ledger(tmp_path / "c.jsonl"), tmp_path / "c.sqlite", batch_size=5, gate=gate)
    bare.admit([(f"L{i}", f"L{i}", "f") for i in range(5)])
    assert [b.batch_id for b in bare.emit(revision_id="rev-1")] == ["batch_0001"]
    moved = Sweep(
        screen, Ledger(tmp_path / "c.jsonl"), tmp_path / "c.sqlite",
        batch_size=5, gate=gate, prefix="v8_",
    )
    moved.admit([(f"M{i}", f"M{i}", "f") for i in range(5)])
    with pytest.raises(SweepError, match="two id families"):
        moved.emit(revision_id="rev-1")
