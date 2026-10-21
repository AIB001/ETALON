"""Freeze a descriptive same-molecule preparation contrast, with missing phenol controls.

Consumes only producer diagnostic evidence already admitted to a research campaign.
It never reads an external validation structure or creates a mechanistic/affinity label.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from etalon.active import CampaignStore
from etalon.active.schema import canonical,digest
from etalon.research import ResearchJournal,Experiment,Observation,Contrast,Coupling


def freeze(database: Path, admissions: list[Path], output: Path, *, task_id: str, added_atom_map: int):
    if output.exists():raise FileExistsError(output)
    if len(admissions)!=2:raise ValueError('this bounded case requires two preparation conditions')
    journal=ResearchJournal(CampaignStore(database))
    summaries=[];molecules=set();methods=set();configs=[]
    for path in admissions:
        receipt=json.loads(path.read_text())
        ex=journal.get(receipt['experiment_id'])
        if ex['task_id']!=task_id:raise ValueError('wrong task')
        result_path=Path(receipt['result_path']);result=json.loads(result_path.read_text())
        if digest(result)!=receipt['result_sha256']:raise ValueError('producer result changed')
        request=json.loads((result_path.parent/'request.json').read_text())
        rows=[journal.get(identifier) for identifier in receipt['observation_ids']]
        measured=[r for r in rows if r['status']=='measured' and 'geometry' in r['values']]
        if not measured:raise ValueError('no qualified geometry observations in this condition')
        methods.update(r['measurement_method_id'] for r in measured)
        molecules.update(r['identity']['molecule_id'] for r in result['outcomes'] if r['status']=='SUCCEEDED')
        # Older admissions retain only full producer geometry. Derive a NEW summary
        # from that original evidence; never rewrite the archived observations.
        counts=[]
        for row in measured:
            count=sum(pair['atom_map']==added_atom_map
                      for pair in row['values']['geometry']['polar_pairs_within_3_5A'])
            summary_counts=row['values'].get('polar_pairs_by_atom_map')
            if summary_counts is not None and summary_counts.get(str(added_atom_map),0)!=count:
                raise ValueError('stored derived count differs from original producer geometry')
            counts.append(count)
        evidence=tuple(sorted({a for row in rows for a in row['evidence']}))
        # Recorded as a descriptive correlated-slot mean, never an independent replicate estimate.
        summary=Experiment(task_id,'reanalysis',{**ex['bindings'],'condition_action_id':request['action_id']},
            evidence,'correlated-slot-mean/1',digest({'source_experiment':receipt['experiment_id'],'atom_map':added_atom_map}),
            ('added_atom_polar_proximity_count',),Coupling(evidence,(),(),('same fixed core and source uncertainty; slots not iid',)))
        journal.record(summary)
        observation=Observation(summary.id,'measured',{'added_atom_polar_proximity_count':sum(counts)/len(counts)},
            {'added_atom_polar_proximity_count':'mean heavy_atom_pairs_within_3.5_angstrom'},evidence,
            measurement_method_id=measured[0]['measurement_method_id'])
        journal.record(observation)
        summaries.append({'experiment_id':summary.id,'observation_id':observation.id,'source_experiment_id':receipt['experiment_id'],
            'wire_action_id':request['action_id'],'slot_counts':counts,'qualified_slots':len(counts),
            'denominator':result['counts'],'scope':'descriptive proxy; no independent uncertainty or preference inferred',
            'configuration':{'parameters':request['parameters'],'intervention':request['intervention'],'coupling':request['coupling']}})
        configs.append(request)
    if len(molecules)!=1 or len(methods)!=1:
        raise ValueError('bounded comparison requires same molecule and same fixed measurement definition')
    contrast=Contrast(task_id,'Does mapped preparation change the added polar-atom proximity proxy?',
        {'00':None,'01':None,'10':summaries[0]['observation_id'],'11':summaries[1]['observation_id']},
        'added_atom_polar_proximity_count',Coupling((),('receptor_state_id','coordinate_frame_id'),(),
        ('same catechol graph, correlated phenol fixed core; conditions differ by declared mapping/preparation',)),next(iter(methods)))
    journal.record(contrast)
    decision={'decision':'retain_current_pending_independent_validation','revision_id':None,'delta':{},
        'status':'proposed_unresolved_not_promoted','executable_requests':configs,
        'request_configuration_sha256':digest(configs),
        'reason':'geometry differences alone do not identify design/preparation/evaluator cause or establish improved independent loss'}
    report={'schema':'etalon-round3-diagnostic-freeze/1','task_id':task_id,'molecule_id':next(iter(molecules)),
        'preparation_conditions':summaries,'contrast':journal.factorial(contrast.id),'contrast_id':contrast.id,
        'axes':{'D0':'phenol original graph (not measured here)','D1':'same catechol chemical graph in both measured cells',
                'E0_E1':'declared attachment/core mapping and preparation condition; not two chemical designs'},
        'decision':decision,'independent_validation_accessed':False,'affinity_endpoint':'unavailable',
        'scientific_judgment':'unresolved','budget':journal.store.balance()}
    report['freeze_sha256']=digest(report)
    with journal.store.connection(write=True) as db:journal._put(db,'decision-'+report['freeze_sha256'],report)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:stream.write(canonical(report)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--admission',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--task',required=True)
    parser.add_argument('--added-atom-map',type=int,required=True)
    args=parser.parse_args();report=freeze(args.database,args.admission,args.output,task_id=args.task,added_atom_map=args.added_atom_map)
    print(json.dumps({'report':str(args.output),'freeze_sha256':report['freeze_sha256'],'scientific_judgment':'unresolved'}))
