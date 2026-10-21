"""Frozen resource-to-workflow bindings over the existing fixed-pool protocol."""
from __future__ import annotations
from .fixed_pool import subset_rank_outcome


def legacy_bindings():
    return {'candidate_count':6,
        'resources':{'r0':{'scorer':'vina','candidate_indices':list(range(6)),'receptor_key':'original'},
            'r1':{'scorer':'vinardo','candidate_indices':[0,1,2],'receptor_key':'original'},
            'r2':{'scorer':'vinardo','candidate_indices':[3,4,5],'receptor_key':'original'}},
        'decision_scores':{'fixed_pool_vina':['r0'],'fixed_pool_vinardo':['r1','r2'],'abstain':[]},
        'probes':{'q1':{'reference_resources':['r0'],'comparison_resources':['r1'],'candidate_indices':[0,1,2]},
                  'q2':{'reference_resources':['r0'],'comparison_resources':['r2'],'candidate_indices':[3,4,5]}}}


def validate_bindings(binding,catalog):
    n=binding['candidate_count']
    if type(n) is not int or n<2:raise ValueError('fixed candidate count required')
    resources=binding['resources'];probes={p.id:p for p in catalog.probes}
    if set(resources)!={r.id for r in catalog.resources} or set(binding['probes'])!=set(probes):
        raise ValueError('workflow bindings must cover catalog resources and probes exactly')
    if set(binding['decision_scores'])!=set(catalog.decision_resources):
        raise ValueError('workflow score bindings must cover every terminal')
    for r in resources.values():
        indices=r['candidate_indices']
        if (r['scorer'] not in {'vina','vinardo'} or not r['receptor_key'] or not indices
                or len(indices)!=len(set(indices)) or any(type(i) is not int or not 0<=i<n for i in indices)):
            raise ValueError('invalid fixed-pool resource binding')
    def coverage(ids):
        if len(set(ids))!=len(ids) or not set(ids)<=set(resources):raise ValueError('unknown/duplicate score resource')
        indices=[i for rid in ids for i in resources[rid]['candidate_indices']]
        if len(indices)!=len(set(indices)):raise ValueError('ambiguous duplicate candidate scores')
        return set(indices)
    for decision,ids in binding['decision_scores'].items():
        if not set(ids)<=set(catalog.decision_resources[decision]):raise ValueError('terminal scores require unpaid resources')
        if decision==catalog.abstention:
            if ids:raise ValueError('abstention has no score resource')
        elif coverage(ids)!=set(range(n)):raise ValueError('terminal must cover every frozen candidate exactly once')
    query_owned=set()
    for pid,b in binding['probes'].items():
        ref,cmp=b['reference_resources'],b['comparison_resources'];indices=set(b['candidate_indices'])
        if not indices or not indices<=coverage(ref) or indices!=coverage(cmp):
            raise ValueError('probe comparison partition differs from reference')
        if not set(ref)<=set(catalog.initial_resources):raise ValueError('query reference must belong to paid initial prefix')
        if set(cmp)!=set(probes[pid].resources) or set(cmp)&set(catalog.initial_resources):
            raise ValueError('probe resource binding differs from priced acquisition')
        if query_owned&set(cmp):raise ValueError('overlapping query score resources need an explicit receipt-sharing extension')
        query_owned.update(cmp)
    cold=binding.get('cold_start_decision_resources',catalog.decision_resources)
    if set(cold)!=set(catalog.decision_resources):raise ValueError('cold-start terminals must be explicit for every decision')
    for decision,ids in cold.items():
        if len(ids)!=len(set(ids)) or not set(ids)<=set(catalog.decision_resources[decision]) or not set(binding['decision_scores'][decision])<=set(ids):
            raise ValueError('cold-start resource binding omits needed scores or invents resources')
    return binding


def bindings_for(policy_or_config,catalog):
    return validate_bindings(policy_or_config.get('workflow_bindings') or legacy_bindings(),catalog)


def merged_scores(resources,ids):
    output={}
    for rid in ids:
        for candidate,value in resources[rid].items():
            if candidate in output:raise ValueError('candidate scored twice in one terminal/query side')
            output[candidate]=value
    return output


def probe_outcome(binding,pid,resources,candidate_ids):
    b=binding['probes'][pid];ids=tuple(candidate_ids[i] for i in b['candidate_indices'])
    reference=merged_scores(resources,b['reference_resources']);comparison=merged_scores(resources,b['comparison_resources'])
    return subset_rank_outcome(candidate_ids=ids,vina={k:reference.get(k) for k in ids},
                               vinardo={k:comparison.get(k) for k in ids})['outcome']


def terminal_scores(binding,decision,resources,candidate_ids,abstention):
    if decision==abstention:return {c:None for c in candidate_ids}
    result=merged_scores(resources,binding['decision_scores'][decision])
    if set(result)!=set(candidate_ids):raise ValueError('terminal scores do not cover the frozen candidates')
    return result


def validate_receptor_binding(request,resource,task_freeze):
    """Root declares availability/coupling; this checks exact frozen request identity."""
    state=task_freeze['receptor_states'][resource['receptor_key']]
    if not state.get('availability') or not state.get('coupling') or not state.get('preparation_evidence'):
        raise ValueError('receptor state requires reviewed availability/coupling/preparation evidence')
    if request['input_identity']!=state['input_identity']:
        raise ValueError('request receptor/preparation identity differs from frozen state')
    for key in ('receptor_source','scorer_receptor','scorer_manifest'):
        expected=state['inputs'][key]
        if any(request['inputs'][key][f]!=expected[f] for f in ('sha256','bytes')):
            raise ValueError('prepared receptor input bytes differ from frozen state')
    if any(request['evaluation'][k]!=state['grid'][k] for k in ('center_A','size_A','spacing_A')):
        raise ValueError('request grid/frame differs from frozen receptor state')
