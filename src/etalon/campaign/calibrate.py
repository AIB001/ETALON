"""Prove a gate configuration is fit to apply to a million molecules, before applying it.

Every hard gate in a cascade is one assertion: *molecules like this are not worth looking at*. The
cheapest way to test an assertion like that is to count how many known binders it deletes, and a
campaign that never does it has no evidence for the only claim its shortlist rests on.

This is not hypothetical caution. Measured on SND1, with MolCascade's shipped defaults
(``Uni-Dock <= -8.5`` and ``KarmaDock >= 40``):

====================================  ========  =========  ==================
molecule                              Uni-Dock  KarmaDock  known activity
====================================  ========  =========  ==================
BindingDB compound                      -7.78      18.56   Kd 23.6 uM
BindingDB compound                      -7.76      17.26   **Kd 570 nM**
BindingDB compound                      -7.21      14.20   Kd 279 uM
imatinib (negative control)             -6.57      14.24   not an SND1 binder
C-26-A6 (co-crystal, 7KNX)              -6.05      14.89   --
C-26-A2 (co-crystal, 7KNW)              -5.90      15.17   --
aspirin (negative control)              -5.07      14.17   not an SND1 binder
caffeine (negative control)             -4.85      16.61   not an SND1 binder
====================================  ========  =========  ==================

The defaults reject **all eight**, including both co-crystal ligands. A campaign that ran them
unexamined would have produced an empty shortlist with every log line reading SUCCEEDED, and the
first screening test did exactly that. Recalibrating the two thresholds against this panel is what
made the 294-hit result possible at all.

The table carries a second finding, and it is the more consequential one. Imatinib -- which does not
bind SND1 -- outscores both co-crystal ligands. Caffeine is within 1 kcal/mol of one. **On this
pocket the docking score cannot order the known binders**, so it is admissible as a coarse filter
and inadmissible as a ranking. Nothing in MolCascade or in ETALON computed that before this module;
it fell out of a table somebody happened to read. :attr:`Calibration.separation` computes it.

Two things this module deliberately does not do.

It does not run the screen. The caller runs the panel through :class:`~etalon.boundary.screen.Screen`
and passes the result in, for the same reason :data:`~etalon.campaign.loop.ExpensiveStage` is a
callable: the part that needs a GPU should not be the part that cannot be tested.

It does not reimplement the retention measurement. MolCascade's ``measure_recall`` recovers the tier
structure from the revision's own pipeline metadata and reports which molecules each tier lost.
MolCascade measures; this module judges. The division matters because only the campaign knows which
of the lost molecules were known to bind.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from etalon.boundary.screen import Screen, ScreenResult

#: The contract every docking engine's score port carries.
DOCKING_SCORE = "docking_score/v1"

#: A score where a smaller number is the stronger binder -- every Vina-family engine.
LOWER_STRONGER = "LOWER_STRONGER"
#: A score where a larger number is the stronger binder -- KarmaDock's MDN, gnina's CNN scales.
HIGHER_STRONGER = "HIGHER_STRONGER"
#: One engine's rows disagreed about which way its own score runs. Fails closed: a separation
#: computed over rows that do not share a convention is not a measurement of anything.
MIXED = "MIXED"

#: Fallback when a row omits ``direction``. The contract declares that field non-nullable, so a row
#: without it is already irregular; deriving from ``score_kind`` is strictly better than assuming,
#: and assuming is what this module did until an ALK2 panel made the cost visible.
_DIRECTION_BY_KIND = {
    "VINA_KCAL_MOL": LOWER_STRONGER,
    "VINARDO_KCAL_MOL": LOWER_STRONGER,
    "AD4_KCAL_MOL": LOWER_STRONGER,
    "CNN_SCORE": HIGHER_STRONGER,
    "CNN_AFFINITY": HIGHER_STRONGER,
    "KARMADOCK_MDN": HIGHER_STRONGER,
}


#: The other spelling of the same two conventions, and the reason it has to be here.
#:
#: :mod:`etalon.active.schema` validates an endpoint's ``direction`` against
#: ``{"minimize", "maximize"}`` and has no ``MIXED`` at all, while this module declares
#: ``LOWER_STRONGER`` / ``HIGHER_STRONGER`` / ``MIXED``. Two vocabularies for one concept, in one
#: repository, with no translation between them -- so a row that reached :func:`_direction` from the
#: active side declared its convention in words this module did not recognise, and fell through to
#: the ``score_kind`` table as though it had declared nothing.
_DIRECTION_SYNONYMS = {
    "MINIMIZE": LOWER_STRONGER,
    "MAXIMIZE": HIGHER_STRONGER,
}


def _direction(row: Mapping[str, Any]) -> str:
    """The convention one row's score runs under, or ``MIXED`` when it cannot be established.

    Three cases, and the middle two are corrections.

    A row that **declares one of this module's two conventions** is taken at its word.

    A row that **declares the active layer's spelling** is translated. ``minimize`` and
    ``maximize`` are the same two conventions under different names, so reading them is a
    translation rather than a guess -- and it is the translation whose absence caused the defect
    below.

    A row that **declares something else** is ``MIXED``. It used to fall through to the
    ``score_kind`` table and then to the ``LOWER_STRONGER`` default, which is the fail-open
    direction. Measured on the 15 KarmaDock MDN rows of the live ``panel_v7`` run, which declare
    ``HIGHER_STRONGER``: relabelled ``maximize`` with the score kind left alone the answer is right
    by luck, because the kind table happens to know ``KARMADOCK_MDN``; relabelled ``maximize`` with
    any score kind the table does not list, ``best_inactive`` moves from **67.329 to 17.285** --
    the two opposite ends of the same panel, the best-scoring molecule swapped for the worst. The
    same fallthrough also discarded an explicitly declared ``MIXED``, which is the one value whose
    entire purpose is to fail closed, and read it as ``LOWER_STRONGER``.

    Only a row that **omits** ``direction`` reaches :data:`_DIRECTION_BY_KIND`. That path is
    deliberate and documented there: the contract declares the field non-nullable, so an absent one
    is already irregular, and deriving from the score kind beats assuming. Declaring a direction
    nobody recognises is a different thing from declaring none, and it is now treated as one.
    """

    declared = str(row.get("direction") or "").strip().upper()
    if declared in (LOWER_STRONGER, HIGHER_STRONGER):
        return declared
    if declared in _DIRECTION_SYNONYMS:
        return _DIRECTION_SYNONYMS[declared]
    if declared:
        return MIXED
    return _DIRECTION_BY_KIND.get(str(row.get("score_kind") or "").strip().upper(), LOWER_STRONGER)


def _finite(value: Any) -> float | None:
    """A score as a float, or ``None`` for anything that cannot be ordered.

    Not a type check with extra steps. A NaN is an instance of ``float``, fails every comparison,
    and corrupts the two operations every verdict in this module is built from: ``min(nan, inf)``
    returns ``nan``, so a molecule's best pose becomes NaN, and ``sorted`` over a list holding one
    is insertion-order dependent, so the extremes become a property of whichever row the parquet
    reader happened to yield first.

    Measured on four molecules whose every score was NaN: ``best_active`` and ``best_inactive``
    both came back NaN, both of :class:`Separation`'s counters came back 0, and ``separates``
    reported ``True`` -- a ranking claim from a score that ordered nothing, reaching the operator
    as "Rankable engines: ['unidock']". With a single NaN among finite inactives the verdict flipped
    with the row order: the NaN row first gave ``separates True``, the finite row first gave
    ``False``, from the same four facts.

    An infinity is refused for the same reason and one more: ``float("inf")`` is this module's own
    "no pose for this molecule yet" sentinel in ``min(canonical, kept.get(parent, inf))``, so a real
    ``+inf`` score and an unmeasured molecule were the same value.

    A missing or non-numeric score answers ``None`` too. "No number came back" and "a number that
    cannot be ordered" are the same thing to everything downstream, and :func:`calibrate` reports
    both with one note rather than pretending a report can tell them apart.
    """

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None

#: Tiers whose criteria judge whether a *pose* is real rather than whether a *molecule* is good.
#: A known active failing one of these has not been shown to be a poor binder -- it has been shown
#: that the docking placed it badly, which is a fact about the run. They are therefore not droppable
#: when they refuse: removing a pose-quality check to pass a calibration keeps the molecule and
#: throws away the evidence that its coordinates are untrustworthy, and every number computed from
#: those coordinates downstream inherits that.
#:
#: Measured on one KarmaDock shard: of 278 poses that all passed the score gate, 22.7% passed the
#: minimum-distance-to-protein check, and the score could not see the difference -- its correlation
#: with the closest protein contact is +0.014. The check is the only thing between a shortlist and
#: ligands packed into the receptor.
STRUCTURAL_TIERS = frozenset({"t9_redock", "t10_docking_metrics"})

#: Fraction of the panel's known actives that must survive the funnel for the configuration to be
#: admissible. One, and the default is not a round number chosen for neatness.
#:
#: The asymmetry is ``learn/admissible.py``'s, applied to thresholds instead of measurements. A gate
#: tuned to keep every known active may pass some molecules that do not bind, and the cost is
#: screening time on a population the next tier will reject. A gate that deletes one known active
#: deletes an unknown number of unknown actives that resemble it, silently, for every molecule the
#: campaign will ever screen -- and no later measurement can recover them, because they were never
#: docked. The first error is visible in the admission rate; the second is invisible by construction.
REQUIRED_RECALL = 1.0


@dataclass(frozen=True, slots=True)
class PanelMember:
    """One molecule in the calibration panel, and what is known about it.

    ``known_active`` is the operator's assertion, not a measurement this module makes. It carries
    ``evidence`` alongside precisely so that a reader a year later can see what the assertion rested
    on -- a co-crystal structure and a 279 uM binding constant are both "active" and are not the same
    claim.
    """

    parent_id: str
    known_active: bool
    evidence: str = ""

    def __post_init__(self) -> None:
        if not str(self.parent_id).strip():
            raise ValueError("a panel member needs a parent_id matching the library's id column")
        if type(self.known_active) is not bool:
            raise ValueError(
                f"panel member {self.parent_id!r}: known_active must be an explicit boolean. "
                "A panel drawn only from binders cannot tell a discriminating gate from one that "
                "keeps everything, so the negatives have to be declared rather than implied."
            )


@dataclass(frozen=True, slots=True)
class TierVerdict:
    """What one tier did to the panel, with the actives it removed named."""

    tier_id: str
    title: str
    entering: int | None
    surviving: int | None
    lost_actives: tuple[str, ...]
    lost_inactives: tuple[str, ...]
    unavailable: str | None = None

    @property
    def deletes_actives(self) -> bool:
        return bool(self.lost_actives)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier_id": self.tier_id,
            "title": self.title,
            "entering": self.entering,
            "surviving": self.surviving,
            "lost_actives": list(self.lost_actives),
            "lost_inactives": list(self.lost_inactives),
            "unavailable": self.unavailable,
            "deletes_actives": self.deletes_actives,
        }


@dataclass(frozen=True, slots=True)
class Separation:
    """Whether one engine's score can tell the panel's actives from its inactives.

    Deliberately not an AUC. A calibration panel is eight to fifty molecules, and an AUC on eight
    points has a standard error wider than the difference anyone would act on -- the same argument
    ``campaign/pipeline.py`` makes with ``resolvable_spearman``. So this reports counts and extremes,
    which a panel that size can support, and one boolean that a reader cannot misread:
    :attr:`separates` is true only when every active outscores every inactive.
    """

    engine_id: str
    #: ``LOWER_STRONGER``, ``HIGHER_STRONGER``, or ``MIXED`` when one engine's rows disagree.
    #: Every extreme reported below is in the engine's own units and is chosen under *this*
    #: convention, so a reader who ignores this field can still read ``best_active`` correctly.
    direction: str
    actives: int
    inactives: int
    best_active: float | None
    median_active: float | None
    worst_active: float | None
    best_inactive: float | None
    #: Inactives scoring at least as well as the *best* active. Says whether a top-N cut would be
    #: contaminated, which is a weaker question than whether the score orders the panel.
    #:
    #: ``None`` when :attr:`direction` is ``MIXED``. The values this is counted over are then a
    #: mixture of two conventions, and a count across a mixture is not a measurement of anything.
    inactives_above_best_active: int | None
    #: Actives **at or below** the best inactive -- the tie counts. This is the number
    #: :attr:`separates` turns on. On the SND1 panel it is 2 of 5: imatinib at -6.57 beats both
    #: co-crystal ligands, -6.05 and -5.90.
    #:
    #: The tie is counted because it used to not be, while the sibling count above has always been
    #: tie-inclusive. Four molecules all scoring exactly -8.0 produced
    #: ``inactives_above_best_active: 2`` and ``actives_below_best_inactive: 0`` in the same row --
    #: so a score that is a constant reported that it separates the panel.
    #:
    #: ``None`` under ``MIXED``, for the reason above.
    actives_below_best_inactive: int | None

    @property
    def separates(self) -> bool | None:
        """Whether every active outscores every inactive.

        ``None`` when either class is empty -- unevaluable, which is not the same as false.

        The predicate is ``actives_below_best_inactive == 0``, and the first version of this property
        used ``inactives_above_best_active == 0`` instead. The two are not equivalent and the
        difference is the whole finding: on the SND1 panel no inactive beats the *best* active, so the
        weaker test reported that the score separates -- while imatinib was outscoring two of the five
        known binders. A campaign reading that boolean would have treated the docking score as a
        potency ranking on a target where it is not one.

        "Outscores" includes the tie, and that is the second correction. An active scoring exactly
        what the best inactive scores has not been ordered ahead of it, and a strict ``>`` said it
        had: four molecules all scoring -8.0 came back ``separates True``. A constant is the
        degenerate case of a score that ranks nothing, so it is the one case the predicate must not
        answer yes to.
        """

        if not self.actives or not self.inactives:
            return None
        if self.direction == MIXED:
            return None
        if self.actives_below_best_inactive is None:
            return None
        return self.actives_below_best_inactive == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "direction": self.direction,
            "actives": self.actives,
            "inactives": self.inactives,
            "best_active": self.best_active,
            "median_active": self.median_active,
            "worst_active": self.worst_active,
            "best_inactive": self.best_inactive,
            "inactives_above_best_active": self.inactives_above_best_active,
            "actives_below_best_inactive": self.actives_below_best_inactive,
            "separates": self.separates,
        }


@dataclass(frozen=True, slots=True)
class Calibration:
    """A gate configuration, measured against a named panel and bound to a revision.

    :attr:`revision_id` is carried because that is what makes the measurement transferable. A
    calibration is a statement about one compiled funnel, and a configuration edited after the panel
    ran is a different funnel whose behaviour on the panel nobody observed.
    """

    revision_id: str
    run_id: str
    outcome: str
    panel_size: int
    actives: int
    registered: int
    tiers: tuple[TierVerdict, ...]
    finalize: tuple[TierVerdict, ...]
    separation: tuple[Separation, ...]
    required_recall: float = REQUIRED_RECALL
    notes: tuple[str, ...] = ()
    #: Scores per molecule per engine, for the panel only. Small by construction and worth keeping:
    #: this is the table that makes an inadmissible configuration arguable rather than merely refused.
    scores: tuple[dict[str, Any], ...] = field(default=(), repr=False)

    @property
    def all_tiers(self) -> tuple[TierVerdict, ...]:
        """Tiers and post-tier stages together.

        The shortlist selector runs *after* the last tier and caps molecules per Murcko scaffold. A
        calibration panel is a congeneric series, so on a real panel that cap can remove more
        molecules than every gate combined while each tier truthfully reports keeping everything.
        Judging tiers alone would miss it.
        """

        return self.tiers + self.finalize

    @property
    def deleted_actives(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for tier in self.all_tiers:
            for name in tier.lost_actives:
                seen.setdefault(name, None)
        return tuple(seen)

    @property
    def recall(self) -> float | None:
        """Surviving known actives over known actives, or ``None`` when unreadable.

        ``None`` rather than a number whenever the panel's passage through the gates was not
        observed, because a retention product computed over what was not read is optimistic by
        construction -- the same reason MolCascade's own report nulls its end-to-end figure instead
        of guessing.

        Three ways it is unreadable, and the second was found by pointing this at a real run rather
        than by a test:

        ``no declared actives``
            Nothing about the gates was tested.
        ``no tiers at all``
            The retention read failed outright, so there is no evidence any tier was passed. Checking
            only for a tier *marked* unavailable is not enough: when the read raises, there are no
            tiers to be marked, ``any()`` over the empty list is false, and this returned 1.0 for a
            configuration nobody measured -- which then authorised. The common cause is a panel
            screened without an id column, so members are not traceable by name.
        ``a tier marked unavailable``
            Part of the funnel could not be read.
        ``nothing registered``
            Every tier read cleanly and the run contained no molecules. MolCascade's own
            ``end_to_end`` is ``None`` there -- ``TierRecall.retention`` nulls on ``entering == 0``
            -- and this returned 1.0, reporting full recall where the measurement it quotes reports
            "unknown". Reproduced on a tier reading ``entering 0, surviving 0, lost []``, which is
            what ``measure_recall`` produces when the parent population is empty: its
            ``registered and not names`` refusal cannot fire on an empty set.

        Whether the panel's *own* molecules were among the registered ones is a separate question
        and is answered in :func:`calibrate`, which is the only place that has the names. A declared
        id matching no library id can never appear in any tier's ``lost`` list, so it would count
        here as surviving every gate.
        """

        if not self.actives:
            return None
        if not self.registered:
            return None
        if not self.all_tiers:
            return None
        if any(tier.unavailable for tier in self.all_tiers):
            return None
        return (self.actives - len(self.deleted_actives)) / self.actives

    @property
    def admissible(self) -> bool:
        """Whether this configuration may be applied at scale.

        Fails closed on an unreadable panel. A campaign that cannot say what its gates did to known
        binders has not measured its gates, and "we could not tell" must not read the same as "they
        kept everything".
        """

        measured = self.recall
        return measured is not None and measured >= self.required_recall

    @property
    def rankable(self) -> tuple[str, ...]:
        """Engines whose score ordered the panel correctly, and may therefore be read as a ranking.

        Empty is the common answer on a protein-protein interface and is not a defect in the engine.
        It means the number is a filter, and a campaign reporting it as potency is over-reading it.
        """

        return tuple(row.engine_id for row in self.separation if row.separates is True)

    def refusals(self) -> tuple[str, ...]:
        """Why this configuration is inadmissible, in the words a report should use."""

        reasons: list[str] = []
        if not self.actives:
            reasons.append(
                "the panel declares no known actives, so nothing about the gates was tested"
            )
        if self.all_tiers and not self.registered and not any(
            tier.unavailable for tier in self.all_tiers
        ):
            reasons.append(
                "every tier read cleanly and the run registered no molecules at all, so the panel "
                "never entered the funnel. MolCascade's own end-to-end retention is null in this "
                "case; the 1.0 this used to report was a retention product over an empty panel"
            )
        if not self.all_tiers:
            reasons.append(
                "no tier retention could be read at all, so the panel's passage through the gates "
                "was never observed. The usual cause is a panel screened without an id column: "
                "MolCascade refuses to measure recall on a run that recorded no molecule names. "
                "Re-screen the panel with --id-column."
            )
        for tier in self.all_tiers:
            if tier.unavailable:
                reasons.append(f"tier {tier.tier_id} could not be read: {tier.unavailable}")
            if tier.deletes_actives:
                if tier.tier_id in STRUCTURAL_TIERS:
                    reasons.append(
                        f"tier {tier.tier_id} deleted known actives {list(tier.lost_actives)}. "
                        "This tier judges the pose, not the molecule, so do NOT drop it: a known "
                        "binder failing it says the docking placed that molecule badly, and every "
                        "number computed from those coordinates inherits the error. Look at the "
                        "pose, re-dock it, or accept the loss and record it -- but keep the check."
                    )
                else:
                    reasons.append(
                        f"tier {tier.tier_id} deleted known actives "
                        f"{list(tier.lost_actives)} -- widen it or drop the tier"
                    )
        measured = self.recall
        if measured is not None and measured < self.required_recall and not reasons:
            reasons.append(
                f"panel recall {measured:.3f} is below the required {self.required_recall:.3f}"
            )
        return tuple(reasons)

    def as_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "run_id": self.run_id,
            "outcome": self.outcome,
            "panel_size": self.panel_size,
            "actives": self.actives,
            "registered": self.registered,
            "recall": self.recall,
            "required_recall": self.required_recall,
            "admissible": self.admissible,
            "deleted_actives": list(self.deleted_actives),
            "rankable_engines": list(self.rankable),
            "refusals": list(self.refusals()),
            "tiers": [tier.as_dict() for tier in self.tiers],
            "finalize": [tier.as_dict() for tier in self.finalize],
            "separation": [row.as_dict() for row in self.separation],
            "notes": list(self.notes),
        }


#: Default share of a screened population a docking tier should keep. One percent of a 540,000
#: molecule sweep is 5,400 molecules -- a number a campaign can do something with, and small enough
#: that the next tier's cost is bounded. There is nothing special about 1%; what matters is that the
#: gate is expressed as a share of the population rather than as a score, for the reason
#: :func:`enrichment` exists.
DEFAULT_KEEP = 0.01


@dataclass(frozen=True, slots=True)
class Enrichment:
    """Where a panel's known actives sit in the distribution of molecules actually screened.

    This is the measurement a threshold should be set from, and the one ETALON did not have.

    A docking score is not a binding free energy -- ``docking_score/v1`` says so in its own
    ``not_affinity`` invariant -- so a threshold justified by "the weakest known binder scored
    -8.497, keep everything at -8.0 or better" is an argument that only works if the two quantities
    share a scale. They do not. Measured, the same number means opposite things on two targets: on
    SND1's shallow PPI groove ``-8.0`` sits *above* every known binder (best -7.78) and kept 2 of
    5,822 docked molecules; on ALK2's kinase ATP site it sits *below* every known active (weakest
    -8.497) and kept 69%. One number, a 2,000-fold difference in what it does, and nothing in the
    threshold says which case you are in.

    A percentile does not have that defect. "Keep the best 1%" means the same thing on both targets,
    bounds the next tier's cost, and makes the shortlist's size a decision rather than an accident.

    What it costs is visible here: :attr:`percentiles` says where each known active falls, so
    :meth:`recall_at` says what a given cut would delete. On ALK2 that is the uncomfortable finding
    the absolute threshold hid -- the eight known actives spread from the 1.7th percentile to the
    81st, so no cut small enough to be a shortlist retains them, which is the same thing
    ``rankable_engines`` being empty was already saying.
    """

    engine_id: str
    direction: str
    population: int
    #: Each known active's percentile in the population, best first. 0.01 means "only 1% of the
    #: screened molecules score at least this well".
    percentiles: tuple[float, ...]
    #: The panel's inactives, for contrast. A score with no enrichment puts both classes everywhere.
    inactive_percentiles: tuple[float, ...] = ()
    #: What one unit of :attr:`population` is. Always ``"molecules"`` from :func:`enrichment`, which
    #: collapses the population's poses the same way it collapses the panel's. Recorded rather than
    #: assumed because the percentile is a share of this and nothing else said what it counted, and
    #: ``docking_score/v1`` is keyed on ``pose_rank`` as well as ``parent_id``. On the ALK2 v7
    #: artifacts the distinction is invisible -- 274,072 kcal/mol rows, all ``pose_rank`` 0, over
    #: 274,043 molecules -- which is exactly why it has to be written down rather than inferred from
    #: a denominator that happened to agree.
    population_unit: str = "molecules"
    #: Population values dropped before the denominator because they carried no orderable number.
    #: Reported because a percentile over a silently shrunk population still looks computed.
    population_dropped: int = 0

    @property
    def actives(self) -> int | None:
        """Known actives placed in the population.

        ``None`` under ``MIXED``, where nothing was placed. Zero would be read as "the panel
        declares no binders" by a panel that declares eight, and the count an operator wants in
        that case is :attr:`Separation.actives`, which is measured without placing anything.
        """

        return None if self.direction == MIXED else len(self.percentiles)

    @property
    def best(self) -> float | None:
        """The strongest known active's percentile, or ``None`` when nothing was placed.

        The ``MIXED`` guard is here rather than in :meth:`as_dict` because the two must not
        disagree. With it only in the serialiser, ``best`` read ``0.0`` while
        ``as_dict()["best_percentile"]`` read ``None``, and :meth:`keep_for`'s own invariant --
        ``keep_for(1.0) == worst`` -- broke on ``None == 0.0``.
        """

        if self.direction == MIXED or not self.percentiles:
            return None
        return self.percentiles[0]

    @property
    def worst(self) -> float | None:
        if self.direction == MIXED or not self.percentiles:
            return None
        return self.percentiles[-1]

    @property
    def median(self) -> float | None:
        if self.direction == MIXED or not self.percentiles:
            return None
        return statistics.median(self.percentiles)

    @property
    def inactive_median(self) -> float | None:
        """Where the panel's inactives sit, for contrast. ``None`` under ``MIXED``."""

        if self.direction == MIXED or not self.inactive_percentiles:
            return None
        return statistics.median(self.inactive_percentiles)

    def recall_at(self, keep: float = DEFAULT_KEEP) -> float | None:
        """Fraction of known actives a "keep the best ``keep``" gate would retain.

        ``None`` when the panel declares no actives, which is the only case where this is
        unevaluable rather than simply low.
        """

        if self.direction == MIXED or not self.percentiles:
            return None
        return sum(1 for p in self.percentiles if p <= keep) / len(self.percentiles)

    def keep_for(self, recall: float) -> float | None:
        """The smallest share of the population that retains ``recall`` of the known actives.

        This is the question an operator actually has -- "how small can the shortlist be and still
        keep the chemistry I know binds" -- and it has an answer only because the panel's positions
        are measured against the population rather than against each other.
        """

        if self.direction == MIXED or not self.percentiles or not 0.0 < recall <= 1.0:
            return None
        needed = math.ceil(recall * len(self.percentiles))
        return self.percentiles[min(needed, len(self.percentiles)) - 1]

    @property
    def informative(self) -> bool | None:
        """Whether the score separates the panel's actives from its inactives at all.

        ``None`` without both classes. False when the median active sits no better than the median
        inactive -- a score ordering the population no better than chance with respect to the only
        molecules whose answer is known. A percentile gate on such a score is a random subsample of
        a chosen size, which is a legitimate thing to want and a different claim from enrichment.
        """

        if self.direction == MIXED:
            return None
        if not self.percentiles or not self.inactive_percentiles:
            return None
        return statistics.median(self.percentiles) < statistics.median(self.inactive_percentiles)

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "direction": self.direction,
            "population": self.population,
            "population_unit": self.population_unit,
            "population_dropped": self.population_dropped,
            "actives": self.actives,
            "best_percentile": self.best,
            "median_percentile": self.median,
            "worst_percentile": self.worst,
            "inactive_median_percentile": self.inactive_median,
            "informative": self.informative,
            "recall_at_0.5%": self.recall_at(0.005),
            "recall_at_1%": self.recall_at(0.01),
            "recall_at_5%": self.recall_at(0.05),
            "keep_for_full_recall": self.keep_for(1.0),
        }


