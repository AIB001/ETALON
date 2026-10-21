"""Cost and outcome-to-improvement diagnostics; no new learner or producer."""
from collections import defaultdict
from .diagnostics import exact_program


def cold_start_comparators(policy,row):
    from .transfer import unpack,_model_from_dict
    _,catalog,model=unpack(policy)
    if policy.get('training_model'):model=_model_from_dict(policy['training_model'],catalog)
    bindings=policy.get('workflow_bindings') or {}
    terminals=bindings.get('cold_start_decision_resources',catalog.decision_resources)
    weight=policy.get('evaluation_cost_weight',policy['cost_weight'])
    train_weight=policy.get('training_cost_weight',policy['cost_weight'])
    reports=[]
    for mode in ('cold_fixed_initial','cold_copy_best','cold_best_training_science'):
        if mode=='cold_fixed_initial':decision=catalog.initial_decision
        else:
            plan=exact_program(model,budget=policy['budget'],max_depth=0,
                cost_weight=0. if mode=='cold_best_training_science' else train_weight,
                world_resource_costs=policy['world_resource_costs'],decision_resources=terminals)
            decision=plan['program']['decision']
        paid=set(terminals[decision]);quote=sum(r.quoted_cost for r in catalog.resources if r.id in paid)
        if quote>policy['budget']+1e-12:decision,paid=catalog.abstention,set()
        cost=sum(row['resource_costs'][r] for r in paid)
        reports.append({'task_id':row['task_id'],'group':row['independence_group'],'mode':mode,'decision':decision,
            'paid_resources':sorted(paid),'scientific_loss':row['losses'][decision],
            'actual_resource_cost':cost,'objective':row['losses'][decision]+weight*cost,
            'queries':[],'selection_labels':'training only',
            'scope':'cold-start deployment scoring cost; no common-prefix acquisition assumed',
            'selection_development_cost':None,'amortization':'training/collection/preparation overhead not free; supply separate receipts'})
    return reports


def outcome_improvement_diagnostic(rows,catalog,*,cost_weight=.001):
    """Observed association and leave-group-out selection; not causal attribution.

    Missing cells stay explicit and receive no imputed gain or learning weight.
    Complete paired development groups can support a descriptive relation check.
    """
    baseline=catalog.initial_decision;cells=[]
    for row in rows:
        for probe in catalog.probes:
            for decision in catalog.decision_resources:
                if decision in {baseline,catalog.abstention}:continue
                base_resources=set(catalog.initial_resources)|set(catalog.decision_resources[baseline])
                revised=base_resources|set(probe.resources)|set(catalog.decision_resources[decision])
                base_loss=row['losses'].get(baseline);loss=row['losses'].get(decision)
                costs=[row['resource_costs'].get(r) for r in revised-base_resources]
                missing=base_loss is None or loss is None or any(c is None for c in costs)
                cells.append({'task_id':row['task_id'],'group':row['independence_group'],'probe_id':probe.id,
                    'outcome':row['outcomes'].get(probe.id),'decision':decision,
                    'scientific_gain':None if missing else base_loss-loss,
                    'incremental_cost':None if any(c is None for c in costs) else sum(costs),
                    'net_gain':None if missing else base_loss-loss-cost_weight*sum(costs),
                    'status':'missing_paired_evidence' if missing else 'measured_pair'})
    grouped=defaultdict(list)
    for cell in cells:grouped[(cell['probe_id'],cell['outcome'],cell['decision'])].append(cell)
    summaries=[]
    for (pid,outcome,decision),values in sorted(grouped.items(),key=lambda kv:str(kv[0])):
        groups=defaultdict(list)
        for value in values:
            if value['net_gain'] is not None:groups[value['group']].append(value['net_gain'])
        means={g:sum(v)/len(v) for g,v in groups.items()}
        summaries.append({'probe_id':pid,'outcome':outcome,'decision':decision,'group_net_gains':means,
            'mean_net_gain':sum(means.values())/len(means) if means else None,
            'positive_groups':sum(v>0 for v in means.values()),'negative_groups':sum(v<0 for v in means.values()),
            'missing_cells':sum(v['status']!='measured_pair' for v in values)})
    heldout=[]
    for cell in cells:
        donors=[c for c in cells if c['group']!=cell['group'] and c['probe_id']==cell['probe_id']
            and c['outcome']==cell['outcome'] and c['decision']==cell['decision'] and c['net_gain'] is not None]
        groups=defaultdict(list)
        for donor in donors:groups[donor['group']].append(donor['net_gain'])
        estimate=sum(sum(v)/len(v) for v in groups.values())/len(groups) if groups else None
        heldout.append({'task_id':cell['task_id'],'group':cell['group'],'probe_id':cell['probe_id'],
            'decision':cell['decision'],'other_group_count':len(groups),'other_group_mean_net_gain':estimate,
            'observed_net_gain':cell['net_gain'],'sign_generalized':None if estimate is None or cell['net_gain'] is None
                else (estimate>0)==(cell['net_gain']>0)})
    return {'schema':'etalon-outcome-improvement-diagnostic/1','cost_weight':cost_weight,'cells':cells,
        'conditional_group_summary':summaries,'leave_group_out_relation':heldout,
        'scope':'seen development association and group omission check; not independent test or causal mechanism evidence',
        'new_scientific_executions':0,'promotion':'none'}
