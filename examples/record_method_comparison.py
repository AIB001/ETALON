"""Archive one actual method comparison through existing research interfaces.

The caller supplies the measured outputs and its scoped judgment. This adapter
never executes science, infers independence or generates an adoption judgment.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research import Artifact, MethodVersion, MethodUse, ResearchJournal, TaskSplit
from etalon.active.schema import digest
from etalon.research.evidence_refs import file_identity


def archive(spec, root, *, detailed=False, journal=None, entry=None):
    """One note by default. Detailed legacy attribution is an explicit opt-in."""
    root = Path(root).absolute()
    if detailed or (root/'DONE.json').exists():
        return archive_detailed(spec, root)
    from etalon.research.notes import original, save_progress, find_notes
    if journal is None:
        if entry is not None and Path(entry).exists():
            from etalon.research.notes import entry_value, entry_journal
            journal = entry_journal(entry_value(entry), read_only=False)
        else:
            store = CampaignStore(root/'research.sqlite')
            store.configure(CampaignSpec('comparison',0,'wall_second',spec['task_id']),
                [Endpoint('comparison','diagnostic','recorded','count','comparison/1',0,queryable=False)])
            journal = ResearchJournal(store)
    note = dict(task_id=spec['task_id'], branch_id=spec['branch_id'], question=spec['question'],
        conditions=spec['conditions'], capability_id=spec['capability_id'], summary=spec['conclusion'],
        outcome=spec['outcome'], lesson=spec['lesson'], invalid_when=spec['invalid_when'],
        metrics=spec['metrics'], initial_configuration=spec['initial_configuration'],
        reconsider_when=spec['reconsider_when'], methods={k:spec[k] for k in ('base','candidate')},
        evidence=[original(spec[k]) for k in ('protocol','problem','result','cost')],
        counterevidence=[original(spec[k]) for k in ('problem','result')],
        residuals=spec.get('residuals',[]), pending_handles=spec.get('pending_handles',[]),
        next_action=spec.get('next_action'), status=spec.get('state','resolved'),
        registration_timing='After execution; source protocol frozen before execution')
    entry = Path(entry) if entry is not None else root/'entry.json'
    rid = save_progress(journal,entry,note,publication_id='comparison-'+digest(note))
    return dict(entry=str(entry), note_id=rid, outcome=spec['outcome'], new_scientific_execution=0,
        applicable_experiences=len(find_notes(journal,task_id=spec['task_id'],
            conditions=spec['conditions'],capability_id=spec['capability_id'])['matches']))


def archive_detailed(spec, root):
    from etalon.research import improvement as imp
    from etalon.research.components import record_json
    from etalon.research.composition import node_step, record_evaluation
    from etalon.research.continuation import CHECKPOINT, STEP, record_content
    from etalon.research.method_revision import journal_ref
    from etalon.research.opportunity_state import build_opportunity_disposition
    root=Path(root).absolute();root.mkdir(parents=True,exist_ok=True)
    done=root/'DONE.json'
    paths=[spec[k] for k in ('protocol','problem','result','cost')]
    request={'spec':spec,'originals':{p:file_identity(p) for p in paths}}
    if done.exists():
        saved=json.loads(done.read_text())
        if saved['request']!=request:raise ValueError('Comparison changed; publish an explicit successor')
        imp.read_material(saved['workspace'],saved['experience_ref'])
        return saved
    task,scope,sid=spec['task_id'],spec['scope'],spec['source_id']
    conditions=spec['conditions']
    store=CampaignStore(root/'research.sqlite')
    store.configure(CampaignSpec('comparison',0,'wall_second',task),
        [Endpoint('comparison','diagnostic','recorded','count','comparison/1',0,queryable=False)])
    journal=ResearchJournal(store)
    ref=lambda rid:journal_ref(journal,rid,source_id=sid)
    def workspace():
        path=root/'workspace.json'
        path.write_text(json.dumps(dict(schema='etalon-evidence-workspace/1',workspace_id=task,
            sources=[dict(source_id=sid,kind='journal',location=str(store.path),
                identity=dict(kind='snapshot_sha256',value=file_identity(store.path)['sha256']))],
            status_refs=[],location_map=[])))
        return str(path)
    def content(body):
        return ref(record_content(journal,body,root/'content',source_id=sid,
                                  source_kind='new_execution',external_workspace=workspace()))
    def value(body):return ref(record_json(journal,body,root/'content',source_kind='new_execution'))
    def original(path,kind='diagnostic'):
        return ref(journal.record(Artifact(str(Path(path).absolute()),**file_identity(path),
            source_kind='existing_real_artifact',role='diagnostic',bindings={'kind':kind})))
    problem,protocol,result,cost=(original(spec[k],'accounting' if k=='cost' else 'diagnostic')
                                  for k in ['problem','protocol','result','cost'])
    initial=value({'conditions':conditions,'configuration':spec['initial_configuration'],
                   'registration_timing':'After execution; source protocol frozen before execution'})
    methods=[]
    for role in ['base','candidate']:
        item=spec[role];body=value(item)
        methods.append(ref(journal.record(MethodVersion(item['name'],('domain_skill','scientific_method'),
            {'artifact_ref':body['record_id']},parents=tuple(r['record_id'] for r in methods),
            dependencies=item.get('dependencies',{}),entrypoint=item['entrypoint'],
            change_summary=item['change_summary']))))
    context=content(imp.build_revision_context(workspace(),task_id=task,scope=scope,conditions=conditions,
        target_ref=methods[0],problem_refs=[problem],evidence_refs=[protocol]))
    opportunity=imp.register_opportunity(journal,context,root/'content',source_id=sid,source_kind='new_execution')
    patch=content(imp.build_method_patch(journal,context_ref=context,
        opportunity_ref=opportunity['opportunity_ref'],base_method_ref=methods[0],candidate_method_ref=methods[1],
        expected_change=spec['expected_change'],source_id=sid)['patch'])
    split=ref(journal.record(TaskSplit((task,),(),(),{task:spec['initial_configuration']},
                                     'Task-local diagnostic comparison; independent validation unperformed')))
    step=dict(schema=STEP,task_id=task,question=spec['question'],branch_id=spec['branch_id'],
        operation='evaluation_plan',reason='Bind actual results to the pre-execution protocol',
        input_refs=[patch],output_refs=[],method_refs=methods,accounting_refs=[],source_watermarks=[],supersedes=[],
        detail=dict(target='method',subjects=[methods[1]],comparators=[methods[0]],protocol_ref=protocol,
            task_split_ref=split,initial_conditions_ref=initial,use_refs=[],trigger_refs=[problem],
            metrics=spec['metrics']))
    plan=content(step)
    use=ref(journal.record(MethodUse((dict(kind='task',id=task,scope=str(store.path)),),
        tuple(dict(method_version_ref=r['record_id'],scope=scope) for r in methods),
        visible_evidence_refs=(problem['record_id'],protocol['record_id']),host_conditions=conditions,
        reason='Actual pinned method execution; attribution recorded after normal results')))
    node=content(node_step(task_id=task,question=spec['question'],branch_id=spec['branch_id'],node_id='comparison',
        state=spec.get('node_state','completed'),reason='Normal scientific execution returned these results',
        input_refs=[problem,protocol],output_refs=[result],receipt_refs=[result],method_refs=methods,
        use_refs=[use],accounting_refs=[cost]))
    assessment=content(imp.assess_revision(journal,patch_ref=patch,plan_ref=plan,
        observed_step_refs=[node],source_id=sid))
    evaluation=record_evaluation(journal,plan,directory=root/'content',source_id=sid,source_kind='new_execution',
        result_refs=[result['record_id'],node['record_id']],accounting_refs=[cost['record_id']],
        assessment_role='diagnostic',conclusion=spec['conclusion'],scope=scope,
        use_refs=[use],node_refs=[node])['evaluation_ref']
    selected=methods[1 if spec['outcome']=='adopt' else 0]
    resolution=content(dict(schema=STEP,task_id=task,question=spec['question'],branch_id=spec['branch_id'],
        operation='decision',reason=spec['conclusion'],input_refs=[evaluation,result],output_refs=[],
        method_refs=[selected],accounting_refs=[cost],source_watermarks=[],supersedes=[],
        detail=dict(choice='proceed',alternatives=[dict(branch_id=spec['branch_id'],
            disposition=spec['outcome'],reason=spec['lesson'])],basis_refs=[evaluation,result],
            reconsider_when=spec['reconsider_when'])))
    experience=imp.record_experience(journal,patch_ref=patch,outcome=spec['outcome'],lesson=spec['lesson'],
        invalid_when=spec['invalid_when'],capability_id=spec['capability_id'],support_refs=[result,node],
        counter_refs=[problem,result],evaluation_ref=evaluation,resolution_ref=resolution,
        directory=root/'content',source_id=sid,source_kind='new_execution')
    disposition=content(build_opportunity_disposition(journal,opportunity_ref=opportunity['opportunity_ref'],
        outcome=spec['outcome'],reason=spec['conclusion'],reviewer='host with normal execution results',
        support_refs=[result,evaluation],counter_refs=[problem,result],
        reconsider_when=[dict(condition_id='new-evidence',condition=spec['reconsider_when'])],
        source_id=sid,candidate_ref=methods[1],evaluation_ref=evaluation,resolution_ref=resolution,
        external_workspace=workspace()))
    cp=content(dict(schema=CHECKPOINT,task_id=task,question=spec['question'],step_refs=[node,resolution],
        claim_refs=[],counter_refs=[problem,result],method_refs=methods,branches=[dict(branch_id=spec['branch_id'],
            state='resolved',basis_refs=[experience,evaluation,disposition],
            next_read_refs=[experience,result],reconsider_when=spec['reconsider_when'])],
        pending_handles=[],source_watermarks=[],status_guards=[],supersedes=[]))
    ws=workspace();entry=root/'entry.json'
    entry.write_text(json.dumps(dict(workspace=ws,checkpoint_ref=cp)))
    readback=imp.retrieve_experience(ws,source_ids=[sid],capability_id=spec['capability_id'],conditions=conditions)
    saved=dict(request=request,workspace=ws,entry=str(entry),experience_ref=experience,
        evaluation_ref=evaluation,assessment_ref=assessment,node_ref=node,use_ref=use,
        opportunity_ref=opportunity['opportunity_ref'],disposition_ref=disposition,
        applicable_experiences=len(readback['matches']),outcome=spec['outcome'],new_scientific_execution=0)
    done.write_text(json.dumps(saved,indent=2)+'\n')
    return saved


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec',type=Path);parser.add_argument('directory',type=Path)
    parser.add_argument('--detailed', action='store_true', help='Use legacy graph attribution when explicitly needed')
    parser.add_argument('--entry', type=Path, help='Stable project entry; reuse its existing journal')
    args=parser.parse_args();print(json.dumps(archive(json.loads(args.spec.read_text()),args.directory,
        detailed=args.detailed,entry=args.entry)))
