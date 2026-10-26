"""Publish a real task-local improvement using existing research interfaces.

The manifest names immutable executed artifacts and a comparison with predeclared
criteria. This example never computes molecules, searches routes or chooses a
scientific objective. It joins normal execution, evaluation, experience and an
entry; --stop-after evaluation exercises interruption without repeating work.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical, digest
from etalon.research import Artifact, MethodVersion, MethodUse, ResearchJournal, TaskSplit
from etalon.research import improvement as imp
from etalon.research.components import record_json
from etalon.research.composition import node_step, record_evaluation
from etalon.research.continuation import STEP, CHECKPOINT, record_content
from etalon.research.evaluation_contract import build_evaluation_contract, evaluate_contract
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.method_revision import (journal_ref, build_method_revision,
    record_method_revision, resolve_method_revision)
from etalon.research.opportunity_state import build_opportunity_disposition, resolve_opportunity_state
from etalon.research.publication import _save


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def verify(pin):
    path = Path(pin['path']).absolute()
    if file_identity(path) != {k:pin[k] for k in ('sha256','bytes')}:
        raise EvidenceError('Executed original changed: '+str(path))
    return path


def run(root, manifest_path, *, stop_after=None):
    root, manifest_path = Path(root).absolute(), Path(manifest_path).absolute()
    manifest = load(manifest_path)
    if manifest['schema'] != 'etalon-discovery-publication/1':
        raise EvidenceError('Expected an explicit real execution manifest')
    root.mkdir(parents=True, exist_ok=True)
    request = dict(manifest=dict(path=str(manifest_path),**file_identity(manifest_path)),
        publisher=dict(path=str(Path(__file__).absolute()),**file_identity(Path(__file__))))
    _save(root/'request.json', request)
    task, scope, conditions = (manifest[k] for k in ('task_id','scope','conditions'))
    sid = 'discovery-'+digest(task)[:12]
    store = CampaignStore(root/'research.sqlite')
    store.configure(CampaignSpec('discovery',0,'wall_second',task),[
        Endpoint('discovery','diagnostic','unmeasured','count','discovery/1',0,queryable=False)])
    journal = ResearchJournal(store)
    ref = lambda rid: journal_ref(journal,rid,source_id=sid)
    directory = root/'memory'
    def artifact(value, **bindings):
        return ref(record_json(journal,value,directory,source_kind='new_execution',bindings=bindings))
    def original(pin, kind):
        path = verify(pin)
        return ref(journal.record(Artifact(str(path),**file_identity(path),
            source_kind='existing_real_artifact',role='diagnostic',bindings={'kind':kind})))
    def save(value):
        return ref(record_content(journal,value,directory,source_id=sid,source_kind='new_execution'))
    def publish():
        path = root/'workspace.json'
        _save(path,dict(schema='etalon-evidence-workspace/1',workspace_id=task,
            sources=[dict(source_id=sid,kind='journal',location=str(store.path),
                identity=dict(kind='snapshot_sha256',value=file_identity(store.path)['sha256']))],
            status_refs=[],location_map=[]),replace=True)
        return path
    # Reuse validates every manifest original before returning the existing terminal.
    all_pins = [manifest['protocol'],manifest['comparison'],manifest['cost'],
        *manifest['problem']['evidence'],*manifest.get('related_products',[]),
        *manifest.get('verify_only_pins',[])]
    for method in manifest['methods'].values():
        all_pins.extend(method['files'])
    for node in manifest['executions']:
        all_pins.extend([*node['inputs'],*node['outputs'],node['receipt'],node['cost']])
    all_pins.extend(item['receipt'] for item in manifest.get('related_nodes',[]))
    for pin in all_pins:
        verify(pin)
    if (root/'result.json').exists():
        report = load(root/'result.json')
        if report['request_digest'] != digest(request):
            raise EvidenceError('Completed request identity differs')
        before = len(store.events())
        state = resolve_opportunity_state(journal,report['opportunity_ref'],source_id=sid,
            external_workspace=root/'workspace.json')
        if not state['terminal'] or state['head_ref'] != report['terminal_ref']:
            raise EvidenceError('Completed publication lost its terminal')
        assert len(store.events()) == before
        return {**report,'replay':'verified_existing','new_scientific_calls':0,
            'new_proposals':0,'new_evaluations':0}

    protocol = original(manifest['protocol'],'pre_execution_protocol')
    problem_evidence = [original(p,'prior_task_evidence') for p in manifest['problem']['evidence']]
    problem = artifact(dict(statement=manifest['problem']['statement'],evidence_refs=problem_evidence))
    methods = {}
    for name in ('baseline','candidate'):
        method = manifest['methods'][name]
        files = [original(p,'executed_method_source') for p in method['files']]
        content = artifact(dict(entrypoint=method['entrypoint'],configuration=method['configuration'],
            source_refs=files,protocol_ref=protocol,scope=scope))
        methods[name] = ref(journal.record(MethodVersion(method['name'],('domain_skill','usage_method'),
            {'artifact_ref':content['record_id']},parents=(methods['baseline']['record_id'],) if name=='candidate' else (),
            entrypoint=method['entrypoint'],verification_basis='host_reported',
            evidence_refs=tuple(r['record_id'] for r in files))))
    context = save(imp.build_revision_context(publish(),task_id=task,scope=scope,conditions=conditions,
        target_ref=methods['baseline'],problem_refs=[problem],evidence_refs=[protocol,*problem_evidence]))
    opportunity = imp.register_opportunity(journal,context,directory,source_id=sid,
        source_kind='new_execution',external_workspace=publish())
    patch = save(imp.build_method_patch(journal,context_ref=context,
        opportunity_ref=opportunity['opportunity_ref'],base_method_ref=methods['baseline'],
        candidate_method_ref=methods['candidate'],source_id=sid,
        expected_change=manifest['mechanism'])['patch'])
    generator_content = artifact(dict(procedure=manifest['candidate_generation'],
        protocol_ref=protocol,problem_ref=problem,
        attribution='Actual host/subagent-authored change; no claim of a separately trained optimizer'))
    generator = ref(journal.record(MethodVersion('task-local-algorithm-revision',('improvement_method',),
        {'artifact_ref':generator_content['record_id']})))
    generator_use = ref(journal.record(MethodUse((dict(kind='task',id=task,scope=str(store.path)),),
        (dict(method_version_ref=generator['record_id'],scope=scope),),
        reason='Actual task-local candidate formation from the cited problem and prior evidence')))
    revision = ref(record_method_revision(journal,build_method_revision(problem_refs=[problem,context],
        target_kind='S',base_method_ref=methods['baseline'],producer_method_ref=generator,
        producer_use_ref=generator_use,candidate_version_refs=[methods['candidate']],
        change_summary=manifest['mechanism'],mechanism_hypothesis=manifest['mechanism'],scope=scope),
        directory,source_id=sid,source_kind='new_execution'))
    initial = artifact(dict(conditions=conditions,protocol_ref=protocol))
    split_config = manifest['split']
    split = ref(journal.record(TaskSplit(tuple(split_config['development']),tuple(split_config['validation']),
        tuple(split_config['transfer']),split_config['frozen'],split_config['scope'])))
    comparison = original(manifest['comparison'],'normal_execution_comparison')
    cost = original(manifest['cost'],'cost_receipt')
    products = [original(p,'related_research_product') for p in manifest.get('related_products',[])]
    related_nodes, related_branches = [], []
    for item in manifest.get('related_nodes',[]):
        basis = [products[i] for i in item['product_indices']]
        receipt = original(item['receipt'],'native_execution_receipt')
        related_nodes.append(save(node_step(task_id=task,question=item['question'],branch_id=item['branch_id'],
            node_id=item['node_id'],state='completed',reason=item['reason'],output_refs=basis,
            receipt_refs=[receipt],accounting_refs=[cost])))
        related_branches.append(dict(branch_id=item['branch_id'],state=item['branch_state'],
            basis_refs=basis,next_read_refs=basis,reconsider_when=item['reconsider_when']))
    nodes, uses, candidate_nodes = [], [], []
    for item in manifest['executions']:
        method = methods[item['method']]
        inputs = [original(p,'executed_input') for p in item['inputs']]
        outputs = [original(p,'actual_output') for p in item['outputs']]
        receipt = original(item['receipt'],'native_execution_receipt')
        accounting = original(item['cost'],'execution_accounting')
        use = ref(journal.record(MethodUse((dict(kind='task',id=task,scope=str(store.path)),
            dict(kind='result',id=receipt['record_id'],scope=str(store.path))),
            (dict(method_version_ref=method['record_id'],scope=scope),),host_conditions=conditions,
            basis='host_reported',reason='Attribution of pinned native receipts; '+item['reason'])))
        uses.append(use)
        nodes.append(save(node_step(task_id=task,question=manifest['question'],branch_id='task-local-improvement',
            node_id=item['id'],state=item['state'],reason=item['reason'],input_refs=inputs,output_refs=outputs,
            method_refs=[method],use_refs=[use],receipt_refs=[receipt],accounting_refs=[accounting])))
        if item['method'] == 'candidate':
            candidate_nodes.append(nodes[-1])
    select = lambda pointer: dict(input='comparison',pointer=pointer)
    contract = build_evaluation_contract(task_id=task,scope=scope,conditions=conditions,protocol_ref=protocol,
        initial_conditions_ref=initial,subjects=[methods['candidate']],comparators=[methods['baseline']],
        inputs=[dict(name='comparison',ref=comparison,kind='result')],
        comparability=[dict(check_id=k,selector=select('/'+k),expected=v) for k,v in manifest['comparability'].items()],
        required_checks=[dict(check_id=k,selector=select('/'+k),expected=v) for k,v in manifest['required_checks'].items()],
        metrics=manifest['metrics'],provenance=dict(mode=manifest['evaluation_mode'],
            note=manifest['evaluation_provenance'],basis_refs=[protocol]))
    contract_ref = artifact(contract,schema=contract['schema'])
    evaluated = evaluate_contract(publish(),contract_ref=contract_ref)
    evaluated_ref = artifact(evaluated,schema=evaluated['schema'])
    plan = save(dict(schema=STEP,task_id=task,question=manifest['question'],branch_id='task-local-improvement',
        operation='evaluation_plan',reason=manifest['evaluation_provenance'],input_refs=[patch,contract_ref],
        output_refs=[],method_refs=list(methods.values()),accounting_refs=[],source_watermarks=[],supersedes=[],
        detail=dict(target='method',subjects=[methods['candidate']],comparators=[methods['baseline']],
            protocol_ref=protocol,task_split_ref=split,initial_conditions_ref=initial,use_refs=[],trigger_refs=[problem],
            metrics=[{k:m[k] for k in ('name','family','unit','denominator','missing_policy')} for m in manifest['metrics']])))
    # Keep the existing 16 KiB research-record bound. Partition observed use,
    # retaining every candidate node, instead of dropping evidence or raising it.
    assessments = []
    for offset in range(0,len(candidate_nodes),4):
        body = imp.assess_revision(journal,patch_ref=patch,plan_ref=plan,
            observed_step_refs=candidate_nodes[offset:offset+4],source_id=sid)
        if body['status'] != 'ready_for_evaluation':
            raise EvidenceError('Candidate normal-use attribution is incomplete')
        assessments.append(save(body))
    assessment = artifact(dict(status='ready_for_evaluation',assessment_refs=assessments,
        observed_candidate_nodes=len(candidate_nodes),coverage='All candidate nodes in disjoint bounded batches'))
    # One MethodEvaluation, multiple bounded attribution steps. Each step links
    # its exact uses/nodes; all refer to the same complete paired result.
    evaluations = []
    for offset in range(0,len(nodes),8):
        evaluations.append(record_evaluation(journal,plan,directory=directory,source_id=sid,source_kind='new_execution',
            result_refs=[comparison['record_id'],evaluated_ref['record_id']],accounting_refs=[cost['record_id']],
            conclusion=manifest['conclusion'],scope=scope,use_refs=imp._unique(uses[offset:offset+8]),
            node_refs=nodes[offset:offset+8]))
    evaluation = evaluations[-1]
    evaluation_steps = [item['step_ref'] for item in evaluations]
    def checkpoint(state,basis,steps,previous=()):
        return save(dict(schema=CHECKPOINT,task_id=task,question=manifest['question'],step_refs=[*steps,*related_nodes],
            claim_refs=[],counter_refs=[problem],method_refs=[*methods.values(),generator],
            branches=[dict(branch_id='task-local-improvement',state=state,basis_refs=basis,next_read_refs=basis,
                reconsider_when=manifest['invalid_when']),*related_branches,dict(branch_id='R4b',state='stopped',
                basis_refs=[protocol],next_read_refs=[protocol],reconsider_when='Explicit user resumption required')],
            pending_handles=[],source_watermarks=[],status_guards=[],supersedes=list(previous)))
    pending = checkpoint('waiting',[comparison,evaluated_ref,cost],[plan,*evaluation_steps])
    _save(root/'evaluation-entry.json',dict(workspace=str(publish()),checkpoint_ref=pending))
    if stop_after == 'evaluation':
        return dict(status='interrupted_after_evaluation',entry=str(root/'evaluation-entry.json'),
            evaluation_ref=evaluation['evaluation_ref'],new_scientific_calls=0)
    outcome = 'adopt' if evaluated['status']=='criteria_passed' else 'retain'
    resolution = resolve_method_revision(journal,revision,evaluation['evaluation_ref'],outcome=outcome,
        candidate_ref=methods['candidate'],task_id=task,question=manifest['question'],scope=scope,
        reason=manifest['conclusion'],directory=directory,source_id=sid,source_kind='new_execution')
    experience = imp.record_experience(journal,patch_ref=patch,outcome=outcome,lesson=manifest['lesson'],
        invalid_when=manifest['invalid_when'],capability_id=manifest['capability_id'],
        support_refs=[comparison,evaluated_ref,assessment],counter_refs=[problem],
        evaluation_ref=evaluation['evaluation_ref'],resolution_ref=resolution['step_ref'],
        directory=directory,source_id=sid,source_kind='new_execution')
    terminal = save(build_opportunity_disposition(journal,opportunity_ref=opportunity['opportunity_ref'],
        outcome=outcome,reason=manifest['conclusion'],reviewer='lead integration',support_refs=[experience,evaluated_ref],
        counter_refs=[problem],reconsider_when=[dict(condition_id='changed-task-evidence',condition=manifest['invalid_when'])],
        source_id=sid,candidate_ref=methods['candidate'],evaluation_ref=evaluation['evaluation_ref'],
        resolution_ref=resolution['step_ref'],external_workspace=publish()))
    final = checkpoint('resolved',[experience,terminal,comparison,cost],
        [plan,*evaluation_steps,resolution['step_ref']],previous=[pending])
    entry = dict(workspace=str(publish()),checkpoint_ref=final)
    _save(root/'entry.json',entry)
    report = dict(schema='etalon-discovery-publication-result/1',request_digest=digest(request),status='complete',
        entry=str(root/'entry.json'),task_id=task,source_id=sid,conditions=conditions,outcome=outcome,
        context_ref=context,opportunity_ref=opportunity['opportunity_ref'],patch_ref=patch,revision_ref=revision,
        comparison_ref=comparison,cost_ref=cost,related_product_refs=products,normal_node_refs=nodes,
        related_node_refs=related_nodes,
        evaluation_ref=evaluation['evaluation_ref'],evaluation_report_ref=evaluated_ref,
        evaluation_step_refs=evaluation_steps,
        experience_ref=experience,terminal_ref=terminal,checkpoint_ref=final,
        new_scientific_calls=0,r4b='paused')
    _save(root/'result.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--stop-after',choices=['evaluation'])
    args=parser.parse_args()
    result=run(args.root,args.manifest,stop_after=args.stop_after)
    print(json.dumps({k:result.get(k) for k in ('status','outcome','entry','replay','new_scientific_calls')},ensure_ascii=False))
