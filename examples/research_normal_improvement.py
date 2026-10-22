"""Use an existing research chain and real earlier reader failure, without a probe.

Caller supplies released entry, original Codex JSONL, paused authority and a
frozen acceptance protocol. No scientific computation or native model is run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from etalon import host
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.research import Artifact, ResearchJournal, MethodVersion, MethodUse, TaskSplit
from etalon.research import improvement as imp
from etalon.research.composition import WORKFLOW, node_step, record_evaluation
from etalon.research.components import record_json
from etalon.research.continuation import STEP, CHECKPOINT, record_content, resume_research, capture_watermarks
from etalon.research.evidence_refs import file_identity
from etalon.research.method_revision import journal_ref, build_method_revision, record_method_revision, resolve_method_revision

SID, TASK = 'improvement', 'normal-research-reader-revision'
QUESTION = 'Recover the released R4 conclusion without repeating a failed reader or scientific task'


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n',encoding='utf-8')


def run(root, entry_path, native_trace, authority, protocol):
    root.mkdir(parents=True, exist_ok=False)
    original_entry=json.loads(entry_path.read_text('utf-8'))
    original_context=resume_research(original_entry['workspace'],original_entry['checkpoint_ref'])
    original_workspace=json.loads(Path(original_entry['workspace']).read_text('utf-8'))
    traces=[json.loads(line) for line in native_trace.read_text('utf-8').splitlines() if line.strip()]
    failures=[e['item'] for e in traces if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='command_execution'
              and e['item'].get('exit_code') and 'unsupported research content schema' in e['item'].get('aggregated_output','')]
    if len(failures)!=1 or 'read_content' not in failures[0]['command']:
        raise ValueError('expected one original typed-reader failure; do not synthesize one')
    candidates=[]
    for r in original_context['checked_refs']:
        if r['record_id'].startswith('artifact-'):
            m=imp.read_material(original_entry['workspace'],r)
            if isinstance(m['content'],dict) and m['content'].get('schema')=='released-r4-cost-reanalysis/1':
                candidates.append((r,m))
    if len(candidates)!=1:
        raise ValueError('need one existing released result')
    result_ref, original_result=candidates[0]
    if result_ref['record_digest'] not in failures[0]['command']:
        raise ValueError('failed native command did not read this exact result')
    store=CampaignStore(root/'research.sqlite')
    store.configure(CampaignSpec('engineering',0,'wall_second',TASK),[
        Endpoint('engineering','diagnostic','unmeasured','count','normal-use/1',0,queryable=False)])
    j=ResearchJournal(store)
    ref=lambda rid:journal_ref(j,rid,source_id=SID)
    def artifact(obj,**bindings):
        return ref(record_json(j,obj,root/'memory',source_kind='new_execution',bindings=bindings))
    def existing(path,kind):
        return ref(j.record(Artifact(str(path.absolute()),**file_identity(path),source_kind='existing_real_artifact',role='diagnostic',bindings={'kind':kind})))
    def save(obj):
        return ref(record_content(j,obj,root/'memory',source_id=SID,source_kind='new_execution'))
    def publish():
        write(root/'etalon-workspace.json',{'schema':'etalon-evidence-workspace/1','workspace_id':TASK,
            'sources':[*original_workspace['sources'],{'source_id':SID,'kind':'journal','location':str(store.path),
                'identity':{'kind':'snapshot_sha256','value':file_identity(store.path)['sha256']}}],
            'status_refs':[str(authority)],'location_map':original_workspace.get('location_map',[])})
        return root/'etalon-workspace.json'
    def use(method,reason,*,conditions=None,subject='normal-use'):
        return ref(j.record(MethodUse(({'kind':'task','id':TASK,'scope':str(store.path)},
            {'kind':'request','id':subject,'scope':str(store.path)}),
            ({'method_version_ref':method['record_id'],'scope':'released-r4-cost-reanalysis/1 JSON'},),
            host_conditions=conditions or {},reason=reason)))
    trace_ref=existing(native_trace,'original_native_trace')
    protocol_ref=existing(protocol,'frozen_acceptance_protocol')
    problem=artifact({'schema':'observed-reader-problem/1','failure':failures[0], 'original_trace_ref':trace_ref,
        'result_ref':result_ref,'baseline_origin':'actual historical native call; not a new baseline run',
        'historical_cost':'whole original session cost retained; per-failed-call cost unknown'},kind='observed_method_problem')
    base_content=artifact({'entrypoint':'etalon.research.continuation.read_content','observed_error':'unsupported research content schema',
        'input_ref':result_ref,'provenance':problem,'scope':'reconstruction of the observed call, not the entire old harness'})
    base=ref(j.record(MethodVersion('observed-typed-reader',('domain_skill','usage_method'),{'artifact_ref':base_content['record_id']})))
    trigger=use(base,'Actual earlier failing call reconstructed from original command/output; host_reported',subject='historical-problem')
    conditions={'problem_kind':'generic-json-through-typed-reader','result_schema':'released-r4-cost-reanalysis/1','visibility':'released'}
    ctx=imp.build_revision_context(publish(),task_id=TASK,scope='read this released result without rerunning its producer',
        conditions=conditions,target_ref=base,problem_refs=[problem],evidence_refs=[result_ref,trigger])
    context_ref=save(ctx)
    opened=[]
    def handler(event,request_id):
        if digest(event['body']['record'])==trigger['record_digest']:
            opened.append(imp.register_opportunity(j,context_ref,root/'memory',source_id=SID,source_kind='new_execution'))
    hooks=dict(log_ref={'kind':'campaign','database':str(store.path)},state_path=root/'trigger-cursor.json',
        handler_method_version='normal-use-reader-v1',event_types=['research_recorded'],record_types=['etalon-method-use/1'],limit=100)
    first=host.consume_events(**hooks,handler=handler)
    opportunity=opened[0]
    import inspect
    candidate_content=artifact({'entrypoint':'etalon.research.improvement.read_material','implementation':inspect.getsource(imp.read_material),
        'module':{'path':str(Path(imp.__file__).absolute()),**file_identity(Path(imp.__file__))},
        'procedure':'Use existing verified reader; generic JSON stays generic. Preserve complete/fragment and provenance.',
        'effects':'reader selection only; producer and scientific conclusion unchanged'})
    candidate=ref(j.record(MethodVersion('generic-result-reader',('domain_skill','usage_method'),{'artifact_ref':candidate_content['record_id']},
        parents=(base['record_id'],),entrypoint='etalon.research.improvement.read_material',change_summary='Select generic evidence reader for ordinary JSON')))
    g_content=artifact({'recipe':'Use the observed failure and current reader contracts to change the reader selection and its optional workflow node.',
                        'author':'current host; no autonomous generation or generator-benefit claim'})
    generator=ref(j.record(MethodVersion('reader-repair-recipe',('improvement_method',),{'artifact_ref':g_content['record_id']})))
    g_use=use(generator,'Host-authored actual candidate and graph change',subject=opportunity['request_id'])
    revision=ref(record_method_revision(j,build_method_revision(problem_refs=[problem,context_ref],target_kind='U',
        base_method_ref=base,producer_method_ref=generator,producer_use_ref=g_use,candidate_version_refs=[candidate],
        change_summary='Route ordinary result to verified generic reader',mechanism_hypothesis='Typed schema rejection is avoided while identity checks remain',
        scope=ctx['scope']),root/'memory',source_id=SID,source_kind='new_execution'))
    old_node={'node_id':'read-result','capability_id':None,'method_refs':[base],'views':['E','J'],
              'purpose':'Read released result for scientific judgment','skip_when':'The exact result has already been read in this task'}
    recovery={'node_id':'recover-reader-error','capability_id':None,'method_refs':[],'views':['E'],
              'purpose':'Recover a typed schema rejection','skip_when':'No typed reader error occurred'}
    old_wf={'schema':WORKFLOW,'name':'released-evidence-continuation','scope':ctx['scope'],'nodes':[old_node,recovery],
            'edges':[{'from':'read-result','to':'recover-reader-error','kind':'control','condition':'typed schema rejected'}],
            'method_refs':[base],'supersedes':[]}
    old_wf_ref=save(old_wf)
    edits=[{'collection':'method_refs','before':base,'after':candidate},
           {'collection':'nodes','before':old_node,'after':{**old_node,'method_refs':[candidate]}},
           {'collection':'edges','before':old_wf['edges'][0],'after':None},
           {'collection':'nodes','before':recovery,'after':None}]
    built=imp.build_method_patch(j,context_ref=context_ref,opportunity_ref=opportunity['opportunity_ref'],base_method_ref=base,
        candidate_method_ref=candidate,expected_change='Read existing JSON directly; remove the obsolete typed-error recovery branch',
        source_id=SID,workflow_before_ref=old_wf_ref,workflow_edits=edits)
    wf=save(built['workflow'])
    patch=save({**built['patch'],'workflow_after_ref':wf})
    initial=artifact({'conditions':conditions,'input_ref':result_ref,'baseline_ref':problem,'evidence_role':'seen historical observation'})
    split=ref(j.record(TaskSplit(('released-r4-existing',),('unused-validation',),('unused-transfer',),
        {'released-r4-existing':'seen','unused-validation':'not-used','unused-transfer':'not-used'},'Local repair only; no independent test')))
    plan=save(dict(schema=STEP,task_id=TASK,question=QUESTION,branch_id='reader-improvement',operation='evaluation_plan',
        reason='Evaluate the next ordinary evidence read; reuse the original failure, do not rerun it',input_refs=[patch,revision],output_refs=[],
        method_refs=[base,candidate],accounting_refs=[],source_watermarks=[],supersedes=[],detail={'target':'workflow_composition',
        'subjects':[candidate],'comparators':[base],'protocol_ref':protocol_ref,'task_split_ref':split,'initial_conditions_ref':initial,
        'use_refs':[trigger],'trigger_refs':[problem],'metrics':[
            {'name':'reader-errors','family':'regression','unit':'count','denominator':'one read of the exact existing result','missing_policy':'unknown'},
            {'name':'complete-json-preserved','family':'retention','unit':'boolean','denominator':'complete original JSON','missing_policy':'fail'},
            {'name':'observed-wall','family':'cost','unit':'second','denominator':'one normal SDK read; historical call cost unknown','missing_policy':'unknown'}]}))
    waiting=save(imp.assess_revision(j,patch_ref=patch,plan_ref=plan,source_id=SID))
    def checkpoint(basis,state,steps,supersedes=()):
        return save({'schema':CHECKPOINT,'task_id':TASK,'question':QUESTION,'step_refs':steps,
            'claim_refs':original_context['claim_refs'],'counter_refs':[problem],'method_refs':[base,candidate,generator],
            'branches':[{'branch_id':'reader-improvement','state':state,'basis_refs':basis,'next_read_refs':basis,
                         'reconsider_when':'New counterevidence or changed result schema; reuse this opportunity'},
                        {'branch_id':'repeat-science','state':'stopped','basis_refs':[original_entry['checkpoint_ref']],
                         'next_read_refs':[],'reconsider_when':'Only a new explicit scientific task; R4b remains paused'}],
            'pending_handles':[],'source_watermarks':capture_watermarks(original_entry['workspace'],source_ids=[s['source_id'] for s in original_workspace['sources']]),
            'supersedes':list(supersedes),'status_guards':[{'location':str(authority),**file_identity(authority),'state':'paused'}]})
    pending_cp=checkpoint([patch,waiting],'waiting',[plan])
    # Normal research work: the chosen method reads the existing result for the current conclusion.
    current=publish()
    start=time.perf_counter()
    material=imp.read_material(current,result_ref)
    elapsed=time.perf_counter()-start
    if not material['complete'] or material['content']!=original_result['content']:
        raise ValueError('normal result read differs from released original')
    cost=artifact({'wall_seconds':elapsed,'scope':'one normal verified result read','model_tokens':None,'money':None,
                   'historical_failed_call_wall':None,'scientific_calls':0},kind='accounting')
    output=artifact({'schema':'normal-evidence-read/1','input_ref':result_ref,'full_content':material['content'],
                     'reader_errors':0,'complete_json_preserved':True,'module':candidate_content,
                     'normal_task':'recover existing scientific conclusion; no producer replay'},kind='normal_execution_receipt')
    applied=use(candidate,'Applied read_material in the normal research continuation',conditions=conditions)
    node=save(node_step(task_id=TASK,question=QUESTION,branch_id='reader-improvement',node_id='read-result',workflow_ref=wf,
        state='completed',reason='Actual normal SDK read',input_refs=[result_ref,patch],output_refs=[output],
        method_refs=[candidate],use_refs=[applied],receipt_refs=[output],accounting_refs=[cost]))
    skipped=save(node_step(task_id=TASK,question=QUESTION,branch_id='reader-improvement',node_id='recover-reader-error',
        workflow_ref=old_wf_ref,state='skipped',reason='No typed schema error; successor graph removes this branch',input_refs=[patch,node]))
    assessment=save(imp.assess_revision(j,patch_ref=patch,plan_ref=plan,observed_step_refs=[node],source_id=SID))
    comparison=artifact({'baseline':{'source_ref':problem,'observed_typed_reader_failure':True,'rerun':False},
        'candidate':{'node_ref':node,'reader_errors':0,'complete_json_preserved':True},
        'scope':'same input JSON; historical native error versus current SDK success, not a model performance comparison',
        'scientific_conclusion':'original cost-adjusted objective is worse; no scientific promotion',
        'net_benefit':'not established; development/model cost not amortized'})
    evaluation=record_evaluation(j,plan,directory=root/'memory',source_id=SID,source_kind='new_execution',
        result_refs=[comparison['record_id'],output['record_id']],accounting_refs=[cost['record_id']],
        conclusion='Scoped reader repair: exact previously rejected JSON read successfully; no scientific or general model gain claim',scope=ctx['scope'])
    resolution=resolve_method_revision(j,revision,evaluation['evaluation_ref'],outcome='adopt',candidate_ref=candidate,
        task_id=TASK,question=QUESTION,scope=ctx['scope'],reason='Use generic reader for this released result; shared defaults unchanged',
        directory=root/'memory',source_id=SID,source_kind='new_execution')
    experience=imp.record_experience(j,patch_ref=patch,outcome='adopt',lesson='For ordinary released result JSON, choose a generic evidence reader and preserve identity/completeness; do not rerun its producer after typed schema rejection.',
        invalid_when='Different schema, changed provenance, incomplete bytes, or new counterevidence',capability_id='etalon.evidence',
        support_refs=[node,assessment],counter_refs=[problem],evaluation_ref=evaluation['evaluation_ref'],resolution_ref=resolution['step_ref'],
        directory=root/'memory',source_id=SID,source_kind='new_execution')
    # Distinct event for the same problem, plus ordinary bookkeeping events, must not propose again.
    duplicate=use(base,'Repeated report of the same already-addressed problem',subject='duplicate-problem-event')
    duplicate_results=[]
    def followup(event, request_id):
        if digest(event['body']['record'])==duplicate['record_digest']:
            duplicate_results.append(imp.register_opportunity(j,context_ref,root/'memory',source_id=SID,source_kind='new_execution'))
    while True:
        consumed=host.consume_events(**hooks,handler=followup)
        if not consumed['has_more']:
            break
    before=len(store.events())
    replay=host.consume_events(**hooks,handler=followup)
    direct=imp.register_opportunity(j,context_ref,root/'memory',source_id=SID,source_kind='new_execution')
    assert direct['state']=='already_proposed' and direct['patch_refs']==[patch]
    assert len(store.events())==before and len(duplicate_results)==1 and not replay['handled']
    final_cp=checkpoint([patch,assessment,experience,resolution['step_ref']],'resolved',
        [plan,node,skipped,evaluation['step_ref'],resolution['step_ref']],supersedes=[pending_cp])
    entry={'workspace':str(publish()),'checkpoint_ref':final_cp}
    write(root/'entry.json',entry)
    context=resume_research(entry['workspace'],final_cp)
    assert not context['issues'] and context['authority']['state']=='paused' and not context['pending']
    retrieved=imp.retrieve_experience(entry['workspace'],source_ids=[SID],capability_id='etalon.evidence',conditions=conditions)
    assert len(retrieved['matches'])==1 and retrieved['matches'][0]['ref']==experience
    report={'entry':entry,'original_entry':original_entry,'problem_ref':problem,'context_ref':context_ref,'patch_ref':patch,
        'waiting_assessment_ref':waiting,'normal_node_ref':node,'assessment_ref':assessment,'experience_ref':experience,
        'revision_ref':revision,'resolution':resolution,'conditions':conditions,'result_ref':result_ref,
        'first_hook':first,'duplicate_event':duplicate_results,'replay':replay,'duplicate_direct':direct,
        'checked_refs':len(context['checked_refs']),'normal_read_wall_seconds':elapsed,'scientific_calls':0,
        'fixed_probe_stage':False,'actions':len(store.actions()),'scope':'one seen reader repair; not scientific/LLM benefit'}
    write(root/'report.json',report)
    print(json.dumps({'entry':str(root/'entry.json'),'checked_refs':len(context['checked_refs']),'outcome':'scoped adopt','actions':len(store.actions())}))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('root','entry','native-trace','authority','protocol'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    run(a.root.absolute(),a.entry,a.native_trace,a.authority.absolute(),a.protocol)
