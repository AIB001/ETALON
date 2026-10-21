"""Explicit finite-support challenges and bounded unknowns, without invented worlds."""
from __future__ import annotations

from etalon.active.schema import finite
from .diagnostics import condition


def loss_interval(value, bounds, *, state='unmeasured'):
    """A domain bound is not an observation or a probability model."""
    if len(bounds)!=2:raise ValueError('two declared loss bounds required')
    lower,upper=bounds
    finite(lower,'loss lower bound',minimum=0);finite(upper,'loss upper bound',minimum=lower)
    if state not in {'unmeasured','unavailable_label','failed_measurement','outside_model','measured'}:
        raise ValueError('explicit missing/measurement state required')
    if value is None:
        if state=='measured':raise ValueError('missing loss cannot be measured')
        return {'value':None,'lower':lower,'upper':upper,'state':state,'bound_basis':'declared_domain_only'}
    if state!='measured':raise ValueError('unknown loss cannot carry an imputed point value')
    finite(value,'measured loss',minimum=lower)
    if value>upper:raise ValueError('measured loss exceeds declared domain')
    return {'value':value,'lower':value,'upper':value,'state':state,'bound_basis':'observed_scientific_loss'}


def validate_guard(guard,catalog):
    if guard.get('fallback_decision')!=catalog.initial_decision:
        raise ValueError('support fallback must be the declared common cheap baseline')
    if set(guard.get('unknown_loss_bounds',{}))!=set(catalog.decision_resources):
        raise ValueError('every terminal needs declared unknown loss bounds')
    for bounds in guard['unknown_loss_bounds'].values():loss_interval(None,bounds)
    if guard.get('unknown_world_probability') is not None:
        raise ValueError('support guard does not invent external-world probability')
    probes={p.id:p for p in catalog.probes}
    for pid,outcomes in guard.get('unavailable_probe_outcomes',{}).items():
        if pid not in probes or not set(outcomes)<=set(probes[pid].outcomes):
            raise ValueError('unavailability gate must use declared preparation probe outcomes')


def support_report(model,observed,bounds):
    probes={p.id:p for p in model.probes}
    if any(k not in probes or v not in probes[k].outcomes for k,v in observed.items()):
        raise ValueError('observations must use frozen probe outcomes')
    result=condition(model,observed)
    mass=sum(model.worlds[i].probability for i in result['world_indices'])
    return {'observed':dict(observed),'joint_support_mass':mass,
        'model_challenge':mass==0,'complete_joint_observed':set(observed)==set(probes),
        'unobserved_probes':sorted(set(probes)-set(observed)),
        'marginal_support':{k:sum(w.probability for w in model.worlds if w.outcomes[k]==v)>0
                            for k,v in observed.items()},
        'unknown_losses':{d:loss_interval(None,b,state='outside_model') for d,b in bounds.items()} if mass==0 else {},
        'external_world_probability':None,'new_worlds_created':0,
        'scope':'support of the supplied visible prefix; no inference from unobserved outcomes'}


def fallback_action(policy,catalog,model,observed,acquired):
    guard=policy['support_guard'];validate_guard(guard,catalog)
    resources={r.id:r for r in catalog.resources}
    decision=guard['fallback_decision']
    fresh=set(catalog.decision_resources[decision])-set(acquired)
    used=sum(resources[r].quoted_cost for r in acquired if not resources[r].existing_artifact_id)
    quote=sum(resources[r].quoted_cost for r in fresh if not resources[r].existing_artifact_id)
    exhausted=used+quote>policy['budget']+1e-12
    if exhausted:decision,fresh,quote=catalog.abstention,set(),0.
    return {'kind':'stop','decision':decision,'status':'unsupported_history',
        'resources_acquired':sorted(fresh),'quoted_incremental_cost':quote,'observed':dict(observed),
        'fallback_reason':'baseline_unaffordable' if exhausted else 'finite_joint_support_failure',
        'scientific_loss':None,'model_challenge':support_report(model,observed,guard['unknown_loss_bounds'])}


def bounded_panel(row,*,decisions,loss_bounds):
    """Retain incomplete panels for review; never turn them into empirical worlds."""
    if set(row['losses'])!=set(decisions) or set(loss_bounds)!=set(decisions):
        raise ValueError('retain every declared decision, including missing values')
    cells={d:loss_interval(row['losses'][d],loss_bounds[d],state=row['loss_states'][d]) for d in decisions}
    return {'task_id':row['task_id'],'losses':cells,
        'eligible_for_existing_complete_row_learner':all(c['state']=='measured' for c in cells.values()),
        'scope':'loss completeness only; full identity/evidence/outcome/cost checks still required',
        'new_worlds_created':0}
