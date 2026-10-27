"""Use normal trajectory feedback to select a concrete next anchor hypothesis."""
import argparse
import json
from pathlib import Path
import numpy as np
from snd1_paired_relaxation import pin


def analyze(result_path, output):
    result_path=Path(result_path);output=Path(output)
    result=json.loads(result_path.read_text());request=pin(result_path)
    if output.exists():
        previous=json.loads(output.read_text())
        if previous['input']!=request:
            raise ValueError('Follow-up input changed')
        for original in previous['originals']:
            if pin(original['path'])!=original:
                raise ValueError('Normal trajectory evidence changed')
        return previous
    rows=[]; originals=[]
    labels=['neither','ASN_only','ARG_only','dual']
    for row in result['rows']:
        if row['status']!='succeeded':continue
        path=Path(row['handle']).parent/'CONTACTS.json'
        contacts=json.loads(path.read_text());originals.append(pin(path))
        states=np.array([int(c['ASN281']['primary'])+2*int(c['ARG255']['primary']) for c in contacts])
        transition=np.zeros((4,4),dtype=int)
        for a,b in zip(states[:-1],states[1:]):transition[a,b]+=1
        late=states[-row['late_samples']:]
        rows.append(dict(mode=row['mode'],seed=row['seed'],late_counts={k:int(sum(late==i)) for i,k in enumerate(labels)},
            transition_counts=transition.tolist(),state_labels=labels,
            final_state=labels[states[-1]],quality=row['quality']['acceptable']))
    totals={k:sum(r['late_counts'][k] for r in rows) for k in labels}
    # A pooled frame count can hide opposite preferences between velocity
    # repeats. Keep method/seed attribution before nominating an anchor.
    by_mode={mode:{k:sum(r['late_counts'][k] for r in rows if r['mode']==mode)
                   for k in labels} for mode in sorted({r['mode'] for r in rows})}
    candidate_rows=[r for r in rows if r['mode']=='coupled' and r['quality']]
    preferences=[('ASN_only' if r['late_counts']['ASN_only']>r['late_counts']['ARG_only']
                  else 'ARG_only' if r['late_counts']['ARG_only']>r['late_counts']['ASN_only'] else None)
                 for r in candidate_rows]
    best=preferences[0] if len(preferences)>=2 and preferences[0] is not None and len(set(preferences))==1 else None
    action=result['decision']['action']
    proposal=dict(question='Can one persistent anchor support a better candidate than enforcing the dual conjunction?',
        candidate_anchor=best,old_primary_endpoint_unchanged=True,
        next_method=dict(operation='select_relaxed',objective=('ASN281' if best=='ASN_only' else 'ARG255') if best else None,
                         status='hypothesis_for_a_new_objective_not_adopted'),
        alternatives=['ASN281','ARG255'] if best is None else [],
        seed_preferences=preferences,
        choice_basis='Nominate one anchor only when eligible repeat preferences agree; otherwise preserve both',
        discrimination='Compare alternate anchoring with matched native dynamics and an explicit new protocol',
        synthesis='No chemical edit made; a later edit must carry its own route and chemical identity')
    answer=dict(schema='snd1-anchor-feedback-followup/1',input=request,originals=originals,rows=rows,
        totals=totals,totals_by_mode=by_mode,triggered_by=action,proposal=proposal,
        interpretation='Correlated short-trajectory states; counts are not independent samples or binding affinity',
        new_MD=0,new_proposals_for_completed_comparison=0)
    output.write_text(json.dumps(answer,indent=2)+'\n')
    return answer


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();r=analyze(args.result,args.output)
    print(json.dumps({'totals':r['totals'],'proposal':r['proposal']}))
