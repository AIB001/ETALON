"""Read a current work object and explicit optional experience queries.

No fixed task IDs, counts, execution or inferred cross-workspace scope. Full
counter originals are verified/read when requested, without echoing their bytes.
"""
import argparse
import base64
import json
import hashlib
import sys
from pathlib import Path



def read(entry,branch_id,*,counter_bodies=False,view=None,selectors=None,offset=0,limit=20,
         max_bytes=4096,record_id=None,definitions=None,response_envelope=None):
    from etalon.research.notes import entry_value
    entry=Path(entry).absolute();metadata=entry_value(entry)
    if metadata.get('schema') == 'etalon-note-entry/1':
        from etalon.research.notes import read_entry, read_current
        if counter_bodies and view not in {None,'full'}:
            raise ValueError('--counter-bodies requires --view full')
        if record_id is not None:
            metadata={k:v for k,v in metadata.items() if k!='branches'}
            metadata.update(record_id=record_id,branch_id=branch_id)
        def wrap(payload):
            result=dict(payload,execution_performed=False,new_RSI_proposal=False)
            if result.get('view') in {'index','selected','brief'} and result.get('followup',{}).get('select'):
                recipe=result['followup']['select']
                result['followup']={**result['followup'],'cli':[sys.executable,str(Path(__file__).absolute()),str(entry),
                    '--branch',result['branch_id'],'--record-id',result['record_id'],
                    '--view',recipe['view'],'--offset',str(recipe['offset']),'--limit',str(recipe['limit']),
                    '--max-bytes',str(max_bytes),
                    *[part for pointer in recipe.get('selectors',[]) for part in ('--select',pointer)],
                    *(['--definitions',json.dumps(definitions,ensure_ascii=False,separators=(',',':'))] if definitions is not None else [])]}
            return result
        measured=lambda payload: json.dumps(response_envelope(wrap(payload)) if response_envelope else wrap(payload),
            ensure_ascii=False,separators=(',',':'))
        if view is None and not counter_bodies and selectors is None and not offset and limit==20:
            result=read_current(metadata,branch_id=branch_id,definitions=definitions,max_bytes=max_bytes,response_envelope=measured)
        else:
            result=read_entry(metadata, branch_id=branch_id, definitions=definitions,
                view=view or ('full' if counter_bodies else 'knowledge'),
                selectors=selectors,offset=offset,limit=limit,max_bytes=max_bytes,response_envelope=measured)
        result=wrap(result)
        if counter_bodies:
            result['counter_originals']=[{'ref':ref,'bytes_read':len(Path(ref['path']).read_bytes()),
                'body_read':True} for ref in result['note'].get('counterevidence',[])]
        return result
    if view is not None or selectors is not None or offset or limit!=20 or record_id is not None:
        raise ValueError('--view/--select/--offset/--limit/--record-id apply to note entries')
    from etalon.research.research_context import load_entry
    from etalon.research.research_segment import read_work
    from etalon.research.continuation import read_content
    from etalon.research.evidence import read_evidence
    from etalon.research.improvement import retrieve_experience
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