def percentile_threshold(
    population: Sequence[float], keep: float, direction: str = LOWER_STRONGER
) -> float | None:
    """The score that keeps the best ``keep`` share of ``population``.

    A cascade gate takes a number, so this is where the percentile becomes one. Recording that the
    number came from here -- and from which population -- is what keeps it from being mistaken later
    for a statement about affinity.

    Refuses a ``direction`` outside the two conventions ``docking_score/v1`` declares, rather than
    falling through to "lower is stronger" the way the default argument does. ``MIXED`` reached here
    as a direction and was read as Vina-family. Measured on the KarmaDock MDN scores of all 27 ALK2
    v7 batches, 299,805 rows declaring ``HIGHER_STRONGER``: the best-2% cut is 59.359 and keeps
    2.00% of them, while the same call read as Vina-family returns 20.200, which keeps 98.00%. Both
    come back as a bare float in the same shape, so a gate that had stopped gating would have been
    recorded as a 2% gate.

    Population values that cannot be ordered are dropped before the cut, and ``None`` comes back
    once nothing orderable is left. The consequence of a corrupted percentile is worse here than in
    a verdict: the output is a number that gets applied to a million molecules.
    """

    if not 0.0 < keep <= 1.0:
        return None
    if direction not in (LOWER_STRONGER, HIGHER_STRONGER):
        return None
    finite = [value for value in (_finite(entry) for entry in population) if value is not None]
    if not finite:
        return None
    ordered = sorted(finite, reverse=direction == HIGHER_STRONGER)
    index = max(0, min(len(ordered) - 1, math.ceil(keep * len(ordered)) - 1))
    return ordered[index]


