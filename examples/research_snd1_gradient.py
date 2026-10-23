"""A bounded SND1 normal-use revision, using existing journal/RSI interfaces.

This is a host-selected example, not a scheduler or mandatory research pipeline.
Run with the deployed ETALON interpreter and explicitly pinned PRISM helpers.
Each optimizer call has an immutable request, resource reservation and receipt.
An unresolved reservation without a receipt is never automatically retried.
"""
from __future__ import annotations
import argparse, json, os, sqlite3, sys, time
from pathlib import Path
import numpy as np
from rdkit import Chem, rdBase
import scipy
import snd1_torsion_workflow as science
from etalon.active.schema import CampaignSpec, Endpoint, canonical, digest
from etalon.active.store import CampaignStore
from etalon.host import _consumer_lock
from etalon.research.journal import ResearchJournal
from etalon.research.schema import Artifact, MethodVersion, MethodUse, TaskSplit
from etalon.research import improvement as imp
from etalon.research.continuation import STEP, CHECKPOINT, record_content, read_content
from etalon.research.composition import node_step, record_evaluation, WORKFLOW
from etalon.research.method_revision import journal_ref, build_method_revision, record_method_revision, resolve_method_revision
from etalon.research.research_context import load_entry
from etalon.research.evidence_refs import load_workspace, file_identity
from etalon.research.scientific_input import compile_input
from etalon.research.publication import _save, prepare_publication, resume_publication

SID='snd1-gradient-cost'
TASK='snd1-specified-torsion-normal-refinement'
QUESTION='Can native Cartesian gradients reduce torsion-refinement cost without changing its scientific result?'
CAPABILITY='snd1.torsion_refinement'
CONDITIONS={'target':'SND1','endpoint':'torsion-solver-cost-with-equivalent-results','chemistry':'neutral-C26-near-neighbor',
    'mobility':'four-bounded-rotations','objective':'unchanged-MMFF94s-pocket-directional','runtime':'PRO6000-rdkit-2026.03.1',
    'scope':'optional-refinement-only; fixed_core scientific preference unchanged'}


def read(path):return json.loads(Path(path).read_text('utf-8'))
def save(path,value):_save(path,value)


