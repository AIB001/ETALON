"""Create a consistent immutable research archive index, including pre-controller failures."""
from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path
from etalon.active.schema import canonical,digest
from etalon.boundary.prism_v1 import file_identity


def archive(database: Path, output: Path):
    output.mkdir(parents=True,exist_ok=False)
    snapshot=output/'research.sqlite'
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as source:
        with sqlite3.connect(snapshot) as destination:source.backup(destination)
    with sqlite3.connect(snapshot.resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=[(key,json.loads(body)) for key,body in db.execute('SELECT key,body FROM metadata ORDER BY key')]
        events=db.execute('SELECT COUNT(*),MAX(sequence) FROM events').fetchone()
    index=[];artifacts=[];costs=[];research_states=[]
    for key,body in rows:
        if key.startswith('research:'):
            index.append({'record_id':key.removeprefix('research:'),'body_sha256':digest(body)})
        if key.startswith('research:artifact-'):
            path=Path(body['path'])
            if file_identity(path)!={'sha256':body['sha256'],'bytes':body['bytes']}:
                raise ValueError('archive referenced artifact changed: '+str(path))
            artifacts.append({'record_id':key.removeprefix('research:'),**body})
        if key.startswith('budget:execution:'):
            costs.append({'record_id':key,'execution_id':body['execution_id'],'status':body['status'],
                          'known_cost':body['cost'] if body['status']=='settled' else None,
                          'reservation':body['reservation'],'unit':body['unit'],
                          'provenance':body['provenance'],'receipt':body['receipt']})
        if key.startswith('research:controllerfeedback-'):research_states.append({'record_id':key,**body})
    body={'schema':'etalon-research-archive/1','database_snapshot':{'path':str(snapshot.resolve()),**file_identity(snapshot)},
          'research_records':index,'artifacts':artifacts,'all_execution_cost_records':costs,
          'event_count':events[0],'event_cutoff':events[1],'feedback_states':research_states,
          'H_scope':'full immutable journal snapshot plus every artifact identity and all execution receipts, including failed smoke before controller registration',
          'hypotheses_are_not_H':True,'new_scientific_executions':0}
    (output/'archive.json').write_text(canonical(body)+'\n',encoding='utf-8')
    return body


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=archive(a.database,a.output)
    print(json.dumps({'research_records':len(r['research_records']),'executions':len(r['all_execution_cost_records']),
                      'events':r['event_count'],'new_scientific_executions':0}))