def enrichment(
    scores: Iterable[Mapping[str, Any]],
    panel: Sequence[PanelMember],
    population: Iterable[Mapping[str, Any]],
) -> tuple[Enrichment, ...]:
    """Place a panel's molecules in the distribution of everything the campaign screened.

    **The population is rows, not one number per molecule, and the old signature was the defect.**
    It asked for ``Mapping[str, Sequence[float]]`` -- a flat list of scores per engine -- and
    ``docking_score/v1`` is keyed on ``(parent_id, engine_id, receptor_id, pose_rank)``, so a caller
    holding those rows has to pick a reading before it can satisfy that type. Three readings are
    available and they are not the same quantile: every row, the best pose per molecule, or the
    one-liner ``{row["parent_id"]: row["score"] for row in rows}``, which is last-wins over a
    best-first pose list. A pose-level denominator is a quantile over poses, so a molecule that
    docked with nine modes is weighted nine times and a gate set from it keeps a different set of
    molecules than the one intended.

    On the ALK2 v7 campaign the three agree exactly, and that is worth recording rather than
    assuming: both engines were configured to commit one pose per molecule, so the 274,072 kcal/mol
    rows pooled over 27 batches carry ``pose_rank`` 0 and nothing else, cover 274,043 molecules, and
    put the best-2% cut at -11.385 whichever reading is taken. **No threshold this campaign set
    changes.** What changes is that the next cascade to raise ``num_modes`` cannot silently reweight
    the quantile by pose count.

    So the collapse is done here, by the same ``min`` over each molecule's poses that the panel side
    has always used, and :attr:`Enrichment.population_unit` records what one unit of the denominator
    counted. An annotation cannot stop the wrong call; taking the rows can.

    Each row is canonicalised under **its own** ``direction``, so a population spanning two
    conventions is not silently flipped one way. A genuine disagreement within one engine -- panel
    rows declaring one convention and population rows the other -- makes that engine ``MIXED``, and
    a ``MIXED`` engine gets a row reporting exactly that and no percentiles, rather than being
    dropped or placed under a guessed heading.

    Args:
        scores: The panel's own ``docking_score/v1`` rows, as :func:`separation` takes them.
        panel: The molecules, with ``known_active`` declared.
        population: The ``docking_score/v1`` rows of the screened library. Not the panel's rows: a
            panel is tens of molecules and the question here is where they sit among hundreds of
            thousands. Poses are collapsed per molecule; a row with no ``parent_id`` counts as one
            molecule of its own rather than merging with every other anonymous row.
    """

    actives = {m.parent_id for m in panel if m.known_active}
    inactives = {m.parent_id for m in panel if not m.known_active}
    best: dict[str, dict[str, float]] = {}
    directions: dict[str, set[str]] = {}
    for row in scores:
        engine = str(row.get("engine_id", "")) or "unknown"
        parent = str(row.get("parent_id", ""))
        if parent not in actives and parent not in inactives:
            continue
        value = _finite(row.get("score"))
        if value is None:
            continue
        heading = _direction(row)
        directions.setdefault(engine, set()).add(heading)
        canonical = value if heading == LOWER_STRONGER else -value
        kept = best.setdefault(engine, {})
        kept[parent] = min(canonical, kept.get(parent, math.inf))

    screened: dict[str, dict[str, float]] = {}
    dropped: dict[str, int] = {}
    for index, row in enumerate(population):
        engine = str(row.get("engine_id", "")) or "unknown"
        value = _finite(row.get("score"))
        if value is None:
            dropped[engine] = dropped.get(engine, 0) + 1
            continue
        heading = _direction(row)
        directions.setdefault(engine, set()).add(heading)
        molecule = str(row.get("parent_id", "")) or f"#{index}"
        canonical = value if heading == LOWER_STRONGER else -value
        pool = screened.setdefault(engine, {})
        pool[molecule] = min(canonical, pool.get(molecule, math.inf))

    rows: list[Enrichment] = []
    for engine in sorted(best):
        seen = directions.get(engine) or set()
        heading = next(iter(seen)) if len(seen) == 1 else MIXED
        canon = sorted(screened.get(engine, {}).values())
        # Before the population check, not after. ``MIXED`` is a fact about the engine's rows and
        # has to be reported whether or not a population came with them.
        if heading == MIXED:
            rows.append(
                Enrichment(
                    engine_id=engine,
                    direction=MIXED,
                    population=len(canon),
                    percentiles=(),
                    population_dropped=dropped.get(engine, 0),
                )
            )
            continue
        if not canon:
            continue
        total = len(canon)

        def place(score: float, _canon: list[float] = canon, _total: int = total) -> float:
            # Share of the population at least as strong, in canonical (smaller is stronger) terms.
            # Bound as defaults: the closure outlives this iteration of the loop.
            return bisect.bisect_right(_canon, score) / _total

        measured = best[engine]
        rows.append(
            Enrichment(
                engine_id=engine,
                direction=heading,
                population=total,
                percentiles=tuple(
                    sorted(place(v) for p, v in measured.items() if p in actives)
                ),
                inactive_percentiles=tuple(
                    sorted(place(v) for p, v in measured.items() if p in inactives)
                ),
                population_unit="molecules",
                population_dropped=dropped.get(engine, 0),
            )
        )
    return tuple(rows)


