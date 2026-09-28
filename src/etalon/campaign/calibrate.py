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

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from etalon.boundary.screen import Screen, ScreenResult

#: The contract every docking engine's score port carries.
DOCKING_SCORE = "docking_score/v1"

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
    actives: int
    inactives: int
    best_active: float | None
    median_active: float | None
    worst_active: float | None
    best_inactive: float | None
    #: Inactives scoring at least as well as the *best* active. Says whether a top-N cut would be
    #: contaminated, which is a weaker question than whether the score orders the panel.
    inactives_above_best_active: int
    #: Actives that an inactive outscores. This is the number :attr:`separates` turns on. On the SND1
    #: panel it is 2 of 5 -- imatinib at -6.57 beats both co-crystal ligands, -6.05 and -5.90.
    actives_below_best_inactive: int

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
        """

        if not self.actives or not self.inactives:
            return None
        return self.actives_below_best_inactive == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
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
        """

        if not self.actives:
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

    Scores are Vina-like: **lower is better**. Every comparison here is written in those terms
    rather than in terms of magnitude, because an engine whose convention is the other way round
    would produce a report that looks computed and is inverted.
    """

    actives = {member.parent_id for member in panel if member.known_active}
    inactives = {member.parent_id for member in panel if not member.known_active}
    by_engine: dict[str, dict[str, float]] = {}
    for row in scores:
        engine = str(row.get("engine_id", "")) or "unknown"
        parent = str(row.get("parent_id", ""))
        value = row.get("score")
        if parent not in actives and parent not in inactives:
            continue
        if not isinstance(value, (int, float)):
            continue
        kept = by_engine.setdefault(engine, {})
        # Best pose per molecule: a molecule docked in several poses has one score here.
        kept[parent] = min(float(value), kept.get(parent, float("inf")))

    rows: list[Separation] = []
    for engine in sorted(by_engine):
        measured = by_engine[engine]
        active_values = sorted(v for p, v in measured.items() if p in actives)
        inactive_values = sorted(v for p, v in measured.items() if p in inactives)
        best_active = active_values[0] if active_values else None
        best_inactive = inactive_values[0] if inactive_values else None
        rows.append(
            Separation(
                engine_id=engine,
                actives=len(active_values),
                inactives=len(inactive_values),
                best_active=best_active,
                median_active=statistics.median(active_values) if active_values else None,
                worst_active=active_values[-1] if active_values else None,
                best_inactive=best_inactive,
                inactives_above_best_active=(
                    0 if best_active is None else sum(1 for v in inactive_values if v <= best_active)
                ),
                actives_below_best_inactive=(
                    0
                    if best_inactive is None
                    else sum(1 for v in active_values if v > best_inactive)
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

    scores: list[dict[str, Any]] = []
    for artifact in screen.artifacts_carrying(result, DOCKING_SCORE):
        try:
            rows = screen.read(artifact, contract_id=DOCKING_SCORE)
        except Exception as error:  # noqa: BLE001 -- one unreadable engine must not hide the others
            notes.append(f"docking scores in {artifact[:19]} unreadable: {error}")
            continue
        for row in rows:
            if str(row.get("parent_id", "")) in seen:
                scores.append(
                    {
                        "parent_id": row.get("parent_id"),
                        "engine_id": row.get("engine_id"),
                        "score": row.get("score"),
                    }
                )
    if not scores:
        notes.append(
            "no docking scores were readable for the panel, so score separation is unevaluable. "
            "Recall alone cannot say whether the number the gate compares against is a ranking."
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
    "DOCKING_SCORE",
    "REQUIRED_RECALL",
    "Calibration",
    "PanelMember",
    "Separation",
    "TierVerdict",
    "calibrate",
    "separation",
]
