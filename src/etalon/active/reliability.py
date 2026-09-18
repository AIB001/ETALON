"""Admission likelihood, separate from scientific surrogate noise and hard QC.

Kernel-weighted Beta pseudo-counts are a working estimate, NOT a calibrated classifier.
Only acquired admission outcomes are used, never rejected numerical labels. Replicates on
one molecule share one vote; a stalled or blocked preflight is not a chemistry failure.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from etalon.active.model import MultiEndpointGP


def admission_estimate(model: MultiEndpointGP, ids: Sequence[str], endpoint: str,
                       observations: Sequence[dict[str, Any]], *, mode: str = "local") -> tuple[Any, Any]:
    """Return probabilities and supporting kernel mass (not an effective sample size)."""
    import numpy as np

    if mode not in {"local", "global", "none"}:
        raise ValueError("unknown admission estimate mode")
    if mode == "none":
        return np.ones(len(ids)), np.zeros(len(ids))
    outcomes: dict[str, list[float]] = defaultdict(list)
    for row in observations:
        result = row["result"]
        if result["endpoint_id"] == endpoint and result["status"] != "blocked":
            if result["candidate_id"] not in model.index:
                raise ValueError("admission outcome has an unknown molecule")
            outcomes[result["candidate_id"]].append(float(row["admitted"]))
    if not outcomes:
        return np.full(len(ids), 0.5), np.zeros(len(ids))
    seen = sorted(outcomes)
    successes = np.asarray([np.mean(outcomes[key]) for key in seen])
    if mode == "global":
        weights = np.ones((len(ids), len(seen)))
    else:
        query = model.x[[model.index[key] for key in ids]]
        train = model.x[[model.index[key] for key in seen]]
        weights = np.exp(-0.5 * model._distance(query, train) / model.lengthscale2)
    mass = weights.sum(axis=1)
    return (1.0 + weights @ successes) / (2.0 + mass), mass