def decision_packet(result, *, previous=None):
    """Project verified material for a decision; previous affects delivery only.

    Original validation still runs. Omitted history remains reachable by refs.
    A delivery receipt does not assert that the host understood the material.
    """
    if 'note' in result or 'knowledge' in result or result.get('view')=='selected':
        # The note already is the compact current record. No extra projection graph.
        packet=result
        digest=hashlib.sha256(json.dumps(packet,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        envelope={'packet':packet,'sha256':digest}
        if previous == envelope:
            return {'unchanged':True,'record_id':result['record_id'],
                    'originals_verified':result['originals_verified'],
                    'counter_originals':result.get('counter_originals',[])},envelope
        return envelope,envelope
    current=result['work']; work=current['work']
    experiences=[]
    for query in result['experiences']:
        heads=[]
        for item in query['heads']:
            exp=item['experience']
            heads.append({'ref':item['ref'], **{k:exp[k] for k in
                ('outcome','lesson','invalid_when','conditions','patch_ref','scope') if k in exp}})
        experiences.append({'entry':query['entry'],'conditions':query['conditions'],
            'heads':heads,'history_count':len(query['history']),
            'next_cursor':query['next_cursor']})
    packet={'schema':'etalon-decision-delivery/1','entry':current['entry'],
        'task_id':work['task_id'],'branch_id':work['branch_id'],
        'checkpoint_ref':current['checkpoint_ref'],'work_ref':current['work_ref'],
        'goal':work['goal'],'question':work['question'],'state':work['branch_state'],
        'summary':work.get('last_summary'),'residuals':work['residuals'],
        'next_segment':work['next_segment'],'status_guards':work['status_guards'],
        'branches':result['branches'],'pending_handles':result['pending_handles'],
        'counter_originals':result['counter_originals'],'experiences':experiences,
        'history':{'completed_ref_count':len(work['completed_refs']),
                   'read_via_work_ref':current['work_ref']},
        'execution_permission':'not_granted','new_RSI_proposal':False}
    stable=lambda value:json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    digest=hashlib.sha256(stable(packet)).hexdigest()
    envelope={'packet':packet,'sha256':digest}
    if previous is None:
        return envelope,envelope
    old=previous.get('packet',{})
    if hashlib.sha256(stable(old)).hexdigest()!=previous.get('sha256'):
        raise ValueError('Previous delivery receipt changed')
    if any(old.get(k)!=packet[k] for k in ('entry','task_id','branch_id')):
        return {**envelope,'delivery':'scope_changed_full_packet'},envelope
    changed={k:v for k,v in packet.items() if old.get(k)!=v}
    delta={'schema':'etalon-decision-delivery-delta/1','base_sha256':previous['sha256'],
        'sha256':digest,'changed':changed,'unchanged_fields_omitted':len(packet)-len(changed),
        'counter_coverage':packet['counter_originals'],
        'counter_bodies_rechecked':all(r['body_read'] for r in packet['counter_originals']),
        'execution_performed':False,'new_RSI_proposal':False}
    return delta,envelope


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('entry',type=Path);parser.add_argument('--branch',required=True)
    parser.add_argument('--counter-bodies',action='store_true')
    parser.add_argument('--view',choices=['brief','index','selected','knowledge','full'],
        help='index gives continuation and pointer titles; selected batches --select fields; full verifies originals')
    parser.add_argument('--select',dest='selectors',action='append',
        help='Literal JSON Pointer; repeat to read several fields/items, e.g. --select /lesson --select /knowledge/0')
    parser.add_argument('--offset',type=int,default=0,help='Collection page offset for index/selected')
    parser.add_argument('--limit',type=int,default=20,help='Maximum collection items per page, 1..50')
    parser.add_argument('--max-bytes',type=int,default=4096,help='Selective note delivery cap, 4096..65536')
    parser.add_argument('--record-id',help='Pin a previously returned note while its project entry advances')
    parser.add_argument('--definitions',type=json.loads,help='Exact semantic definition name-to-hash bindings')
    parser.add_argument('--packet',action='store_true',help='Deliver current choices and scoped experience heads')
    parser.add_argument('--previous',type=Path,help='Previous packet receipt; affects output only')
    parser.add_argument('--receipt',type=Path,help='Save current packet for later delta delivery')
    args=parser.parse_args()
    previous=json.loads(args.previous.read_text()) if args.previous else None
    envelope_size=(lambda payload: decision_packet(payload,previous=previous)[0]) if args.packet else None
    result=read(args.entry,args.branch,counter_bodies=args.counter_bodies,view=args.view,
        selectors=args.selectors,offset=args.offset,limit=args.limit,max_bytes=args.max_bytes,record_id=args.record_id,
        definitions=args.definitions,response_envelope=envelope_size)
    if args.packet:
        result,envelope=decision_packet(result,previous=previous)
        if args.receipt:
            args.receipt.write_text(json.dumps(envelope,ensure_ascii=False),encoding='utf-8')
    elif args.previous or args.receipt:
        parser.error('--previous/--receipt require --packet')
    # Match the UTF-8 transport counted while packing, including on Windows.
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdout.write(json.dumps(result,ensure_ascii=False,separators=(',',':')))
