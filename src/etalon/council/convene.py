"""Sit the council, and bound what its agreement is allowed to do.

The jurisdiction is narrow and the narrowness is the design.

``faults/preflight.py`` returns nine observations for every handoff record, firing or not, and
``unchecked()`` exists so that a check which *could not run* is never reported as one that passed.
Those unevaluable checks are what the council rules on. A deterministic check that ran is a
measurement -- a receptor digest compared, a hydrogen count read off a file -- and no vote overturns
a measurement. A council convened over a fired fault would be a mechanism for arguing with a
hash comparison, which is the mechanism this whole repository exists to not have.

**The authority is asymmetric, and the asymmetry is ``learn/admissible.py``'s.** Withholding a good
measurement costs one molecule's information; admitting a bad one costs a shift in the policy
applied to all of them. So:

* A council may move a check from *unevaluable* to *fired*. It can add a refusal.
* A council may **never** move a check from unevaluable to cleared. Seats agreeing they see no
  problem produces :attr:`~etalon.council.ballot.Outcome.CLEARED_BUT_STILL_UNCHECKED`, the
  observation stays ``evaluable=False``, and ``unchecked()`` still reports it. A language model
  saying "this looks fine" is not a check having passed, and the one thing that must not happen is
  a campaign reading a clean summary it bought from an advisor.

That bound is what makes the layer safe to add. The worst a compromised, miscalibrated or simply
wrong council can do is refuse molecules that were fine -- which costs compute, shows up as a
collapsed admission rate, and is visible in the ledger. It cannot manufacture a clean record.

And a council that refuses everything is caught before it sits: :mod:`etalon.council.reliability`
scores a seat on Youden's J, which is zero for an unconditional refuser. That pairing is deliberate.
ADR 0002 is about a gate that refused every molecule in the population, and the remedy there was to
separate a magnitude band from a consequence. The remedy here is that the gate's members have to
have demonstrated they discriminate before their refusals count.

**The split is the product.** A council that agrees is mildly useful; a council that splits has
found the record a person should read, and has spent no GPU time doing it. That is
``learn/acquire.py``'s argument moved to a different scarce resource: there, a reserved share of the
compute budget goes to the molecules the surrogate is least sure about; here, operator attention
goes to the records the council cannot settle. :func:`for_a_person` returns them in the order a
reader should take them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from etalon.council.ballot import Ballot, Finding, Outcome, Vote
from etalon.council.seat import Seat, composition
from etalon.faults.attribution import Observation
from etalon.faults.taxonomy import BY_CODE

#: What a seat is asked to answer with. Two keys and nothing else: a decision and one sentence.
#: ADR 0003's reasoning applies directly -- hallucination rates in the drug-discovery benchmarks
#: were highest under extended reasoning, and the reason field here is read by people and acted on
#: by nothing.
_REQUIRED = ("vote", "reason")

_QUESTION = """\
A computational drug-discovery campaign is deciding whether to spend GPU-days simulating one
molecule. One check could not be evaluated automatically, and you are one seat on a council ruling
on it.

The cause: {code} -- {summary}
What it would mean if present: {consequence}

You can see only this evidence. You have not been shown the rest of the record, deliberately, and
the parts you cannot see are held by other seats.

{evidence}

{brief}

