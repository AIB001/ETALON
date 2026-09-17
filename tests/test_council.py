"""The council, and the two things it must never do.

It must never turn an unevaluated check into a passed one, and it must never sit without having
been measured. Everything else here is detail around those two.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etalon.council import (  # noqa: E402
    Evidence,
    Outcome,
    Seat,
    SeatError,
    Vote,
    adjudicate,
    as_observations,
    charter,
    cohen_kappa,
    composition,
    effective_votes,
    fleiss_kappa,
    for_a_person,
    report,
    rule,
)
from etalon.faults.attribution import Observation  # noqa: E402
from etalon.judgment.advisor import Advisory, Scripted  # noqa: E402

R, C, A = Vote.REFUSE, Vote.CLEAR, Vote.ABSTAIN


def _seat(name: str, sees: set[str], reply: str) -> Seat:
    return Seat(name, Advisory(Scripted({"": reply}), name), frozenset(sees))


def _answers(vote: str, reason: str = "a sentence long enough to read") -> str:
    return json.dumps({"vote": vote, "reason": reason})


RECORD = {
    "handoff_record": {"coordinate_origin": "SMILES_DEPICTION", "hydrogens": "NONE"},
    "provenance": {"toolchain_seeded": False, "revision_id": "rev-1"},
    "chemistry": {"smiles": "CCO"},
}
UNEVALUABLE = (
    Observation(
        code="F_RECEPTOR_NOT_THE_ONE_SCORED",
        fired=False,
        evaluable=False,
        detail="no receptor path supplied",
    ),
)


def _qualified():
    truth = [True] * 12 + [False] * 12
    left = [R] * 11 + [C] + [C] * 11 + [R]
    right = [R] * 9 + [C] * 3 + [C] * 10 + [R] * 2
    return rule({"a": left, "b": right}, truth, labels_described="24 hand-labelled rows")


# -- composition ------------------------------------------------------------------------


def test_two_seats_shown_the_same_evidence_are_one_opinion_at_two_prices() -> None:
    left = _seat("record-reader", {"handoff_record"}, _answers("refuse"))
    right = _seat("record-sceptic", {"handoff_record"}, _answers("clear"))
    with pytest.raises(SeatError) as refused:
        charter([left, right])
    assert "one opinion at two prices" in str(refused.value)
    # Named as the inherited sentence it is, so a reader can find where the rule comes from.
    assert "tuning/knob.py" in str(refused.value)


def test_differing_briefs_do_not_buy_independence() -> None:
    """A persona cannot change what is in front of it, and the refusal says so."""

    left = Seat(
        "optimist",
        Advisory(Scripted({"": _answers("clear")}), "optimist"),
        frozenset({Evidence.HANDOFF_RECORD}),
        brief="Assume the producer is competent.",
    )
    right = Seat(
        "pessimist",
        Advisory(Scripted({"": _answers("refuse")}), "pessimist"),
        frozenset({Evidence.HANDOFF_RECORD}),
        brief="Assume the producer is careless.",
    )
    with pytest.raises(SeatError):
        charter([left, right])


def test_a_council_of_one_is_an_advisor_and_is_refused() -> None:
    with pytest.raises(SeatError) as refused:
        charter([_seat("alone", {"handoff_record"}, _answers("refuse"))])
    assert "Advisory" in str(refused.value)


def test_an_unknown_evidence_kind_is_refused_at_construction_not_at_the_sitting() -> None:
    """The failure this prevents lands after the reliability has been quoted."""

    with pytest.raises(SeatError) as refused:
        Seat("typo", Advisory(Scripted({"": "{}"}), "t"), frozenset({"handoff_recrd"}))
    assert "handoff_record" in str(refused.value)


def test_evidence_is_withheld_in_the_process_rather_than_in_the_prompt() -> None:
    seat = _seat("provenance-reader", {"provenance"}, _answers("abstain"))
    shown = seat.evidence_for(RECORD)
    assert set(shown) == {"provenance"}
    assert "handoff_record" not in shown


def test_partial_overlap_is_reported_and_not_refused() -> None:
    left = _seat("a", {"handoff_record", "chemistry"}, _answers("refuse"))
    right = _seat("b", {"handoff_record", "provenance"}, _answers("clear"))
    charter([left, right])
    pair = composition([left, right])["pairs"][0]
    assert pair["shared_evidence"] == ["handoff_record"]
    assert 0.0 < pair["jaccard"] < 1.0


# -- reliability ------------------------------------------------------------------------


def test_a_seat_that_refuses_everything_is_not_above_chance() -> None:
    """ADR 0002's failure -- a gate that refuses the whole population -- caught statistically."""

    truth = [True] * 10 + [False] * 10
    ruling = rule(
        {"careful": [R] * 9 + [C] + [C] * 9 + [R], "refuses-everything": [R] * 20},
        truth,
        labels_described="20 rows",
    )
    assert not ruling.qualified
    assert any("refuses-everything" in reason for reason in ruling.refusals)
    skill = next(s for s in ruling.skills if s.seat == "refuses-everything")
    assert skill.sensitivity == 1.0 and skill.specificity == 0.0
    assert skill.youden_j == 0.0


