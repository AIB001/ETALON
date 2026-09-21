"""Explicit identities, endpoints and observations for sequential CADD experiments.

An endpoint is a particular observable under a particular protocol on one target. Sharing a
unit does not make two endpoints interchangeable. Relative FEP edges need an edge observation
model and must not be passed here as absolute affinities.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from numbers import Real
from typing import Any

from etalon.faults.attribution import Observation


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def finite(value: float, name: str, *, minimum: float | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number, not a boolean or text")
    try:
        valid = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        valid = False
    if not valid or (minimum is not None and value < minimum):
        raise ValueError(f"{name} must be finite" + (f" and >= {minimum}" if minimum is not None else ""))


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


@dataclass(frozen=True)
class Endpoint:
    id: str
    target: str
    quantity: str
    units: str
    protocol: str
    cost: float
    direction: str = "minimize"
    noise: float = 0.1
    requires_handoff: bool = True
    max_replicates: int = 1
    prior_mean: float = 0.0
    prior_scale: float = 1.0
    # Historical assay evidence can train the model without advertising an executable assay.
    queryable: bool = True

    def __post_init__(self) -> None:
        for name in ("id", "target", "quantity", "units", "protocol", "direction"):
            _text(getattr(self, name), name)
        if type(self.requires_handoff) is not bool:
            raise ValueError("requires_handoff must be an explicit boolean")
        if type(self.queryable) is not bool:
            raise ValueError("queryable must be an explicit boolean")
        if self.direction not in {"minimize", "maximize"}:
            raise ValueError("direction must be minimize or maximize")
        if self.quantity.lower() in {"ddg", "relative_free_energy", "rbfe"}:
            raise ValueError("relative FEP is an edge observable, not a scalar molecule label")
        finite(self.cost, "cost", minimum=0.0)
        finite(self.noise, "noise", minimum=0.0)
        finite(self.prior_mean, "prior_mean")
        finite(self.prior_scale, "prior_scale", minimum=0.0)
        if ((self.queryable and self.cost <= 0) or self.noise <= 0 or self.prior_scale <= 0
                or type(self.max_replicates) is not int or self.max_replicates < 1):
            raise ValueError("executable cost, noise floor and max_replicates must be positive")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Candidate:
    id: str
    smiles: str
    features: tuple[float, ...]
    scaffold: str = ""
    source: str = "library"
    handoff: dict[str, Any] = field(default_factory=dict)
    cheap_value: float | None = None

    def __post_init__(self) -> None:
        _text(self.id, "candidate id")
        _text(self.smiles, "SMILES")
        if not isinstance(self.features, (list, tuple)) or not self.features:
            raise ValueError("candidate features must be a nonempty numeric sequence")
        if not isinstance(self.handoff, dict):
            raise ValueError("candidate handoff must be a mapping")
        for value in self.features:
            finite(value, "feature")
        if self.cheap_value is not None:
            finite(self.cheap_value, "cheap_value")
        if self.handoff and self.handoff.get("parent_id") != self.id:
            raise ValueError("candidate and handoff identify different molecules")
        if self.handoff and self.handoff.get("parent_smiles") != self.smiles:
            raise ValueError("candidate SMILES and handoff disagree")
        canonical(self.handoff)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Candidate:
        return cls(**{**value, "features": tuple(value["features"])})


@dataclass(frozen=True)
class Evaluation:
    candidate_id: str
    endpoint_id: str
    value: float | None
    units: str
    cost: float
    uncertainty: float | None = None
    status: str = "ok"
    checks: tuple[Observation, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("candidate_id", "endpoint_id", "units", "status"):
            _text(getattr(self, name), name)
        if not isinstance(self.provenance, dict):
            raise ValueError("evaluation provenance must be a mapping")
        if (not isinstance(self.checks, (tuple, list))
                or any(not isinstance(check, Observation) for check in self.checks)):
            raise ValueError("evaluation checks must contain structured Observations")
        object.__setattr__(self, "checks", tuple(self.checks))
        if self.status not in {"ok", "failed", "invalid", "blocked"}:
            raise ValueError(f"unknown evaluation status: {self.status}")
        finite(self.cost, "actual cost", minimum=0.0)
        if self.value is not None:
            finite(self.value, "value")
        if self.uncertainty is not None:
            finite(self.uncertainty, "uncertainty", minimum=0.0)
        if self.status == "ok" and self.value is None:
            raise ValueError("an ok evaluation needs a finite value")
        canonical(self.provenance)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Evaluation:
        return cls(**{**value, "checks": tuple(Observation(**c) for c in value.get("checks", ()))})


@dataclass(frozen=True)
class Action:
    id: str
    round_id: int
    candidate_id: str
    endpoint_id: str
    replicate: int
    reserved_cost: float
    decision: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "candidate_id", "endpoint_id"):
            _text(getattr(self, name), name)
        if any(type(value) is not int or value < 0 for value in (self.round_id, self.replicate)):
            raise ValueError("action round and replicate must be nonnegative integers")
        finite(self.reserved_cost, "reserved cost", minimum=0.0)
        if not isinstance(self.decision, dict):
            raise ValueError("action decision must be a mapping")
        canonical(self.decision)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CampaignSpec:
    objective: str
    budget: float
    cost_unit: str
    representation: str
    batch_size: int = 4
    seed: int = 0
    policy: str = "cost_aware"
    beta: float = 1.0
    explore_fraction: float = 0.25
    bootstrap: int = 4
    max_observations: int = 1500
    max_candidates: int = 5000
    # Used only by the explicit KG policies; legacy decisions keep their old semantics.
    calibration_fraction: float = 0.15
    confirmation_reserve: int = 1
    validity_mode: str = "local"
    max_kg_candidates: int = 512

    def __post_init__(self) -> None:
        finite(self.budget, "budget", minimum=0.0)
        finite(self.beta, "beta", minimum=0.0)
        for name in ("objective", "cost_unit", "representation", "policy", "validity_mode"):
            _text(getattr(self, name), name)
        if self.policy not in {"cost_aware", "cost_only", "ucb", "greedy", "random", "mf_kg", "decision_aware"}:
            raise ValueError(f"unknown policy: {self.policy}")
        finite(self.calibration_fraction, "calibration_fraction", minimum=0.0)
        if not 0 <= self.calibration_fraction <= 1:
            raise ValueError("calibration_fraction must be in [0, 1]")
        if type(self.confirmation_reserve) is not int or self.confirmation_reserve < 0:
            raise ValueError("confirmation_reserve must be a nonnegative integer")
        if self.validity_mode not in {"local", "global", "none"}:
            raise ValueError("validity_mode must be local, global or none")
        if type(self.max_kg_candidates) is not int or self.max_kg_candidates < 1:
            raise ValueError("max_kg_candidates must be a positive integer")
        finite(self.explore_fraction, "explore_fraction", minimum=0.0)
        if not 0 <= self.explore_fraction <= 1:
            raise ValueError("explore_fraction must be in [0, 1]")
        if (any(type(v) is not int for v in (self.batch_size, self.bootstrap, self.max_observations, self.max_candidates, self.seed))
                or self.batch_size < 1 or self.bootstrap < 2 or self.max_observations < 2 or self.max_candidates < 1):
            raise ValueError("positive batch size and at least two bootstrap observations required")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
