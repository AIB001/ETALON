"""Conservative empirical cost quotes, shared by selection and transactional reservation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from etalon.active.schema import Endpoint, finite


def fits_budget(cost: float, remaining: float) -> bool:
    """Compare charges in their declared units without creating a free absolute allowance.

    The small relative tolerance accommodates ordinary arithmetic roundoff only between
    positive finite amounts. A zero/negative balance cannot fund a positive quote.
    """
    finite(cost, "cost", minimum=0.0)
    finite(remaining, "remaining budget")
    return remaining >= 0 and (cost <= remaining or (
        cost > 0 and remaining > 0 and math.isclose(cost, remaining, rel_tol=1e-12, abs_tol=0.0)))


def affordable_capacity(cost: float, remaining: float, *, limit: int) -> int:
    """Maximum affordable count within an explicit action cap, using the same comparison.

    Binary search avoids division overflow for tiny quotes and prevents an absolute
    epsilon from manufacturing allowance at zero remaining budget.
    """
    finite(cost, "cost", minimum=0.0)
    finite(remaining, "remaining budget")
    if cost <= 0 or isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("capacity needs a positive quote and a nonnegative integer limit")
    low, high = 0, limit
    while low < high:
        count = (low + high + 1) // 2
        try:
            charge = cost * count
        except OverflowError:
            charge = math.inf
        if math.isfinite(charge) and fits_budget(charge, remaining):
            low = count
        else:
            high = count - 1
    return low


def cost_quote(endpoint: Endpoint, observations: Sequence[dict[str, Any]]) -> float:
    """At least the configured quote and the observed 90th-percentile positive charge.

    This is a planning heuristic, not a bound on a real tool's runtime. A preflight refusal has
    no runtime evidence. Historical imports with zero (sunk) cost do not make new jobs free.
    Actual overruns remain visible and can exceed the campaign budget.
    """
    costs = sorted(float(o["result"]["cost"]) for o in observations
                   if o["result"]["endpoint_id"] == endpoint.id
                   and o["result"]["status"] != "blocked" and o["result"]["cost"] > 0)
    return max(endpoint.cost, costs[math.ceil(0.9 * len(costs)) - 1]) if costs else endpoint.cost