class Case:
    def __init__(self,root):
        self.root=Path(root).absolute();self.config=read(self.root/'case.json')
        self.database=self.root/'research.sqlite';self.directory=self.root/'memory'
        store=CampaignStore(self.database)
        store.configure(CampaignSpec('structure',300,'wall_second',TASK),[
            Endpoint('structure','diagnostic','unmeasured','count','snd1-gradient/1',0,queryable=False)])
        self.j=ResearchJournal(store)
        bootstrap=self.root/'bootstrap-entry.json'
        if not bootstrap.exists():
            ws,ref=load_entry(self.config['parent_entry'])
            step={'schema':STEP,'task_id':TASK,'question':QUESTION,'branch_id':'gradient-cost','operation':'decision',
                'reason':'User authorizes a new paired solver-cost question; completed pose-fidelity comparisons remain retained.',
                'input_refs':[ref],'output_refs':[],'method_refs':[],'accounting_refs':[],'source_watermarks':[],'supersedes':[],
                'detail':{'choice':'proceed','alternatives':[{'branch_id':'rerun-pose-ranking','disposition':'skip','reason':'No new pose-quality hypothesis'}],
                    'basis_refs':[ref],'reconsider_when':'A new scientific objective or condition is explicitly authorized'}}
            prepared=prepare_publication(entry=bootstrap,parent_entry=self.config['parent_entry'],operation_id='snd1-gradient-bootstrap',
                expected_entry_identity=None,results=[],steps=[{'name':'new-question','content':step}])
            resume_publication(prepared['intent_path'])
        self.parent_ws,self.parent_ref=load_entry(bootstrap)
        self.parent=read_content(self.parent_ws,self.parent_ref)
        ws=load_workspace(self.parent_ws);self.manifest=ws.manifest;self.viewbase=ws.base

    def ref(self,rid):return journal_ref(self.j,rid,source_id=SID)
    def file(self,path,kind='result'):
        return self.ref(self.j.record(Artifact(str(Path(path).absolute()),**file_identity(path),source_kind='new_execution',
            role='diagnostic',bindings={'kind':kind})))
    def artifact(self,value,kind='evidence'):
        path=self.directory/(digest(value)+'.json');save(path,value);return self.file(path,kind)
    def content(self,value):
        return self.ref(record_content(self.j,value,self.directory,source_id=SID,source_kind='new_execution',external_workspace=self.parent_ws))
    def state(self):return read(self.root/'state.json')
    def store_state(self,s):_save(self.root/'state.json',s,replace=True)
    def workspace(self):
        manifest={**self.manifest,'workspace_id':TASK+'-gradient', 'sources':[
            {'source_id':SID,'kind':'journal','location':str(self.database),'identity':{'kind':'snapshot_sha256','value':file_identity(self.database)['sha256']}},
            *self.manifest['sources']]}
        path=self.viewbase/'gradient-live-workspace.json';_save(path,manifest,replace=True);return path
    def use(self,method,request,reason):
        return self.ref(self.j.record(MethodUse(({'kind':'task','id':TASK,'scope':str(self.database)},
            {'kind':'request','id':request,'scope':str(self.database)}),
            ({'method_version_ref':method['record_id'],'scope':QUESTION,'adoption':'full'},),
            host_conditions=CONDITIONS,reason=reason)))
    def checkpoint(self,s):
        from snd1_gradient_checkpoint import checkpoint
        checkpoint(self,s)

    def initialize(self):
        if (self.root/'state.json').exists():return self.state()
        self.j.store.configure(CampaignSpec('structure',300,'wall_second',TASK),[
            Endpoint('structure','diagnostic','unmeasured','count','snd1-gradient/1',0,queryable=False)])
        protocol=self.file(self.root/'protocol.json','protocol');inputref=self.file(self.root/'input.json','scientific-input')
        problem=self.artifact({'problem':'Finite-difference torsion gradients repeat full MMFF and pocket energies; available native Cartesian gradients are not consumed.',
            'basis':{'prior_entry':self.config['parent_entry'],'prior_checkpoint':self.parent_ref,'input_ref':inputref,
                'prior_torsion_result':self.config['historical_result']},
            'negative_evidence':'Prior torsion refinement did not improve pose fidelity. Faster torsion optimization must not reverse the fixed_core preference.',
            'rejected_shortcut':'12 slots have 11 distinct all-atom coordinates. Do not deduplicate by heavy atoms.',
            'prior_results_are_seen':True,'not_a_fixed_probe_stage':True})
        methods={}
        for key,mode in [('base','finite_difference'),('candidate','cartesian_chain')]:
            content=self.artifact({'entrypoint':'snd1_torsion_workflow.refine','gradient_mode':mode,
                'source_identity':file_identity(science.__file__),'protocol_ref':protocol,
                'execution_parameter':'gradient_mode','fixed_standards':read(self.root/'protocol.json')['gates']})
            methods[key]=self.ref(self.j.record(MethodVersion('snd1-torsion-'+mode,('domain_skill','usage_method'),
                {'artifact_ref':content['record_id']},parents=(methods['base']['record_id'],) if key=='candidate' else (),
                change_summary='Hybrid Cartesian chain gradient; identical objective and bounds' if key=='candidate' else 'Existing finite-difference solver')))
        context=self.content(imp.build_revision_context(self.workspace(),task_id=TASK,scope=QUESTION,conditions=CONDITIONS,
            target_ref=methods['base'],problem_refs=[problem],evidence_refs=[protocol]))
        opportunity=imp.register_opportunity(self.j,context,self.directory,source_id=SID,source_kind='new_execution')
        producer_content=self.artifact({'procedure':'Host inspects completed traces, identifies reusable native gradients, proposes one bounded solver candidate.',
            'authorship':'Codex host-authored revision; no autonomous candidate-generator performance claim'})
        generator=self.ref(self.j.record(MethodVersion('existing-evidence-to-gradient-revision',('improvement_method',),{'artifact_ref':producer_content['record_id']})))
        guse=self.use(generator,opportunity['request_id'],'Actually authored the candidate implementation from existing execution evidence')
        revision=self.ref(record_method_revision(self.j,build_method_revision(problem_refs=[problem,context],target_kind='U',
            base_method_ref=methods['base'],producer_method_ref=generator,producer_use_ref=guse,candidate_version_refs=[methods['candidate']],
            change_summary='Replace full-energy finite differences with native coordinate gradients plus cheap rotation/direction differences',
            mechanism_hypothesis='Fewer expensive energy evaluations with equivalent objective, geometry and convergence',scope=QUESTION),
            self.directory,source_id=SID,source_kind='new_execution'))
        workflow=self.content({'schema':WORKFLOW,'name':'optional-torsion-refinement','scope':QUESTION,
            'nodes':[{'node_id':'refine','capability_id':CAPABILITY,'method_refs':[methods['base']],'views':['X','R'],
                'purpose':'Refine an explicitly selected pose','skip_when':'Exact completed request or host retains fixed_core without requesting refinement'}],
            'edges':[],'method_refs':[methods['base']],'supersedes':[]})
        before=read_content(self.workspace(),workflow)['nodes'][0]
        built=imp.build_method_patch(self.j,context_ref=context,opportunity_ref=opportunity['opportunity_ref'],
            base_method_ref=methods['base'],candidate_method_ref=methods['candidate'],expected_change='Lower actual solver cost with unchanged scientific outputs',
            source_id=SID,workflow_before_ref=workflow,workflow_edits=[{'collection':'nodes','before':before,'after':{**before,'method_refs':[methods['candidate']]}},
                {'collection':'method_refs','before':methods['base'],'after':methods['candidate']}])
        newwf=self.content(built['workflow']);patch=self.content({**built['patch'],'workflow_after_ref':newwf})
        split=self.ref(self.j.record(TaskSplit(('SND1-seen-12-correlated-slots',),('not-run-independent-validation',),('not-run-transfer',),
            {'SND1-seen-12-correlated-slots':'same input in both methods','not-run-independent-validation':'not performed','not-run-transfer':'not performed'},
            'Only paired normal-use solver equivalence and cost; no affinity or transfer claim')))
        command=self.artifact({'purpose':'Optional host-authorized continuation of this exact SND1 case',
            'command':self.config['continue_command'],'status_command':self.config['status_command'],
            'behavior':'Existing complete receipts are verified and reconciled, never rerun. Missing receipts on reserved requests stop for review.',
            'scope':'Continue only this case. R4b paused. No shared defaults, environment or allocation changes.'})
        plan=self.content({'schema':STEP,'task_id':TASK,'question':QUESTION,'branch_id':'gradient-cost','operation':'evaluation_plan',
            'reason':'A new solver-cost question on normal SND1 refinement; standards frozen before new execution',
            'input_refs':[problem,patch,revision,command],'output_refs':[],'method_refs':list(methods.values()),'accounting_refs':[],'source_watermarks':[],'supersedes':[],
            'detail':{'target':'method','subjects':[methods['candidate']],'comparators':[methods['base']],'protocol_ref':protocol,
                'task_split_ref':split,'initial_conditions_ref':self.artifact({'conditions':CONDITIONS,'input':file_identity(self.root/'input.json'),'runtime':self.config['runtime']}),
                'use_refs':[],'trigger_refs':[problem],'metrics':[
                    {'name':'solver-equivalence','family':'retention','unit':'paired_slot','denominator':'12 requested correlated slots','missing_policy':'fail'},
                    {'name':'quality-regressions','family':'regression','unit':'paired_slot','denominator':'12 requested correlated slots','missing_policy':'fail'},
                    {'name':'measured-cost','family':'cost','unit':'wall_second','denominator':'all executed optimizer calls including failures','missing_policy':'unknown'}]}})
        s={**methods,'generator':generator,'context':context,'opportunity':opportunity,'revision':revision,'patch':patch,
            'workflow':workflow,'candidate_workflow':newwf,'problem':problem,'command':command,'plan':plan,'protocol':protocol,
            'input':inputref,'steps':[plan],'nodes':{},'uses':{},'accounting':{},'results':{}}
        self.checkpoint(s);return s

    def request(self,mode,slot):
        cfg=self.config
        pose=Path(cfg['baseline'])/f'pose-{slot:03d}.sdf'
        return {'mode':mode,'slot':slot,'pose':{'path':str(pose),**file_identity(pose)},'input':file_identity(self.root/'input.json'),
            'implementation':cfg['source_freeze'],'runtime':cfg['runtime'],'protocol':file_identity(self.root/'protocol.json'),
            'method':'torsion_directional'}

    def verify_source(self):
        for path,identity in self.config['source_freeze'].items():
            if file_identity(path)!=identity:raise ValueError('source changed after freeze: '+path)
        if rdBase.rdkitVersion!='2026.03.1':raise ValueError('runtime changed')

    def execute_slot(self,s,mode,slot,inject=False):
        request=self.request(mode,slot);key=digest(request);eid='snd1-gradient-'+key
        root=self.root/'executions'/key;root.mkdir(parents=True,exist_ok=True)
        save(root/'request.json',request)
        reservation=self.j.store.reserve_resource_execution(eid,key,10,unit='wall_second',provenance={'request':request})
        receipt=root/'receipt.json'
        if receipt.exists():
            observed=read(receipt)
            if observed['request_id']!=key:raise ValueError('receipt identity conflict')
            for name,pin in observed['artifacts'].items():
                if file_identity(root/name)!=pin:raise ValueError('completed artifact changed; never rerun')
        elif not reservation['new_reservation']:
            raise RuntimeError('Unresolved reservation without receipt: inspect this request, do not retry '+eid)
        else:
            # This event is mechanically emitted immediately before the native solver.
            save(root/'started.json',{'execution_id':eid,'pid':os.getpid(),'time_ns':time.time_ns(),'request_id':key})
            mol=Chem.SDMolSupplier(request['pose']['path'],removeHs=False)[0]
            params=compile_input(read(self.root/'input.json'),allowed_targets=science.TARGETS)['parameters']
            start=time.perf_counter();cpu=time.process_time();error=None;artifacts={}
            try:
                result,detail,trace,xyz=science.refine(mol,params,'torsion_directional',gradient_mode=mode)
                chemical_exact=science.legacy.old.canonical_smiles(result)==science.legacy.old.canonical_smiles(mol)
                detail.update(chemical_exact=chemical_exact,metrics=science.legacy.metrics(result,science.legacy_parameters(params)),
                    required_checks=['chemical_identity','hard_geometry','convergence','pocket_clashes','contact_distances','objective','paired_coordinates'])
                with Chem.SDWriter(str(root/'pose.sdf')) as writer:writer.write(result)
                np.savez_compressed(root/'trajectory.npz',xyz=xyz,angles=trace)
                save(root/'detail.json',detail)
                artifacts={name:file_identity(root/name) for name in ['pose.sdf','trajectory.npz','detail.json']}
            except Exception as exc:error={'type':type(exc).__name__,'message':str(exc)}
            observed={'request_id':key,'execution_id':eid,'mode':mode,'slot':slot,'status':'failed' if error else 'completed',
                'error':error,'artifacts':artifacts,'wall_seconds':time.perf_counter()-start,'cpu_seconds':time.process_time()-cpu}
            save(receipt,observed)
            if inject and error is None:
                save(self.root/'injected-interruption.json',{'at':'receipt durable, before settlement/node/checkpoint',
                    'execution_id':eid,'receipt':str(receipt),'pid':os.getpid(),'exit_code':86})
                os._exit(86)
        self.j.store.settle_resource_execution(eid,measured_cost=observed['wall_seconds'],receipt=observed)
        if key in s['nodes']:return
        method=s['base'] if mode=='finite_difference' else s['candidate']
        resultref=self.file(root/'detail.json') if observed['status']=='completed' else self.file(receipt)
        receiptr=self.file(receipt,'cost_receipt');use=self.use(method,eid,'Actual normal-use solver call; exact receipt and source binding')
        node=self.content(node_step(task_id=TASK,question=QUESTION,branch_id='gradient-cost',node_id='refine',
            capability_id=CAPABILITY,workflow_ref=s['workflow'] if mode=='finite_difference' else s['candidate_workflow'],
            state=observed['status'],reason=f'{mode} slot {slot}; same scientific checks and standards',input_refs=[s['input']],
            output_refs=[resultref],method_refs=[method],use_refs=[use],receipt_refs=[receiptr],accounting_refs=[receiptr]))
        s['nodes'][key]=node;s['uses'][key]=use;s['accounting'][key]=receiptr;s['results'][key]=resultref;s['steps'].append(node)
        self.checkpoint(s)

    def evaluate(self,s):
        if s.get('experience'):return s
        pairs=[];totals={m:{'wall_seconds':0.,'cpu_seconds':0.,'mmff_energy_calls':0,'mmff_gradient_calls':0} for m in ['finite_difference','cartesian_chain']}
        for slot in range(1,13):
            values={}
            for mode in totals:
                root=self.root/'executions'/digest(self.request(mode,slot));receipt=read(root/'receipt.json')
                for k in ['wall_seconds','cpu_seconds']:totals[mode][k]+=receipt[k]
                if receipt['status']!='completed':values[mode]=None;continue
                d=read(root/'detail.json');values[mode]=(d,np.load(root/'trajectory.npz')['xyz'])
                for k in ['mmff_energy_calls','mmff_gradient_calls']:totals[mode][k]+=d['work_counts'][k]
            a,b=values['finite_difference'],values['cartesian_chain']
            if a is None or b is None:pairs.append({'slot':slot,'retained':False,'regression':True,'reason':'missing successful pair'});continue
            ad,ax=a;bd,bx=b
            error=float(np.max(np.linalg.norm(ax-bx,axis=1)));energy=abs(sum(ad['final_terms'].values())-sum(bd['final_terms'].values()))
            quality=all(d['chemical_exact'] and d['hard_invariants']['valid'] for d in [ad,bd])
            regression=(bd['convergence']>ad['convergence'] or bd['metrics']['severe_clash']>ad['metrics']['severe_clash'] or
                bd['metrics']['contacts_in_window']<ad['metrics']['contacts_in_window'])
            pairs.append({'slot':slot,'max_atom_delta_A':error,'objective_delta':energy,
                'retained':quality and error<=1e-3 and energy<=1e-4,'regression':regression,
                'same_checks':ad['required_checks']==bd['required_checks'], 'baseline_convergence':ad['convergence'],'candidate_convergence':bd['convergence']})
        ratio=totals['cartesian_chain']['wall_seconds']/totals['finite_difference']['wall_seconds']
        call_ratio=totals['cartesian_chain']['mmff_energy_calls']/max(1,totals['finite_difference']['mmff_energy_calls'])
        gates={'all_12_equivalent':all(p['retained'] for p in pairs),'no_quality_regression':not any(p['regression'] for p in pairs),
            'same_checks':all(p.get('same_checks',False) for p in pairs),'wall_ratio_below_0.9':ratio<.9,'energy_call_ratio_below_0.5':call_ratio<.5}
        outcome='adopt' if all(gates.values()) else 'retain'
        report={'schema':'snd1-gradient-comparison/1','pairs':pairs,'totals':totals,'gates':gates,'outcome':outcome,'wall_ratio':ratio,
            'energy_call_ratio':call_ratio,'scope':'Seen SND1 solver engineering; no improved pose fidelity, affinity or transfer claim',
            'fixed_core_preference':'unchanged','timing_limit':'One counterbalanced paired pass on 12 correlated slots; no statistical generalization',
            'accounting':'Solver scopes include output serialization; journal, host and interruption overhead excluded and not zero'}
        save(self.root/'comparison.json',report);report_ref=self.file(self.root/'comparison.json')
        nodes=[s['nodes'][digest(self.request('cartesian_chain',slot))] for slot in range(1,13)]
        assessment=self.content(imp.assess_revision(self.j,patch_ref=s['patch'],plan_ref=s['plan'],observed_step_refs=nodes,source_id=SID))
        if read_content(self.workspace(),assessment)['status']!='ready_for_evaluation':raise ValueError('incomplete normal-use assessment')
        evaluation=record_evaluation(self.j,s['plan'],directory=self.directory,source_id=SID,source_kind='new_execution',
            result_refs=[report_ref['record_id']],accounting_refs=[r['record_id'] for r in s['accounting'].values()],
            conclusion=outcome+': predeclared equivalence, regression and actual cost criteria',scope=QUESTION,
            use_refs=list(s['uses'].values()),node_refs=list(s['nodes'].values()))
        resolution=resolve_method_revision(self.j,s['revision'],evaluation['evaluation_ref'],outcome=outcome,
            candidate_ref=s['candidate'] if outcome=='adopt' else None,task_id=TASK,question=QUESTION,scope=QUESTION,
            reason='Scoped solver decision; preserve fixed_core as preferred scientific preparation',directory=self.directory,source_id=SID,source_kind='new_execution')
        experience=imp.record_experience(self.j,patch_ref=s['patch'],outcome=outcome,
            lesson=f'{outcome}: hybrid gradient wall ratio {ratio:.4f}, full-energy call ratio {call_ratio:.4f}; gates {gates}. This changes optional solver execution only.',
            invalid_when='Changed objective, torsion mapping, native environment, molecular family, failed equivalence or contradictory evidence. Does not support promoting torsion over fixed_core.',
            capability_id=CAPABILITY,support_refs=[report_ref,assessment],counter_refs=[s['problem']],
            evaluation_ref=evaluation['evaluation_ref'],resolution_ref=resolution['step_ref'],directory=self.directory,source_id=SID,source_kind='new_execution')
        s.update(assessment=assessment,evaluation=evaluation,resolution=resolution,experience=experience,comparison=report_ref)
        s['steps'] += [evaluation['step_ref'],resolution['step_ref']];self.checkpoint(s);return s

    def continue_case(self,inject=False):
        with _consumer_lock(self.root/'.case.lock'):
            self.verify_source();s=self.initialize()
            opportunity=imp.register_opportunity(self.j,s['context'],self.directory,source_id=SID,source_kind='new_execution')
            if s.get('experience'):
                self.verify_outputs()
                return {'status':'already_complete','opportunity':opportunity,**self.status()}
            for slot in range(1,13):
                order=['finite_difference','cartesian_chain'] if slot%2 else ['cartesian_chain','finite_difference']
                for mode in order:
                    self.execute_slot(s,mode,slot,inject=inject and slot==1 and mode=='cartesian_chain' and not (self.root/'injected-interruption.json').exists())
            self.evaluate(s);return {'status':'completed','opportunity':opportunity,**self.status()}

    def verify_outputs(self):
        for slot in range(1,13):
            for mode in ['finite_difference','cartesian_chain']:
                root=self.root/'executions'/digest(self.request(mode,slot));receipt=read(root/'receipt.json')
                for name,identity in receipt['artifacts'].items():
                    if file_identity(root/name)!=identity:raise ValueError('saved execution changed')

    def status(self):
        s=self.state();root=self.root/'executions';receipts=list(root.glob('*/receipt.json'));started=list(root.glob('*/started.json'))
        schemas={}
        with self.j.store.connection() as db:
            for row in db.execute("SELECT body FROM metadata WHERE key LIKE 'research:%'"):
                body=json.loads(row[0]);schema=body.get('bindings',{}).get('schema',body.get('schema'))
                schemas[schema]=schemas.get(schema,0)+1
        return {'entry':str(self.root/'entry.json'),'opportunities':schemas.get(imp.OPPORTUNITY,0),'patches':schemas.get(imp.PATCH,0),
            'experiences':schemas.get(imp.EXPERIENCE,0),'scientific_starts':len(started),'durable_receipts':len(receipts),
            'recorded_nodes':len(s['nodes']),'unrecorded_receipts':len(receipts)-len(s['nodes']),
            'remaining_optimizer_calls':24-len(receipts),'resolved':bool(s.get('experience')),'balance':self.j.store.balance(),
            'comparison':read(self.root/'comparison.json') if (self.root/'comparison.json').exists() else None}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['initialize','continue','status']);p.add_argument('--root',required=True)
    p.add_argument('--interrupt-after-receipt',action='store_true');args=p.parse_args();case=Case(args.root)
    if args.action=='initialize':case.initialize();result=case.status()
    elif args.action=='continue':result=case.continue_case(args.interrupt_after_receipt)
    else:result=case.status()
    print(json.dumps(result,ensure_ascii=False,indent=2))
