"""The agent's three judgement calls, and the ways each one goes wrong quietly.

A loop that screens, simulates and retunes itself has three places where it can deceive
its own operator, and none of them look like a bug at the time.

It can learn from a number that was not about the molecule on the label. It can accept an
improvement it cannot measure. And it can forget that it tried something that failed. The
tests here are mostly about those, because the arithmetic around them is small and the
failure modes are not.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.campaign.ledger import Ledger
from etalon.faults import Observation, blocking, check_record, waived_blocking
from etalon.judgment.waiver import Waiver, WaiverError, WaiverSet
from etalon.learn.admissible import Admission, Measurement, admissible, rule
from etalon.learn.calibrate import (
    Verdict,
    auc,
    decide,
    hanley_mcneil_se,
    scaffold_groups,
    score,
)

_REASON = (
    "Ligands are screened at pH 7.4 and the ligand state is accepted as the "
    "standardizer's neutral form for this round."
)


def _waiver(code: str = "F_PROTONATION_UNDECIDED", expires: date | None = None) -> Waiver:
    return Waiver(code, _REASON, "a-reviewer", expires or date(2099, 1, 1))


#: A handoff record built from a name rather than from geometry, which is what
#: MolCascade's SDF exporter actually produces and what PRISM's validator accepts.
FROM_A_DRAWING = {
    "parent_id": "p:drawing",
    "coordinate_source": "TWO_D_DEPICTION",
    "hydrogens": "IMPLICIT",
    "hydrogen_count": 0,
    "heavy_atom_count": 19,
    "formal_charge": 0,
    "stereo_smiles": None,
    "parent_smiles": "CC(C)NCC(O)COc1cccc2ccccc12",
    "protonation_state_id": "INHERITED_FROM_STANDARDIZER",
    "receptor_id": None,
    "status": "OK",
}


# -- waivers ---------------------------------------------------------------


def test_a_waiver_must_be_a_decision_rather_than_a_switch() -> None:
    """Each refusal here is a way of making a disabled check look like a judgement."""

    with pytest.raises(WaiverError, match="not in the taxonomy"):
        Waiver("F_INVENTED", _REASON, "someone", date(2099, 1, 1))
    with pytest.raises(WaiverError, match="too short to be a reason"):
        Waiver("F_PROTONATION_UNDECIDED", "fine", "someone", date(2099, 1, 1))
    with pytest.raises(WaiverError, match="must name who granted it"):
        Waiver("F_PROTONATION_UNDECIDED", _REASON, "   ", date(2099, 1, 1))


def test_a_fault_that_never_blocked_cannot_be_waived() -> None:
    """Accepting one would imply a refusal that was never going to happen.

    F_RUN_NOT_REPRODUCIBLE invalidates a claim about the run, not the run. A waiver for it
    would read as permission to spend, which was never withheld.
    """

    with pytest.raises(WaiverError, match="does not block a spend"):
        Waiver("F_RUN_NOT_REPRODUCIBLE", _REASON, "someone", date(2099, 1, 1))


def test_a_waiver_expires() -> None:
    """An unbounded waiver becomes the configuration and nobody revisits it."""

    live, dead = _waiver(), _waiver(expires=date(2020, 1, 1))
    waivers = WaiverSet((live, dead))

    assert waivers.codes(date(2026, 6, 1)) == frozenset({"F_PROTONATION_UNDECIDED"})
    assert dead in waivers.expired(date(2026, 6, 1))
    assert not dead.active_on(date(2026, 6, 1))


def test_the_waiver_reaches_the_decision_to_spend() -> None:
    """The bug this pairing was written for.

    The waiver released a measurement's right to teach, and the molecule was refused
    before any measurement existed -- so it released something that could never happen.
    """

    record = {**FROM_A_DRAWING, "coordinate_source": "EMBEDDED_CONFORMER",
              "hydrogens": "EXPLICIT_ALL", "hydrogen_count": 21,
              "stereo_smiles": FROM_A_DRAWING["parent_smiles"]}
    seen = check_record(record, toolchain_active=True)

    assert [o.code for o in blocking(seen)] == ["F_PROTONATION_UNDECIDED"]
    released = frozenset({"F_PROTONATION_UNDECIDED"})
    assert blocking(seen, waived=released) == ()
    # And it is still reported, because "nothing blocked" and "one thing blocked and was
    # accepted" must not be the same sentence.
    assert [o.code for o in waived_blocking(seen, released)] == ["F_PROTONATION_UNDECIDED"]


def test_a_waiver_releases_only_what_it_names() -> None:
    seen = check_record(FROM_A_DRAWING, toolchain_active=True)
    released = frozenset({"F_PROTONATION_UNDECIDED"})

    still = {o.code for o in blocking(seen, waived=released)}

    assert "F_COORDINATES_ARE_A_DEPICTION" in still
    assert "F_HYDROGENS_IMPLICIT" in still
    assert "F_PROTONATION_UNDECIDED" not in still


# -- admissibility ---------------------------------------------------------


def test_a_label_from_a_drawing_may_not_teach() -> None:
    """The measured case: a flat, hydrogen-free structure simulates without a warning.

    Across 41 gaff2 builds from hydrogen-free input the topology carried zero hydrogens
    in 41 of 41. A loop without this gate does not record one bad number -- it fits the
    screen's thresholds to a molecule that was never simulated.
    """

    observed = check_record(FROM_A_DRAWING, toolchain_active=True)
    ruling = rule(Measurement("p:drawing", -8.2, -31.0, observed))

    assert ruling.admission is Admission.WITHHELD
    assert not ruling.teaches
    assert "F_COORDINATES_ARE_A_DEPICTION" in ruling.blocking
    assert any("different species" in note for note in ruling.notes)


def test_an_unreproducible_measurement_still_teaches() -> None:
    """It may be a perfectly good number. What is missing is a check on a claim."""

    ruling = rule(
        Measurement(
            "p",
            -8.0,
            -30.0,
            (Observation("F_RUN_NOT_REPRODUCIBLE", True, "no seed shim"),),
        )
    )

    assert ruling.admission is Admission.ADMITTED
    assert ruling.teaches
    assert ruling.reproducible is False


def test_a_measurement_with_no_expensive_number_is_withheld_not_dropped() -> None:
    """A molecule sent for simulation that came back empty is a fact about the campaign."""

    ruling = rule(Measurement("p", -8.0, None, ()))

    assert ruling.admission is Admission.WITHHELD
    assert "NO_EXPENSIVE_VALUE" in ruling.blocking


def test_a_waived_cause_is_admitted_and_stays_standing() -> None:
    observed = (Observation("F_PROTONATION_UNDECIDED", True, "INHERITED_FROM_STANDARDIZER"),)

    ruling = rule(Measurement("p", -8.0, -30.0, observed), WaiverSet((_waiver(),)))

    assert ruling.admission is Admission.ADMITTED_UNDER_WAIVER
    assert ruling.waived == ("F_PROTONATION_UNDECIDED",)
    assert any("not cleared" in note for note in ruling.notes)


def test_a_low_admission_rate_is_reported_as_a_selection() -> None:
    """The caveat the literature's loops do not carry.

    Molecules that survive preflight are not a random draw from the pool. On a congeneric
    series they are the ones whose geometry was easy, which are not the ones a screen is
    getting wrong.
    """

    drawing = check_record(FROM_A_DRAWING, toolchain_active=True)
    clean = (Observation("F_COORDINATES_ARE_A_DEPICTION", False, "DOCKED_POSE"),)
    report = admissible(
        [
            Measurement("a", -8.0, -30.0, clean),
            Measurement("b", -7.0, -29.0, drawing),
            Measurement("c", -6.0, -28.0, drawing),
        ]
    )

    assert len(report.admitted) == 1
    assert report.rate == pytest.approx(1 / 3)
    assert any("selection rather than a sample" in note for note in report.notes)
    assert report.reasons()["F_COORDINATES_ARE_A_DEPICTION"] == 2


def test_an_unknown_fault_code_is_refused_rather_than_ignored() -> None:
    with pytest.raises(KeyError, match="not in the taxonomy"):
        rule(Measurement("p", -8.0, -30.0, (Observation("F_MADE_UP", True, "x"),)))


# -- calibration -----------------------------------------------------------


def test_the_panel_cannot_resolve_a_small_improvement() -> None:
    """Measured on the real shape of the STK17B panel: 40 potent of 231.

    The Hanley-McNeil standard error there is about 0.047, so the smallest difference the
    panel can resolve is roughly 0.10 AUC. A round reporting 0.71 to 0.74 has reported
    the same measurement twice, and accepting that update is how a campaign drifts while
    announcing progress every round.
    """

    assert hanley_mcneil_se(0.75, 40, 191) == pytest.approx(0.047, abs=0.002)

    random.seed(7)
    labels = [1] * 40 + [0] * 191
    before = [random.gauss(0.6 if y else 0.0, 1.0) for y in labels]
    baseline = score(before, labels)

    faint = decide(baseline, score([v + (0.08 if y else 0) for v, y in zip(before, labels, strict=True)], labels))
    clear = decide(baseline, score([v + (0.45 if y else 0) for v, y in zip(before, labels, strict=True)], labels))
    worse = decide(baseline, score([v - (0.50 if y else 0) for v, y in zip(before, labels, strict=True)], labels))

    assert faint.verdict is Verdict.WITHIN_NOISE
    assert not faint.accepted
    assert clear.verdict is Verdict.ACCEPTED
    assert worse.verdict is Verdict.WORSE
    assert "may be real and this panel cannot tell" in faint.note


def test_a_score_says_which_split_it_came_from_and_defaults_to_the_weakest() -> None:
    """The field was free text defaulting to "scaffold", and ``decide`` printed "on a
    scaffold-grouped holdout" whenever it saw that value. The only production caller ranks the whole
    panel and passed the default, so every accepted update claimed a holdout nobody took."""

    from etalon.learn.calibrate import Split

    random.seed(7)
    labels = [1] * 40 + [0] * 191
    before = [random.gauss(0.6 if y else 0.0, 1.0) for y in labels]
    after = [v + (0.45 if y else 0) for v, y in zip(before, labels, strict=True)]

    assert score(before, labels).split is Split.WHOLE_PANEL
    assert score(before, labels, split=Split.SCAFFOLD_GROUPED).split is Split.SCAFFOLD_GROUPED

    baseline = score(before, labels)
    accepted = decide(baseline, score(after, labels))
    assert accepted.accepted
    assert "nothing held out" in accepted.note
    assert "scaffold-grouped holdout" not in accepted.note

    grouped = decide(baseline, score(after, labels, split=Split.SCAFFOLD_GROUPED))
    assert grouped.accepted
    assert "scaffold-grouped holdout" in grouped.note


def test_the_panel_scored_change_does_not_claim_a_holdout_it_did_not_take() -> None:
    """The production path, pinned: an edited scoring function has nothing to leak, and that is a
    reason to call the comparison whole-panel rather than a reason to call it grouped."""

    from etalon.campaign.propose import Panel, ParameterChange
    from etalon.learn.calibrate import Split

    rings = ["c1ccccc1", "c1ccncc1", "c1ccsc1", "c1cc[nH]c1", "C1CCCCC1", "C1CCNCC1"]
    smiles = tuple(f"{'C' * (index % 4 + 1)}{rings[index % len(rings)]}" for index in range(40))
    affinity = tuple(1.0 if index % 3 else 5000.0 for index in range(40))

    change = ParameterChange(panel=Panel(smiles=smiles, affinity_nM=affinity))
    try:
        # Panel featurises lazily, through MolCascade's featuriser, on the first use of
        # usable_smiles -- which is inside score_of rather than in the constructor.
        scored = change.score_of(lambda text: float(len(text)))
    except ImportError as error:  # pragma: no cover -- environment-dependent
        pytest.skip(f"vendored molcascade is not usable here ({error}); it needs pydantic v2")

    assert scored.split is Split.WHOLE_PANEL
    assert scored.as_dict()["split"] == "whole_panel"


def test_too_few_positives_is_underpowered_rather_than_a_verdict() -> None:
    labels = [1] * 4 + [0] * 40
    values = [1.0] * 4 + [0.0] * 40
    before = score(values, labels)

    assert decide(before, before).verdict is Verdict.UNDERPOWERED


def test_the_auc_is_exact_and_counts_ties_as_half() -> None:
    """A screen giving many molecules one score must not be flattered by interpolation."""

    assert auc([1.0, 0.0], [1, 0]) == 1.0
    assert auc([0.0, 1.0], [1, 0]) == 0.0
    assert auc([0.5, 0.5], [1, 0]) == 0.5


def test_an_auc_needs_both_classes() -> None:
    with pytest.raises(ValueError, match="needs both classes"):
        auc([1.0, 2.0], [1, 1])


def test_scaffolds_group_a_series_and_isolate_what_has_none() -> None:
    """Pooling every failure into one group re-introduces the leakage grouping prevents."""

    groups = scaffold_groups(["c1ccccc1CC", "c1ccccc1CCC", "CCO", "not a molecule"])

    assert groups[0] == groups[1]
    assert groups[2].startswith("acyclic:")
    assert groups[3].startswith("unparsed:")
    assert len(set(groups)) == 3


# -- the ledger ------------------------------------------------------------


def test_only_accepted_changes_reach_the_state(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "c.jsonl")
    ledger.append("round", "r1", change={"qed_min": 0.30}, decision={"accepted": True})
    ledger.append("round", "r2", change={"qed_min": 0.40}, decision={"accepted": False})

    state = ledger.replay()

    assert state["parameters"] == {"qed_min": 0.30}
    assert state["changes_refused_in"] == ["r2"]


def test_a_rewind_abandons_without_deleting(tmp_path: Path) -> None:
    """An agent that can erase its history can erase the evidence it was going wrong."""

    ledger = Ledger(tmp_path / "c.jsonl")
    for name, value in (("r1", 0.30), ("r2", 0.35), ("r3", 0.40)):
        ledger.append("round", name, change={"qed_min": value}, decision={"accepted": True})
    ledger.rewind("r1", reason="r3 was fitted on a leaking split", by="a-reviewer")
    ledger.append("round", "r4", change={"sa_max": 4.5}, decision={"accepted": True})

    state = ledger.replay()

    assert state["rounds_live"] == ["r1", "r4"]
    assert state["rounds_abandoned"] == ["r2", "r3"]
    assert state["parameters"] == {"qed_min": 0.30, "sa_max": 4.5}
    # Still on disk: three rounds, one rewind, one round.
    assert len(ledger.entries()) == 5


def test_a_rewind_to_a_round_that_never_happened_is_refused(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "c.jsonl")
    ledger.append("round", "r1")

    with pytest.raises(KeyError, match="never recorded"):
        ledger.rewind("r9", reason="a reason long enough to read", by="a")
    with pytest.raises(ValueError, match="needs a reason"):
        ledger.rewind("r1", reason="oops", by="a")


def test_an_unreadable_line_is_a_gap_rather_than_a_nuisance(tmp_path: Path) -> None:
    """The ledger is the authority for the configuration, so skipping a line loses a decision."""

    path = tmp_path / "c.jsonl"
    ledger = Ledger(path)
    ledger.append("round", "r1", change={"qed_min": 0.3}, decision={"accepted": True})
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{this is not json}\n")

    with pytest.raises(ValueError, match="readable ledger entry"):
        ledger.entries()


def test_every_round_line_round_trips_as_json(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "c.jsonl")
    ledger.append("round", "r1", waivers=WaiverSet((_waiver(),)).as_dict())

    payload = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[0])

    assert payload["kind"] == "round"
    assert payload["waivers"]["active"][0]["code"] == "F_PROTONATION_UNDECIDED"
    # The record says what was accepted, not only which code, so a reader needs no
    # access to this source tree.
    assert payload["waivers"]["active"][0]["accepted_consequence"] == "wrong_subject"


# -- the workspace ---------------------------------------------------------


def test_a_workspace_under_a_temporary_directory_is_reported_as_volatile(tmp_path: Path) -> None:
    """It cost a measured loss: a calibration run's 231 docked poses were under /tmp and gone.

    The findings survived because they had been committed; the artifacts did not. Reported rather than
    refused, because a smoke test legitimately wants a temporary directory and a guard that refuses one
    gets worked around instead of read.
    """

    from etalon.boundary.infra import load
    from etalon.boundary.screen import Screen

    load("molcascade")

    volatile = Screen("/tmp/etalon-durability-probe").durability()
    durable = Screen(tmp_path / "ws").durability()

    assert volatile["durable"] is False
    assert volatile["volatile_root"] == "/tmp"
    assert "may clear at any time" in str(volatile["why_it_matters"])
    # pytest's tmp_path is itself under a temporary root on most hosts, so the durable case is
    # asserted on the shape of the answer rather than on its value.
    assert set(durable) == set(volatile)
    assert isinstance(durable["durable"], bool)


# -- the composed loop ----------------------------------------------------
#
# Every test above this line tests a component: the ledger, the admissibility rule, the calibration
# gate, the waiver. None of them ran Campaign.round, which is the function the README calls the
# agent and the one that composes all of them. The gap was not cosmetic -- Acquisition.propose takes
# candidates and returns a Proposal while the loop's Proposer hook takes measurements and returns a
# change with a decision, so Act.SPEND, the one act an advisor is allowed to settle by itself, had
# no implementation and nothing noticed for as long as nothing composed the loop.
#
# The screen is a double rather than MolCascade. What is under test is the order of the loop's own
# steps and what it refuses between them; driving a real cascade here would test MolCascade and
# would need an environment this suite deliberately does not require.


class _FakeScreenResult:
    run_id = "run-fake-0001"
    committed = ("validity", "docking")


class _FakeScreen:
    """Stands in for MolCascade: the four calls Campaign.round makes, and nothing else."""

    def __init__(self, rows, metrics=None, *, durable=True):
        self.rows = rows
        self._metrics = metrics
        self._durable = durable
        self.planned = None

    def durability(self):
        return {"durable": self._durable, "why_it_matters": "workspace is under a temp dir"}

    def plan(self, config_path, library, *, target=None):
        self.planned = (config_path, library, target)
        return SimpleNamespace(revision_id="rev-fake-0001")

    def run(self, plan, *, workers=None):
        return _FakeScreenResult()

    def handoff(self, result):
        return list(self.rows)

    def metrics(self, result, *, metric_id=None):
        if self._metrics is None:
            raise ValueError("no docking tier in this cascade and no metric id supplied")
        return dict(self._metrics)


def _handoff_row(parent_id: str, smiles: str, *, clean: bool) -> dict:
    return {
        "parent_id": parent_id,
        "parent_smiles": smiles,
        "molblock": "(a molblock)",
        "coordinate_source": "DOCKED_POSE" if clean else "TWO_D_DEPICTION",
        "hydrogens": "EXPLICIT_ALL" if clean else "IMPLICIT",
        "hydrogen_count": 14 if clean else 0,
        "heavy_atom_count": 12,
        "formal_charge": 0,
        "stereo_smiles": smiles,
        "protonation_state_id": "propka:ph7.4",
        "receptor_id": "receptor:abc123",
        "status": "OK",
    }


def _campaign(tmp_path: Path, screen):
    """A Campaign with the screen replaced, constructed without running __init__.

    ``Campaign.__init__`` builds a real ``Screen``, which reaches for the vendored MolCascade. What
    is under test here is the order of the loop's own steps, so the screen is a double and the
    constructor is bypassed rather than parameterised -- adding an injection point to production
    code only so a test can reach it would be the test shaping the design.
    """

    from etalon.campaign.loop import Campaign

    campaign = Campaign.__new__(Campaign)
    campaign.screen = screen
    campaign.ledger = Ledger(tmp_path / "campaign.jsonl")
    campaign.waivers = WaiverSet()
    return campaign


def test_a_round_refuses_before_spending_and_records_what_it_refused(tmp_path: Path) -> None:
    """The loop's whole order in one pass: screen, refuse, measure only what survived, admit,
    record. The refused molecule must never reach the expensive stage."""

    rows = [
        _handoff_row("p:good", "CCOc1ccccc1", clean=True),
        _handoff_row("p:drawing", "CCNc1ccccc1", clean=False),
    ]
    screen = _FakeScreen(rows, metrics={"p:good": -8.4, "p:drawing": -8.1})
    campaign = _campaign(tmp_path, screen)

    handed_to_the_expensive_stage: list[str] = []

    def measure(allowed, cheap):
        handed_to_the_expensive_stage.extend(str(row["parent_id"]) for row in allowed)
        return [
            Measurement(
                parent_id=str(row["parent_id"]),
                cheap_value=cheap.get(str(row["parent_id"])),
                expensive_value=-31.2,
            )
            for row in allowed
        ]

    outcome = campaign.round("r1", "cascade.yaml", "library.csv", measure=measure)

    # The refusal happened before the spend, which is the entire point of the ordering.
    assert handed_to_the_expensive_stage == ["p:good"]
    assert outcome.refused_before_spending == ("p:drawing",)
    assert outcome.handed_off == 2
    assert outcome.measured == 1
    assert outcome.admission is not None
    assert outcome.admission.admitted[0].parent_id == "p:good"

    # And the round is on disk, replayable, with the refusal in it.
    entries = list(campaign.ledger.entries())
    assert [entry.kind for entry in entries] == ["round"]
    assert entries[0].body["refused_before_spending"] == ["p:drawing"]


def test_a_round_without_a_comparator_measures_and_learns_nothing(tmp_path: Path) -> None:
    """A cascade with no docking tier is a real configuration, and the honest outcome is a round
    that records numbers and updates nothing -- not a crash, and not an update fitted to whatever
    number happened to be available."""

    screen = _FakeScreen([_handoff_row("p:good", "CCOc1ccccc1", clean=True)], metrics=None)
    campaign = _campaign(tmp_path, screen)

    outcome = campaign.round(
        "r1",
        "cascade.yaml",
        "library.csv",
        measure=lambda allowed, cheap: [
            Measurement(parent_id="p:good", cheap_value=None, expensive_value=-31.2)
        ],
    )

    assert outcome.measured == 1
    assert any("learn nothing from it" in note for note in outcome.notes)
    # No cheap value means the measurement is withheld from teaching rather than dropped.
    assert outcome.admission is not None
    assert [w.parent_id for w in outcome.admission.withheld] == ["p:good"]


def test_a_proposal_the_panel_cannot_resolve_is_recorded_and_not_applied(tmp_path: Path) -> None:
    """The gate on the feedback, reached through the loop rather than called directly."""

    from etalon.learn.calibrate import Score, Split, Verdict

    screen = _FakeScreen(
        [_handoff_row("p:good", "CCOc1ccccc1", clean=True)], metrics={"p:good": -8.4}
    )
    campaign = _campaign(tmp_path, screen)

    within_noise = decide(
        Score(0.71, 0.047, 40, 191, Split.SCAFFOLD_GROUPED),
        Score(0.74, 0.047, 40, 191, Split.SCAFFOLD_GROUPED),
    )
    assert within_noise.verdict is Verdict.WITHIN_NOISE

    outcome = campaign.round(
        "r1",
        "cascade.yaml",
        "library.csv",
        measure=lambda allowed, cheap: [
            Measurement(parent_id="p:good", cheap_value=-8.4, expensive_value=-31.2)
        ],
        propose=lambda teach: ({"exhaustiveness": 32}, within_noise),
    )

    assert outcome.decision is not None
    assert not outcome.decision.accepted
    assert outcome.change == {"exhaustiveness": 32}
    # Recorded, and not in the state the next round starts from. The replay has to be able to say
    # the round tried something and that it did not stick -- a ledger holding only accepted changes
    # cannot be read to find out what the campaign attempted.
    state = campaign.state()
    assert state["parameters"] == {}
    assert state["changes_refused_in"] == ["r1"]
    assert state["changes_accepted_in"] == []
    assert any("recorded and not applied" in note for note in outcome.notes)


def test_the_acquisition_hook_carries_the_one_act_an_advisor_may_settle(tmp_path: Path) -> None:
    """Act.SPEND is ACTED_ON in the autonomy table and had no way into the loop at all: the two
    signatures could not be connected. This is that hook, and the ledger line it writes."""

    from etalon.judgment.proposal import Act, Advisor, AdvisorKind, Proposal

    screen = _FakeScreen(
        [
            _handoff_row("p:good", "CCOc1ccccc1", clean=True),
            _handoff_row("p:drawing", "CCNc1ccccc1", clean=False),
        ],
        metrics={"p:good": -8.4},
    )
    campaign = _campaign(tmp_path, screen)
    seen: dict[str, str] = {}

    def acquire(candidates):
        seen.update(candidates)
        return Proposal(
            act=Act.SPEND,
            advisor=Advisor(AdvisorKind.ALGORITHM, "conformal-acquisition@0.1.0", "in-process"),
            payload={"chosen": sorted(candidates)},
        )

    outcome = campaign.round(
        "r1",
        "cascade.yaml",
        "library.csv",
        measure=lambda allowed, cheap: [
            Measurement(parent_id="p:good", cheap_value=-8.4, expensive_value=-31.2)
        ],
        acquire=acquire,
    )

    # A molecule refused for this round is still a candidate for the next one: excluding it would
    # make the next batch a function of which records happened to be well formed.
    assert set(seen) == {"p:good", "p:drawing"}
    assert outcome.next_batch is not None
    assert outcome.next_batch["act"] == "spend"
    assert outcome.next_batch["autonomy"] == "acted_on"
    assert list(campaign.ledger.entries())[0].body["next_batch"]["payload"]["chosen"] == [
        "p:drawing",
        "p:good",
    ]


def test_an_act_that_needs_a_person_cannot_reach_the_loop(tmp_path: Path) -> None:
    """The autonomy guard had zero production callers -- it was a table stating the rule and
    nothing checking it. The adapters call it now, so a COMPARATOR or WAIVER proposal handed to the
    loop raises rather than being applied like any other."""

    from etalon.campaign.propose import as_loop_acquirer
    from etalon.judgment.proposal import (
        Act,
        Advisor,
        AdvisorKind,
        NotAnAdvisorsDecision,
        Proposal,
    )

    class _ProposesAWaiver:
        def propose(self, candidates):
            return Proposal(
                act=Act.WAIVER,
                advisor=Advisor(AdvisorKind.LANGUAGE_MODEL, "claude-sonnet-5", "claude-cli"),
                payload={"code": "F_PROTONATION_UNDECIDED"},
            )

    acquirer = as_loop_acquirer(_ProposesAWaiver())

    with pytest.raises(NotAnAdvisorsDecision, match="recommendation and not a decision"):
        acquirer({"p:good": "CCOc1ccccc1"})

    # And an empty candidate set is a round with no next batch rather than an error.
    assert acquirer({}) is None
