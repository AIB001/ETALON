"""Exact finite adaptive experimental-design baseline over explicit JOINT outcomes.

Worlds include nuisance draws. Marginal independence is never assumed. Complexity is
exponential: this is a bounded reference solver, not a novel scalable search algorithm.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from math import isclose

from etalon.active.schema import digest, finite
from etalon.active.budget import fits_budget
from .schema import Record, nonempty


@dataclass(frozen=True)
class Resource:
    id: str
    quoted_cost: float
    unit: str
    existing_artifact_id: str | None = None

    def __post_init__(self):
        nonempty(self.id); nonempty(self.unit)
        finite(self.quoted_cost, 'quoted incremental cost', minimum=0)
        if self.existing_artifact_id and self.quoted_cost != 0:
            raise ValueError('existing artifact reuse cannot charge its original execution again')


@dataclass(frozen=True)
class Probe:
    id: str
    primitive: str
    bindings: dict[str, str]
    outcomes: tuple[str, ...]
    resources: tuple[str, ...]
    depends_on: tuple[str, ...] = ()

    def __post_init__(self):
        nonempty(self.id)
        if self.primitive not in {'file_audit', 'prepare', 'measure', 'reference_query', 'resample'}:
            raise ValueError('unregistered diagnostic primitive')
        if not self.outcomes or len(set(self.outcomes)) != len(self.outcomes):
            raise ValueError('probe needs explicit distinct outcomes, including failure/missing when modeled')
        if len(set(self.resources)) != len(self.resources):
            raise ValueError('one resource identity cannot be quoted twice within a probe')


@dataclass(frozen=True)
class World:
    id: str
    hypothesis: str
    probability: float
    outcomes: dict[str, str]
    losses: dict[str, float]

    def __post_init__(self):
        finite(self.probability, 'world probability', minimum=0)
        if self.probability <= 0 or not self.losses:
            raise ValueError('world needs positive mass and independent-objective decision losses')
        for value in self.losses.values(): finite(value, 'loss', minimum=0)


@dataclass(frozen=True)
class JointModel(Record):
    hypotheses: dict[str, str]
    worlds: tuple[World, ...]
    probes: tuple[Probe, ...]
    resources: tuple[Resource, ...]
    probability_basis: str
    assumptions: tuple[str, ...]
    independent_loss_definition: str
    cost_unit: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self):
        if self.probability_basis not in {'synthetic_fixture', 'declared_hypothesis', 'estimated_from_evidence'}:
            raise ValueError('probability provenance is required')
        if self.probability_basis == 'estimated_from_evidence' and not self.evidence:
            raise ValueError('estimated probabilities require evidence')
        if not self.worlds or not self.assumptions: raise ValueError('joint worlds and assumptions required')
        nonempty(self.independent_loss_definition)
        if not isclose(sum(w.probability for w in self.worlds), 1., abs_tol=1e-12):
            raise ValueError('joint world masses must sum to one')
        for items in (self.worlds, self.probes, self.resources):
            if len({v.id for v in items}) != len(items): raise ValueError('duplicate finite-model identities')
        probes = {p.id: p for p in self.probes}
        resources = {r.id: r for r in self.resources}
        decisions = set(self.worlds[0].losses)
        if any(r.unit != self.cost_unit for r in self.resources):
            raise ValueError('reference solver requires one explicit cost unit; units cannot be summed')
        for world in self.worlds:
            if world.hypothesis not in self.hypotheses or set(world.outcomes) != set(probes) or set(world.losses) != decisions:
                raise ValueError('every world must bind the same full joint probe and decision space')
            if any(outcome not in probes[key].outcomes for key, outcome in world.outcomes.items()):
                raise ValueError('world predicts an undeclared outcome')
        available = set()
        while len(available) < len(probes):
            ready = {p.id for p in self.probes if set(p.depends_on) <= available} - available
            if not ready: raise ValueError('cyclic or unknown probe dependency')
            available |= ready
        if any(not set(p.resources) <= set(resources) for p in self.probes):
            raise ValueError('probe refers to an unpriced resource')


def condition(model: JointModel, observed: dict[str, str]) -> dict:
    """A zero-support joint result is an external-model challenge, not forced attribution."""
    probes = {p.id: p for p in model.probes}
    if not set(observed) <= set(probes): raise ValueError('observation names an unknown probe')
    indices = tuple(i for i, w in enumerate(model.worlds)
                    if all(w.outcomes[k] == value for k, value in observed.items()))
    mass = sum(model.worlds[i].probability for i in indices)
    posterior = {}
    for i in indices:
        world = model.worlds[i]
        posterior[world.hypothesis] = posterior.get(world.hypothesis, 0) + world.probability / mass
    return {'status': 'conditioned' if indices else 'model_challenge', 'world_indices': indices,
            'posterior': posterior, 'observed': observed, 'model_id': model.id,
            'scope': 'conditional on explicit finite model; external mechanisms remain possible'}


def exact_program(model: JointModel, *, budget: float, max_depth: int, cost_weight: float,
                  observed: dict[str, str] | None = None, acquired_resources: tuple[str, ...] = (),
                  myopic: bool = False,
                  world_resource_costs: dict[str, dict[str, float]] | None = None,
                  decision_resources: dict[str, tuple[str, ...]] | None = None) -> dict:
    """Exhaustive adaptive-tree baseline with hard per-branch budget, shared cost paid once.

    `myopic=True` is the depth-one decision-value baseline. It may discard XOR queries.
    The exact search prunes only exhausted depth, type/dependency conflicts and budget.
    """
    finite(budget, 'remaining budget', minimum=0); finite(cost_weight, 'loss per cost unit', minimum=0)
    if type(max_depth) is not int or not 0 <= max_depth <= 8 or len(model.probes) > 12:
        raise ValueError('reference enumeration bounded to 12 probes and depth 0..8')
    observed = dict(observed or {})
    conditioned = condition(model, observed)
    if conditioned['status'] == 'model_challenge':
        return {**conditioned, 'program': None, 'decision': None}
    resources = {r.id: r for r in model.resources}
    if decision_resources is not None:
        if set(decision_resources) != set(model.worlds[0].losses):
            raise ValueError('every terminal decision requires an explicit resource binding')
        if any(len(set(rs)) != len(rs) or not set(rs) <= set(resources)
               for rs in decision_resources.values()):
            raise ValueError('invalid terminal resource binding')
        if not any(not rs for rs in decision_resources.values()):
            raise ValueError('a zero-resource abstention decision is required for budget exhaustion')
    # Optional measured costs retain outcome/cost correlation in the joint worlds.
    # Quotes remain hard per-branch reservation bounds, not measured costs.
    if world_resource_costs is not None:
        if set(world_resource_costs) != {w.id for w in model.worlds}:
            raise ValueError('measured cost rows must bind every world')
        for costs in world_resource_costs.values():
            if set(costs) != set(resources):
                raise ValueError('complete resource costs required; no counterfactual imputation')
            for rid, value in costs.items():
                finite(value, 'measured resource cost', minimum=0)
                if not fits_budget(value, resources[rid].quoted_cost):
                    raise ValueError('measured cost exceeds reservation quote')
    initial = set(acquired_resources) | {r.id for r in model.resources if r.existing_artifact_id}
    if not initial <= set(resources): raise ValueError('unknown acquired resource')
    used = set(observed)
    for p in model.probes:
        if p.id in used:
            if not set(p.depends_on) <= used: raise ValueError('observed probe lacks its dependency')
            initial.update(p.resources)
    stats = {'expanded_states': 0, 'evaluated_extensions': 0}

    @lru_cache(None)
    def search(indices, queried, paid, depth):
        stats['expanded_states'] += 1
        mass = sum(model.worlds[i].probability for i in indices)
        def average(f): return sum(model.worlds[i].probability * f(model.worlds[i]) for i in indices) / mass
        spent = sum(resources[r].quoted_cost for r in set(paid) - initial)
        terminals = []
        for decision in model.worlds[0].losses:
            fresh = set((decision_resources or {}).get(decision, ())) - set(paid)
            quote = sum(resources[r].quoted_cost for r in fresh)
            if not fits_budget(quote, budget-spent): continue
            cost = (average(lambda w: sum(world_resource_costs[w.id][r] for r in fresh))
                    if world_resource_costs is not None else quote)
            risk = average(lambda w: w.losses[decision])
            stop = {'kind': 'stop', 'decision': decision, 'expected_loss': risk,
                    'expected_additional_cost': cost, 'worst_additional_cost': quote,
                    'objective': risk + cost_weight * cost, 'branches': {}}
            if decision_resources is not None:
                stop.update(resources_acquired=sorted(fresh), quoted_incremental_cost=quote)
            terminals.append(stop)
        best = min(terminals, key=lambda s: (s['objective'], s['decision']))
        if depth == 0: return best
        for probe in sorted(model.probes, key=lambda p: p.id):
            if probe.id in queried or not set(probe.depends_on) <= set(queried): continue
            fresh = set(probe.resources) - set(paid)
            cost = sum(resources[r].quoted_cost for r in fresh)
            if not fits_budget(cost, budget-spent): continue
            stats['evaluated_extensions'] += 1
            groups = {}
            for i in indices: groups.setdefault(model.worlds[i].outcomes[probe.id], []).append(i)
            branches = {}
            measured_cost = (average(lambda w: sum(world_resource_costs[w.id][r] for r in fresh))
                             if world_resource_costs is not None else cost)
            expected_loss, expected_cost, worst_cost = 0., measured_cost, cost
            for outcome, members in sorted(groups.items()):
                child = search(tuple(members), tuple(sorted((*queried, probe.id))),
                               tuple(sorted(set(paid) | fresh)), 0 if myopic else depth - 1)
                probability = sum(model.worlds[i].probability for i in members) / mass
                branches[outcome] = {'probability': probability, 'program': child}
                expected_loss += probability * child['expected_loss']
                expected_cost += probability * child['expected_additional_cost']
                worst_cost = max(worst_cost, cost + child['worst_additional_cost'])
            objective = expected_loss + cost_weight * expected_cost
            if objective < best['objective'] - 1e-12:
                best = {'kind': 'query', 'probe_id': probe.id, 'bindings': probe.bindings,
                        'resources_acquired': sorted(fresh), 'quoted_incremental_cost': cost,
                        'expected_loss': expected_loss, 'expected_additional_cost': expected_cost,
                        'worst_additional_cost': worst_cost, 'objective': objective, 'branches': branches}
        return best

    program = search(conditioned['world_indices'], tuple(sorted(used)), tuple(sorted(initial)), max_depth)
    body = {'schema': 'etalon-diagnostic-program/1', 'model_id': model.id, 'program': program,
            'budget': budget, 'cost_unit': model.cost_unit, 'cost_weight': cost_weight,
            'max_depth': max_depth, 'observed': observed, 'initial_resources': sorted(initial),
            'solver': 'myopic_decision_value' if myopic else 'exact_finite_adaptive_enumeration',
            'probability_basis': model.probability_basis,
            'scope': 'optimal only within supplied finite joint worlds, probe catalog, depth and loss'}
    if world_resource_costs is not None:
        body['world_resource_costs_sha256'] = digest(world_resource_costs)
        body['cost_basis'] = 'measured joint world costs; quotes enforce conservative branch caps'
    if decision_resources is not None:
        body['decision_resources'] = decision_resources
    return {**body, 'program_id': 'program-' + digest(body), 'search_statistics': stats}