def test_a_seat_that_clears_everything_is_equally_refused() -> None:
    truth = [True] * 10 + [False] * 10
    ruling = rule(
        {"careful": [R] * 9 + [C] + [C] * 9 + [R], "clears-everything": [C] * 20},
        truth,
        labels_described="20 rows",
    )
    assert not ruling.qualified


def test_a_one_class_label_set_cannot_measure_a_seat() -> None:
    """The case where an unconditional refuser looks perfect."""

    ruling = rule(
        {"a": [R] * 12, "b": [R] * 12}, [True] * 12, labels_described="12 bad rows only"
    )
    assert not ruling.qualified
    assert any("one-class" in reason for reason in ruling.refusals)


def test_too_few_labels_is_refused_rather_than_answered() -> None:
    ruling = rule({"a": [R, C], "b": [C, R]}, [True, False], labels_described="2 rows")
    assert not ruling.qualified
    assert any("below the floor" in reason for reason in ruling.refusals)


def test_abstentions_neither_score_nor_penalise_a_seat() -> None:
    """A seat cannot buy a clean record by never answering."""

    truth = [True] * 6 + [False] * 6
    votes = [R, R, R, A, A, A, C, C, C, A, A, A]
    ruling = rule({"a": votes, "b": votes}, truth, labels_described="12 rows")
    skill = ruling.skills[0]
    assert skill.abstentions == 6
    assert skill.labelled == 6
    assert skill.youden_j == 1.0


def test_three_seats_can_carry_barely_one_opinion() -> None:
    truth = [True] * 12 + [False] * 12
    twin = [R] * 11 + [C] + [C] * 11 + [R]
    other = [R] * 9 + [C] * 3 + [C] * 10 + [R] * 2
    ruling = rule(
        {"one": twin, "two": list(twin), "three": other},
        truth,
        labels_described="24 rows",
    )
    assert ruling.effective_votes < 2.0
    assert any("effective votes" in note for note in ruling.notes)


def test_effective_votes_never_exceed_the_seats() -> None:
    """Anti-correlated seats are not four opinions in a council of three."""

    assert effective_votes({("a", "b"): (-0.8, 0.1)}, 3) == 3.0


def test_kappa_is_zero_rather_than_one_when_both_raters_used_one_category() -> None:
    value, error = cohen_kappa(["refuse"] * 10, ["refuse"] * 10)
    assert value == 0.0 and error == 0.0


def test_fleiss_refuses_a_council_whose_membership_changed() -> None:
    with pytest.raises(ValueError, match="has not been measured as one council"):
        fleiss_kappa([["refuse", "clear"], ["refuse"]])


