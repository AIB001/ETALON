"""Group-separated finite policy learning and measured-cost offline evaluation.

This module does not execute PRISM or invent missing counterfactuals. A row is a
scientific task, not a pose/seed/pair. Independence is an externally reviewed
family assignment. Probabilities are empirical forecasts, not mechanism beliefs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from etalon.active.schema import canonical, digest, finite
from .calibration import empirical_model
from .diagnostics import JointModel, Probe, Resource, World, condition, exact_program


@dataclass(frozen=True)
class GroupSplit:
    tasks: dict[str, dict[str, str]]
    grouping_evidence: str
    protocol_sha256: str

    def __post_init__(self):
        if not self.grouping_evidence or len(self.protocol_sha256) != 64:
            raise ValueError('reviewed grouping evidence and frozen protocol hash required')
        groups, families, roles = {}, {}, set()
        for task, entry in self.tasks.items():
            role, group, family = (entry[k] for k in ('role', 'group', 'family'))
            if not task or not group or not family or role not in {'train', 'calibration', 'test'}:
                raise ValueError('explicit task/family/group and split role required')
            if group in groups and groups[group] != (role, family):
                raise ValueError('group crosses role or family boundary')
            if family in families and families[family] != group:
                raise ValueError('one family cannot be split into nominally independent groups')
            groups[group], families[family] = (role, family), group
            roles.add(role)
        if roles != {'train', 'calibration', 'test'}:
            raise ValueError('train/calibration/test groups must all be frozen')
        if sum(role == 'train' for role, _ in groups.values()) < 2:
            raise ValueError('at least two independent training groups required')

    @property
    def sha256(self):
        return digest(asdict(self))

    def task_ids(self, role):
        return {task for task, item in self.tasks.items() if item['role'] == role}


@dataclass(frozen=True)
class Catalog:
    probes: tuple[Probe, ...]
    resources: tuple[Resource, ...]
    decision_resources: dict[str, tuple[str, ...]]
    loss_definition: str
    cost_unit: str
    abstention: str
    initial_decision: str
    initial_resources: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.loss_definition or not self.cost_unit:
            raise ValueError('scientific loss and common cost unit required')
        if self.abstention not in self.decision_resources or self.decision_resources[self.abstention]:
            raise ValueError('explicit zero-resource abstention required')
        if self.initial_decision not in self.decision_resources:
            raise ValueError('common initial workflow decision required')
        if len(set(self.initial_resources)) != len(self.initial_resources) or not set(self.initial_resources) <= {r.id for r in self.resources}:
            raise ValueError('initial resources must bind the same priced catalog')

    @property
    def sha256(self):
        return digest(asdict(self))


def checked_rows(rows: list[dict], split: GroupSplit, role: str, catalog: Catalog) -> list[dict]:
    """Reject missing cells/identity leakage; numerical zero is never imputed."""
    ids = [r['task_id'] for r in rows]
    if len(set(ids)) != len(ids) or set(ids) != split.task_ids(role):
        raise ValueError('complete unique tasks for exactly the requested split role required')
    probes = {p.id: p for p in catalog.probes}
    resources = {r.id: r for r in catalog.resources}
    kinds = {r['source_kind'] for r in rows}
    if len(kinds) != 1 or not kinds <= {'synthetic_fixture', 'measured'}:
        raise ValueError('fixture and measured rows cannot be mixed')
    for row in rows:
        if row['sample_unit'] != 'task' or row['independence_group'] != split.tasks[row['task_id']]['group']:
            raise ValueError('task/group identity mismatch; poses/seeds are not independent tasks')
        if row['loss_definition'] != catalog.loss_definition or row['cost_unit'] != catalog.cost_unit:
            raise ValueError('loss definition/cost unit mismatch')
        if set(row['outcomes']) != set(probes) or set(row['losses']) != set(catalog.decision_resources):
            raise ValueError('complete paired query outcomes and terminal losses required')
        if set(row['resource_costs']) != set(resources):
            raise ValueError('complete measured resource costs required; unknown is not zero')
        evidence = set(row['evidence'])
        if not evidence:
            raise ValueError('row evidence required')
        for field, expected in [('outcome_evidence', probes), ('loss_evidence', catalog.decision_resources),
                                ('cost_evidence', resources)]:
            refs = row[field]
            if set(refs) != set(expected) or any(not v or not set(v) <= evidence for v in refs.values()):
                raise ValueError('every outcome/loss/cost needs declared evidence')
        for pid, value in row['outcomes'].items():
            if value not in probes[pid].outcomes:
                raise ValueError('undeclared outcome; failure/missing require explicit categories')
        for value in row['losses'].values(): finite(value, 'scientific loss', minimum=0)
        for rid, value in row['resource_costs'].items():
            finite(value, 'measured resource cost', minimum=0)
            if value > resources[rid].quoted_cost + 1e-12:
                raise ValueError('actual resource cost exceeds frozen quote; amendment required')
    return sorted(rows, key=lambda r: r['task_id'])


def fit_policy(rows: list[dict], *, split: GroupSplit, catalog: Catalog,
               budget: float, max_depth: int, cost_weight: float,
               static_order: tuple[str, ...] = (), calibration_prefix: tuple[str, ...] = (),
               calibration_selection: dict | None = None, support_guard: dict | None = None,
               workflow_bindings: dict | None = None, evaluation_cost_weight: float | None = None) -> dict:
    """Group-balanced empirical risk minimization over finite contingent programs.

    Training includes scientific losses AND measured query/terminal resource costs.
    No calibration/test row is accepted here. This learned policy is the exact
    finite nonmyopic baseline itself, not evidence of a new optimizer's advantage.
    """
    rows = checked_rows(rows, split, 'train', catalog)
    if support_guard is not None:
        if cost_weight!=.001 or (evaluation_cost_weight is not None and evaluation_cost_weight!=.001):
            raise ValueError('R4b support policy fixes planning and comparison weight at .001')
        evaluation_cost_weight=.001
        if calibration_selection is not None and set(calibration_selection['cost_weights'])!={.001}:
            raise ValueError('R4b calibration cannot retune the fixed planning cost weight')
    probes = {p.id: p for p in catalog.probes}
    for schedule in (static_order, calibration_prefix):
        prior = set()
        for pid in schedule:
            if pid not in probes or pid in prior or not set(probes[pid].depends_on) <= prior:
                raise ValueError('static/calibration sequence must be frozen in dependency order')
            prior.add(pid)
    if len(static_order) > max_depth: raise ValueError('static schedule exceeds common depth')
    if calibration_selection is not None:
        alphas, weights = calibration_selection['mixture_weights'], calibration_selection['cost_weights']
        if not alphas or not weights or 0. not in alphas:
            raise ValueError('calibration grid needs unchanged-model control and cost candidates')
        for alpha in alphas:
            finite(alpha, 'mixture weight', minimum=0)
            if alpha >= 1: raise ValueError('mixture must retain positive training support')
        for weight in weights: finite(weight, 'cost weight', minimum=0)
        finite(calibration_selection['selection_cost_weight'], 'common selection tradeoff', minimum=0)
        if calibration_selection['probability_selection_metric'] != 'group_mean_brier':
            raise ValueError('only predeclared group Brier selection implemented')
    model = empirical_model(rows=rows, probes=catalog.probes, resources=catalog.resources,
        evidence=tuple(sorted({e for r in rows for e in r['evidence']})),
        development_tasks=tuple(sorted(split.task_ids('train'))),
        heldout_tasks=tuple(sorted(split.task_ids('calibration') | split.task_ids('test'))),
        loss_definition=catalog.loss_definition, cost_unit=catalog.cost_unit, group_balanced=True)
    if rows[0]['source_kind'] == 'synthetic_fixture':
        model = replace(model, probability_basis='synthetic_fixture')
    costs = {digest(r): r['resource_costs'] for r in rows}
    initial_quote = sum(r.quoted_cost for r in catalog.resources if r.id in catalog.initial_resources)
    finite(budget, 'total path budget', minimum=0)
    if initial_quote > budget: raise ValueError('common initial resources exceed path budget')
    params = dict(budget=budget-initial_quote, max_depth=max_depth, cost_weight=cost_weight,
                  acquired_resources=catalog.initial_resources,
                  world_resource_costs=costs, decision_resources=catalog.decision_resources)
    learned = exact_program(model, **params)
    body = {'schema': 'etalon-group-policy/1', 'split': asdict(split), 'split_sha256': split.sha256,
        'catalog': asdict(catalog), 'catalog_sha256': catalog.sha256,
        'training_rows_sha256': digest(rows), 'training_tasks': [r['task_id'] for r in rows],
        'training_groups': sorted({r['independence_group'] for r in rows}),
        'origin': 'learned_group_balanced_loss_and_measured_cost_erm',
        'model': model.as_dict(), 'world_resource_costs': costs,
        'budget': budget, 'max_depth': max_depth, 'cost_weight': cost_weight,
        'static_order': static_order, 'calibration_prefix': calibration_prefix,
        'calibration_selection': calibration_selection,
        'program': learned['program'], 'program_id': learned['program_id'],
        'training_expected_initial_cost': sum(w.probability * sum(costs[w.id][r]
            for r in catalog.initial_resources) for w in model.worlds),
        'probability_status': 'empirical_uncalibrated_pending_group_heldout_assessment',
        'baseline_equivalence': 'phi is exact finite nonmyopic ERM in this implementation',
        'source_kind': rows[0]['source_kind'], 'promotion': 'none'}
    if support_guard is not None:
        from .support import validate_guard
        validate_guard(support_guard,catalog);body['support_guard']=support_guard
    if workflow_bindings is not None:
        from .workflows import validate_bindings
        validate_bindings(workflow_bindings,catalog);body['workflow_bindings']=workflow_bindings
    if evaluation_cost_weight is not None:
        finite(evaluation_cost_weight,'common evaluation tradeoff',minimum=0)
        body['evaluation_cost_weight']=evaluation_cost_weight
    return {**body, 'policy_sha256': digest(body)}


def unpack(policy):
    body = {k: v for k, v in policy.items() if k != 'policy_sha256'}
    if policy.get('policy_sha256') != digest(body): raise ValueError('policy hash mismatch')
    split = GroupSplit(**policy['split'])
    c = policy['catalog']
    catalog = Catalog(tuple(Probe(**p) for p in c['probes']), tuple(Resource(**r) for r in c['resources']),
        {k: tuple(v) for k, v in c['decision_resources'].items()}, c['loss_definition'], c['cost_unit'],
        c['abstention'], c['initial_decision'], tuple(c.get('initial_resources', ())))
    if split.sha256 != policy['split_sha256'] or catalog.sha256 != policy['catalog_sha256']:
        raise ValueError('split/catalog digest mismatch')
    if policy.get('support_guard') is not None:
        from .support import validate_guard
        validate_guard(policy['support_guard'],catalog)
    if policy.get('workflow_bindings') is not None:
        from .workflows import validate_bindings
        validate_bindings(policy['workflow_bindings'],catalog)
    m = policy['model']
    model = JointModel(m['hypotheses'], tuple(World(**w) for w in m['worlds']), catalog.probes,
        catalog.resources, m['probability_basis'], tuple(m['assumptions']),
        m['independent_loss_definition'], m['cost_unit'], tuple(m['evidence']))
    return split, catalog, model


def save_policy(policy: dict, path: Path):
    unpack(policy)
    # Exclusive create: a frozen policy is never silently replaced.
    with Path(path).open('x', encoding='utf-8') as stream: stream.write(canonical(policy) + '\n')


def load_policy(path: Path) -> dict:
    policy = json.loads(Path(path).read_text(encoding='utf-8'))
    unpack(policy)
    return policy


def record_policy(journal, policy: dict) -> str:
    """Admit a learned policy only after the existing journal verifies its evidence."""
    _, _, model = unpack(policy)
    with journal.store.connection(write=True) as db:
        artifacts=journal._artifacts(db, model.evidence, diagnostic_inputs=True)
        if any(a.bindings.get('development_split_sha256',policy['split_sha256'])!=policy['split_sha256'] for a in artifacts):
            raise ValueError('seen development release belongs to a different split')
        return journal._put(db, 'learnedpolicy-' + policy['policy_sha256'], policy)


def _model_from_dict(m, catalog):
    return JointModel(m['hypotheses'], tuple(World(**w) for w in m['worlds']), catalog.probes,
        catalog.resources, m['probability_basis'], tuple(m['assumptions']),
        m['independent_loss_definition'], m['cost_unit'], tuple(m['evidence']))


def _recompile(policy, model, catalog, weight, **provenance):
    initial_quote = sum(r.quoted_cost for r in catalog.resources if r.id in catalog.initial_resources)
    program = exact_program(model, budget=policy['budget']-initial_quote,
        max_depth=policy['max_depth'], cost_weight=weight, acquired_resources=catalog.initial_resources,
        world_resource_costs=policy['world_resource_costs'], decision_resources=catalog.decision_resources)
    body = {k:v for k,v in policy.items() if k != 'policy_sha256'}
    body.update(model=model.as_dict(), cost_weight=weight, program=program['program'],
                program_id=program['program_id'], **provenance)
    return {**body, 'policy_sha256': digest(body)}


def calibrate_policy(policy: dict, rows: list[dict]) -> tuple[dict, dict]:
    """Predeclared empirical-support mixture calibration and loss/cost selection.

    Reweight existing train worlds toward supported calibration joint frequencies.
    No world, terminal loss, or cost is manufactured. Calibration outcomes select
    the mixture by Brier; calibration losses select a frozen cost tradeoff grid.
    All resulting scores are selection scores, NOT independent test estimates.
    """
    split, catalog, model = unpack(policy)
    if policy.get('training_policy_sha256'): raise ValueError('calibration cannot be silently repeated/refit')
    grid = policy.get('calibration_selection')
    if not grid: raise ValueError('calibration selection grid must be frozen at training')
    rows = checked_rows(rows, split, 'calibration', catalog)
    if any(r['source_kind'] != policy['source_kind'] for r in rows):
        raise ValueError('fixture and measured calibration cannot be mixed')
    group_counts = Counter(r['independence_group'] for r in rows)
    frequencies = defaultdict(float)
    for row in rows:
        frequencies[digest(row['outcomes'])] += 1/(len(group_counts)*group_counts[row['independence_group']])
    train_mass = defaultdict(float)
    for w in model.worlds: train_mass[digest(w.outcomes)] += w.probability
    supported_mass = sum(v for k,v in frequencies.items() if k in train_mass)
    target = {w.id: (frequencies[digest(w.outcomes)] / supported_mass * w.probability / train_mass[digest(w.outcomes)]
                      if supported_mass else w.probability) for w in model.worlds}
    provenance = dict(training_policy_sha256=policy['policy_sha256'], training_model=policy['model'],
        training_cost_weight=policy['cost_weight'],
        calibration_rows_sha256=digest(rows), calibration_groups=sorted(group_counts),
        probability_status='calibration_selected_support_preserving_mixture; independent_test_pending')
    candidates = []
    for alpha in sorted(set(grid['mixture_weights'])):
        fitted = replace(model, worlds=tuple(replace(w, probability=(1-alpha)*w.probability+alpha*target[w.id])
                                            for w in model.worlds),
            evidence=tuple(sorted(set(model.evidence) | {e for r in rows for e in r['evidence']})),
            assumptions=(*model.assumptions, 'calibration supported-joint-frequency mixture='+str(alpha),
                         'calibration used for parameter selection; its scores are not independent test estimates'))
        candidate = _recompile(policy, fitted, catalog, policy['cost_weight'], mixture_weight=alpha, **provenance)
        assessment = probability_assessment(candidate, rows)
        scores = assessment['group_scores']
        brier = sum(r['brier'] for r in scores)/len(scores) if all(r['brier'] is not None for r in scores) else None
        candidates.append((alpha, fitted, assessment, brier))
    alpha, fitted, _, _ = min(candidates, key=lambda item: (math.inf if item[3] is None else item[3],item[0]))
    curve = []
    final_candidates = []
    for weight in sorted(set(grid['cost_weights'])):
        candidate = _recompile(policy, fitted, catalog, weight, mixture_weight=alpha, **provenance)
        evaluation = evaluate_policies(candidate, rows, role='calibration')
        phi = [r for r in evaluation['group_means'] if r['mode'] == 'phi']
        loss = sum(r['scientific_loss'] for r in phi)/len(phi)
        cost = sum(r['actual_resource_cost'] for r in phi)/len(phi)
        objective = loss + grid['selection_cost_weight'] * cost
        curve.append({'cost_weight':weight,'group_mean_scientific_loss':loss,'group_mean_actual_cost':cost,
            'common_selection_objective':objective,'group_results':evaluation['group_means'],
            'policy_sha256':candidate['policy_sha256']})
        final_candidates.append((objective,cost,weight,candidate))
    # Coordinator-frozen tie rule: smallest cost weight, not favorable path cost.
    final = min(final_candidates,key=lambda x:(x[0],x[2]))[3]
    report = {'schema':'etalon-group-calibration-selection/1','training_policy_sha256':policy['policy_sha256'],
        'final_policy_sha256':final['policy_sha256'],'calibration_rows_sha256':digest(rows),
        'selected_mixture_weight':alpha,'selected_cost_weight':final['cost_weight'],
        'supported_calibration_joint_mass':supported_mass,'unsupported_joint_mass':1-supported_mass,
        'probability_candidates':[{'mixture_weight':a,'group_mean_brier':b,'assessment':s} for a,_,s,b in candidates],
        'loss_cost_curve':curve,'selection_cost_weight':grid['selection_cost_weight'],
        'scope':'calibration fitting/model-selection scores; final test is the independent evaluation',
        'new_worlds_created':0,'scientific_losses_fitted_on':'training groups only; calibration losses select cost weight',
        'mechanism_posterior':None,'promotion':'none'}
    return final, report


def _solve(policy, model, catalog, observed, acquired, *, depth=None, myopic=False):
    resources = {r.id: r for r in catalog.resources}
    # Reservation accounting is identical across all controllers, including after
    # cheaper-than-quoted actual execution. Quotes are public, actual future costs are not.
    used_quote = sum(resources[r].quoted_cost for r in acquired if not resources[r].existing_artifact_id)
    return exact_program(model, budget=max(0., policy['budget'] - used_quote),
        max_depth=policy['max_depth'] if depth is None else depth, cost_weight=policy['cost_weight'],
        observed=observed, acquired_resources=tuple(acquired), myopic=myopic,
        world_resource_costs=policy['world_resource_costs'], decision_resources=catalog.decision_resources)


def next_action(policy: dict, history: list[dict], *, mode='phi', static_order=None,
                initial_receipt: dict | None = None) -> dict:
    """Executable controller: sees only ordered query receipts, never a task loss row.

    Receipts bind policy/query/outcome and actual resource costs. A real adapter must
    verify their evidence via ResearchJournal before calling this pure controller.
    """
    _, catalog, model = unpack(policy)
    planning_policy = policy
    if mode in {'copy_best', 'static'} and policy.get('training_model'):
        model = _model_from_dict(policy['training_model'], catalog)
        planning_policy = {**policy, 'cost_weight':policy['training_cost_weight']}
    if mode not in {'phi0', 'phi', 'copy_best', 'static', 'myopic', 'nonmyopic'}:
        raise ValueError('unknown baseline')
    frozen_order = tuple(policy['static_order'])
    if static_order is not None and tuple(static_order) != frozen_order:
        raise ValueError('static order differs from frozen policy comparator')
    static_order = frozen_order
    if len(history) > policy['max_depth']: raise ValueError('history exceeds frozen depth')
    if mode in {'phi0', 'copy_best'} and history:
        raise ValueError('constant baseline cannot acquire diagnostic outcomes')
    if mode == 'static' and [r['probe_id'] for r in history] != list(static_order[:len(history)]):
        raise ValueError('history differs from fixed static schedule')
    probes, resources = {p.id: p for p in catalog.probes}, {r.id: r for r in catalog.resources}
    observed = {}
    acquired = {r.id for r in catalog.resources if r.existing_artifact_id} | set(catalog.initial_resources)
    spent = 0.
    if catalog.initial_resources:
        if (not initial_receipt or not initial_receipt.get('evidence')
                or initial_receipt.get('policy_sha256') != policy['policy_sha256']
                or set(initial_receipt.get('resource_costs', {})) != set(catalog.initial_resources)):
            raise ValueError('common initial resource receipt required before decisions')
        for rid, value in initial_receipt['resource_costs'].items():
            finite(value, 'initial measured cost', minimum=0)
            if value > resources[rid].quoted_cost + 1e-12: raise ValueError('initial receipt exceeds quote')
            spent += value
    frozen_node = policy['program']
    for index, receipt in enumerate(history):
        pid = receipt['probe_id']
        if receipt['policy_sha256'] != policy['policy_sha256'] or pid not in probes or pid in observed:
            raise ValueError('receipt policy/query identity mismatch')
        probe = probes[pid]
        if mode in {'phi', 'nonmyopic'}:
            if not frozen_node or frozen_node['kind'] != 'query' or frozen_node['probe_id'] != pid:
                raise ValueError('receipt is not the next query of the frozen program')
            branch = frozen_node['branches'].get(receipt['outcome'])
            frozen_node = branch['program'] if branch else None
        elif mode == 'myopic':
            proposed = _solve(policy, model, catalog, observed, acquired,
                depth=policy['max_depth']-index, myopic=True).get('program')
            if not proposed or proposed['kind'] != 'query' or proposed['probe_id'] != pid:
                raise ValueError('receipt is not the next myopic query')
        if not set(probe.depends_on) <= set(observed) or receipt['outcome'] not in probe.outcomes:
            raise ValueError('receipt dependency/outcome mismatch')
        fresh = set(probe.resources) - acquired
        reserved = sum(resources[r].quoted_cost for r in (acquired | fresh) if not resources[r].existing_artifact_id)
        if reserved > policy['budget'] + 1e-12:
            raise ValueError('query receipt violates reservation budget')
        if set(receipt['resource_costs']) != fresh or not receipt.get('evidence'):
            raise ValueError('receipt must expose only newly charged resources with evidence')
        for rid, value in receipt['resource_costs'].items():
            finite(value, 'actual receipt cost', minimum=0)
            if value > resources[rid].quoted_cost + 1e-12: raise ValueError('receipt exceeds quote')
            spent += value
        acquired.update(fresh); observed[pid] = receipt['outcome']
    if spent > policy['budget'] + 1e-12: raise ValueError('receipt history exceeds budget')
    unsupported = condition(model, observed)['status'] == 'model_challenge'
    unavailable=any(observed.get(pid) in outcomes for pid,outcomes in
        policy.get('support_guard',{}).get('unavailable_probe_outcomes',{}).items())
    if unavailable:
        from .support import fallback_action
        result=fallback_action(policy,catalog,model,observed,acquired)
        result['fallback_reason']='prepared_workflow_unavailable'
        return result
    if unsupported and mode != 'static':
        if policy.get('support_guard'):
            from .support import fallback_action
            return fallback_action(policy,catalog,model,observed,acquired)
        return {'kind': 'stop', 'decision': catalog.abstention, 'status': 'unsupported_history',
                'resources_acquired': [], 'observed': observed}
    if mode == 'phi0':
        if history: raise ValueError('initial workflow cannot acquire diagnostic outcomes')
        decision = catalog.initial_decision
        fresh = set(catalog.decision_resources[decision]) - acquired
        quote = sum(resources[r].quoted_cost for r in fresh)
        initial_quote = sum(resources[r].quoted_cost for r in acquired if not resources[r].existing_artifact_id)
        if quote > policy['budget'] - initial_quote + 1e-12:
            decision, fresh, quote = catalog.abstention, set(), 0.
        plan = {'kind': 'stop', 'decision': decision, 'resources_acquired': sorted(fresh),
                'quoted_incremental_cost': quote}
    elif mode == 'copy_best':
        if history: raise ValueError('copy-best cannot acquire diagnostic outcomes')
        plan = _solve(planning_policy, model, catalog, {}, acquired, depth=0)['program']
    elif mode == 'static':
        if len(set(static_order)) != len(static_order) or not set(static_order) <= set(probes):
            raise ValueError('invalid static schedule')
        prior = set()
        for pid in static_order:
            if not set(probes[pid].depends_on) <= prior: raise ValueError('static dependency order')
            prior.add(pid)
        if [r['probe_id'] for r in history] != list(static_order[:len(history)]):
            raise ValueError('history differs from fixed static schedule')
        remaining = list(static_order[len(history):policy['max_depth']])
        pid = remaining[0] if remaining else None
        fresh = set(probes[pid].resources) - acquired if pid else set()
        quote = sum(resources[r].quoted_cost for r in fresh)
        used = sum(resources[r].quoted_cost for r in acquired if not resources[r].existing_artifact_id)
        if pid and quote <= policy['budget'] - used + 1e-12:
            plan = {'kind': 'query', 'probe_id': pid, 'resources_acquired': sorted(fresh),
                    'quoted_incremental_cost': quote}
        elif unsupported:
            if policy.get('support_guard'):
                from .support import fallback_action
                return fallback_action(policy,catalog,model,observed,acquired)
            plan = {'kind':'stop','decision':catalog.abstention,'resources_acquired':[],'branches':{}}
        else: plan = _solve(planning_policy, model, catalog, observed, acquired, depth=0)['program']
    else:
        # Execute the learned frozen tree; myopic replans one query at a time.
        if mode in {'phi', 'nonmyopic'}:
            plan = policy['program']
            for receipt in history:
                if plan['kind'] != 'query' or plan['probe_id'] != receipt['probe_id']:
                    raise ValueError('history does not follow frozen learned program')
                branch = plan['branches'].get(receipt['outcome'])
                if branch is None: raise ValueError('unsupported learned branch')
                plan = branch['program']
        else:
            plan = _solve(policy, model, catalog, observed, acquired,
                depth=policy['max_depth'] - len(history), myopic=True)['program']
    return {**plan, 'status': 'unsupported_history' if unsupported else 'supported', 'observed': observed}


def probability_assessment(policy: dict, rows: list[dict], *, role='calibration',
                           visible_prefix: tuple[str, ...] | None = None, release: dict | None = None) -> dict:
    """Leave-group-out proper scores; zero probability gives infinite log loss.

    Assessment does not refit the policy. Prefix selection must be protocol-frozen,
    not chosen after seeing calibration results. No mechanism labels are inferred.
    """
    split, catalog, model = unpack(policy)
    if role not in {'calibration','test'}: raise ValueError('probability assessment requires held-out groups')
    if role == 'test' and (not release or release.get('policy_sha256') != policy['policy_sha256']
            or release.get('split_sha256') != split.sha256 or not release.get('evidence')):
        raise ValueError('test probabilities require independent release bound to frozen policy')
    if visible_prefix is not None and tuple(visible_prefix) != tuple(policy['calibration_prefix']):
        raise ValueError('assessment prefix differs from frozen protocol')
    visible_prefix = tuple(policy['calibration_prefix'])
    rows = checked_rows(rows, split, role, catalog)
    if any(r['source_kind'] != policy['source_kind'] for r in rows):
        raise ValueError('fixture policy and measured evaluation cannot be mixed')
    probes = {p.id: p for p in catalog.probes}
    previous = set()
    for pid in visible_prefix:
        if pid not in probes or pid in previous or not set(probes[pid].depends_on) <= previous:
            raise ValueError('invalid frozen visible prefix')
        previous.add(pid)
    results = []
    for row in rows:
        observed = {p: row['outcomes'][p] for p in visible_prefix}
        support = condition(model, observed)['world_indices']
        mass = sum(model.worlds[i].probability for i in support)
        for probe in catalog.probes:
            if probe.id in observed: continue
            if not support:
                results.append({'task_id': row['task_id'], 'group': row['independence_group'],
                    'probe_id': probe.id, 'support_failure': True, 'brier': None,
                    'log_loss': None, 'infinite_log_loss': True, 'probabilities': None})
                continue
            probabilities = {v: sum(model.worlds[i].probability for i in support
                if model.worlds[i].outcomes[probe.id] == v) / mass for v in probe.outcomes}
            actual = row['outcomes'][probe.id]; p = probabilities[actual]
            results.append({'task_id': row['task_id'], 'group': row['independence_group'],
                'probe_id': probe.id, 'probabilities': probabilities, 'support_failure': p == 0,
                'brier': sum((q - (v == actual)) ** 2 for v, q in probabilities.items()),
                'log_loss': -math.log(p) if p else None, 'infinite_log_loss': p == 0})
    if not results: raise ValueError('assessment requires an unobserved query')
    groups = defaultdict(list)
    for result in results: groups[result['group']].append(result)
    means = []
    for group, records in sorted(groups.items()):
        infinite = any(r['infinite_log_loss'] for r in records)
        means.append({'group': group, 'support_failure_rate': sum(r['support_failure'] for r in records)/len(records),
            'brier': sum(r['brier'] for r in records)/len(records) if all(r['brier'] is not None for r in records) else None,
            'log_loss': None if infinite else sum(r['log_loss'] for r in records)/len(records),
            'infinite_log_loss': infinite})
    joint=[]
    for row in rows:
        from .support import support_report
        joint.append({'task_id':row['task_id'],'group':row['independence_group'],
            **support_report(model,row['outcomes'],policy.get('support_guard',{}).get('unknown_loss_bounds',{}))})
    return {'schema': 'etalon-group-probability-assessment/1', 'policy_sha256': policy['policy_sha256'],
        'full_panel_joint_support':joint,'joint_assessment_scope':'offline acquired panel; never supplied to online decisions',
        'rows_sha256': digest(rows), 'role': role, 'visible_prefix': visible_prefix, 'predictions': results,
        'groups': sorted({r['independence_group'] for r in rows}),
        'group_scores': means,
        'calibration_claim': 'held-out predictive scores only; no automatic calibrated/mechanism claim',
        'scope': 'descriptive at the available number of independent groups; policy unchanged'}


def evaluate_policies(policy: dict, rows: list[dict], *, role: str,
                      static_order: tuple[str, ...] | None = None, release: dict | None = None) -> dict:
    """Offline replay of complete observed counterfactual panels, not new executions.

    The evaluator alone accesses terminal losses. The controller receives only the
    requested query result. Independent test release must bind this exact policy.
    """
    split, catalog, _ = unpack(policy)
    if role not in {'calibration', 'test'}: raise ValueError('evaluation requires held-out groups')
    if role == 'test' and (not release or release.get('policy_sha256') != policy['policy_sha256']
            or release.get('split_sha256') != split.sha256 or not release.get('evidence')):
        raise ValueError('test labels require independent release bound to frozen policy and split')
    rows = checked_rows(rows, split, role, catalog)
    if any(r['source_kind'] != policy['source_kind'] for r in rows):
        raise ValueError('fixture policy and measured evaluation cannot be mixed')
    probes = {p.id: p for p in catalog.probes}
    resource_map = {r.id: r for r in catalog.resources}
    reports = []
    for row in rows:
        for mode in ('phi0', 'phi', 'copy_best', 'static', 'myopic', 'nonmyopic'):
            history = []
            spent = sum(row['resource_costs'][r] for r in catalog.initial_resources)
            initial_receipt = {'policy_sha256': policy['policy_sha256'],
                'resource_costs': {r: row['resource_costs'][r] for r in catalog.initial_resources},
                'evidence': sorted({e for r in catalog.initial_resources for e in row['cost_evidence'][r]})}
            paid = {r.id for r in catalog.resources if r.existing_artifact_id} | set(catalog.initial_resources)
            while True:
                action = next_action(policy, history, mode=mode, static_order=static_order,
                                     initial_receipt=initial_receipt)
                fresh = set(action.get('resources_acquired', ())) - paid
                if action['kind'] == 'query':
                    pid = action['probe_id']
                    assert fresh == set(probes[pid].resources) - paid
                    costs = {r: row['resource_costs'][r] for r in fresh}
                    history.append({'policy_sha256': policy['policy_sha256'], 'probe_id': pid,
                        'outcome': row['outcomes'][pid], 'resource_costs': costs,
                        'evidence': row['outcome_evidence'][pid]})
                paid.update(fresh); spent += sum(row['resource_costs'][r] for r in fresh)
                if action['kind'] == 'stop': break
            decision = action['decision']
            assert spent <= policy['budget'] + 1e-12
            reports.append({'task_id': row['task_id'], 'group': row['independence_group'], 'mode': mode,
                'queries': [r['probe_id'] for r in history], 'visible_outcomes': action['observed'],
                'decision': decision, 'status': action['status'], 'scientific_loss': row['losses'][decision],
                'actual_resource_cost': spent, 'cost_unit': catalog.cost_unit,
                'common_initial_cost': sum(row['resource_costs'][r] for r in catalog.initial_resources),
                'objective': row['losses'][decision] + policy.get('evaluation_cost_weight',policy['cost_weight']) * spent,
                'paid_resources': sorted(r for r in paid if not resource_map[r].existing_artifact_id)})
    grouped = defaultdict(list)
    for result in reports: grouped[(result['mode'], result['group'])].append(result)
    by_group = [{'mode': mode, 'group': group, **{field: sum(r[field] for r in values)/len(values)
        for field in ('scientific_loss', 'actual_resource_cost', 'objective')}}
        for (mode, group), values in sorted(grouped.items())]
    cold=[]
    if 'evaluation_cost_weight' in policy:
        from .transfer_checks import cold_start_comparators
        cold=[report for row in rows for report in cold_start_comparators(policy,row)]
    return {'schema': 'etalon-group-transfer-evaluation/1', 'policy_sha256': policy['policy_sha256'],
        'rows_sha256': digest(rows), 'role': role, 'results': reports, 'group_means': by_group,
        'static_order': policy['static_order'], 'release': release, 'promotion': 'none',
        'new_producer_executions': 0, 'accounting': 'counterfactual offline measured-resource replay; not separate live runs',
        'evaluation_cost_weight':policy.get('evaluation_cost_weight',policy['cost_weight']),
        'cold_start_comparators':cold,
        'training_cost_amortization': 'not included; supply actual collection/training receipts separately',
        'baseline_equivalence': policy['baseline_equivalence']}