def _verdict(row: Mapping[str, Any], actives: frozenset[str]) -> TierVerdict:
    lost = tuple(str(name) for name in row.get("lost", ()))
    return TierVerdict(
        tier_id=str(row.get("tier_id", "")),
        title=str(row.get("title", "")),
        entering=row.get("entering"),
        surviving=row.get("surviving"),
        lost_actives=tuple(name for name in lost if name in actives),
        lost_inactives=tuple(name for name in lost if name not in actives),
        unavailable=row.get("unavailable"),
    )


def separation(
    scores: Iterable[Mapping[str, Any]], panel: Sequence[PanelMember]
) -> tuple[Separation, ...]:
    """Per-engine separation of the panel's actives from its inactives.

    **Each engine is read under its own convention.** Vina-family scores run down and KarmaDock's
    MDN runs up, and the first version of this function hardcoded "lower is better" for both -- while
    this module's own docstring warned that "an engine whose convention is the other way round would
    produce a report that looks computed and is inverted". It did. On an ALK2 panel it reported
    KarmaDock's ``best_active`` as 3.247, which was the *worst* of the four actives; the strongest
    was 11.084. ``docking_score/v1`` carries ``direction`` as a non-nullable field for exactly this
    reason and nothing was reading it.

    Comparisons are done on a canonical form where smaller is always stronger; every number
    *reported* stays in the engine's own units, so ``best_active`` is a value a reader can look up in
    the run's own artifacts.

    **An engine whose rows disagree about their own convention reports no numbers at all.** The
    canonical form is a mixture there, and the un-flip applied to report it was chosen from a single
    heading -- so a rising KarmaDock MDN came back in the shape of a kcal/mol score. There is no
    scalar that undoes a mixture, so every extreme and both counts come back ``None`` and the
    direction says ``MIXED``.

    Scores that cannot be ordered are dropped at intake; :func:`calibrate` reports how many per
    engine, because an engine whose every panel score was NaN used to vanish from this tuple and be
    read downstream as a score that failed to rank.
    """

    actives = {member.parent_id for member in panel if member.known_active}
    inactives = {member.parent_id for member in panel if not member.known_active}
    by_engine: dict[str, dict[str, float]] = {}
    directions: dict[str, set[str]] = {}
    for row in scores:
        engine = str(row.get("engine_id", "")) or "unknown"
        parent = str(row.get("parent_id", ""))
        if parent not in actives and parent not in inactives:
            continue
        value = _finite(row.get("score"))
        if value is None:
            continue
        heading = _direction(row)
        directions.setdefault(engine, set()).add(heading)
        canonical = value if heading == LOWER_STRONGER else -value
        kept = by_engine.setdefault(engine, {})
        # Best pose per molecule, under this engine's own convention.
        kept[parent] = min(canonical, kept.get(parent, math.inf))

    rows: list[Separation] = []
    for engine in sorted(by_engine):
        seen = directions[engine]
        heading = seen.pop() if len(seen) == 1 else MIXED
        measured = by_engine[engine]
        active_values = sorted(v for p, v in measured.items() if p in actives)
        inactive_values = sorted(v for p, v in measured.items() if p in inactives)
        if heading == MIXED:
            # No scalar undoes a mixture of two conventions. The predecessor of this branch fell
            # through to the ``v -> v`` arm of ``shown`` -- the one for Vina-family scores -- and
            # reported a rising KarmaDock MDN as though it were kcal/mol. The two counts below are
            # computed from the canonical locals and never pass through ``shown``, so they look
            # like cross-convention numbers and are not; they are nulled for the same reason.
            rows.append(
                Separation(
                    engine_id=engine,
                    direction=MIXED,
                    actives=len(active_values),
                    inactives=len(inactive_values),
                    best_active=None,
                    median_active=None,
                    worst_active=None,
                    best_inactive=None,
                    inactives_above_best_active=None,
                    actives_below_best_inactive=None,
                )
            )
            continue
        # Undo the canonical form for reporting, so every extreme is in the engine's own units.
        shown = (lambda v: -v) if heading == HIGHER_STRONGER else (lambda v: v)
        best_active = active_values[0] if active_values else None
        best_inactive = inactive_values[0] if inactive_values else None
        rows.append(
            Separation(
                engine_id=engine,
                direction=heading,
                actives=len(active_values),
                inactives=len(inactive_values),
                best_active=None if best_active is None else shown(best_active),
                median_active=(
                    shown(statistics.median(active_values)) if active_values else None
                ),
                worst_active=None if not active_values else shown(active_values[-1]),
                best_inactive=None if best_inactive is None else shown(best_inactive),
                inactives_above_best_active=(
                    0 if best_active is None else sum(1 for v in inactive_values if v <= best_active)
                ),
                actives_below_best_inactive=(
                    # ``>=``, not ``>``. An active scoring exactly what the best inactive scores
                    # has not been ordered ahead of it, and the sibling count above has always
                    # included its tie. Four molecules all scoring -8.0 reported
                    # ``inactives_above_best_active: 2`` and ``actives_below_best_inactive: 0`` in
                    # one row, so a constant was reported as a score that separates the panel.
                    0
                    if best_inactive is None
                    else sum(1 for v in active_values if v >= best_inactive)
                ),
            )
        )
    return tuple(rows)


