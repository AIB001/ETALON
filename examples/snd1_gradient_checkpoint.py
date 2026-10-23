"""Bounded checkpoint for the SND1 example; all detailed nodes remain linked."""
import os,sqlite3
from etalon.research.publication import _save
from etalon.research.evidence_refs import file_identity
from etalon.research.composition import node_step


def checkpoint(case,s):
    from research_snd1_gradient import SID,TASK,QUESTION
    progress=case.artifact({'schema':'snd1-gradient-progress/1','step_refs':s['steps'],
        'recorded_optimizer_nodes':len(s['nodes']),'resolved':bool(s.get('experience')),'command_ref':s['command'],
        'receipt_directory':str(case.root/'executions'),
        'pending_semantics':'No runtime action handles: this SDK executor is tracked by waiting branch and durable per-request receipts. Empty handles do not mean completed science.'})
    aggregate=case.content(node_step(task_id=TASK,question=QUESTION,branch_id='gradient-cost',node_id='gradient-progress',
        state='completed' if s.get('experience') else 'partial',reason='Bounded frontier; retain every detailed step through explicit input references',
        input_refs=s['steps'],output_refs=[progress],receipt_refs=[progress]))
    cp={**case.parent,'step_refs':case.parent['step_refs']+[aggregate],
        'method_refs':case.parent['method_refs']+[s['base'],s['candidate'],s['generator']],
        'branches':case.parent['branches']+[{'branch_id':'gradient-cost','state':'resolved' if s.get('experience') else 'waiting',
            'basis_refs':[s.get('experience',s['problem'])],'next_read_refs':[s['command'],progress,s.get('experience',s['plan'])],
            'reconsider_when':'Reconcile saved receipts, continue only unfinished exact requests. Changed conditions require an explicit new question.'}],
        'supersedes':[s.get('checkpoint',case.parent_ref)]}
    s['checkpoint']=case.content(cp);case.store_state(s)
    snapshots=case.viewbase/'gradient-snapshots';snapshots.mkdir(exist_ok=True);temporary=snapshots/'pending.sqlite'
    with sqlite3.connect(case.database) as src,sqlite3.connect(temporary) as dst:src.backup(dst)
    identity=file_identity(temporary);snapshot=snapshots/(identity['sha256']+'.sqlite')
    if snapshot.exists():temporary.unlink()
    else:os.replace(temporary,snapshot)
    manifest={**case.manifest,'workspace_id':TASK+'-gradient','sources':[
        {'source_id':SID,'kind':'journal','location':str(snapshot),'identity':{'kind':'snapshot_sha256','value':identity['sha256']}},*case.manifest['sources']],
        'location_map':[*case.manifest.get('location_map',[]),{'source_id':SID,'original':str(case.database),'location':str(snapshot),**identity}]}
    wsp=case.viewbase/(identity['sha256']+'.workspace.json');_save(wsp,manifest)
    _save(case.root/'entry.json',{'workspace':str(wsp),'checkpoint_ref':s['checkpoint']},replace=True)
