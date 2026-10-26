"""Read a current work object and explicit optional experience queries.

No fixed task IDs, counts, execution or inferred cross-workspace scope. Full
counter originals are verified/read when requested, without echoing their bytes.
"""
import argparse
import base64
import json
from pathlib import Path

from etalon.research.research_context import load_entry
from etalon.research.research_segment import read_work
from etalon.research.continuation import read_content
from etalon.research.evidence import read_evidence
from etalon.research.improvement import retrieve_experience


def read(entry,branch_id,*,counter_bodies=False):
    entry=Path(entry).absolute();metadata=json.loads(entry.read_text())
    ws,checkpoint_ref=load_entry(entry);checkpoint=read_content(ws,checkpoint_ref)
    current=read_work(entry,branch_id=branch_id)
    counters=[]
    for ref in checkpoint['counter_refs']:
        row={'ref':ref,'body_read':False}
        if counter_bodies:
            offset=0;count=0
            while True:
                item=read_evidence(ws,ref,view='artifact',offset=offset,limit=32768)
                if item['verification']['artifact']!='verified':raise ValueError('Counter original not verified')
                fragment=item['fragment'];count+=len(base64.b64decode(fragment['content']))
                offset=fragment['next_offset']
                if offset is None:break
            row.update(body_read=True,bytes_read=count)
        counters.append(row)
    experiences=[]
    for query in metadata.get('experience_queries',[]):
        other=metadata['related_entries'][query['entry_name']]
        if not Path(other).is_absolute():other=str(entry.parent/other)
        query_ws,_=load_entry(other)
        result=retrieve_experience(query_ws,source_ids=query['source_ids'],capability_id=query['capability_id'],conditions=query['conditions'])
        # Superseded history remains addressable; a head is not an adoption.
        replaced={r['record_id'] for item in result['matches'] for r in item['experience']['supersedes']}
        heads=[item for item in result['matches'] if item['ref']['record_id'] not in replaced]
        experiences.append({'entry':other,'conditions':query['conditions'],'heads':heads,
            'history':[{'ref':item['ref'],'outcome':item['experience']['outcome']} for item in result['matches']],
            'next_cursor':result.get('next_cursor'),'selection':result['selection']})
    return {'work':current,'branches':[{'branch_id':b['branch_id'],'state':b['state']} for b in checkpoint['branches']],
        'counter_originals':counters,'experiences':experiences,'pending_handles':checkpoint['pending_handles'],
        'execution_performed':False,'new_RSI_proposal':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('entry',type=Path);parser.add_argument('--branch',required=True)
    parser.add_argument('--counter-bodies',action='store_true')
    args=parser.parse_args();print(json.dumps(read(args.entry,args.branch,counter_bodies=args.counter_bodies),ensure_ascii=False,separators=(',',':')))
