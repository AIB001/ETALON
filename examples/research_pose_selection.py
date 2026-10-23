"""Frozen input-only selection versus both fixed branches on a new audit panel.

Reuses the pose pilot's request/receipt ledger, native kernel, verifier and
checkpoint publication. No new Probe stage, scheduler, or host hook is required.
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
from research_posebusters import Case, read, CONDITIONS
from etalon.host import _consumer_lock
from etalon.research import improvement as imp
from etalon.research.schema import MethodVersion, MethodUse, TaskSplit
from etalon.research.continuation import STEP
from etalon.research.composition import node_step, record_evaluation
from etalon.research.evidence_refs import file_identity
from etalon.research.pose_selection import input_features, select_with_policy
from etalon.research.pose_task import select_pose_branch
from etalon.research.publication import _save


class SelectionCase(Case):
    SID='pose-selection24'; TASK='pose-selection24-independent-validation'
    QUESTION='Can an input-only conditional ranking workflow retain CNN pose recovery while avoiding unnecessary or harmful rescoring?'
    CAP='molcascade.gnina.input-conditioned-ranking'
    CONDITIONS={**CONDITIONS,'selection_stage':'before-scientific-execution','policy':'seen-panel-hbd-stump-v1'}
    driver_name='research_pose_selection.py'
    protocol_file='protocol-v2.json'

    def initialize(self):
        if (self.root/'state.json').exists(): return self.state()
        protocol=self.file(self.root/self.protocol_file,'protocol')
        full_history=self.file(self.root/'seen-diagnosis.json','seen-feedback')
        historical=read(self.root/'seen-diagnosis.json')['rows']
        history=self.artifact({'schema':'pose-selection-seen-summary/1','full_diagnosis_ref':full_history,
            'rows':[{'case_id':r['case_id'],'seed':r['seed'],'features':r['features'],'delta':r['delta']} for r in historical],
            'scope':'All previous development and validation cases now seen; new independent evaluation still required.'})
        policy=self.file(self.root/'policy.json','candidate-selection-rule')
        problem=self.artifact({'question':self.QUESTION,'basis_refs':[history],
            'problem':'Previous validation skipped eight CNN rescues and avoided four regressions; input-only conditioning is unproven.',
            'scope':'All previous 24 cases now seen. No new validation label participates in fitting. No affinity claim.'})
        methods={}
        for name in ('empirical','cnn','base','candidate'):
            body=self.artifact({'method':name,'protocol_ref':protocol,'policy_ref':policy if name=='candidate' else None,
                'meaning':{'empirical':'native empirical search','cnn':'native CNN score of unchanged pool',
                    'base':'fixed workflow: empirical search then CNN ordering',
                    'candidate':'conditional workflow: empirical search; predeclared input rule enables CNN or keeps empirical ordering'}[name]})
            methods[name]=self.ref(self.j.record(MethodVersion('pb-selection-'+name,('usage_method','domain_skill'),
                {'artifact_ref':body['record_id']},parents=(methods['base']['record_id'],) if name=='candidate' else (),
                change_summary='Optional input-conditioned workflow; scientific standards unchanged')))
        context=self.content(imp.build_revision_context(self.workspace(),task_id=self.TASK,scope=self.QUESTION,conditions=self.CONDITIONS,
            target_ref=methods['base'],problem_refs=[problem],evidence_refs=[protocol,history,policy]))
        opportunity=imp.register_opportunity(self.j,context,self.directory,source_id=self.SID,source_kind='new_execution')
        patch=self.content(imp.build_method_patch(self.j,context_ref=context,opportunity_ref=opportunity['opportunity_ref'],
            base_method_ref=methods['base'],candidate_method_ref=methods['candidate'],
            expected_change='Retain fixed-CNN valid Top1 and physical validity while reducing CNN calls; also beat fixed empirical recovery',source_id=self.SID)['patch'])
        split=self.ref(self.j.record(TaskSplit(tuple(read(self.root/'policy.json')['training_cases']),
            tuple(r['id'] for r in self.panel['cases']),('unrun-further-transfer',),
            {**{i:'seen-feedback' for i in read(self.root/'policy.json')['training_cases']},
             **{r['id']:'frozen-independent-input' for r in self.panel['cases']},'unrun-further-transfer':'not run'},
            'New case split frozen before scientific execution; protein similarity filtering is a heuristic, not family certification')))
        initial=self.artifact({'conditions':self.CONDITIONS,'policy_ref':policy,'panel':file_identity(self.root/'cases.json')})
        command=self.artifact({'continue_command':str(self.root/'venv/bin/python')+' '+str(self.root/self.driver_name)+' continue --root '+str(self.root),
            'status_command':str(self.root/'venv/bin/python')+' '+str(self.root/self.driver_name)+' status --root '+str(self.root),
            'scope':'Only this frozen 24-case panel. R4b paused; hooks deferred. Never retry ambiguous reservations.',
            'remaining_semantics':'96 native calls = 24 new cases x 2 seeds x CPU search and GPU score. All alternatives charged; completed requests reused.'})
        plan=self.content({'schema':STEP,'task_id':self.TASK,'question':self.QUESTION,'branch_id':'panel','operation':'evaluation_plan',
            'reason':'Frozen rule and fixed evaluator precede new predictions','input_refs':[problem,patch,command,history,policy],
            'output_refs':[],'method_refs':[methods['base'],methods['candidate']],'accounting_refs':[],'source_watermarks':[],'supersedes':[],
            'detail':{'target':'method','subjects':[methods['candidate']],'comparators':[methods['base']],
                'protocol_ref':protocol,'task_split_ref':split,'initial_conditions_ref':initial,'use_refs':[],'trigger_refs':[problem],
                'metrics':[{'name':'valid-top1','family':'retention','unit':'case-seed','denominator':'all requested case-seeds; including failures','missing_policy':'fail'},
                    {'name':'lost-valid-top1','family':'regression','unit':'case-seed','denominator':'fixed strategy successes','missing_policy':'fail'},
                    {'name':'runtime','family':'cost','unit':'wall_second','denominator':'actual full audit; attributed path separate','missing_policy':'unknown'}]}})
        s={**methods,'stage_methods':{k:methods[k] for k in ('empirical','cnn')},'context':context,'opportunity':opportunity,
            'patch':patch,'problem':problem,'plan':plan,'protocol':protocol,'policy_ref':policy,'history':history,'command':command,
            'nodes':{},'uses':{},'decisions':{},'results':{},'cost_ids':[],'workflow_nodes':{},'workflow_uses':{}}
        self.checkpoint(s);return s

    def execution_method(self,s,stage):return s['stage_methods'][stage]

    def choose(self,s,row):
        path=self.root/'choices'/(row['id']+'.json')
        if row['id'] in s['decisions']:return
        if path.exists(): choice=read(path)
        else:
            start=time.perf_counter();features=input_features(self.root/'inputs'/row['id']/'ligand.sdf')
            policy=read(self.root/'policy.json');choice=select_with_policy(policy,features)
            choice.update(case_id=row['id'],features=features,policy_identity=file_identity(self.root/'policy.json'),
                selected_at_ns=time.time_ns(),selection_wall_seconds=time.perf_counter()-start,
                historical_experience=read(self.root/'historical-experience.json'),
                basis_refs=[s['policy_ref'],s['history']],
                authorization='User-authorized bounded evaluation of a candidate, not automatic promotion of a prior lesson')
            _save(path,choice)
        evidence=self.file(path,'branch-selection')
        s['decisions'][row['id']]=self.content({'schema':STEP,'task_id':self.TASK,'question':self.QUESTION,'branch_id':row['id'],
            'operation':'decision','reason':'Input-only frozen choice before all new science and evaluation',
            'input_refs':[evidence,s['policy_ref'],s['history']],'output_refs':[],'method_refs':[s['candidate']],
            'accounting_refs':[],'source_watermarks':[],'supersedes':[],
            'detail':{'choice':'proceed','alternatives':[{'branch_id':'cnn','disposition':'select' if choice['selected']=='cnn' else 'skip',
                'reason':choice['reason']}],'basis_refs':[evidence],
                'reconsider_when':'New independent feedback only; validation cannot refit this frozen rule'}})
        self.persist(s)

    def record_workflows(self,s,row,seed):
        key=row['id']+'-'+str(seed)
        if key in s['workflow_nodes']:return
        relevant=[k for k in s['nodes'] if (lambda r:r['case_id']==row['id'] and r['seed']==seed)(read(self.root/'executions'/k/'receipt.json'))]
        selected=read(self.root/'choices'/(row['id']+'.json'))['selected']
        uses=[];nodes=[]
        for method in ('base','candidate'):
            stages=('empirical','cnn') if method=='base' or selected=='cnn' else ('empirical',)
            keys=[k for k in relevant if read(self.root/'executions'/k/'receipt.json')['stage'] in stages]
            use=self.ref(self.j.record(MethodUse(({'kind':'task','id':self.TASK,'scope':str(self.database)},),
                ({'method_version_ref':s[method]['record_id'],'scope':key,'adoption':'full'},),host_conditions=self.CONDITIONS,
                reason='Observed workflow composition over shared charged audit executions: '+','.join(stages))))
            artifact=self.artifact({'workflow':method,'case_id':row['id'],'seed':seed,'actual_stages':stages,
                'native_node_refs':[s['nodes'][k] for k in keys],'sharing':'Same actual executions attributed to two evaluated workflows, never charged twice'})
            node=self.content(node_step(task_id=self.TASK,question=self.QUESTION,branch_id=row['id'],node_id=key+'-'+method,
                capability_id=self.CAP,state='completed',reason='Evaluate actual frozen workflow use; alternate audit work remains separately visible',
                input_refs=[s['decisions'][row['id']],*[s['nodes'][k] for k in keys]],output_refs=[artifact],receipt_refs=[s['results'][k] for k in keys],
                method_refs=[s[method]],use_refs=[use],accounting_refs=[s['results'][k] for k in keys]))
            uses.append(use);nodes.append(node)
        s['workflow_nodes'][key]=nodes;s['workflow_uses'][key]=uses;self.persist(s)

    def finish(self,s):
        from collections import Counter
        import numpy as np
        report=self.comparison(self.panel['cases']);rows=report['rows']
        choices={r['id']:read(self.root/'choices'/(r['id']+'.json')) for r in self.panel['cases']}
        for row in rows:row['selected']=choices[row['case_id']]['selected'];row['policy']=row[row['selected']]
        metrics={m:{'valid_top1':sum(r[m]['top1'] for r in rows),'physical_valid':sum(r[m]['physical_valid'] for r in rows)} for m in ('empirical','cnn','policy')}
        receipts=[read(p) for p in (self.root/'executions').glob('*/receipt.json')]
        cpu=sum(r['wall_seconds'] for r in receipts if r['stage']=='empirical')
        cnn=sum(r['wall_seconds'] for r in receipts if r['stage']=='cnn')
        policy_cnn=sum(r['wall_seconds'] for r in receipts if r['stage']=='cnn' and choices[r['case_id']]['selected']=='cnn')
        selected_calls=2*sum(c['selected']=='cnn' for c in choices.values())
        differences={}
        for other in ('empirical','cnn'):
            d=np.array([np.mean([int(r['policy']['top1'])-int(r[other]['top1']) for r in rows if r['case_id']==case]) for case in choices])
            samples=np.random.default_rng(20261023).choice(d,size=(10000,len(d)),replace=True).mean(axis=1)
            differences[other]={'mean_rate_difference':float(d.mean()),'case_bootstrap_95pct':np.quantile(samples,[.025,.975]).tolist(),
                'rescued':sum(r['policy']['top1'] and not r[other]['top1'] for r in rows),
                'regressed':sum(not r['policy']['top1'] and r[other]['top1'] for r in rows)}
        adopt=(metrics['policy']['valid_top1']>=metrics['cnn']['valid_top1'] and
               metrics['policy']['physical_valid']>=max(metrics['cnn']['physical_valid'],metrics['empirical']['physical_valid']) and
               metrics['policy']['valid_top1']>metrics['empirical']['valid_top1'] and selected_calls<len(rows))
        outcome='adopt' if adopt else 'retain'
        report.update(metrics=metrics,policy_vs=differences,outcome=outcome,selected_cnn_calls=selected_calls,
            choices=dict(Counter(c['selected'] for c in choices.values())),
            costs={'actual_native_audit_seconds':cpu+cnn,'empirical_path_seconds':cpu,'fixed_cnn_path_seconds':cpu+cnn,
                'policy_attributed_native_seconds':cpu+policy_cnn,'policy_feature_selection_seconds':sum(c['selection_wall_seconds'] for c in choices.values()),
                'scope':'Measured shared executions; policy path is attribution, not an independent end-to-end runtime measurement. Prep, verifier and host costs excluded.'},
            interpretation='24 independent input cases, seeds clustered; frozen retrospective known-pocket task. Point-count adoption is scoped, not statistical noninferiority or affinity.')
        _save(self.root/'comparison.json',report);result=self.file(self.root/'comparison.json')
        workflow_uses=[u for v in s['workflow_uses'].values() for u in v]
        workflow_nodes=[n for v in s['workflow_nodes'].values() for n in v]
        assessments=[]
        candidate_nodes=[pair[1] for pair in s['workflow_nodes'].values()]
        for offset in range(0,len(candidate_nodes),8):
            content=imp.assess_revision(self.j,patch_ref=s['patch'],plan_ref=s['plan'],observed_step_refs=candidate_nodes[offset:offset+8],source_id=self.SID)
            if content['status']!='ready_for_evaluation':raise ValueError(str(content['missing']))
            assessments.append(self.content(content))
        evaluation=None
        for offset in range(0,len(workflow_nodes),16):
            evaluation=record_evaluation(self.j,s['plan'],directory=self.directory,source_id=self.SID,source_kind='new_execution',
                result_refs=(result['record_id'],),accounting_refs=tuple(s['cost_ids']),conclusion=outcome+' frozen conditional workflow on new 24 cases',scope=self.QUESTION,
                use_refs=workflow_uses[offset:offset+16],node_refs=workflow_nodes[offset:offset+16])
        decision=self.content({'schema':STEP,'task_id':self.TASK,'question':self.QUESTION,'branch_id':'validation','operation':'decision',
            'reason':'Apply predeclared valid-pose, validity and invocation-count criteria without changing threshold',
            'input_refs':[evaluation['evaluation_ref']],'output_refs':[],'method_refs':[s['candidate'] if adopt else s['base']],
            'accounting_refs':[],'source_watermarks':[],'supersedes':[],
            'detail':{'choice':'stop','alternatives':[{'branch_id':'input-conditioned-workflow','disposition':outcome,'reason':json.dumps(metrics)}],
                'basis_refs':[result],'reconsider_when':'New independent cases or changed protocol; this panel is now seen'}})
        experience=imp.record_experience(self.j,patch_ref=s['patch'],outcome=outcome,lesson=outcome+': '+json.dumps(metrics)+'; compare both static baselines; no affinity claim',
            invalid_when='Changed engine, model, pocket task, chemical state, endpoint or rule. This panel is now seen; new feedback requires a new evaluation.',
            capability_id=self.CAP,support_refs=[result,*assessments[:8]],counter_refs=[s['history']],evaluation_ref=evaluation['evaluation_ref'],resolution_ref=decision,
            directory=self.directory,source_id=self.SID,source_kind='new_execution')
        s.update(final=result,experience=experience,validation_outcome=outcome);self.checkpoint(s)
        lessons=imp.retrieve_experience(self.workspace(),source_ids=[self.SID],capability_id=self.CAP,conditions=self.CONDITIONS)
        _save(self.root/'experience-readback.json',{'retrieval':lessons,'choice':select_pose_branch(lessons,default='fixed-cnn',candidate='input-conditioned-workflow')})

    def continue_case(self,interrupt=False,limit=None):
        with _consumer_lock(self.root/'.case.lock'):
            for path,pin in self.protocol['implementation'].items():
                if file_identity(path)!=pin:raise ValueError('implementation changed: '+path)
            for filename in ('cases.json','policy.json'):
                if file_identity(self.root/filename)!=self.protocol['frozen_files'][filename]:raise ValueError('frozen file changed: '+filename)
            for row in self.panel['cases']:self.task(row,self.panel['seeds'][0])
            self.recover_publication();s=self.initialize()
            imp.register_opportunity(self.j,s['context'],self.directory,source_id=self.SID,source_kind='new_execution')
            if s.get('final'):
                for receipt in (self.root/'executions').glob('*/receipt.json'):
                    for path,pin in read(receipt)['artifacts'].items():
                        if file_identity(path)!=pin:raise ValueError('saved output changed')
                return {'status':'already_complete',**self.status()}
            for row in self.panel['cases']:self.choose(s,row)
            processed=0
            for row in self.panel['cases']:
                before=len(s['nodes'])
                for seed in self.panel['seeds']:
                    for stage in ('empirical','cnn'):
                        if stage=='cnn' and not (self.root/'outputs'/row['id']/str(seed)/'empirical.sdf').exists():
                            raise RuntimeError('Baseline failed; preserve receipt and inspect missing dependency')
                        self.execute(s,row,seed,stage,interrupt=interrupt)
                    self.record_workflows(s,row,seed)
                if len(s['nodes'])>before:
                    processed+=1;self.checkpoint(s,interrupt=interrupt)
                    if limit and processed>=limit:return {'status':'partial',**self.status()}
            self.finish(s)
            return {'status':'completed',**self.status()}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['initialize','continue','status']);p.add_argument('--root',required=True)
    p.add_argument('--interrupt',action='store_true');p.add_argument('--limit',type=int);a=p.parse_args();case=SelectionCase(a.root)
    if a.action=='initialize':case.initialize();v=case.status()
    elif a.action=='continue':v=case.continue_case(a.interrupt,a.limit)
    else:v=case.status()
    print(json.dumps(v,indent=2),flush=True)