Answer "refuse" only if this evidence shows the cause is present. Answer "clear" if this evidence
shows it is absent. Answer "abstain" if this evidence does not decide it -- abstaining is the
correct answer whenever the question turns on something you were not shown, and it costs nothing.
"""


def _ask(seat: Seat, code: str, record: Mapping[str, Any]) -> Ballot:
    """Put one question to one seat, and turn anything unusable into a recorded abstention.

    A seat that cannot be reached, times out, or answers in a shape the parser refuses is recorded
    as ``ABSTAIN`` **with** ``error`` set. Both halves matter: treating an outage as agreement is
    how a council silently shrinks to the seats that happened to answer, and treating it as a
    refusal would let a network problem block a molecule.
    """

    from etalon.judgment.advisor import AdvisorError
    from etalon.judgment.proposal import Act

    fault = BY_CODE.get(code)
    if fault is None:
        raise KeyError(
            f"{code} is not in the fault taxonomy, so a council ruling on it would be ruling on "
            "something no other layer of ETALON can read back."
        )
    evidence = seat.evidence_for(record)
    question = _QUESTION.format(
        code=code,
        summary=fault.summary,
        consequence=fault.consequence.value,
        evidence=(
            json.dumps(evidence, indent=2, sort_keys=True, default=str)
            if evidence
            else "(nothing in this record falls inside your evidence scope)"
        ),
        brief=seat.brief or "",
    )
    try:
        proposal = seat.advisory.ask(Act.HYPOTHESIS, question, required=_REQUIRED)
    except AdvisorError as error:
        return Ballot(
            seat=seat.name, code=code, vote=Vote.ABSTAIN, error=str(error)[:400]
        )
    raw = str(proposal.payload.get("vote", "")).strip().lower()
    try:
        vote = Vote(raw)
    except ValueError:
        # Refused rather than mapped onto the nearest word. advisor.py's rule: reaching into a
        # malformed answer for the part that looks right is how a hallucination becomes a record.
        return Ballot(
            seat=seat.name,
            code=code,
            vote=Vote.ABSTAIN,
            error=f"answered {raw!r}, which is not one of {[v.value for v in Vote]}",
            prompt_sha256=proposal.advisor.prompt_sha256,
            response_sha256=proposal.advisor.response_sha256,
        )
    return Ballot(
        seat=seat.name,
        code=code,
        vote=vote,
        reason=str(proposal.payload.get("reason", proposal.rationale))[:400],
        prompt_sha256=proposal.advisor.prompt_sha256,
        response_sha256=proposal.advisor.response_sha256,
        attempts=int(proposal.payload.get("_attempts", 1)),
    )


def adjudicate(
    record: Mapping[str, Any],
    observations: Sequence[Observation],
    seats: Sequence[Seat],
    *,
    reliability: Any = None,
    parent_id: str = "",
) -> tuple[Finding, ...]:
    """Rule on every check the deterministic layer could not evaluate.

    Args:
        record: The evidence, keyed by :class:`~etalon.council.seat.Evidence` values. Each seat is
            handed only its own slice; the rest never enters its prompt.
        observations: What ``faults.preflight`` or ``faults.postflight`` produced. Only the
            unevaluable ones are in jurisdiction, and the rest are not passed to any seat.
        reliability: The :class:`~etalon.council.reliability.Reliability` in force. When it is
            absent or not qualified, **no seat is asked anything** -- the council returns
            ``COUNCIL_NOT_QUALIFIED`` for each code and the campaign proceeds on the deterministic
            layer alone, with the checks as unevaluable as they already were. Failing closed here
            costs nothing that was not already missing.
    """

    if isinstance(observations, Observation):
        # A single Observation is iterable-looking enough to get this far and not iterable, and
        # the bare TypeError names the line rather than the mistake. Caught here because a missing
        # trailing comma is how a caller writes it, and because the failure would otherwise land
        # only when a council was about to sit.
        raise TypeError(
            "observations must be a sequence of Observation, and one was passed on its own. If "
            "this came from a literal, it is a missing trailing comma: (obs,) not (obs)."
        )
    in_jurisdiction = [entry.code for entry in observations if not entry.evaluable]
    if not in_jurisdiction:
        return ()

    qualified = bool(getattr(reliability, "qualified", False))
    if not qualified:
        why = (
            "no reliability measurement was supplied"
            if reliability is None
            else "; ".join(getattr(reliability, "refusals", ()) or ("refused",))
        )
        return tuple(
            Finding(
                code=code,
                parent_id=parent_id,
                outcome=Outcome.COUNCIL_NOT_QUALIFIED,
                note=(
                    f"The council did not sit: {why}. The check stays exactly as unevaluable as "
                    "it was, which is the honest state and the one the campaign was already in. "
                    "Measure the council with etalon.council.reliability.rule against labelled "
                    "adjudications before convening it -- a council whose seats have not been "
                    "shown to discriminate is a ranking tier with no measured correlation, and "
                    "economics/allocate.py refuses one of those too."
                ),
                qualification=(
                    {} if reliability is None else reliability.as_dict()
                ),
            )
            for code in in_jurisdiction
        )

    seated = tuple(seats)
    qualification = reliability.as_dict()
    findings: list[Finding] = []
    for code in in_jurisdiction:
        ballots = tuple(_ask(seat, code, record) for seat in seated)
        refusing = [b for b in ballots if b.vote is Vote.REFUSE]
        clearing = [b for b in ballots if b.vote is Vote.CLEAR]
        errored = [b for b in ballots if b.error]

        if refusing and clearing:
            outcome = Outcome.SPLIT
            note = (
                f"{len(refusing)} seat(s) refuse and {len(clearing)} clear. Routed to a person "
                "rather than resolved by majority: a quorum that outvotes a dissent records one "
                "number where there were two readings, and the dissenting sentence is the thing "
                "worth reading. The molecule is neither spent on nor discarded until somebody "
                "rules."
            )
        elif refusing:
            outcome = Outcome.REFUSED
            note = (
                f"{len(refusing)} seat(s) refuse and none clears. The check moves from "
                "unevaluable to fired, which is the only direction a council may move one."
            )
        elif clearing:
            outcome = Outcome.CLEARED_BUT_STILL_UNCHECKED
            note = (
                f"{len(clearing)} seat(s) see no problem and none refuses. Recorded, and the "
                "check stays unevaluable: an advisor saying it looks fine is not the check having "
                "run, and unchecked() will still report it. Nothing was cleared, and nothing was "
                "spent finding that out."
            )
        else:
            outcome = Outcome.UNDECIDED
            note = (
                "Every seat abstained. The evidence each holds does not decide this cause, which "
                "is a fact about the record rather than about the council -- and it is the "
                "correct answer often enough that a council which never returns it should be "
                "suspected of answering the question it was able to answer instead."
            )
        if errored:
            note += (
                f" {len(errored)} seat(s) could not be reached or answered in a shape the parser "
                "refused, and are counted as abstaining rather than as agreeing; a council that "
                "reads an outage as assent shrinks without anybody noticing."
            )
        findings.append(
            Finding(
                code=code,
                parent_id=parent_id,
                outcome=outcome,
                ballots=ballots,
                note=note,
                qualification=qualification,
            )
        )
    return tuple(findings)


def as_observations(findings: Sequence[Finding]) -> tuple[Observation, ...]:
    """Turn findings into observations the rest of ETALON already knows how to read.

    This is the only door between the council and the deterministic layers, and it is one-way by
    construction: ``evaluable=True`` is emitted for :attr:`~etalon.council.ballot.Outcome.REFUSED`
    and for nothing else. Every other outcome produces ``evaluable=False``, so ``preflight.blocking``
    ignores it and ``preflight.unchecked`` still reports the cause as never having been checked.

    A split is *not* a refusal here. It is unevaluable with a detail naming the dissent, because a
    split says a person has to look, and encoding "a person has to look" as "the molecule is bad"
    would spend the operator's authority on the council's behalf.
    """

    produced: list[Observation] = []
    for finding in findings:
        if finding.outcome is Outcome.REFUSED:
            reasons = "; ".join(
                f"{b.seat}: {b.reason}" for b in finding.ballots if b.vote is Vote.REFUSE
            )
            produced.append(
                Observation(
                    code=finding.code,
                    fired=True,
                    evaluable=True,
                    detail=f"council refused, no dissent -- {reasons}"[:600],
                )
            )
            continue
        detail = {
            Outcome.SPLIT: "council split; a person must rule before this molecule is spent on",
            Outcome.UNDECIDED: "council sat and every seat abstained",
            Outcome.CLEARED_BUT_STILL_UNCHECKED: (
                "council saw no problem, which is not the check having run"
            ),
            Outcome.COUNCIL_NOT_QUALIFIED: "council not qualified to sit",
        }[finding.outcome]
        produced.append(
            Observation(code=finding.code, fired=False, evaluable=False, detail=detail)
        )
    return tuple(produced)


def for_a_person(findings: Sequence[Finding]) -> tuple[Finding, ...]:
    """The splits, which are the records worth an operator's time.

    Ordered by how close the split was -- an even one first -- because that is where the council
    carries least information and a reader adds most. This is ``learn/acquire.py``'s argument with
    operator attention in place of GPU-hours: a fixed, scarce budget spent where the estimate is
    least determined rather than where it is most alarming.
    """

    splits = [f for f in findings if f.needs_a_person]
    def imbalance(finding: Finding) -> tuple[int, str]:
        counted = finding.votes()
        return abs(counted[Vote.REFUSE.value] - counted[Vote.CLEAR.value]), finding.code
    return tuple(sorted(splits, key=imbalance))


def report(findings: Sequence[Finding], seats: Sequence[Seat]) -> dict[str, Any]:
    """Everything one sitting produced, in the shape the ledger stores."""

    counted: dict[str, int] = {outcome.value: 0 for outcome in Outcome}
    for finding in findings:
        counted[finding.outcome.value] += 1
    return {
        "composition": composition(seats),
        "outcomes": counted,
        "findings": [finding.as_dict() for finding in findings],
        "for_a_person": [finding.as_dict() for finding in for_a_person(findings)],
        "authority": (
            "A council may move a check from unevaluable to fired and may not move one to "
            "cleared. The worst a wrong council does is refuse a molecule that was fine, which "
            "costs compute and is visible in the admission rate; it cannot produce a clean record."
        ),
    }


__all__ = ["adjudicate", "as_observations", "for_a_person", "report"]
