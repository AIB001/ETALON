"""Prepare a bounded explicit index of completed steps using publication APIs.

This only closes provenance links after a completed panel. It does not execute,
score or alter a scientific result. resume_publication performs the actual
checkpoint/entry update and is independently idempotent.
"""
import argparse,json
from pathlib import Path
from etalon.research.continuation import STEP,read_content
from etalon.research.entry import resolve_entry
from etalon.research.evidence import _collect
from etalon.research.evidence_refs import file_identity,load_workspace,make_ref
from etalon.research.publication import prepare_publication,_save

def prepare_step_index(entry):
    entry=Path(entry).absolute();workspace,ref,_,_=resolve_entry(entry)
    cp=read_content(workspace,ref);ws=load_workspace(workspace)
    records,coverage,warnings=_collect(ws)
    if warnings or any(c['status']!='complete' for c in coverage):
        raise ValueError('Incomplete source coverage; cannot seal a provenance index')
    if cp['pending_handles'] or any(b['state']!='resolved' for b in cp['branches']):
        raise ValueError('Only resolved task branches can be sealed by this helper')
    refs=[make_ref(sid,rid,row['record']) for sid,rows in records.items() for rid,row in rows.items()
          if row.get('projected_content',{}).get('schema')==STEP and row['projected_content']['task_id']==cp['task_id']]
    refs.sort(key=lambda r:(r['source_id'],r['record_id']))
    steps=[]
    for offset in range(0,len(refs),24):
        batch=refs[offset:offset+24]
        steps.append({'name':f'index-{offset//24}', 'content':{'schema':STEP,'task_id':cp['task_id'],'question':cp['question'],
            'branch_id':'panel','operation':'decision','reason':'Explicit index of already recorded steps, not a scientific execution',
            'input_refs':batch,'output_refs':[],'method_refs':[],'accounting_refs':[],'source_watermarks':[],'supersedes':[],
            'detail':{'choice':'reuse','alternatives':[],'basis_refs':batch[:1],
                'reconsider_when':'Changed evidence or protocol; preserved references never authorize recomputation'}}})
    if not 1<=len(steps)<=16:raise ValueError('Index exceeds this bounded publication; partition the task explicitly')
    return prepare_publication(entry,operation_id='complete-step-index-'+ref['record_digest'],
        expected_entry_identity=file_identity(entry),steps=steps)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--entry',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    saved=Path(a.output)
    # The intent persists the original parent/CAS request. Never regenerate it
    # from an advanced entry when retrying the same closure publication.
    result=json.loads(saved.read_text('utf-8')) if saved.exists() else prepare_step_index(a.entry)
    if not saved.exists():_save(saved,result)
    print(json.dumps(result,indent=2))
