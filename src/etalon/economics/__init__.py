"""What each tier of a funnel costs, what it buys, and whether it is worth its place.

A cascade is filters of increasing cost and increasing accuracy, and the exchange rate between
those two is normally left to convention. :mod:`stage` writes it down with a source for every
number; :mod:`allocate` turns it into a plan and refuses a tier that cannot pay.

``docs/adr/0004`` carries the reasoning. The result that justifies the module: improving the
cheapest ranking tier's correlation from 0.35 to 0.50 retains more true actives than a ten-fold
increase in the compute budget, because at 0.35 a cut to the top 1% already discards 93% of them
and nothing downstream recovers a molecule already gone.
"""

from etalon.economics.allocate import Funnel, Tier, plan, retention
from etalon.economics.measure import (
    EnrichmentPoint,
    Measured,
    as_stage,
    measure,
    measure_enrichment,
    with_enrichment,
)
from etalon.economics.stage import (
    BY_ID,
    STAGES,
    Answers,
    Evidence,
    Stage,
    needs_an_ensemble,
    ranking_stages,
)

__all__ = [
    "BY_ID",
    "STAGES",
    "Answers",
    "Evidence",
    "Funnel",
    "EnrichmentPoint",
    "Measured",
    "Stage",
    "Tier",
    "needs_an_ensemble",
    "as_stage",
    "measure",
    "measure_enrichment",
    "with_enrichment",
    "plan",
    "ranking_stages",
    "retention",
]
