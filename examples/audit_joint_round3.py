"""Audit real staged receipts, denominators, seed allocation and shared costs.

The scorer timing is a subset of action time, never an extra ledger charge.
No labels, coordinates of independent references or affinity data are read.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from etalon.active import CampaignStore
from etalon.active.schema import canonical,digest
from etalon.boundary.prism_v1 import file_identity


def audit(database:Path,admissions:list[Path]):
    store=CampaignStore(database)
    with store.connection() as db:
        executions=[json.loads(r[0]) for r in db.execute("SELECT body FROM metadata WHERE key LIKE 'budget:execution:%'")]
    stages={}
    for row in executions:
        stage=row['provenance'].get('stage','unspecified')
        group=stages.setdefault(stage,{'settled_wall_second':0.,'pending_reservation_wall_second':0.,
                                      'unknown_actual_cost_executions':[],'execution_ids':[]})
        group['execution_ids'].append(row['execution_id'])
        if row['status']=='settled':group['settled_wall_second']+=row['cost']
        else:
            group['pending_reservation_wall_second']+=row['reservation']
            group['unknown_actual_cost_executions'].append(row['execution_id'])
    rows=[];seen=set()
    for path in admissions:
        admission=json.loads(path.read_text());execution=admission['execution_id']
        if execution in seen:continue
        seen.add(execution)
        result_path=Path(admission['result_path']);result=json.loads(result_path.read_text())
        if digest(result)!=admission['result_sha256']:raise ValueError('changed producer result')
        for ref in result['artifacts']:
            actual=file_identity(result_path.parent/ref['path'])
            if actual!={k:ref[k] for k in ('sha256','bytes')}:raise ValueError('changed producer artifact')
        request=json.loads((result_path.parent/'request.json').read_text())
        scores=[];raw_hashes=[];seed_values=[];transforms=[];eligible=0;failed_scores=0
        for slot in result['outcomes']:
            seed_values.append(slot.get('seed'));transforms.append(slot.get('transform_id'))
            if slot.get('raw'):raw_hashes.append(slot['raw']['sha256'])
            if slot.get('pose_admitted'):eligible+=1
            if slot.get('measurement'):
                measurement=json.loads((result_path.parent/slot['measurement']['path']).read_text())
                scorer=measurement.get('vina_score',{})
                if scorer.get('status')=='failed':failed_scores+=1
                if scorer.get('cost'):scores.append(scorer['cost'])
        setup_path=result_path.parent/'scorer-setup-cost.json'
        setup=json.loads(setup_path.read_text()) if setup_path.is_file() else None
        rows.append({'execution_id':execution,'stage':admission['stage'],'status':result['status'],
            'admission':{'path':str(path.resolve()),**file_identity(path)},'result_path':str(result_path),
            'counts':result['counts'],'pose_eligible':eligible,'score_failures':failed_scores,
            'base_seed_values_requested':list(range(request['seed'],request['seed']+request['parameters']['base_conformers'])),
            'observed_seed_values':sorted({s for s in seed_values if s is not None}),
            'declared_transforms':[t['id'] for t in request['pose_constraints']['transforms']],
            'observed_transforms':sorted({t for t in transforms if t is not None}),
            'raw_pose_files':len(raw_hashes),'unique_raw_pose_hashes':len(set(raw_hashes)),
            'byte_duplicate_slots':len(raw_hashes)-len(set(raw_hashes)),
            'action_cost':result['cost'],'scorer_setup_cost_subset':setup,
            'per_pose_score_cost_subsets':scores,
            'E0_only_counterfactual_cost':None,
            'cost_scope':'combined action charged once; score timing subsets do not measure a separate E0-only run'})
    return {'schema':'etalon-r3b-execution-audit/1','python':sys.executable,
            'consumer_import':__import__('etalon').__file__,'budget':store.balance(),
            'stages':stages,'executions':rows,'active_affinity_observations':len(store.observations()),
            'slot_comparison':'P0 four seeds xidentity; P1 first seed xfour transforms; only first identity paired',
            'seed_independence':'seed values are algorithmic variation; shared core/receptor errors remain correlated',
            'setup_test_validation_overhead':'external evidence, separately reported; not assumed zero'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--admission',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    report=audit(a.database,a.admission)
    with a.output.open('x',encoding='utf-8') as f:f.write(canonical(report)+'\n')
    print(json.dumps({'budget':report['budget'],'executions_audited':len(report['executions'])}))
