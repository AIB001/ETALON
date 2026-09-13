"""Account for each generative model separately, and say where the comparison is decidable.

Five models producing 150,000 molecules each is a decision about where to spend generation, and it
is normally made once and never revisited: the campaign runs the models it has, screens the union,
and reports the survivors. The per-model question -- *which of these was worth running* -- is
answerable and almost never asked, which makes generation the one stage of a modern CADD pipeline
with no feedback on it at all.

It is answerable at some stages and not at others, and knowing which is the point of this module.

A model's QC failure rate is measured on 150,000 molecules, so a difference of a few percent
between two models is solid. A model's share of the ten molecules that reach the end is measured on
ten, and there the difference between "contributed four" and "contributed one" is a coin-flip: the
95% Wilson interval on 4/10 runs from 17% to 69%. So a campaign that reallocates generation on the
strength of final survivors is reallocating on noise, and one that reallocates on QC rates and
early-tier survival is using numbers that mean something.

Three accounting facts about generative output make the early stages worth measuring carefully.

**These models emit heavy atoms only.** Measured in the vendored PRISM, not here: across 82 gaff2
builds, the topology carried zero hydrogens in 41 of 41 of those built from hydrogen-free input,
with no warning at any stage, while hydrogenated input gave the right count in 35 of 41. The record
is ``asset/prism/prism/generation/handoff.py`` at the pinned commit; ETALON has no ``findings/``
entry for it because ETALON did not run it, and this docstring used to say "measured in this
project", which named the wrong project for the most-repeated number in the repository.
So "generated" and "usable" are different counts, and a model whose output hydrogenates badly has a
lower real yield than its raw count suggests.

**Validity is not uniform across models.** PoseBusters found that no deep-learning docking method
outperformed classical tools on physical plausibility, and the same checks applied to generated
structures separate models sharply. A model with a 40% sanitisation failure rate contributed 90,000
molecules, not 150,000, and the campaign's economics should be told the smaller number.

**Novelty and yield pull in opposite directions.** A model that reproduces known chemotypes scores
well on every filter calibrated against known actives and contributes nothing a campaign could not
have bought. This module reports scaffold overlap with the training panel beside the yield, because
a generator's value is the molecules it finds that nothing else would have.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


def wilson(successes: int, trials: int, *, confidence: float = 0.95) -> tuple[float, float]:
    """A Wilson score interval for a rate, which is the honest one at small counts.

    The textbook normal approximation gives an interval that runs below zero at small counts and
    has coverage far from nominal; Wilson's is correct enough at the counts that matter here and
    needs no table. At 4 successes in 10 it returns roughly 0.17 to 0.69, which is the number that
    decides whether a campaign may act on a generator's share of its final survivors.
    """

    from scipy import stats

    if trials < 1:
        raise ValueError("a rate needs at least one trial")
    if not 0 <= successes <= trials:
        raise ValueError(f"{successes} successes in {trials} trials is not a rate")
    z = float(stats.norm.isf((1.0 - confidence) / 2.0))
    rate = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (rate + z * z / (2 * trials)) / denominator
    spread = (
        z
        * math.sqrt(rate * (1.0 - rate) / trials + z * z / (4 * trials * trials))
        / denominator
    )
    # The boundaries are exact rather than clamped. A rate of zero has a lower bound of exactly
    # zero and a rate of one an upper bound of exactly one, and the arithmetic above reaches
    # 0.9999999999999999 for the latter -- a float artefact that would make an interval look open
    # where it is closed, and that a reader comparing two intervals for disjointness could trip on.
    low = 0.0 if successes == 0 else max(0.0, centre - spread)
    high = 1.0 if successes == trials else min(1.0, centre + spread)
    return low, high


@dataclass(frozen=True, slots=True)
class GeneratorYield:
    """One model's passage through the funnel, counted at every stage it was measured at."""

    model: str
    generated: int
    #: Stage id to how many of this model's molecules were still present after it. Ordered by the
    #: caller, because the funnel's order is the campaign's.
    survivors: Mapping[str, int] = field(default_factory=dict)
    #: Molecules whose scaffold also appears in the training panel. A generator reproducing known
    #: chemotypes passes every filter calibrated on known actives and contributes nothing new.
    known_scaffolds: int = 0

    def rate_at(self, stage: str) -> tuple[float, tuple[float, float]]:
        """Survival rate to a stage, with its Wilson interval."""

        if stage not in self.survivors:
            raise KeyError(f"{self.model} has no count recorded at {stage!r}")
        count = self.survivors[stage]
        return count / self.generated, wilson(count, self.generated)

    @property
    def novel_fraction(self) -> float:
        return 0.0 if not self.generated else 1.0 - self.known_scaffolds / self.generated

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "generated": self.generated,
            "survivors": dict(self.survivors),
            "novel_scaffold_fraction": round(self.novel_fraction, 4),
            "rates": {
                stage: {
                    "rate": round(count / self.generated, 6),
                    "wilson95": [round(v, 6) for v in wilson(count, self.generated)],
                }
                for stage, count in self.survivors.items()
            },
        }