def calibrate(
    screen: Screen,
    result: ScreenResult,
    panel: Sequence[PanelMember],
    *,
    required_recall: float = REQUIRED_RECALL,
) -> Calibration:
    """Measure what the funnel did to a panel of known molecules, and judge it.

    The run may have ended exhausted -- every molecule gated out -- and that is the case this is
    most useful in, because the docking scores are committed regardless and they are what says how
    far the threshold is from the known binders. Measured: a batch whose final gate emptied had
    7,545 scores from two engines already in the store.

    Args:
        screen: The adapter the panel was screened through.
        result: That run's result. Its ``revision_id`` is what the calibration is about.
        panel: The molecules, with ``known_active`` declared for each.
        required_recall: Fraction of known actives that must survive. See :data:`REQUIRED_RECALL`
            before lowering it.

    Raises:
        ValueError: If the panel is empty or names a molecule twice.
    """

    if not panel:
        raise ValueError("a calibration needs a panel; an empty panel measures nothing")
    seen: set[str] = set()
    for member in panel:
        if member.parent_id in seen:
            raise ValueError(f"panel names {member.parent_id!r} twice")
        seen.add(member.parent_id)
    if not 0.0 < float(required_recall) <= 1.0:
        raise ValueError("required_recall must be in (0, 1]")

    actives = frozenset(member.parent_id for member in panel if member.known_active)
    notes: list[str] = []

    try:
        report = screen.recall(result.run_id, panel_size=len(panel))
        tiers = tuple(_verdict(row, actives) for row in report["tiers"])
        finalize = tuple(_verdict(row, actives) for row in report["finalize"])
        registered = int(report["registered"])
        notes.extend(str(note) for note in report["notes"])
    except Exception as error:  # noqa: BLE001 -- an unreadable panel is a result, reported as one
        # Fails closed: no tiers means recall() is None means admissible is False.
        tiers, finalize, registered = (), (), 0
        notes.append(
            f"per-tier retention unreadable ({type(error).__name__}: {error}). The library must be "
            "read with an id column for a panel to be traceable by name, and the run must have come "
            "from a cascade rather than a flat pipeline."
        )

    # Score rows key on a digest of the standardised molecule; the panel declares the library's own
    # identifiers. Without this join nothing matches and the separation comes back empty, which
    # reads as "the score was not shown to rank" rather than "nobody looked" -- see
    # :meth:`~etalon.boundary.screen.Screen.parent_names`.
    try:
        named = screen.parent_names(result)
    except Exception as error:  # noqa: BLE001 -- an unjoinable run is a result, reported as one
        named = {}
        notes.append(f"parent ids could not be joined to library names ({error})")

    # Whether the panel's own molecules were in this run at all. ``parent_names`` is intake-level --
    # it joins the registered molecules to the library's id column before any gate -- so a declared
    # id missing from its values was never screened, which is a different thing from a gate having
    # deleted it.
    #
    # Recall cannot see the difference and must not be asked to. It is computed from names: a
    # declared id that matches no library id can never appear in any tier's ``lost`` list, so it
    # counts as surviving every gate. Measured on a panel declared under this campaign's
    # ``act_``/``neg_`` convention against a panel library whose id column carried the bare names:
    # one tier reporting ``entering 3, surviving 0, lost [LDN-193189, SARACATINIB, aspirin]`` -- a
    # funnel that deleted every molecule it was given -- calibrated to ``recall 1.0``,
    # ``admissible True``, ``refusals ()``, and minted a signed authorization carving the pool.
    observable = set(named.values())
    absent = sorted(seen - observable) if observable else []
    if (tiers or finalize) and absent:
        shown_absent = absent if len(absent) <= 6 else [*absent[:6], f"... and {len(absent) - 6} more"]
        finalize = finalize + (
            TierVerdict(
                tier_id="panel_identity",
                title="the panel's own molecules, in this run",
                entering=None,
                surviving=None,
                lost_actives=(),
                lost_inactives=(),
                unavailable=(
                    f"{len(absent)} of the {len(panel)} declared panel members are not among the "
                    f"{len(observable)} molecules this run registered under the library's own ids: "
                    f"{shown_absent}. A declared id matching no library id cannot appear in any "
                    "tier's lost list, so recall would count it as surviving every gate -- 1.0 for "
                    "a funnel that deleted everything, when none of them match. Declare the panel "
                    "under the library's id column, or re-screen the panel library under these ids."
                ),
            ),
        )

    scores: list[dict[str, Any]] = []
    unmatched = 0
    # Counted per engine so an engine that came back entirely unorderable can say so. The rows
    # themselves stay in ``scores``: that table is the evidence a reader audits, and dropping them
    # would leave the note as the only trace of a number that did arrive.
    row_total: dict[str, int] = {}
    row_unorderable: dict[str, int] = {}
    for artifact in screen.artifacts_carrying(result, DOCKING_SCORE):
        try:
            rows = screen.read(artifact, contract_id=DOCKING_SCORE)
        except Exception as error:  # noqa: BLE001 -- one unreadable engine must not hide the others
            notes.append(f"docking scores in {artifact[:19]} unreadable: {error}")
            continue
        for row in rows:
            raw = str(row.get("parent_id", ""))
            # Fall back to the raw id: a flat pipeline may already key on the library's names.
            member = named.get(raw, raw)
            if member not in seen:
                unmatched += 1
                continue
            engine = str(row.get("engine_id", "")) or "unknown"
            row_total[engine] = row_total.get(engine, 0) + 1
            if _finite(row.get("score")) is None:
                row_unorderable[engine] = row_unorderable.get(engine, 0) + 1
            scores.append(
                {
                    "parent_id": member,
                    "engine_id": row.get("engine_id"),
                    "score": row.get("score"),
                    "score_kind": row.get("score_kind"),
                    "direction": row.get("direction"),
                }
            )
    for engine in sorted(row_unorderable):
        count, total = row_unorderable[engine], row_total[engine]
        note = (
            f"{count} of {total} {engine} score rows for the panel carried no orderable number "
            "(missing, NaN or infinite) and are excluded from the separation"
        )
        if count == total:
            # Without this, an engine whose every score was NaN simply disappeared from the
            # separation tuple with no note anywhere, and the operator was told "no engine's score
            # ordered the panel" -- a measured negative, where the truth was that no number came
            # back at all.
            note += (
                f". {engine} therefore does not appear in the separation at all, and its absence "
                "there is for this reason rather than because its score failed to rank the panel"
            )
        else:
            # Partial loss shrinks the panel the separation is measured over without shrinking the
            # panel the recall is measured over, so the two disagree about how many actives exist.
            note += (
                ", so the separation for this engine is measured over fewer panel molecules than "
                "the calibration declares"
            )
        notes.append(note)

    if not scores:
        notes.append(
            "no docking scores were readable for the panel, so score separation is unevaluable. "
            "Recall alone cannot say whether the number the gate compares against is a ranking."
            + (
                " Scores exist but none belong to a declared panel member, and no parent-to-name "
                "mapping could be built -- the usual cause is a run screened without an id column."
                if unmatched and not named
                else ""
            )
        )

    return Calibration(
        revision_id=result.revision_id,
        run_id=result.run_id,
        outcome=result.outcome,
        panel_size=len(panel),
        actives=len(actives),
        registered=registered,
        tiers=tiers,
        finalize=finalize,
        separation=separation(scores, panel),
        required_recall=float(required_recall),
        notes=tuple(notes),
        scores=tuple(scores),
    )


__all__ = [
    "DEFAULT_KEEP",
    "DOCKING_SCORE",
    "Enrichment",
    "HIGHER_STRONGER",
    "LOWER_STRONGER",
    "MIXED",
    "REQUIRED_RECALL",
    "STRUCTURAL_TIERS",
    "Calibration",
    "PanelMember",
    "Separation",
    "TierVerdict",
    "calibrate",
    "enrichment",
    "percentile_threshold",
    "separation",
]