def test_mismatched_votes_and_labels_are_refused() -> None:
    with pytest.raises(ValueError, match="cannot be paired"):
        rule({"a": [R, C]}, [True], labels_described="x")


# -- the one-way door -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("refuse", "refuse", Outcome.REFUSED),
        ("refuse", "clear", Outcome.SPLIT),
        ("clear", "clear", Outcome.CLEARED_BUT_STILL_UNCHECKED),
        ("abstain", "abstain", Outcome.UNDECIDED),
        ("clear", "abstain", Outcome.CLEARED_BUT_STILL_UNCHECKED),
    ],
)
def test_only_a_unanimous_refusal_makes_a_check_evaluable(left, right, expected) -> None:
    findings = adjudicate(
        RECORD,
        UNEVALUABLE,
        [
            _seat("a", {"handoff_record"}, _answers(left)),
            _seat("b", {"provenance"}, _answers(right)),
        ],
        reliability=_qualified(),
        parent_id="M1",
    )
    assert findings[0].outcome is expected
    observation = as_observations(findings)[0]
    assert observation.evaluable is (expected is Outcome.REFUSED)
    assert observation.fired is (expected is Outcome.REFUSED)


def test_agreement_that_nothing_is_wrong_does_not_clear_the_check() -> None:
    """The one result that would let a campaign buy a clean record from an advisor."""

    findings = adjudicate(
        RECORD,
        UNEVALUABLE,
        [
            _seat("a", {"handoff_record"}, _answers("clear")),
            _seat("b", {"provenance"}, _answers("clear")),
        ],
        reliability=_qualified(),
    )
    from etalon.faults.preflight import unchecked

    # Still reported by the very function that exists so an unevaluated check is never
    # mistaken for a passed one.
    assert unchecked(as_observations(findings))


def test_a_fired_deterministic_check_is_out_of_jurisdiction() -> None:
    """No vote overturns a measurement -- a digest comparison is not up for discussion."""

    measured = (
        Observation(
            code="F_HYDROGENS_IMPLICIT", fired=True, evaluable=True, detail="0 hydrogens"
        ),
    )
    seats = [
        _seat("a", {"handoff_record"}, _answers("clear")),
        _seat("b", {"provenance"}, _answers("clear")),
    ]
    assert adjudicate(RECORD, measured, seats, reliability=_qualified()) == ()


# -- failing closed ---------------------------------------------------------------------


def test_an_unmeasured_council_does_not_sit_and_asks_nobody() -> None:
    transport = Scripted({"": _answers("refuse")})
    seats = [
        Seat("a", Advisory(transport, "a"), frozenset({Evidence.HANDOFF_RECORD})),
        Seat("b", Advisory(transport, "b"), frozenset({Evidence.PROVENANCE})),
    ]
    findings = adjudicate(RECORD, UNEVALUABLE, seats)
    assert findings[0].outcome is Outcome.COUNCIL_NOT_QUALIFIED
    assert transport.asked == [], "no seat should be asked anything before qualification"
    assert as_observations(findings)[0].evaluable is False


def test_a_refused_council_does_not_sit_either() -> None:
    at_chance = rule(
        {"a": [R] * 20, "b": [R] * 10 + [C] * 10},
        [True] * 10 + [False] * 10,
        labels_described="20 rows",
    )
    assert not at_chance.qualified
    findings = adjudicate(
        RECORD,
        UNEVALUABLE,
        [
            _seat("a", {"handoff_record"}, _answers("refuse")),
            _seat("b", {"provenance"}, _answers("refuse")),
        ],
        reliability=at_chance,
    )
    assert findings[0].outcome is Outcome.COUNCIL_NOT_QUALIFIED
    assert "refusals" in findings[0].qualification