@dataclass(frozen=True, slots=True)
class Comparison:
    """Whether two generators differ at one stage, and whether the stage can tell."""

    stage: str
    left: str
    right: str
    left_rate: float
    right_rate: float
    left_interval: tuple[float, float]
    right_interval: tuple[float, float]

    @property
    def decidable(self) -> bool:
        """Whether the intervals are disjoint. Overlapping intervals are a tie, not an order."""

        return (
            self.left_interval[0] > self.right_interval[1]
            or self.right_interval[0] > self.left_interval[1]
        )

    @property
    def better(self) -> str | None:
        if not self.decidable:
            return None
        return self.left if self.left_rate > self.right_rate else self.right

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "decidable": self.decidable,
            "better": self.better,
            self.left: {"rate": round(self.left_rate, 6), "wilson95": [round(v, 6) for v in self.left_interval]},
            self.right: {"rate": round(self.right_rate, 6), "wilson95": [round(v, 6) for v in self.right_interval]},
            "note": (
                f"{self.better} survives {self.stage} at a rate the counts can distinguish."
                if self.decidable
                else (
                    f"The intervals overlap, so {self.stage} cannot tell these two apart. "
                    "Reallocating generation on this comparison would be reallocating on noise."
                )
            ),
        }


def compare(left: GeneratorYield, right: GeneratorYield, stage: str) -> Comparison:
    """Compare two generators at one stage of the funnel."""

    left_rate, left_interval = left.rate_at(stage)
    right_rate, right_interval = right.rate_at(stage)
    return Comparison(
        stage=stage,
        left=left.model,
        right=right.model,
        left_rate=left_rate,
        right_rate=right_rate,
        left_interval=left_interval,
        right_interval=right_interval,
    )


@dataclass(frozen=True, slots=True)
class Audit:
    """Every generator, every stage, and the deepest stage that can still rank them."""

    yields: tuple[GeneratorYield, ...]
    stages: tuple[str, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def comparisons(self, stage: str) -> list[Comparison]:
        out: list[Comparison] = []
        for index, left in enumerate(self.yields):
            for right in self.yields[index + 1 :]:
                out.append(compare(left, right, stage))
        return out

    def deepest_decidable_stage(self) -> str | None:
        """The last stage at which any two generators can still be told apart.

        The stage a campaign should reallocate generation on. Past it the counts are too small, and
        a model's share of the final survivors is the least informative number in the whole audit
        despite being the one everybody reports.
        """

        decidable = [
            stage
            for stage in self.stages
            if any(comparison.decidable for comparison in self.comparisons(stage))
        ]
        return decidable[-1] if decidable else None

    def as_dict(self) -> dict[str, object]:
        deepest = self.deepest_decidable_stage()
        return {
            "generators": [item.as_dict() for item in self.yields],
            "stages": list(self.stages),
            "deepest_decidable_stage": deepest,
            "comparisons": {
                stage: [c.as_dict() for c in self.comparisons(stage)] for stage in self.stages
            },
            "notes": list(self.notes),
            "how_to_use_this": (
                f"Reallocate generation on {deepest}, which is the deepest stage whose counts can "
                "distinguish any two of these models."
                if deepest
                else "No stage in this funnel can distinguish any two of these generators. The "
                "honest conclusion is that this campaign has not learned which model to favour, "
                "and generating more from each is the way to find out -- not reading the final "
                "survivors, whose counts are too small to mean anything."
            ),
        }


def audit(
    yields: Sequence[GeneratorYield],
    stages: Sequence[str],
) -> Audit:
    """Assemble a per-generator audit and say where its conclusions are usable."""

    if len(yields) < 2:
        raise ValueError(
            "an audit compares generators; with one there is nothing to compare and the counts "
            "belong in the campaign's ledger rather than here"
        )
    missing = {
        item.model: [stage for stage in stages if stage not in item.survivors] for item in yields
    }
    absent = {model: gaps for model, gaps in missing.items() if gaps}
    if absent:
        raise KeyError(
            f"these generators have no counts at some stages: {absent}. A stage measured for one "
            "model and not another cannot be compared, and filling a gap with zero would report a "
            "model as having failed where it was simply not counted."
        )

    notes: list[str] = []
    final = stages[-1]
    smallest = min(item.survivors[final] for item in yields)
    if smallest < 30:
        low, high = wilson(smallest, sum(i.survivors[final] for i in yields))
        notes.append(
            f"At {final} the smallest contribution is {smallest} molecules, and a share that small "
            f"carries a 95% interval of {low:.0%} to {high:.0%}. Generation reallocated on the "
            "final survivors is reallocated on noise; use the deepest decidable stage instead."
        )
    for item in yields:
        if item.novel_fraction < 0.5:
            notes.append(
                f"{item.model}: {1 - item.novel_fraction:.0%} of its molecules share a scaffold "
                "with the training panel. A generator reproducing known chemotypes passes every "
                "filter calibrated on known actives and contributes little a campaign could not "
                "have bought, so its survival rate overstates its value."
            )
    return Audit(tuple(yields), tuple(stages), tuple(notes))


__all__ = ["Audit", "Comparison", "GeneratorYield", "audit", "compare", "wilson"]
