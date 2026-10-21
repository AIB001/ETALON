"""Read-only old-evidence support audit; no fitting, ledger writes or execution.

Uses a delivered SQLite archive read-only and maps original absolute artifact
paths into a verified delivery root. Future query outcomes enter only the
offline support report, never the historical online controller history.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import time

from etalon.active.schema import digest
from etalon.boundary.prism_v1 import file_identity
from etalon.research.fixed_pool import subset_rank_outcome
from etalon.research.support import support_report, fallback_action
from etalon.research.transfer import unpack, next_action


def replay(*,database,evidence_root,policy_path,rows_path,predictions_path,task_id,
           remote_root='/root/zx/prism-minimal-20261017'):
    started=time.perf_counter();cpu=time.process_time()
    database=Path(database).absolute();before=file_identity(database)
    root=Path(evidence_root).absolute();verified={}
    db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    def record(key):
        row=db.execute('SELECT body FROM metadata WHERE key=?',(key,)).fetchone()
        if not row:raise ValueError('missing original record: '+key)
        return json.loads(row[0])
    def artifact(aid):
        a=record('research:'+aid)
        relative=PurePosixPath(a['path']).relative_to(PurePosixPath(remote_root))
        path=root/str(relative)
        if file_identity(path)!={k:a[k] for k in ('sha256','bytes')}:
            raise ValueError('original evidence hash/size mismatch: '+aid)
        verified[aid]={'original_path':a['path'],'local_path':str(path),**file_identity(path),'role':a['role']}
        return path
    def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
    policy=load(policy_path);rows=load(rows_path);predictions=load(predictions_path)
    _,catalog,model=unpack(policy)
    if record('research:learnedpolicy-'+policy['policy_sha256'])!=policy:
        raise ValueError('policy differs from original H')
    if record('research:onlinepredictions-'+digest(predictions['run_id']))!=predictions:
        raise ValueError('predictions differ from original H')
    row_record=record('research:taskrows-'+digest(rows))
    if row_record['role']!='test' or row_record['rows_sha256']!=digest(rows):
        raise ValueError('old independent row identity/role changed')
    matches=[r for r in rows if r['task_id']==task_id]
    if len(matches)!=1:raise ValueError('one original task row required')
    row=matches[0];online=predictions['tasks'][task_id]
    for aid in sorted(set(row['evidence'])|set(model.evidence)):artifact(aid)
    scores={};charges={};score_receipts={}
    for rid,eid in row_record['executions'][task_id].items():
        candidates=[json.loads(r[0]) for r in db.execute(
            "SELECT body FROM metadata WHERE key LIKE 'research:pooladmission-%'")]
        candidates=[a for a in candidates if a['execution_id']==eid and a['task_id']==task_id and a['resource_id']==rid]
        if len(candidates)!=1:raise ValueError('one original score admission required')
        a=candidates[0];charge=record('budget:execution:'+eid)
        result_path=artifact(a['result_artifact_id']);result=load(result_path)
        if (charge['status']!='settled' or charge['cost']!=a['cost']['wall_second']
                or row['resource_costs'][rid]!=charge['cost'] or digest(result)!=a['result_sha256']):
            raise ValueError('original charge/score identity mismatch')
        measured_scores={}
        for outcome in result['outcomes']:
            ref=outcome['measurement']
            if ref:
                path=result_path.parent/ref['path']
                if file_identity(path)!={k:ref[k] for k in ('sha256','bytes')}:
                    raise ValueError('original native measurement changed')
                measured_scores[outcome['candidate_id']]=load(path)['total_kcal_mol']
            else:measured_scores[outcome['candidate_id']]=None
        art=record('research:'+a['result_artifact_id'])
        for parent in art['parents']:artifact(parent)
        scores[rid]={s['candidate_id']:s['score'] for s in a['slots']}
        if measured_scores!=scores[rid]:raise ValueError('admitted scores differ from native measurements')
        charges[rid]=charge['cost'];score_receipts[rid]={'execution_id':eid,'result_sha256':a['result_sha256']}
    reconstructed={}
    for probe in catalog.probes:
        resource=probe.resources[0];chunk=scores[resource]
        reconstructed[probe.id]=subset_rank_outcome(candidate_ids=tuple(chunk),
            vina={c:scores['r0'][c] for c in chunk},vinardo=chunk)['outcome']
    if reconstructed!=row['outcomes']:raise ValueError('row outcomes differ from actual scores')
    history=online['query_history'];visible={r['probe_id']:r['outcome'] for r in history}
    if any(reconstructed[k]!=v for k,v in visible.items()):raise ValueError('visible history differs from actual score')
    original_action=next_action(policy,history,initial_receipt=online['initial_receipt'])
    if original_action['decision']!=online['decision']:raise ValueError('historical controller replay changed')
    # Guard is a review-only adapter configuration, not a newly fitted policy.
    bounds={d:[0,1] for d in catalog.decision_resources}
    guard={'fallback_decision':catalog.initial_decision,'unknown_world_probability':None,'unknown_loss_bounds':bounds}
    guarded={**policy,'support_guard':guard}
    prefix=support_report(model,visible,bounds);full=support_report(model,reconstructed,bounds)
    scenarios={
        'offline_full_joint_already_paid':fallback_action(guarded,catalog,model,reconstructed,set(charges)),
        'counterfactual_missing_B_sufficient_quote':fallback_action(guarded,catalog,model,reconstructed,set()),
        'counterfactual_missing_B_insufficient_quote':fallback_action(
            {**guarded,'budget':299},catalog,model,reconstructed,set())}
    db.close()
    if file_identity(database)!=before:raise ValueError('read-only archive changed')
    return {'schema':'etalon-old-evidence-support-replay/1','task_id':task_id,
        'scope':'old R4 independent evidence is now seen; read-only replay, not a new independent test or refit',
        'inputs':{k:{'path':str(Path(p).absolute()),**file_identity(p)} for k,p in
            [('database',database),('policy',policy_path),('rows',rows_path),('predictions',predictions_path)]},
        'policy_sha256':policy['policy_sha256'],'model_sha256':digest(policy['model']),
        'row_sha256':digest(row),'rows_semantic_sha256':digest(rows),'original_row_role':row_record['role'],
        'actual_score_receipts':score_receipts,'verified_artifacts':verified,
        'actual_online_visible_prefix':visible,'historical_online_action':original_action,
        'visible_prefix_support':prefix,'offline_full_joint_support':full,
        'marginal_probability_mass':{k:sum(w.probability for w in model.worlds if w.outcomes[k]==v) for k,v in reconstructed.items()},
        'future_outcomes_passed_to_historical_controller':False,'review_only_guard':guard,'fallback_scenarios':scenarios,
        'costs':{'historical_online_path_wall_second':online['actual_path_cost'],'actual_resource_wall_second':charges,
            'offline_full_panel_wall_second':sum(charges.values()),'new_scientific_executions':0,'new_ledger_charges':0,
            'fallback_quotes_are_not_new_measured_receipts':True,
            'accounting':'already-paid B reuse has zero incremental quote, never zero total historical cost; missing B requires reserved quote; unaffordable B abstains'},
        'refitted':False,'promotion':'none','new_worlds_created':0,
        'runtime':{'python':sys.executable,'support_module':str(Path(sys.modules[support_report.__module__].__file__).absolute()),
            'wall_second':time.perf_counter()-started,'cpu_second':time.process_time()-cpu}}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('database','evidence-root','policy','rows','predictions','task','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    report=replay(database=args.database,evidence_root=args.evidence_root,policy_path=args.policy,
        rows_path=args.rows,predictions_path=args.predictions,task_id=args.task)
    report['source_pins']={str(Path(p).absolute()):file_identity(p) for p in
        (__file__,sys.modules[support_report.__module__].__file__,sys.modules[next_action.__module__].__file__)}
    with Path(args.output).open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps({'output':args.output,'prefix_mass':report['visible_prefix_support']['joint_support_mass'],
        'joint_mass':report['offline_full_joint_support']['joint_support_mass'],
        'marginal_mass':report['marginal_probability_mass'],'verified_artifacts':len(report['verified_artifacts'])}))