def test_a_seat_that_cannot_be_reached_abstains_and_is_recorded_as_having_failed() -> None:
    """An outage read as assent is how a council silently shrinks."""

    findings = adjudicate(
        RECORD,
        UNEVALUABLE,
        [
            _seat("reachable", {"handoff_record"}, _answers("refuse")),
            Seat(
                "unreachable",
                Advisory(Scripted({"nothing matches": "{}"}), "unreachable"),
                frozenset({Evidence.PROVENANCE}),
            ),
        ],
        reliability=_qualified(),
    )
    ballots = {b.seat: b for b in findings[0].ballots}
    assert ballots["unreachable"].vote is Vote.ABSTAIN
    assert ballots["unreachable"].error
    assert "could not be reached" in findings[0].note


def test_an_answer_of_the_wrong_shape_is_not_mined_for_a_vote() -> None:
    findings = adjudicate(
        RECORD,
        UNEVALUABLE,
        [
            _seat("a", {"handoff_record"}, _answers("maybe")),
            _seat("b", {"provenance"}, _answers("abstain")),
        ],
        reliability=_qualified(),
    )
    ballot = next(b for b in findings[0].ballots if b.seat == "a")
    assert ballot.vote is Vote.ABSTAIN
    assert "not one of" in ballot.error


def test_a_code_outside_the_taxonomy_is_refused() -> None:
    with pytest.raises(KeyError, match="not in the fault taxonomy"):
        adjudicate(
            RECORD,
            (Observation(code="F_INVENTED", fired=False, evaluable=False, detail=""),),
            [
                _seat("a", {"handoff_record"}, _answers("refuse")),
                _seat("b", {"provenance"}, _answers("refuse")),
            ],
            reliability=_qualified(),
        )


# -- the split is the product -----------------------------------------------------------


def test_a_split_carries_the_minority_sentence_and_waits_for_a_person() -> None:
    findings = adjudicate(
        RECORD,
        UNEVALUABLE,
        [
            _seat("a", {"handoff_record"}, _answers("refuse", "the origin is a depiction")),
            _seat("b", {"provenance"}, _answers("clear", "provenance is intact")),
        ],
        reliability=_qualified(),
        parent_id="M1",
    )
    finding = findings[0]
    assert finding.needs_a_person and not finding.refusing
    assert [b.reason for b in finding.dissent()] == ["the origin is a depiction"]
    # A split is not encoded as a refusal: that would spend the operator's authority for them.
    assert as_observations(findings)[0].fired is False
    assert for_a_person(findings) == (finding,)


def test_the_report_states_the_bound_on_what_a_council_may_do() -> None:
    seats = [
        _seat("a", {"handoff_record"}, _answers("refuse")),
        _seat("b", {"provenance"}, _answers("refuse")),
    ]
    findings = adjudicate(RECORD, UNEVALUABLE, seats, reliability=_qualified())
    rendered = report(findings, seats)
    assert rendered["outcomes"]["refused"] == 1
    assert "may not move one to cleared" in rendered["authority"]


def test_a_seat_that_declines_the_class_that_matters_is_reported_as_such() -> None:
    """Found by the first real measurement: a J of 1.000 over two answered positives.

    The seat abstained on ten of the twelve records carrying the fault and answered both of the
    rest correctly, so its sensitivity was 1.0 and true of what it judged. Its overall abstention
    rate was 46%, under any threshold worth setting. The quantity that shows it is per class.
    """

    truth = [True] * 12 + [False] * 12
    declines_positives = [R, R] + [A] * 10 + [C] * 12
    thorough = [R] * 11 + [C] + [C] * 11 + [R]
    ruling = rule(
        {"declines-positives": declines_positives, "thorough": thorough},
        truth,
        labels_described="24 rows",
    )
    skill = next(s for s in ruling.skills if s.seat == "declines-positives")
    assert skill.youden_j == 1.0, "the headline is true of what it answered"
    assert skill.abstention_rate < 0.5, "and an overall rate would not have caught it"
    assert skill.abstained_when_present == 10
    assert skill.abstained_when_absent == 0
    assert any(
        "10 of 12 records where the fault was present" in note for note in ruling.notes
    )
