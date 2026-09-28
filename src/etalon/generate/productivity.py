"""Whether a generator is still worth running, decided during generation rather than after it.

:mod:`etalon.generate.audit` answers the question after the funnel: which model's molecules survive.
It also establishes why that answer is usually unusable -- ``findings/0005``, on counts of the shape a
real campaign produces, a model's share of the final hits distinguishes none of five models from each
other. This module answers the question *before* the funnel, from two measurements taken while the
model runs, and the reason to prefer them is statistical power rather than convenience: they are
measured on 10^5 molecules instead of on 10^1 hits.

**Viability.** Delivered over requested, at the production box. A model can be perfectly good and
deliver almost nothing because the pocket box is too large for it. Measured: TargetDiff returned
2 of 100 on a 28 A box and 92 of 100 on a 22 A box; Pocket2Mol, 72 of 200 and 95 of 100. Read as a
model ranking, those first numbers retire two usable models. Read as :attr:`Productivity.viable`,
they say the box is wrong -- which is the remedy, and it is cheap.

**Exhaustion.** New-to-the-library molecules over delivered molecules. A generator conditioned on one
reference ligand eventually resamples the space it already covered, and when it does, a GPU-hour buys
duplicates. Measured over one 44-hour campaign:

===========  =========  ==========  ==========  ==================================
model        delivered  unique      uniqueness  cost per unique molecule
===========  =========  ==========  ==========  ==================================
FLOWR         ~187,000     163,191     **87%**  1.15x, flat across 39 chunks
MolCRAFT       219,492      76,027     **32%**  3.1x, falling to 24% by the end
===========  =========  ==========  ==========  ==================================

MolCRAFT was not broken. It had finished, and nothing was watching for that -- it was noticed by a
human comparing chunk records by eye. The stopping rule belongs here because it is a scientific
statement about a model's accessible chemical space, not a resource-management preference.

The threshold is expressed as a cost multiplier rather than as a uniqueness floor, because that is
the form the decision actually takes. At uniqueness *u* a campaign pays 1/*u* GPU-hours per molecule
it can screen, and the question is never "is 32% low" but "is 3.1x worth paying here". A floor
answers the wrong question with a number that looks objective.

Both measurements are free: they come from records a generation loop already writes.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from etalon.generate.audit import wilson

#: Paying more than this many GPU-hours per screenable molecule, relative to a perfect generator,
#: is the point at which continuing costs more than it delivers. Two -- half the output is
#: duplicates. Deliberately not a uniqueness floor; see the module docstring.
DEFAULT_COST_CEILING = 2.0

#: Delivery below this fraction of what was requested means the model and the pocket box disagree.
#: A quarter, which sits well clear of both measured regimes: the failures were 2% and 36%, the
#: recoveries 92% and 95%.
DEFAULT_VIABLE_DELIVERY = 0.25

#: Chunks required before exhaustion may be declared. Three, because a single low chunk is ordinary
#: -- a sampler discarding its own reconstruction failures produces one routinely -- and a campaign
#: that retired a model on one chunk retired it on noise. The same asymmetry as everywhere else:
#: stopping a good generator costs its remaining output, continuing a spent one costs GPU-hours,
#: and only the first is unrecoverable within the round.
MINIMUM_CHUNKS = 3


@dataclass(frozen=True, slots=True)
class Chunk:
    """One generation chunk, as its loop recorded it.

    ``unique`` is new-to-the-library after global deduplication, which is why this cannot be computed
    inside the generator: only the collector knows what the library already held.
    """

    requested: int
    delivered: int
    unique: int
    seconds: float

    def __post_init__(self) -> None:
        if self.requested <= 0:
            raise ValueError("a chunk requested a nonpositive number of molecules")
        if not 0 <= self.delivered:
            raise ValueError("delivered must be nonnegative")
        if not 0 <= self.unique <= self.delivered:
            raise ValueError(
                f"unique ({self.unique}) must lie in [0, delivered ({self.delivered})]; a molecule "
                "cannot be new to the library without having been delivered"
            )
        if self.seconds <= 0:
            raise ValueError("a chunk took a nonpositive time")


@dataclass(frozen=True, slots=True)
class Productivity:
    """One generation loop's measured productivity, over a trailing window of its chunks.

    ``tag`` rather than ``model``, and the distinction earned itself: one campaign ran five loops of
    the same model on five GPUs, and a report keyed on the model would have merged them -- hiding
    both the death of one loop and the fact that their uniqueness rates were independent evidence
    that they were sampling different space.
    """

    tag: str
    model: str
    #: A label for the conditioning geometry -- pocket and box. Carried because viability is a
    #: property of the pair, not of the model: the same TargetDiff is unusable at 28 A and fine at 22.
    pocket: str
    chunks: tuple[Chunk, ...]
    cost_ceiling: float = DEFAULT_COST_CEILING
    viable_delivery: float = DEFAULT_VIABLE_DELIVERY

    def __post_init__(self) -> None:
        if not self.chunks:
            raise ValueError(f"{self.tag}: productivity over no chunks measures nothing")
        if self.cost_ceiling < 1.0:
            raise ValueError("cost_ceiling below 1.0 demands a better-than-perfect generator")

    # -- viability ----------------------------------------------------------

    @property
    def requested(self) -> int:
        return sum(chunk.requested for chunk in self.chunks)

    @property
    def delivered(self) -> int:
        return sum(chunk.delivered for chunk in self.chunks)

    @property
    def unique(self) -> int:
        return sum(chunk.unique for chunk in self.chunks)

    @property
    def hours(self) -> float:
        return sum(chunk.seconds for chunk in self.chunks) / 3600.0

    @property
    def delivery(self) -> tuple[float, tuple[float, float]]:
        """Delivered over requested, with its Wilson interval."""

        return self.delivered / self.requested, wilson(self.delivered, self.requested)

    @property
    def viable(self) -> bool:
        """Whether this model and this box agree well enough to be worth running.

        Judged on the interval's upper bound, not on the point estimate: a model that *might* be
        delivering acceptably should not be retired, because the cheap remedy for the alternative
        (shrink the box and re-measure) has not been tried.
        """

        _, (_, high) = self.delivery
        return high >= self.viable_delivery

    # -- exhaustion ---------------------------------------------------------

    @property
    def uniqueness(self) -> tuple[float, tuple[float, float]]:
        """New-to-the-library over delivered, with its Wilson interval."""

        if not self.delivered:
            return 0.0, (0.0, 0.0)
        return self.unique / self.delivered, wilson(self.unique, self.delivered)

    @property
    def cost_multiplier(self) -> float | None:
        """GPU-hours per screenable molecule, relative to a perfect generator.

        ``None`` when nothing unique was delivered -- the multiplier is unbounded, and reporting a
        large finite number instead would invite comparison with a finite ceiling.
        """

        rate, _ = self.uniqueness
        return None if rate <= 0.0 else 1.0 / rate

    @property
    def unique_per_hour(self) -> float:
        return 0.0 if self.hours <= 0 else self.unique / self.hours

    @property
    def exhausted(self) -> bool:
        """Whether this loop has run out of accessible space at the price set by the ceiling.

        Requires :data:`MINIMUM_CHUNKS` and judges on the interval's *upper* bound, so a loop is
        retired only when the counts cannot support its still being productive. Uniqueness of zero
        over enough chunks is exhaustion regardless of the ceiling.
        """

        if len(self.chunks) < MINIMUM_CHUNKS:
            return False
        rate, (_, high) = self.uniqueness
        if rate <= 0.0:
            return True
        return (1.0 / high if high > 0 else float("inf")) > self.cost_ceiling

    @property
    def trend(self) -> float | None:
        """Uniqueness of the last third of the window minus that of the first third.

        ``None`` with fewer than :data:`MINIMUM_CHUNKS` chunks. Negative means the model is still
        losing ground, which distinguishes a generator that has plateaued at a usable rate from one
        that is on its way to exhaustion -- FLOWR was flat near 0.87 for 39 chunks while MolCRAFT
        fell from 0.32 to 0.24, and only one of those is a reason to plan a replacement.
        """

        if len(self.chunks) < MINIMUM_CHUNKS:
            return None
        cut = max(1, len(self.chunks) // 3)
        share = lambda group: (  # noqa: E731
            0.0
            if not sum(c.delivered for c in group)
            else sum(c.unique for c in group) / sum(c.delivered for c in group)
        )
        return share(self.chunks[-cut:]) - share(self.chunks[:cut])

    # -- verdict ------------------------------------------------------------

    def retire(self) -> tuple[str, ...]:
        """Reasons to stop this loop, empty when there are none.

        Returns reasons rather than a boolean so that the report says *which* measurement decided,
        and so that a caller can act on viability (shrink the box, re-measure) differently from
        exhaustion (the model is finished on this pocket).
        """

        reasons: list[str] = []
        rate, (low, high) = self.delivery
        if not self.viable:
            reasons.append(
                f"delivers {rate:.1%} of what is requested (95% CI {low:.1%}-{high:.1%}) on pocket "
                f"{self.pocket!r}. Before retiring the model, shrink the box and re-measure: "
                "measured elsewhere, 2/100 at 28 A became 92/100 at 22 A."
            )
        if self.exhausted:
            multiplier = self.cost_multiplier
            reasons.append(
                f"uniqueness {self.uniqueness[0]:.1%} costs "
                + ("unbounded" if multiplier is None else f"{multiplier:.1f}x")
                + f" per screenable molecule, above the {self.cost_ceiling:.1f}x ceiling. This model "
                "has covered the space its conditioning reaches on this pocket."
            )
        return tuple(reasons)

    def as_dict(self) -> dict[str, Any]:
        rate, delivery_interval = self.delivery
        unique_rate, unique_interval = self.uniqueness
        return {
            "tag": self.tag,
            "model": self.model,
            "pocket": self.pocket,
            "chunks": len(self.chunks),
            "requested": self.requested,
            "delivered": self.delivered,
            "unique": self.unique,
            "hours": round(self.hours, 3),
            "unique_per_hour": round(self.unique_per_hour, 1),
            "delivery": {
                "rate": round(rate, 4),
                "wilson95": [round(v, 4) for v in delivery_interval],
                "viable": self.viable,
            },
            "uniqueness": {
                "rate": round(unique_rate, 4),
                "wilson95": [round(v, 4) for v in unique_interval],
                "cost_multiplier": (
                    None if self.cost_multiplier is None else round(self.cost_multiplier, 2)
                ),
                "trend": None if self.trend is None else round(self.trend, 4),
                "exhausted": self.exhausted,
            },
            "retire": list(self.retire()),
        }


def window(chunks: Sequence[Chunk], *, keep: int = 6) -> tuple[Chunk, ...]:
    """The trailing ``keep`` chunks, which is the window an exhaustion decision should use.

    A generator's lifetime average hides the thing being decided. MolCRAFT's average uniqueness over
    the campaign was 32%; over its last six chunks it was 24%, and the second number is the one that
    says what the next chunk will cost.
    """

    if keep <= 0:
        raise ValueError("a window keeps at least one chunk")
    return tuple(chunks[-keep:])


def allocate(profiles: Sequence[Productivity]) -> dict[str, Any]:
    """Rank running loops by unique molecules per hour, and say who should stop.

    Deliberately ranked on unique-per-hour and *not* on hits. Hits are the product, and it is
    tempting to allocate on them directly -- one campaign did, retiring a model at 15:45 on a
    molecules-per-hour comparison and reinstating it three hours later on a hits-per-batch one. Both
    were wrong for the same reason: ``findings/0005`` and :func:`etalon.generate.audit.compare` show
    that at realistic counts the hit rates of two decent generators have overlapping Wilson
    intervals, so the comparison is noise either way.

    Checked against the SND1 campaign's own final numbers, ``compare`` calls the FLOWR-versus-MolCRAFT
    and MolCRAFT-versus-PocketXMol differences decidable -- they are 3x and 7x -- and calls the five
    FLOWR loops mutually undecidable, and PocketXMol versus DiffSBDD undecidable at 2.8 against 0.0
    per 100,000. So hit rate decides between *families*, where the differences are order-of-magnitude,
    and this function decides *within* a family, where only throughput can.
    """

    rows = sorted(profiles, key=lambda p: -p.unique_per_hour)
    retire = {p.tag: list(p.retire()) for p in rows if p.retire()}
    rates = [p.unique_per_hour for p in rows if p.tag not in retire]
    return {
        "loops": [p.as_dict() for p in rows],
        "retire": retire,
        "median_unique_per_hour": round(statistics.median(rates), 1) if rates else None,
        "note": (
            "Ranked on unique molecules per hour. Use etalon.generate.audit.compare for hit rates, "
            "and act on it only where its intervals are disjoint."
        ),
    }


__all__ = [
    "DEFAULT_COST_CEILING",
    "DEFAULT_VIABLE_DELIVERY",
    "MINIMUM_CHUNKS",
    "Chunk",
    "Productivity",
    "allocate",
    "window",
]
