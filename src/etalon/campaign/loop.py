"""One round of a self-tuning campaign, with the expensive stage kept at arm's length.

The loop is six steps and the order is the argument.

**Screen.** MolCascade compiles to a revision id before anything runs, and the round
records it. Two rounds with different revisions screened different funnels, and a change
in enrichment between them is not attributable to a learned update.

**Hand off.** The ``md_system_input/v1`` rows say what a force field would receive.

**Refuse.** Preflight rules on those rows before a GPU-second is spent. A molecule whose
coordinates are a drawing is refused here for free, and the campaign records the refusal
rather than quietly shortening its own list.

**Measure.** The expensive stage runs -- and it arrives as a callable rather than as an
import. That is not indecision about PRISM. An expensive stage is the one part of this
loop that cannot be exercised without a GPU, an AmberTools install and hours of wall
clock, and a design in which the only way to test the orchestration is to run a real
simulation is a design whose orchestration does not get tested. Passing it in also means
a campaign can put MM-PBSA, a free energy perturbation or an external service behind the
same seam without the loop learning anything about which.

**Admit.** Only measurements that can be said to be about the molecule they are filed
under may update the screen. This is the step the literature's loops do not have.

**Decide.** A proposed parameter change is accepted only if it beats the panel's own
resolution on a scaffold-grouped holdout. Refusals are recorded, because a loop that
silently declines to learn is indistinguishable from one with nothing to learn.

Everything lands in the ledger as one append-only line. The loop holds no state of its
own between rounds: the ledger is the authority and the configuration is derived from it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from etalon.authority.grant import SpendAuthorization, authorize
from etalon.boundary import toolchain as toolchain_module
from etalon.boundary.infra import Infra, describe
from etalon.boundary.screen import Screen, ScreenResult
from etalon.campaign.ledger import Ledger
from etalon.faults.attribution import Observation
from etalon.faults.preflight import check_population, check_record, unchecked
from etalon.judgment.waiver import WaiverSet
from etalon.learn.admissible import AdmissionReport, Measurement, admissible
from etalon.learn.calibrate import Decision

#: What the expensive stage must look like from here: given the handoff rows it is allowed
#: to spend on, and the screen's own number for each, return one measurement each.
#:
#: The second argument was missing from the first version of this protocol and the omission
#: was not cosmetic. ``md_system_input/v1`` carries coordinates, hydrogens, charge,
#: stereochemistry and the receptor -- everything needed to decide whether to spend -- and
#: no score, because a score is not a property of a system. So a stage handed only the rows
#: could not fill in ``Measurement.cheap_value``, every measurement came back with
#: ``NO_CHEAP_VALUE``, and the calibration had nothing to calibrate. The screen's numbers
#: have to be carried across the seam explicitly.
#: The third argument is the one added after ADR 0006's class of bug was traced past its instance.
#: ``authority.authorize`` runs the preflight itself and mints a token per surviving row; a stage
#: receives those tokens and calls ``authority.require`` before it spends. The check is therefore an
#: argument rather than a recommendation, and the path that builds a system nobody ruled on does not
#: exist -- it is not guarded, it is unreachable, because the function that spends cannot be called
#: without the object that checking produces.
#:
#: Passing it positionally rather than as a keyword is deliberate: a stage that ignored the tokens
#: would still typecheck, and nothing can prevent that, but a stage that never received them cannot
#: pretend it did. That is the difference between a hole and a decision somebody made in the open.
ExpensiveStage = Callable[
    [
        Sequence[dict[str, Any]],
        Mapping[str, float],
        Mapping[str, "SpendAuthorization"],
    ],
    Sequence[Measurement],
]

#: Given the admitted measurements, propose a parameter change and score it. Returning
#: ``None`` means the round had nothing to propose, which is a result.
Proposer = Callable[[Sequence[Measurement]], tuple[dict[str, Any], Decision] | None]

#: Given ``{parent_id: smiles}`` for the molecules this round screened, choose the next batch to
#: spend on. Returns a :class:`~etalon.judgment.proposal.Proposal` carrying ``Act.SPEND``, or
#: ``None``.
#:
#: Separate from :data:`Proposer` because they are different acts with different autonomy. A
#: parameter change is GATED -- it is applied only if the panel can resolve it -- while choosing
#: what to measure next is ACTED_ON, because being wrong costs compute and the next round shows it.
#: Until this hook existed, ``Act.SPEND`` was the only row in the autonomy table with no
#: implementation: :meth:`etalon.campaign.propose.Acquisition.propose` takes candidates and returns
#: a proposal, the ``Proposer`` hook takes measurements and returns a change with a decision, and
#: the two signatures cannot be connected. Nothing noticed, because nothing composed the loop.
Acquirer = Callable[[Mapping[str, str]], Any | None]


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    """Everything one round did, in the shape the ledger stores."""

    round_id: str
    revision_id: str
    run_id: str
    screened: int
    handed_off: int
    refused_before_spending: tuple[str, ...]
    measured: int
    admission: AdmissionReport | None
    change: dict[str, Any]
    decision: Decision | None
    #: One line per authorised and refused molecule, so a reader of the ledger can see what the
    #: gate permitted without re-deriving it from the handoff rows -- which, being a
    #: content digest, is the one thing that cannot be reconstructed later.
    authorization: dict[str, Any] | None = None
    #: What the acquisition chose to measure next, as the proposal's own record. ``None`` when no
    #: acquirer was supplied, which is different from an acquirer that chose nothing.
    next_batch: dict[str, Any] | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "run_id": self.run_id,
            "screened": self.screened,
            "handed_off": self.handed_off,
            "refused_before_spending": list(self.refused_before_spending),
            "measured": self.measured,
            "admission": None if self.admission is None else self.admission.as_dict(),
            "change": dict(self.change),
            "decision": None if self.decision is None else self.decision.as_dict(),
            "authorization": self.authorization,
            "next_batch": self.next_batch,
            "notes": list(self.notes),
        }


class Campaign:
    """A screen that is allowed to learn from simulation, under stated conditions."""

    def __init__(
        self,
        workspace: str | Path,
        ledger: str | Path,
        *,
        waivers: WaiverSet | None = None,
        infra: Infra | None = None,
    ) -> None:
        self.screen = Screen(workspace, infra=infra)
        self.ledger = Ledger(ledger)
        self.waivers = waivers or WaiverSet()

    def round(
        self,
        round_id: str,
        config_path: str | Path,
        library: str | Path,
        *,
        measure: ExpensiveStage,
        propose: Proposer | None = None,
        acquire: Acquirer | None = None,
        target: dict[str, Any] | None = None,
        expected_ids: Sequence[str] | None = None,
        workers: int | None = None,
        receptor_path: Path | None = None,
        calibrate_against: str | None = None,
    ) -> RoundOutcome:
        """Run one round and append it to the ledger.

        Args:
            expected_ids: The ``parent_id`` values the caller believes are being handed
                over. Supplying them turns ``F_HANDOFF_ABSENT`` from unevaluable into a
                real check. Note that MolCascade's parent ids are content digests, not
                library names, so a caller has to read them from the run rather than
                assume the names it supplied survive -- which is why this is a parameter
                and not something inferred from the library file.
            calibrate_against: The ``derived_metric/v1`` metric id the screen's number
                should be read from, when the round has no docking score. Required in that
                case: a derived metric is whatever a criterion computed, and a demerit total
                is not on the same axis as a binding free energy.
        """

        notes: list[str] = []
        toolchain_active = toolchain_module.active()
        if not toolchain_active:
            notes.append(
                "The seed shim is not on the path, so ion placement and velocity "
                "generation will draw from the clock. Results stay usable and no claim "
                "that they are reproducible does."
            )

        durability = self.screen.durability()
        if not durability["durable"]:
            notes.append(str(durability["why_it_matters"]))

        plan = self.screen.plan(config_path, library, target=target)
        result: ScreenResult = self.screen.run(plan, workers=workers)
        rows = self.screen.handoff(result)

        # -- refuse before spending ---------------------------------------
        # The ruling and the permission are now one act. `authorize` runs exactly the check this
        # block used to run inline and returns a token per surviving row; the expensive stage takes
        # those tokens and cannot spend without them. Before, this loop computed `allowed` and
        # handed it over, and any stage that ignored the list -- or any caller who assembled one
        # itself -- spent unchecked. ADR 0006 is about a guard on a path nobody takes; this is the
        # same guard moved onto the only path there is.
        observations: dict[str, tuple[Observation, ...]] = {
            str(row["parent_id"]): check_record(
                row, receptor_path=receptor_path, toolchain_active=toolchain_active
            )
            for row in rows
        }
        granted = authorize(
            rows,
            receptor_path=receptor_path,
            toolchain_active=toolchain_active,
            waivers=self.waivers,
        )
        refused: list[str] = sorted(granted.refused)
        allowed: list[dict[str, Any]] = [
            row for row in rows if str(row.get("parent_id", "")) in granted.grants
        ]
        spent_under_waiver: dict[str, list[str]] = {
            identifier: list(token.proceeded_under_waiver)
            for identifier, token in granted.grants.items()
            if token.proceeded_under_waiver
        }

        if expected_ids is not None:
            population = check_population(
                expected_ids, {str(row["parent_id"]): row for row in rows}
            )
            if population[0].fired:
                notes.append(f"Population check: {population[0].detail}")

        if spent_under_waiver:
            codes = sorted({c for codes in spent_under_waiver.values() for c in codes})
            notes.append(
                f"{len(spent_under_waiver)} record(s) proceeded under waiver for "
                + ", ".join(codes)
                + ". The cause fired and was accepted on the record, which is not the "
                "same as the check having passed, and every number this round produces "
                "inherits it."
            )
        if refused:
            notes.append(
                f"{len(refused)} of {len(rows)} handoff records were refused before any "
                "simulation was started. Each would have produced a number about "
                "something other than the molecule it is filed under."
            )
        never_checked = {
            entry.code for seen in observations.values() for entry in unchecked(seen)
        }
        if never_checked:
            notes.append(
                "Not every check could be evaluated ("
                + ", ".join(sorted(never_checked))
                + "), so what passed is clean as far as anyone looked."
            )

        # -- measure -------------------------------------------------------
        try:
            cheap = self.screen.metrics(result, metric_id=calibrate_against)
        # Not named `refused`: `except ... as <name>` unbinds that name when the handler
        # ends, so reusing the name of the list of refused molecules silently deleted it
        # and the round failed three lines from the end with an UnboundLocalError.
        except (ValueError, KeyError) as no_comparator:
            # Refused rather than guessed, and a refusal is a round that measures without
            # learning -- not a round that fails. The simulations still happen and are
            # still recorded; what does not happen is an update fitted to a comparator
            # nobody chose.
            cheap = {}
            notes.append(
                f"No comparator for the screen: {no_comparator}. The round will measure "
                "and record, and the screen will learn nothing from it."
            )
        if allowed and not cheap:
            notes.append(
                "The screen produced no per-molecule number, so nothing can be calibrated "
                "against what the simulations return. The measurements stand on their own "
                "and the screen learns nothing from them -- which is the honest outcome of "
                "a round with no docking or affinity tier."
            )
        measurements = list(measure(allowed, cheap, granted.grants))
        # The checks the campaign already performed are attached here, so the
        # admissibility ruling sees them even when the expensive stage did not bother to.
        enriched = [
            Measurement(
                parent_id=m.parent_id,
                cheap_value=m.cheap_value,
                expensive_value=m.expensive_value,
                observations=m.observations or observations.get(m.parent_id, ()),
                units=m.units,
                provenance={
                    **m.provenance,
                    "revision_id": plan.revision_id,
                    "run_id": result.run_id,
                    "infra": describe(),
                },
            )
            for m in measurements
        ]
        report = admissible(enriched, self.waivers)
        notes.extend(report.notes)
        notes.extend(granted.notes)

        # -- decide --------------------------------------------------------
        change: dict[str, Any] = {}
        decision: Decision | None = None
        if propose is not None:
            teach = [m for m in enriched if m.parent_id in {r.parent_id for r in report.admitted}]
            proposed = propose(teach)
            if proposed is None:
                notes.append(
                    f"Nothing proposed from {len(teach)} admissible measurement(s). A "
                    "round that changes nothing is a result, not a failure."
                )
            else:
                change, decision = proposed
                notes.append(decision.note)
                if not decision.accepted:
                    notes.append(
                        "The proposed change is recorded and not applied, so the next "
                        "round starts from the configuration that earned its place."
                    )

        # -- choose what to measure next --------------------------------------
        next_batch: dict[str, Any] | None = None
        if acquire is not None:
            # Every molecule that reached the handoff, refused or not. A molecule this round
            # declined to spend on is still a candidate the surrogate can rank, and excluding the
            # refusals would make the next batch a function of which records happened to be
            # well-formed rather than of which molecules are worth measuring.
            candidates = {
                str(row.get("parent_id", "")): str(row.get("parent_smiles", ""))
                for row in rows
                if row.get("parent_id") and row.get("parent_smiles")
            }
            proposal = acquire(candidates)
            if proposal is None:
                notes.append(
                    f"No next batch proposed from {len(candidates)} candidate(s). A round that "
                    "chooses nothing to measure next is a result, not a failure."
                )
            else:
                next_batch = proposal.as_dict()
                notes.append(
                    f"Next batch chosen from {len(candidates)} candidate(s) and applied without "
                    "asking: being wrong about what to measure costs compute, and the round after "
                    "shows it."
                )

        outcome = RoundOutcome(
            round_id=round_id,
            revision_id=plan.revision_id,
            run_id=result.run_id,
            screened=len(result.committed),
            handed_off=len(rows),
            refused_before_spending=tuple(refused),
            measured=len(enriched),
            admission=report,
            change=change,
            decision=decision,
            authorization=granted.as_dict(),
            next_batch=next_batch,
            notes=tuple(notes),
        )
        self.ledger.append(
            "round",
            round_id,
            waivers=self.waivers.as_dict(),
            workspace=durability,
            toolchain={"active": toolchain_active},
            **outcome.as_dict(),
        )
        return outcome

    def state(self) -> dict[str, Any]:
        """The configuration the ledger says is current."""

        return self.ledger.replay()


__all__ = ["Acquirer", "Campaign", "ExpensiveStage", "Proposer", "RoundOutcome"]
