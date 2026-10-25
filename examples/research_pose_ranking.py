"""Archive externally frozen ranking science through existing RSI APIs.

This is retrospective journal admission. It does not pretend that its evaluation
plan or policy was in the journal before scientific execution. It never executes
science, settles native charges again, changes a deployment, or releases SND1.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.research import Artifact, MethodVersion, MethodUse, TaskSplit, ResearchJournal
from etalon.research import improvement as imp
from etalon.research.components import record_json
from etalon.research.composition import node_step, record_evaluation
from etalon.research.continuation import STEP, CHECKPOINT, record_content, resume_research
from etalon.research.evaluation_contract import build_evaluation_contract, evaluate_contract, record_evaluation_artifact
from etalon.research.evidence_refs import file_identity, make_ref
from etalon.research.method_revision import build_method_revision, record_method_revision, resolve_method_revision
from etalon.research.opportunity_state import build_opportunity_disposition, resolve_opportunity_state
from etalon.research.publication import _save, prepare_publication, resume_publication

TASK='ranking-confirmation-20261025'
SID='ranking-confirmation-science'
CAP='molcascade.pose-ranking.contact-relaxation-v1'
QUESTION='Does a frozen contact/relaxation heuristic improve same-pool native-like top1 recovery on new structures?'
SCOPE='24 externally predeclared project-new PB308 structure/scaffold units; known-pocket redocking; heuristic sequence exclusion; no affinity or remote-homology claim'


def read(path):
    return json.loads(Path(path).read_text('utf-8-sig'))


class Archive:
    def __init__(self,science):
        self.science=Path(science).absolute()
        self.root=self.science/'harness'
        self.root.mkdir(parents=True,exist_ok=True)
        self.store=CampaignStore(self.root/'research.sqlite')
        self.store.configure(CampaignSpec('archive',0,'wall_second',TASK),
            [Endpoint('archive','research','archive','count',TASK,0,queryable=False)])
        self.j=ResearchJournal(self.store)
        self.content=self.root/'content'
    def ref(self,rid):
        return make_ref(SID,rid,self.j.get(rid))
    def obj(self,value,kind='evidence'):
        return self.ref(record_json(self.j,value,self.content,source_kind='existing_real_artifact',bindings={'task_id':TASK,'kind':kind}))
    def file(self,path,kind='original'):
        path=Path(path).absolute()
        return self.ref(self.j.record(Artifact(str(path),**file_identity(path),source_kind='existing_real_artifact',role='diagnostic',bindings={'task_id':TASK,'kind':kind})))
    def content_ref(self,value):
        return self.ref(record_content(self.j,value,self.content,source_id=SID,source_kind='existing_real_artifact'))
    def workspace(self,name):
        name=name+'-'+digest(self.store.events())[:12]
        snapshot=self.root/(name+'.sqlite')
        if not snapshot.exists():
            with self.store.connection() as src,sqlite3.connect(snapshot) as dst:
                src.backup(dst)
        ws=self.root/(name+'-workspace.json')
        _save(ws,{'schema':'etalon-evidence-workspace/1','workspace_id':TASK,'sources':[{'source_id':SID,'kind':'journal','location':str(snapshot),
            'identity':{'kind':'snapshot_sha256','value':file_identity(snapshot)['sha256']}}],
            'status_refs':[str(self.root/'AUTHORITY.json')],'location_map':[]})
        return ws


def archive(science):
    a=Archive(science)
    if (a.root/'completed.json').exists():
        final=read(a.root/'completed.json')
        if final['scientific_evaluation_identity']!=file_identity(a.science/'evaluation.json'):
            raise ValueError('completed archive binds different original science')
        return final
    scientific=read(a.science/'evaluation.json')
    audited=read(a.science/'audit.json')
    frozen=read(a.science/'freeze.json')
    panel=read(a.science/'panel.json')
    design=read(a.science/'design-freeze.json')
    if audited['status']!='passed' or audited['freeze']!=file_identity(a.science/'freeze.json') or scientific['freeze']!=audited['freeze']:
        raise ValueError('science audit/protocol binding incomplete')
    if frozen['design']!=file_identity(a.science/'design-freeze.json') or frozen['panel']!=file_identity(a.science/'panel.json'):
        raise ValueError('original freeze changed')
    _save(a.root/'AUTHORITY.json',{'task_id':TASK,'state':'complete_external_science_archive','r4b':'paused','automatic_scientific_retry':False,
        'chronology':'Science design/panel/source frozen before predictions; this journal, plan and evaluation contract recorded afterwards',
        'archive_cannot_claim_native_journal_pre_registration':True,'scope':SCOPE})
    conditions={'task':'known-pocket-redocking','candidate':'contact-relaxation-v1','pool':'GNINA1.3.3-vina-exh8-modes10-seed41',
        'checker':'PoseBusters-dock-22+fixed-frame-symmetry-RMSD2A','panel_sha256':frozen['panel']['sha256'],
        'protocol_sha256':scientific['freeze']['sha256'],'input_roles':'graph-regenerated-ligand+protein+pocket-centroid;reference-evaluator-only',
        'cost_regime':'candidateCPU<=15percent-of-search;meanwall<=5s'}
    protocol=a.file(a.science/'freeze.json','external_execution_freeze')
    design_ref=a.file(a.science/'design-freeze.json','external_predeclared_rules')
    panel_ref=a.file(a.science/'panel.json','external_predeclared_panel')
    audit_ref=a.file(a.science/'audit.json','read_only_science_integrity_audit')
    original=a.file(a.science/'evaluation.json','scientific_result')
    seen=a.file(a.science/'seen-diagnostic/REPORT.md','seen_two_parent_diagnosis')
    chronology=a.obj({'design_frozen_at_ns':design['frozen_at_ns'],'execution_freeze_at_ns':frozen['frozen_at_ns'],
        'execution_started_at_ns':read(a.science/'execution-start.json')['started_at_ns'],
        'journal_admission':'after external science; chronology audited from immutable task files; no independent timestamp attestation',
        'scientific_role':'external prospective freeze, new local structure/scaffold cases',
        'journal_assessment_role':'diagnostic; no retroactive phi-policy freeze/validation receipt',
        'nonexecuted_transfer':'TaskSplit API requires a nonempty transfer field; a real additional PB308 case is registered after science for navigation only, with real raw-input hashes; no execution or transfer claim'})
    problem=a.obj({'problem':'Seen SND1 own-receptor pools contain valid native-like poses but empirical and CNN top1 each recover only3/6 case-seed rows',
        'independent_unit_limit':'2 correlated parents within one series; 3seeds each, not6 independent molecules',
        'source_ref':seen,'hypothesis':'Reference-free contact coverage and local relaxation might resolve near-tied empirical orderings',
        'no_mechanism_claim':'The prior does not establish which contact/strain feature is causal or that the new candidate will improve',
        'chronology_ref':chronology})
    base_payload=a.obj({'id':'empirical-vina-same-pool','protocol_ref':protocol,'order':'minimizedAffinity ascending; stable original index'})
    candidate_payload=a.obj({'method':frozen['candidate'],'protocol_ref':protocol,'source_identity':frozen['source'],
        'predictor_information':'native empirical pool+conditioning receptor only; no gold/CNN/checker observations'})
    base=a.ref(a.j.record(MethodVersion('ranking-empirical-v1',('domain_skill',),{'artifact_ref':base_payload['record_id']},evidence_refs=(protocol['record_id'],))))
    candidate=a.ref(a.j.record(MethodVersion('contact-relaxation-v1',('domain_skill',),{'artifact_ref':candidate_payload['record_id']},
        parents=(base['record_id'],),evidence_refs=(protocol['record_id'],),change_summary='Fixed contact/local-relaxation correction within empirical1kcal/mol window')))
    context_basis=a.obj({'external_protocol_ref':protocol,'external_panel_ref':panel_ref,'design_ref':design_ref,
        'candidate_id':frozen['candidate']['id'],'case_count':len(panel['cases']),
        'raw_originals_are_large':'This compact context narrows navigation only; full originals remain exact archived Artifacts and later evaluation dependencies',
        'chronology_ref':chronology})
    context=a.content_ref(imp.build_revision_context(a.workspace('context'),task_id=TASK,scope=SCOPE,conditions=conditions,
        target_ref=base,problem_refs=[problem],evidence_refs=[context_basis,design_ref,chronology]))
    opportunity=imp.register_opportunity(a.j,context,a.content,source_id=SID,source_kind='existing_real_artifact')
    patch=a.content_ref(imp.build_method_patch(a.j,context_ref=context,opportunity_ref=opportunity['opportunity_ref'],base_method_ref=base,
        candidate_method_ref=candidate,expected_change='Improve physical-valid native-like top1 without unacceptable regression or ranking overhead',source_id=SID)['patch'])
    generator_payload=a.obj({'recipe':'Host-authored small reference-free tie-window heuristic informed by already-seen SND1 pool-ranking failures',
        'attribution':'Host generated candidate and chose bounded design; no autonomous improvement-generator quality/benefit claim',
        'external_design_ref':design_ref,'journal_is_retrospective':True})
    generator=a.ref(a.j.record(MethodVersion('host-contact-relaxation-proposal-v1',('improvement_method',),{'artifact_ref':generator_payload['record_id']})))
    generator_use=a.ref(a.j.record(MethodUse(({'kind':'task','id':TASK,'scope':str(a.store.path)},),
        ({'method_version_ref':generator['record_id'],'scope':SCOPE},),reason='Retrospective attribution to actual host-authored candidate formation; not automatic generator benefit',host_conditions=conditions)))
    revision=a.ref(record_method_revision(a.j,build_method_revision(problem_refs=[problem,context],target_kind='S',base_method_ref=base,
        producer_method_ref=generator,producer_use_ref=generator_use,candidate_version_refs=[candidate],scope=SCOPE,
        change_summary='Near-tie same-pool contact/local-relaxation ranking under unchanged pose coordinates and checker requirements',
        mechanism_hypothesis='A fixed conditioning-only score correction may prefer native-like members already sampled in the same pool'),a.content,source_id=SID,source_kind='existing_real_artifact'))
    initial=a.obj({'conditions':conditions,'freeze_ref':protocol,'panel_ref':panel_ref,'design_ref':design_ref,'chronology_ref':chronology,
        'coordinates_repaired':False,'post_prediction_tuning_or_replacement':False})
    excluded=set(panel['excluded_historical_ids'])|{r['id'] for r in panel['cases']}
    raw=Path(panel['sources']['archive']['path']).parent.parent/'organizer/raw'
    navigation=None
    for cid in sorted(Path(panel['sources']['pb308']['path']).read_text('ascii').split()):
        if cid in excluded:
            continue
        matches=list(raw.rglob(cid))
        if len(matches)!=1:
            continue
        ligand=matches[0]/(cid+'_ligand.sdf')
        proteins=sorted(matches[0].glob('*protein*.pdb'))
        if ligand.exists() and len(proteins)==1:
            navigation={'case_id':cid,'raw_inputs':{str(p):file_identity(p) for p in (ligand,proteins[0])},
                'status':'not_executed','registration':'after24case results, source IDs and file existence only; outcomes not used',
                'role':'navigation only to satisfy existing TaskSplit transfer field; not in24denominator; eligibility/independence not assessed; no transfer validation claim'}
            break
    if navigation is None:
        raise ValueError('No real unexecuted navigation case; cannot fill TaskSplit without inventing a task')
    _save(a.root/'additional-navigation-case.json',navigation)
    navigation_ref=a.obj(navigation,'unexecuted_real_case_navigation')
    config={r['id']:digest(r['inputs']) for r in panel['cases']}
    config.update({'snd1-scale-P2':file_identity(a.science/'seen-diagnostic/REPORT.md')['sha256'],navigation['case_id']:digest(navigation['raw_inputs'])})
    split=a.ref(a.j.record(TaskSplit(('snd1-scale-P2',),tuple(r['id'] for r in panel['cases']),(navigation['case_id'],),config,
        'External design froze all24 new structures before predictions; chain-string/scaffold exclusion is operational; journal admitted later; additional real source-case transfer navigation is unrun, post-registered, excluded from scientific denominators')))
    rules=[('net_gain','retention','ge',design['adopt_requirements']['net_top1_gain_over_empirical_min'],'case'),
        ('lost_baseline','regression','le',design['adopt_requirements']['lost_baseline_successes_max'],'case'),
        ('paired_p','retention','le',design['adopt_requirements']['paired_one_sided_exact_binomial_p_max'],'probability'),
        ('gain_over_cnn','retention','ge',0,'case'),('physical_valid_gain','regression','ge',0,'case'),
        ('cpu_fraction','cost','le',design['adopt_requirements']['candidate_cpu_fraction_of_search_max'],'ratio'),
        ('mean_wall','cost','le',design['adopt_requirements']['candidate_mean_wall_second_max'],'second')]
    plan=a.content_ref({'schema':STEP,'task_id':TASK,'question':QUESTION,'branch_id':'ranking','operation':'evaluation_plan',
        'reason':'Retrospective journal transcription of exact externally frozen rules, not a journal pre-registration',
        'input_refs':[patch,revision,chronology],'output_refs':[],'method_refs':[base,candidate],'accounting_refs':[],'source_watermarks':[],'supersedes':[],
        'detail':{'target':'method','subjects':[candidate],'comparators':[base],'protocol_ref':protocol,'task_split_ref':split,
            'initial_conditions_ref':initial,'use_refs':[],'trigger_refs':[problem],
            'metrics':[{'name':n,'family':f,'unit':u,'denominator':'all24 requested structure/scaffold units, including failures; no seed pseudoreplication',
                'missing_policy':'insufficient'} for n,f,op,t,u in rules]}})
    deps=[]
    for directory,pattern in [('receipts','*/receipt.json'),('outputs','*/candidate.json'),('outputs','*/verifier.json'),('outputs','*/result.json')]:
        deps.extend(a.file(p,'native_or_derived_original') for p in sorted((a.science/directory).glob(pattern)))
    receipt_index=a.obj({'source_ref':original,'dependencies':deps,'native_receipts':scientific['costs']['native_receipts'],
        'acquisition':'actual raw native/candidate/verifier artifacts; unchanged originals; no new execution in archive'})
    metrics=scientific['metrics']
    summary={'candidate_id':'contact-relaxation-v1','protocol_sha256':scientific['freeze']['sha256'],'denominator':24,
        'net_gain':scientific['paired_candidate_vs']['empirical']['net_gain'],'lost_baseline':scientific['paired_candidate_vs']['empirical']['losses'],
        'paired_p':scientific['paired_candidate_vs']['empirical']['paired_one_sided_exact_p'],
        'gain_over_cnn':metrics['candidate']['top1_success']-metrics['cnn']['top1_success'],
        'physical_valid_gain':metrics['candidate']['physical_valid_top1']-metrics['empirical']['physical_valid_top1'],
        'cpu_fraction':scientific['costs']['candidate_cpu_fraction_of_search'],'mean_wall':scientific['costs']['candidate_mean_wall_seconds'],
        'denominator_complete':len(scientific['cases'])==24,'required_checks_complete':scientific['complete_verifier_cases']==24,
        'all_method_top1_observed':all(m['unknown_top1']==0 for m in metrics.values()),
        'native_calls_complete':scientific['adoption_checks']['native_calls_complete'],'original_pins_verified':audited['status']=='passed',
        'candidate_before_cnn_and_verifier':audited['candidate_order_frozen_before_CNN_and_verifier'],
        'original_ref':original,'audit_ref':audit_ref,'receipt_index_ref':receipt_index,'chronology_ref':chronology}
    summary_ref=a.obj(summary,'scientific_result')
    cost=a.obj({'source_ref':original,'receipt_index_ref':receipt_index,'costs':scientific['costs'],
        'accounting':'actual science already paid; archive does not reserve/settle it again; unknown formation costs remain unknown'},'accounting')
    use=a.ref(a.j.record(MethodUse(({'kind':'task','id':TASK,'scope':str(a.store.path)},{'kind':'result','id':receipt_index['record_id'],'scope':str(a.store.path)}),
        ({'method_version_ref':candidate['record_id'],'scope':SCOPE},),visible_evidence_refs=(receipt_index['record_id'],summary_ref['record_id']),
        host_conditions=conditions,reason='Actual candidate rankings executed and frozen before verifier; retrospective attribution to preserved originals')))
    observed=a.content_ref(node_step(task_id=TASK,question=QUESTION,branch_id='ranking',node_id='same-pool-ranking24',state='completed',
        reason='External frozen24case native experiment completed; archive itself ran no scientific tool',input_refs=[protocol,panel_ref],output_refs=[summary_ref],
        method_refs=[candidate],use_refs=[use],receipt_refs=[receipt_index,audit_ref],accounting_refs=[cost]))
    assessment=a.content_ref(imp.assess_revision(a.j,patch_ref=patch,plan_ref=plan,observed_step_refs=[observed],source_id=SID))
    selector=lambda name:{'input':'summary','pointer':'/'+name}
    contract=build_evaluation_contract(task_id=TASK,scope=SCOPE,conditions=conditions,protocol_ref=protocol,initial_conditions_ref=initial,
        subjects=[candidate],comparators=[base],plan_ref=plan,inputs=[{'name':'summary','ref':summary_ref,'kind':'result'}],
        metrics=[{'name':n,'family':f,'measure':{'operation':'value','left':selector(n),'right':None},'operator':op,'threshold':threshold,'unit':unit,
            'denominator':'24 requested structure/scaffold units; counts include failures; paired p uses discordant cases internally',
            'denominator_selector':selector('denominator'),'missing_policy':'insufficient'} for n,f,op,threshold,unit in rules],
        comparability=[{'check_id':'exact_protocol','selector':selector('protocol_sha256'),'expected':scientific['freeze']['sha256']},
            {'check_id':'exact_candidate','selector':selector('candidate_id'),'expected':'contact-relaxation-v1'}],
        required_checks=[{'check_id':k,'selector':selector(k),'expected':True} for k in
            ('denominator_complete','required_checks_complete','all_method_top1_observed','native_calls_complete','original_pins_verified','candidate_before_cnn_and_verifier')],
        provenance={'mode':'retrospective','basis_refs':[design_ref,protocol,chronology],
            'note':'Contract/plan/journal created after results; numeric rules copied exactly from external pre-execution design freeze. No retroactive journal phi-policy freeze.'})
    contract_ref=a.obj(contract,'evaluation_contract')
    ws=a.workspace('evaluation')
    report=evaluate_contract(ws,contract)
    _save(a.root/'evaluation-report.json',report)
    report_ref=a.ref(record_evaluation_artifact(a.j,report,a.content,source_kind='existing_real_artifact',workspace=ws))
    outcome=scientific['decision']
    if outcome=='adopt' and report['status']!='criteria_passed':
        raise ValueError('archive contract does not support proposed adoption')
    conclusion=(f"contact-relaxation-v1 top1={metrics['candidate']['top1_success']}/24, empirical={metrics['empirical']['top1_success']}/24, "
        f"CNN={metrics['cnn']['top1_success']}/24; paired candidate wins/losses={summary['net_gain']+summary['lost_baseline']}/{summary['lost_baseline']}; "
        f"frozen criteria={report['status']}; scoped decision={outcome}. "+SCOPE)
    counter=a.obj({'source_ref':original,'paired':scientific['paired_candidate_vs'],'metrics':metrics,
        'candidate_fallbacks':scientific['candidate_fallbacks'],'pool_any_success':scientific['pool_any_success'],
        'limitations':['Candidate contacts are distance descriptors, not H-bond energy','MMFF local-copy relaxation is not global strain or pose repair',
            'Protein-string exclusion is not remote homology certification','Single bounded24case panel; no affinity or biological efficacy',
            'Journal independent_validation role not established by retrospective admission'],'chronology_ref':chronology})
    evaluation=record_evaluation(a.j,plan,directory=a.content,source_id=SID,source_kind='existing_real_artifact',
        result_refs=[summary_ref['record_id'],report_ref['record_id']],accounting_refs=[cost['record_id']],assessment_role='diagnostic',
        conclusion=conclusion,scope=SCOPE,use_refs=[use],node_refs=[observed])
    resolution=resolve_method_revision(a.j,revision,evaluation['evaluation_ref'],outcome=outcome,candidate_ref=candidate if outcome=='adopt' else None,
        task_id=TASK,question=QUESTION,scope=SCOPE,reason=conclusion,directory=a.content,source_id=SID,source_kind='existing_real_artifact')
    experience=imp.record_experience(a.j,patch_ref=patch,outcome=outcome,lesson=conclusion,
        invalid_when='Any change to candidate coefficients, graph/preparation, source panel, pocket conditioning, pool generation, checker, reference role or cost regime; new evidence needs a new frozen confirmation',
        capability_id=CAP,support_refs=[summary_ref,report_ref,assessment],counter_refs=[problem,counter,chronology],
        evaluation_ref=evaluation['evaluation_ref'],resolution_ref=resolution['step_ref'],directory=a.content,source_id=SID,source_kind='existing_real_artifact')
    terminal=a.content_ref(build_opportunity_disposition(a.j,opportunity_ref=opportunity['opportunity_ref'],outcome=outcome,reason=conclusion,
        reviewer='host scoped frozen-criteria review',support_refs=[summary_ref,report_ref],counter_refs=[problem,counter,chronology],
        candidate_ref=candidate,evaluation_ref=evaluation['evaluation_ref'],resolution_ref=resolution['step_ref'],source_id=SID,
        reconsider_when=[{'condition_id':'new-original-evidence-or-mechanism','condition':'New measured independent evidence or genuinely changed candidate/physical condition; rereading or another seed is not new transfer evidence'}]))
    authority=a.root/'AUTHORITY.json'
    checkpoint=a.content_ref({'schema':CHECKPOINT,'task_id':TASK,'question':QUESTION,'step_refs':[plan,observed,evaluation['step_ref'],resolution['step_ref']],
        'claim_refs':[],'counter_refs':[problem,counter,chronology],'method_refs':[base,candidate],
        'branches':[{'branch_id':'ranking','state':'resolved','basis_refs':[experience,terminal],
            'next_read_refs':[experience,terminal,original,report_ref],'reconsider_when':'New independent original evidence or mechanism change; no automatic rerun'}],
        'pending_handles':[],'source_watermarks':[],'supersedes':[],
        'status_guards':[{'location':str(authority),**file_identity(authority),'state':'active'}]})
    parent=a.root/'completed-entry.json'
    _save(parent,{'workspace':str(a.workspace('completed')),'checkpoint_ref':checkpoint})
    publication_step={'schema':STEP,'task_id':TASK,'question':QUESTION,'branch_id':'publication','operation':'decision',
        'reason':'Publish completed external science, scoped experience and terminal; no scientific retries',
        'input_refs':[experience,terminal],'output_refs':[],'method_refs':[],'accounting_refs':[],'source_watermarks':[],'supersedes':[],
        'detail':{'choice':'reuse','alternatives':[],'basis_refs':[experience,terminal],'reconsider_when':'New original evidence, not chat restart'}}
    publication=prepare_publication(a.root/'entry.json',parent_entry=parent,operation_id=TASK+'-terminal',expected_entry_identity=None,
        steps=[{'name':'publish_terminal','content':publication_step}])
    resume_publication(publication['intent_path'])
    final={'scientific_evaluation_identity':file_identity(a.science/'evaluation.json'),'entry':str(a.root/'entry.json'),'source_id':SID,
        'conditions':conditions,'capability_id':CAP,'outcome':outcome,'conclusion':conclusion,'experience_ref':experience,'terminal_ref':terminal,
        'evaluation_ref':evaluation['evaluation_ref'],'evaluation_contract_ref':contract_ref,'evaluation_report_ref':report_ref,
        'opportunity_ref':opportunity['opportunity_ref'],'context_ref':context,'patch_ref':patch,'revision_ref':revision,'resolution':resolution,
        'base_ref':base,'candidate_ref':candidate,'chronology_ref':chronology,'scientific_execution_by_archive':False,
        'journal_assessment_role':'diagnostic','external_science_predeclared':True}
    _save(a.root/'completed.json',final)
    return final


def audit(science):
    a=Archive(science)
    final=read(a.root/'completed.json')
    entry=read(a.root/'entry.json')
    before=len(a.store.events())
    context=resume_research(entry['workspace'],entry['checkpoint_ref'])
    same=imp.register_opportunity(a.j,final['context_ref'],a.content,source_id=SID,source_kind='existing_real_artifact')
    exact=imp.retrieve_experience(entry['workspace'],source_ids=[SID],capability_id=CAP,conditions=final['conditions'])
    changed=imp.retrieve_experience(entry['workspace'],source_ids=[SID],capability_id=CAP,conditions={**final['conditions'],'candidate':'changed-candidate'})
    terminal=resolve_opportunity_state(a.j,final['opportunity_ref'],source_id=SID)
    assert not context['issues'] and context['automatic_retry'] is False
    assert any(x['ref']==final['experience_ref'] for x in exact['matches'])
    assert not changed['matches']
    assert terminal['terminal']
    assert len(a.store.events())==before
    result={'entry':final['entry'],'issues':context['issues'],'automatic_retry':context['automatic_retry'],'same_opportunity':same,
        'terminal':terminal,'exact_condition_experience':exact,'changed_candidate_experience':changed,'new_journal_events':0,
        'scientific_execution_by_archive':False,'journal_assessment_role':'diagnostic','external_science_predeclared':True}
    _save(a.root/'audit.json',result)
    return result


def reconcile(science):
    """Append metadata repair and newly found input limitation; no new science."""
    import copy
    a=Archive(science)
    saved=a.root/'reconciled.json'
    if saved.exists():
        return read(saved)
    old=read(a.root/'completed.json')
    oldreport=read(a.root/'evaluation-report.json')
    countervalue=read(a.science/'input-counterevidence.json')
    counter=a.file(a.science/'input-counterevidence.json','input_counterevidence')
    sensitivity=a.file(a.science/'sensitivity-23.json','posthoc_sensitivity_not_primary')
    old_report_ref=old['evaluation_report_ref']
    original_eval=a.j.get(old['evaluation_ref']['record_id'])
    oldcontract=oldreport['contract']
    def material(ref):
        return read(a.j.artifact_path(Artifact(**a.j.get(ref['record_id']))))
    oldplan=material(oldcontract['plan_ref'])
    oldsummary=material(oldcontract['inputs'][0]['ref'])
    corrected_conditions={**old['conditions'],
        'input_roles':'graph-regenerated ligand+protein+pocket centroid; goldSDF evaluator-only; receptor target-H contamination retained',
        'native_input_limitation':'8EX2_Q2Q targetCCD single H; candidate skips H/D; GNINA/CNN treatment unknown'}
    correction=a.obj({'schema':'ranking-archive-reconciliation/1','old_experience_ref':old['experience_ref'],
        'old_evaluation_report_ref':old_report_ref,'old_status':oldreport['status'],
        'metadata_error':'plan and contract denominator description strings differed; API declaration comparison rejected them',
        'scientific_pair_comparable':True,'numeric_rules_or_thresholds_changed':False,'scientific_inputs_or_outputs_changed':False,
        'new_native_calls':0,'input_counterevidence_ref':counter,'sensitivity_ref':sensitivity,
        'withdrawal':'The universal statement that no crystal-ligand atom occurs in native receptor inputs is withdrawn. GoldSDF was verifier-only; target-named heavy atoms were not found, but targetH exists and native handling is unknown',
        'new_scope_conditions':corrected_conditions,
        'history':'old report/plan/evaluation/terminal remain unchanged. New narrowed applicability cannot semantically supersede a differently conditioned experience; current entry names the reconciled scoped result explicitly'})
    problem=a.obj({'problem':'Correct the archive metric-declaration mismatch and expose independently found target-H input contamination',
        'same_executed_candidate_and_native_evidence':True,'old_opportunity_ref':old['opportunity_ref'],
        'correction_ref':correction,'counterevidence_ref':counter,'not_a_new_scientific_trial':True})
    context=a.content_ref(imp.build_revision_context(a.workspace('reconciliation-context'),task_id=TASK,scope=SCOPE,
        conditions=corrected_conditions,target_ref=old['base_ref'],problem_refs=[problem],evidence_refs=[correction,counter]))
    opportunity=imp.register_opportunity(a.j,context,a.content,source_id=SID,source_kind='existing_real_artifact')
    patch=a.content_ref(imp.build_method_patch(a.j,context_ref=context,opportunity_ref=opportunity['opportunity_ref'],
        base_method_ref=old['base_ref'],candidate_method_ref=old['candidate_ref'],
        expected_change='Historical candidate comparison under corrected, narrower input applicability; no new candidate or execution',source_id=SID)['patch'])
    initial=material(oldcontract['initial_conditions_ref'])
    initial.update(conditions=corrected_conditions,input_counterevidence_ref=counter,archive_reconciliation_ref=correction)
    initial_ref=a.obj(initial)
    plan=copy.deepcopy(oldplan)
    plan.update(reason='Append-only metadata wording alignment after incomparable archive; rules and original24case science unchanged',
        input_refs=[patch,old['revision_ref'],correction],supersedes=[oldcontract['plan_ref']])
    plan['detail'].update(initial_conditions_ref=initial_ref,trigger_refs=[problem],metrics=[
        {k:m[k] for k in ('name','family','unit','denominator','missing_policy')} for m in oldcontract['metrics']])
    plan_ref=a.content_ref(plan)
    summary={**oldsummary,'input_counterevidence_ref':counter,'posthoc_sensitivity_ref':sensitivity,
        'scientific_pair_comparable':True,'strict_no_reference_atom_claim_withdrawn':True}
    summary_ref=a.obj(summary,'scientific_result')
    contract=copy.deepcopy(oldcontract)
    contract.update(conditions=corrected_conditions,initial_conditions_ref=initial_ref,plan_ref=plan_ref,
        inputs=[{'name':'summary','ref':summary_ref,'kind':'result'}])
    contract['provenance']['basis_refs'] += [correction,counter]
    contract['provenance']['note'] += ' Append-only repair aligns denominator wording with the existing contract, while narrowing input applicability for target-H contamination. Numeric standards/results unchanged; scientific same-pool comparison remains comparable.'
    contract_ref=a.obj(contract,'evaluation_contract')
    workspace=a.workspace('reconciled-evaluation')
    report=evaluate_contract(workspace,contract)
    if report['status']!='criteria_failed' or any(b['status']=='incomparable' for b in report['bindings']):
        raise ValueError('metadata reconciliation did not yield the expected comparable negative evaluation')
    _save(a.root/'reconciled-evaluation-report.json',report)
    report_ref=a.ref(record_evaluation_artifact(a.j,report,a.content,source_kind='existing_real_artifact',workspace=workspace))
    cost_ref=a.ref(original_eval['accounting_refs'][0])
    use=a.ref(a.j.record(MethodUse(({'kind':'task','id':TASK,'scope':str(a.store.path)},
        {'kind':'result','id':summary_ref['record_id'],'scope':str(a.store.path)}),
        ({'method_version_ref':old['candidate_ref']['record_id'],'scope':SCOPE},),
        visible_evidence_refs=(summary_ref['record_id'],counter['record_id']),host_conditions=corrected_conditions,
        reason='Retrospective corrected attribution to the unchanged candidate execution; no rerun or new scientific cost')))
    observed=a.content_ref(node_step(task_id=TASK,question=QUESTION,branch_id='ranking-reconciled',node_id='same-science-corrected-metadata',state='reused',
        reason='Reuse authenticated24case outputs; clarify input-H boundary and archive declaration wording only',
        input_refs=[oldcontract['protocol_ref'],correction],output_refs=[summary_ref],method_refs=[old['candidate_ref']],use_refs=[use],
        receipt_refs=[oldsummary['receipt_index_ref'],counter],accounting_refs=[cost_ref]))
    assessment=a.content_ref(imp.assess_revision(a.j,patch_ref=patch,plan_ref=plan_ref,observed_step_refs=[observed],source_id=SID))
    conclusion=('Comparable frozen24case same-pool science: candidate14/24, empirical15/24, CNN16/24, candidate0wins/1loss; '
        'frozen criteria_failed, retain/no_change. TargetCCD hydrogen in8EX2 receptor was not used by candidate; native handling unknown, '
        'strict no-reference-atom claim withdrawn; posthoc23case sensitivity14/23 vs15/23 vs16/23. Journal admission remains retrospective diagnostic.')
    evaluation=record_evaluation(a.j,plan_ref,directory=a.content,source_id=SID,source_kind='existing_real_artifact',
        result_refs=[summary_ref['record_id'],report_ref['record_id']],accounting_refs=[cost_ref['record_id']],assessment_role='diagnostic',
        conclusion=conclusion,scope=SCOPE,use_refs=[use],node_refs=[observed])
    resolution=resolve_method_revision(a.j,old['revision_ref'],evaluation['evaluation_ref'],outcome='retain',task_id=TASK,question=QUESTION,
        scope=SCOPE,reason=conclusion,directory=a.content,source_id=SID,source_kind='existing_real_artifact')
    experience=imp.record_experience(a.j,patch_ref=patch,outcome='retain',lesson=conclusion,
        invalid_when='Different candidate/parameters, input provenance/purity, native H handling, panel/preparation, pool generation, checker or cost regime; strict clean-input claims are outside this evidence',
        capability_id=CAP,support_refs=[summary_ref,report_ref,assessment],counter_refs=[counter,correction,sensitivity],
        evaluation_ref=evaluation['evaluation_ref'],resolution_ref=resolution['step_ref'],directory=a.content,source_id=SID,source_kind='existing_real_artifact')
    terminal=a.content_ref(build_opportunity_disposition(a.j,opportunity_ref=opportunity['opportunity_ref'],outcome='retain',reason=conclusion,
        reviewer='host corrected-scope review of existing science',support_refs=[summary_ref,report_ref],counter_refs=[counter,correction,sensitivity],
        candidate_ref=old['candidate_ref'],evaluation_ref=evaluation['evaluation_ref'],resolution_ref=resolution['step_ref'],source_id=SID,
        reconsider_when=[{'condition_id':'new-authenticated-evidence','condition':'New independent executed evidence under an explicitly frozen input condition; another read or the23case sensitivity is not new science'}]))
    authority=a.root/'AUTHORITY.json'
    checkpoint=a.content_ref({'schema':CHECKPOINT,'task_id':TASK,'question':QUESTION,'step_refs':[plan_ref,observed,evaluation['step_ref'],resolution['step_ref']],
        'claim_refs':[],'counter_refs':[counter,correction,sensitivity,old['experience_ref'],old_report_ref],
        'method_refs':[old['base_ref'],old['candidate_ref']],
        'branches':[{'branch_id':'ranking-reconciled','state':'resolved','basis_refs':[experience,terminal],
            'next_read_refs':[experience,terminal,correction,counter,report_ref],'reconsider_when':'New authenticated scientific evidence; no automatic retry'}],
        'pending_handles':[],'source_watermarks':[],'supersedes':[read(a.root/'completed-entry.json')['checkpoint_ref']],
        'status_guards':[{'location':str(authority),**file_identity(authority),'state':'active'}]})
    parent=a.root/'reconciled-completed-entry.json'
    _save(parent,{'workspace':str(a.workspace('reconciled-completed')),'checkpoint_ref':checkpoint})
    step={'schema':STEP,'task_id':TASK,'question':QUESTION,'branch_id':'publication','operation':'decision',
        'reason':'Publish explicit corrected-scope negative experience; preserve original incomparable archive and original24case science',
        'input_refs':[experience,terminal,correction],'output_refs':[],'method_refs':[],'accounting_refs':[],'source_watermarks':[],'supersedes':[],
        'detail':{'choice':'reuse','alternatives':[],'basis_refs':[experience,terminal,correction],'reconsider_when':'New original scientific evidence or verified input-handling evidence'}}
    publication=prepare_publication(a.root/'reconciled-entry.json',parent_entry=parent,operation_id=TASK+'-reconciled-terminal',expected_entry_identity=None,
        steps=[{'name':'publish_reconciled_terminal','content':step}])
    resume_publication(publication['intent_path'])
    final={**old,'entry':str(a.root/'reconciled-entry.json'),'conditions':corrected_conditions,'conclusion':conclusion,'outcome':'retain',
        'experience_ref':experience,'terminal_ref':terminal,'evaluation_ref':evaluation['evaluation_ref'],'evaluation_report_ref':report_ref,
        'evaluation_contract_ref':contract_ref,'resolution':resolution,'opportunity_ref':opportunity['opportunity_ref'],'context_ref':context,'patch_ref':patch,
        'correction_ref':correction,'input_counterevidence_ref':counter,'sensitivity_ref':sensitivity,'original_completed':str(a.root/'completed.json'),
        'original_incomparable_preserved':True,'science_comparable':True,'contract_status':'criteria_failed','strict_no_reference_atoms_established':False,
        'new_scientific_executions':0}
    _save(saved,final)
    return final


def audit_reconciled(science):
    a=Archive(science)
    final=read(a.root/'reconciled.json')
    entry=read(final['entry'])
    before=len(a.store.events())
    context=resume_research(entry['workspace'],entry['checkpoint_ref'])
    exact=imp.retrieve_experience(entry['workspace'],source_ids=[SID],capability_id=CAP,conditions=final['conditions'])
    changed=imp.retrieve_experience(entry['workspace'],source_ids=[SID],capability_id=CAP,conditions={**final['conditions'],'candidate':'changed-candidate'})
    terminal=resolve_opportunity_state(a.j,final['opportunity_ref'],source_id=SID)
    assert not context['issues'] and context['automatic_retry'] is False
    assert any(x['ref']==final['experience_ref'] for x in exact['matches']) and not changed['matches']
    assert terminal['terminal'] and len(a.store.events())==before
    report=read(a.root/'reconciled-evaluation-report.json')
    assert report['status']=='criteria_failed' and all(b['status']!='incomparable' for b in report['bindings'])
    result={'entry':final['entry'],'issues':context['issues'],'automatic_retry':False,'new_journal_events':0,
        'exact_condition_experience':exact,'changed_candidate_experience':changed,'terminal':terminal,'contract_status':report['status'],
        'scientific_pair_comparable':True,'original_incomparable_preserved':True,'strict_no_reference_atoms_established':False,
        'new_scientific_executions':0}
    _save(a.root/'reconciled-audit.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['archive','audit','reconcile','audit-reconciled'])
    p.add_argument('--science',type=Path,required=True)
    args=p.parse_args()
    result={'archive':archive,'audit':audit,'reconcile':reconcile,'audit-reconciled':audit_reconciled}[args.action](args.science)
    print(json.dumps(result,indent=2))
