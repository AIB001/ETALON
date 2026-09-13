"""Where an advisor may act, and the two places it must not.

The measured failure mode of autonomous research agents is not a wrong answer -- it is a
confident, well-formatted one, produced most readily under extended reasoning and most dangerous
where a reader cannot tell it from a correct one. So these tests are almost entirely about
refusals: of an answer in the wrong shape, of a proposal used past the point its act may travel,
and of a model signing the one document in this system whose value is that a person signed it.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.judgment.advisor import AdvisorError, Advisory, Scripted
from etalon.judgment.proposal import (
    AUTONOMY,
    Act,
    Advisor,
    AdvisorKind,
    Autonomy,
    NotAnAdvisorsDecision,
    Proposal,
    refuse_if_not_an_advisors_decision,
)
from etalon.judgment.waiver import Waiver, WaiverError

_REASON = (
    "Ligands are screened at pH 7.4 and the ligand state is accepted as the standardizer's "
    "neutral form for this round."
)


def _model() -> Advisor:
    return Advisor(AdvisorKind.LANGUAGE_MODEL, "claude-sonnet-5", "claude-cli")


def _person() -> Advisor:
    return Advisor(AdvisorKind.PERSON, "a-reviewer", "keyboard")


def test_every_act_has_a_stated_autonomy() -> None:
    """An act with no rule would default to whatever the first caller decided."""

    assert set(AUTONOMY) == set(Act)


def test_the_gradation_is_by_what_being_wrong_costs() -> None:
    """Pinned by value, because each row is an argument in ADR 0003 rather than a preference."""

    assert AUTONOMY[Act.SPEND] is Autonomy.ACTED_ON
    assert AUTONOMY[Act.PARAMETER_CHANGE] is Autonomy.GATED
    assert AUTONOMY[Act.COMPARATOR] is Autonomy.NEEDS_A_PERSON
    assert AUTONOMY[Act.WAIVER] is Autonomy.NEEDS_A_PERSON
    assert AUTONOMY[Act.HYPOTHESIS] is Autonomy.RECORDED_ONLY


def test_a_spend_proposal_travels_and_a_waiver_does_not() -> None:
    spend = Proposal(Act.SPEND, _model(), {"parent_ids": ["a", "b"]})
    waiver = Proposal(Act.WAIVER, _model(), {"code": "F_PROTONATION_UNDECIDED"})

    refuse_if_not_an_advisors_decision(spend)  # does not raise
    assert spend.may_be_applied_without_a_person is True
    with pytest.raises(NotAnAdvisorsDecision, match="recommendation and not a decision"):
        refuse_if_not_an_advisors_decision(waiver)


def test_a_hypothesis_is_not_an_instruction() -> None:
    hypothesis = Proposal(Act.HYPOTHESIS, _model(), {"cause": "the pose drifted"})

    with pytest.raises(NotAnAdvisorsDecision, match="acted on by nothing"):
        refuse_if_not_an_advisors_decision(hypothesis)


def test_a_payload_that_cannot_be_recorded_is_refused() -> None:
    """It goes in the ledger, and a proposal nothing can record cannot be audited."""

    with pytest.raises(ValueError, match="must serialise"):
        Proposal(Act.SPEND, _model(), {"model": object()})


def test_an_anonymous_advisor_is_refused() -> None:
    with pytest.raises(ValueError, match="identifiable"):
        Advisor(AdvisorKind.LANGUAGE_MODEL, "   ")


# -- the waiver line -------------------------------------------------------


def test_a_model_cannot_grant_a_waiver_by_name_or_by_kind() -> None:
    """Both paths, because granted_by is a string and a kind is not always available."""

    with pytest.raises(WaiverError, match="looks like a language model"):
        Waiver("F_PROTONATION_UNDECIDED", _REASON, "claude-sonnet-5", date(2099, 1, 1))
    with pytest.raises(WaiverError, match="is a language model and cannot grant"):
        Waiver.granted_by_advisor(_model(), "F_PROTONATION_UNDECIDED", _REASON, date(2099, 1, 1))


def test_a_person_can_grant_one() -> None:
    granted = Waiver.granted_by_advisor(
        _person(), "F_PROTONATION_UNDECIDED", _REASON, date(2099, 1, 1)
    )

    assert granted.granted_by == "a-reviewer"
    assert granted.active_on(date(2026, 6, 1))


def test_a_recommendation_is_plain_data_and_not_a_waiver() -> None:
    """A half-granted Waiver object would sit in a variable looking like a granted one."""

    recommended = Waiver.recommend(
        _model(), "F_PROTONATION_UNDECIDED", _REASON, date(2099, 1, 1)
    )["recommended_waiver"]

    assert not isinstance(recommended, Waiver)
    assert recommended["status"] == "AWAITING_A_PERSON"
    assert recommended["recommended_by"] == "claude-sonnet-5"
    assert recommended["advisor_kind"] == "language_model"


# -- the transport ---------------------------------------------------------


def test_a_well_shaped_answer_becomes_a_proposal() -> None:
    transport = Scripted({"comparator": '{"metric_id": "docking_score", "reason": "Only one."}'})

    proposal = Advisory(transport, "test-model").ask(
        Act.COMPARATOR, "Which metric?", required=["metric_id"]
    )

    assert proposal.payload["metric_id"] == "docking_score"
    assert proposal.rationale == "Only one."
    assert proposal.advisor.prompt_sha256 and proposal.advisor.response_sha256
    assert proposal.payload["_attempts"] == 1


def test_a_fenced_answer_is_tolerated_because_a_fence_is_a_wrapper() -> None:
    transport = Scripted({"spend": '```json\n{"parent_ids": ["a"], "reason": "x"}\n```'})

    proposal = Advisory(transport, "m").ask(Act.SPEND, "Which?", required=["parent_ids"])

    assert proposal.payload["parent_ids"] == ["a"]


def test_prose_where_an_object_was_asked_for_is_refused_not_mined() -> None:
    """Reaching in for the part that looks right is how a hallucination becomes a record."""

    transport = Scripted({"comparator": "You should probably use the docking score, about -8."})

    with pytest.raises(AdvisorError, match="did not answer"):
        Advisory(transport, "m", attempts=2).ask(
            Act.COMPARATOR, "Which metric?", required=["metric_id"]
        )
    assert len(transport.asked) == 2


def test_a_missing_key_is_refused_even_though_the_json_parsed() -> None:
    transport = Scripted({"spend": '{"reason": "These look promising."}'})

    with pytest.raises(AdvisorError, match="missing"):
        Advisory(transport, "m", attempts=1).ask(Act.SPEND, "Which?", required=["parent_ids"])


def test_an_unscripted_question_is_an_error_rather_than_a_default() -> None:
    """A scripted advisor that answers anything tests the harness against a fiction."""

    with pytest.raises(AdvisorError, match="no answer for this question"):
        Advisory(Scripted({"spend": "{}"}), "m").ask(
            Act.HYPOTHESIS, "Why?", required=["cause"]
        )


def test_a_retried_question_records_that_it_was_retried() -> None:
    """Three attempts is different evidence from one, and a record must keep the difference."""

    class Flaky:
        name = "flaky"

        def __init__(self) -> None:
            self.calls = 0

        def ask(self, prompt: str) -> str:
            self.calls += 1
            return "nonsense" if self.calls == 1 else '{"metric_id": "x", "reason": "y"}'

    proposal = Advisory(Flaky(), "m", attempts=3).ask(
        Act.COMPARATOR, "Which?", required=["metric_id"]
    )

    assert proposal.payload["_attempts"] == 2
    assert len(proposal.payload["_earlier_attempts"]) == 1


def test_the_prompt_discourages_extended_reasoning() -> None:
    """Hallucinations in the benchmarks were highest under chain-of-thought prompting."""

    transport = Scripted({"spend": '{"parent_ids": [], "reason": "none"}'})
    Advisory(transport, "m").ask(Act.SPEND, "Which?", required=["parent_ids"])

    sent = transport.asked[0]
    assert "one sentence" in sent
    assert "Do not explain your reasoning at length" in sent
    assert "exactly one JSON object" in sent
