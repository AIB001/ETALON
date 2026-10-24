"""Frozen, retrospective SND1 torsion-range x pocket-term diagnostic.

The historical panel is seen development evidence. This executable neither
selects a production method nor repairs predictions using evaluator references.
All four cells share a torsion map, starting pose, MMFF and positional anchors.
"""
from __future__ import annotations

import argparse
import collections
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

import numpy as np
from rdkit import Chem
import snd1_scale_methods as w
from etalon.research.pose_check_bridge import freeze_posebusters_dock, audit_pose_verification

CELLS={
    'narrow_off': {'half_width_degrees':[10,10,10,20],'pocket_terms':False},
    'narrow_on': {'half_width_degrees':[10,10,10,20],'pocket_terms':True},
    'wide_off': {'half_width_degrees':[30,30,30,60],'pocket_terms':False},
    'wide_on': {'half_width_degrees':[30,30,30,60],'pocket_terms':True},
}
CANDIDATE='snd1-wide-torsion-pocket-v1'


def protocol():
    return {
        'schema':'snd1-torsion-range-pocket-protocol/1', 'candidate_id':CANDIDATE,
        'question':'Are residual physical-check failures sensitive to the allowed range of the same four torsions, pocket terms, or their interaction?',
        'mechanism':'Narrow corrections hit hard bounds. Widen only their ranges to the existing large perturbation half widths; retain the four rotation axes and complete moving components.',
        'cells':CELLS, 'baseline':'narrow_on','candidate':'wide_on',
        'fixed':['starting coordinates','indexed chemical graph/state','four torsion axes/moving groups','fixed atom set','MMFF94s','crystal-derived positional anchors and contact windows','L-BFGS-B settings','cartesian_chain gradient','hard geometry tolerances','independent physical-check and fixed-frame RMSD criteria'],
        'pocket_off':'Remove reported-contact distance terms and set protein exclusion coefficient to zero; preserve positional anchors. Directional proxy terms disabled in all cells.',
        'selection':'All 17 historical F/T0 common failures; for each choose nearest standardized initialization-angle control in same system/family from common-success and T0-rescue strata, with replacement; tie by condition_id. Execute unique conditions once.',
        'selection_distance':'Euclidean distance after division by original initialization half widths; both controls remain seen diagnostic evidence',
        'paired_order':'Dictionary cell order for odd seed; reverse for even seed. Unique conditions sorted by condition_id.',
        'scope':'Seen retrospective conditional diagnosis, two near-neighbor ligands, two receptor contexts; own-crystal initialization and crystal-derived anchors disclosed; neither blind pose prediction nor affinity/independent transfer.',
        'evaluation':'Original frozen pose_verifier: all PoseBusters dock checks true AND fixed-frame symmetry heavy RMSD<=2A; no ligand alignment or pose repair.',
        'prespecified_candidate_gate':{'minimum_rescues_among_historical_17':3,'maximum_regressions_among_previously_passing_unique_controls':0,'maximum_new_hard_violations':0,'maximum_solver_cpu_ratio_wide_on_over_narrow_on':2.,'scope':'At most this seen matched conditional repair panel; never a global default'},
        'falsifiers':['Fewer than3/17 rescues','Any control regression or new hard violation','Solver CPU ratio greater than2','Narrow/on does not reproduce historical T0 within frozen numerical tolerance'],
        'reproduction_tolerance':{'maximum_atom_delta_A':.001,'maximum_objective_delta_kcal_mol':.0001},
        'attribution':'Paired range effects at each pocket state and pocket effects at each range; rescued pose is a reachability witness under that cell. Unrescued is not proof of infeasibility/global optimum.',
        'diagnostics':'Min heavy-pair distance/(RDKit vdw-radius sum), exact offending atom/key, active torsion bounds and per-axis endpoint motion sensitivity; independent PoseBusters remains success oracle.',
        'resources':{'workers':1,'cpu_affinity':[24],'nice':10,'threads':1,'gpu':False,'cpu_cap_seconds':900,'no_shared_environment_changes':True},
        'resume':'Per-execution immutable request and terminal receipt; verify and reuse completed, fail closed on unknown request. External detached-worker helper can resume publication after controller interruption without native recomputation.',
        'cost':'Solver/attempt/evaluation costs measured; outer run CPU includes nested attempts and evaluations and must not be added to them. Formation/tests/transfer unknown unless separately measured.',
        'r4b':'paused', 'no_fixed_probe':True,
    }


def select_conditions(conditions, panel):
    lookup={r['request_id']:r for r in panel}
    strata={}
    for c in conditions:
        prefix=c['condition_id']+'--'
        f=lookup[prefix+'fixed_core']['verification']['success']
        t=lookup[prefix+'torsion_distance_cartesian_chain']['verification']['success']
        strata[c['condition_id']]='common_failure' if not f and not t else 'rescue' if not f and t else 'common_success' if f and t else 'regression'
    failures=sorted((c for c in conditions if strata[c['condition_id']]=='common_failure'),key=lambda c:c['condition_id'])
    pairs=[]; selected={c['condition_id']:c for c in failures}
    for failed in failures:
        for label in ['common_success','rescue']:
            pool=[c for c in conditions if c['system_id']==failed['system_id'] and c['family']==failed['family'] and strata[c['condition_id']]==label]
            if not pool:
                raise ValueError('no exact-stratum matched control: '+failed['condition_id']+' '+label)
            scale=np.array(w.protocol()['initialization_families'][failed['family']])
            def distance(c):
                return float(np.linalg.norm((np.array(c['angles_degrees'])-failed['angles_degrees'])/scale))
            control=min(pool,key=lambda c:(distance(c),c['condition_id']))
            selected[control['condition_id']]=control
            pairs.append({'failure':failed['condition_id'],'control':control['condition_id'],'stratum':label,'standardized_angle_distance':distance(control)})
    unique=[]
    for cid,c in sorted(selected.items()):
        row=dict(c);row['historical_stratum']=strata[cid];unique.append(row)
    return {'conditions':unique,'matches':pairs,'historical_common_failure_count':len(failures),
            'matched_slots':len(pairs),'unique_conditions':len(unique),
            'unique_coordinate_hashes':len({c['coordinate_sha256'] for c in unique}),
            'unique_strata':dict(collections.Counter(c['historical_stratum'] for c in unique)),
            'control_reuse':dict(collections.Counter(p['control'] for p in pairs))}


def freeze(root,parent):
    root,parent=Path(root),Path(parent)
    if (root/'freeze.json').exists():
        value=w.read(root/'freeze.json');verify_freeze(root,value);return value
    st,sc=time.perf_counter(),time.process_time()
    root.mkdir(parents=True,exist_ok=True);revised=parent/'input-identity-v2'
    selection=select_conditions(w.read(parent/'conditions.json'),w.read(parent/'panel-results.json'))
    if selection['historical_common_failure_count']!=17:
        raise ValueError('parent panel no longer matches the frozen17-failure question')
    systems=w.read(revised/'systems.json');parent_freeze=w.read(revised/'freeze.json')
    files=set()
    for s in systems:
        files.update(s[k] for k in ['base','base_xyz','parameters','reference','receptor_pdb'])
    files.update(c['input_xyz'] for c in selection['conditions'])
    for rel in sorted(files):
        src=revised/rel
        if w.pin(src)!=parent_freeze['inputs'][rel]:raise ValueError('parent frozen input changed: '+rel)
        dest=root/rel;dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists() and w.pin(dest)!=w.pin(src):raise ValueError('different preexisting input')
        shutil.copyfile(src,dest)
    w.write(root/'protocol.json',protocol());w.write(root/'selection.json',selection);w.write(root/'systems.json',systems)
    # Freeze parent sources and selected actual outputs before new optimization.
    panel=w.read(parent/'panel-results.json');selected_ids={c['condition_id'] for c in selection['conditions']}
    prior=[r for r in panel if r['request_id'].split('--')[0] in selected_ids and r['configuration'] in ['fixed_core','torsion_distance_cartesian_chain']]
    parent_pins={str(parent/n):w.pin(parent/n) for n in ['freeze.json','conditions.json','panel-results.json']}
    parent_pins[str(revised/'freeze.json')]=w.pin(revised/'freeze.json')
    for row in prior:
        path=Path(row['receipt_path'])
        if w.pin(path)!=row['receipt_identity']:raise ValueError('parent receipt changed')
        parent_pins[str(path)]=w.pin(path)
        for name,identity in row['artifacts'].items():
            path2=path.parent/name
            if w.pin(path2)!=identity:raise ValueError('parent artifact changed')
            parent_pins[str(path2)]=identity
    w.write(root/'historical-selected.json',prior)
    by_system={s['system_id']:s for s in systems};diagnostics=[]
    for row in prior:
        if row['configuration']!='torsion_distance_cartesian_chain':continue
        system=by_system[row['system_id']]
        mol=Chem.SDMolSupplier(str(Path(row['receipt_path']).parent/'pose.sdf'),removeHs=False)[0]
        diagnostics.append({'condition_id':row['request_id'].split('--')[0],
            'historical_success':row['verification']['success'],'active_bounds':row['details']['active_hard_bounds'],
            'pair':pair_diagnostics(mol,w.read(root/system['parameters']))})
    w.write(root/'historical-diagnostics.json',diagnostics)
    modules={}
    for name in ['rdkit','rdkit.Chem.rdForceFieldHelpers','rdkit.Chem.rdMolTransforms','scipy.optimize._lbfgsb','numpy._core._multiarray_umath','posebusters','posebusters.modules.intermolecular_distance','etalon.research.geometry_input','etalon.research.scientific_input','etalon.research.result_integrity','etalon.research.pose_check_bridge']:
        module=importlib.import_module(name);modules[name]={'path':module.__file__,**w.pin(module.__file__)}
    source={str(p):w.pin(p) for p in sorted((root/'source').rglob('*.py'))}
    frozen={'schema':'snd1-controlled-ablation-freeze/2','candidate_id':CANDIDATE,'frozen_before_new_execution':True,
            'check_contract':freeze_posebusters_dock(),
            'inputs':{rel:w.pin(root/rel) for rel in sorted(files)},
            'documents':{name:w.pin(root/name) for name in ['protocol.json','selection.json','systems.json','historical-selected.json','historical-diagnostics.json']},
            'sources':source,'modules':modules,'parent_pins':parent_pins,
            'python':sys.executable,'python_executable':w.pin(sys.executable),'created_unix':time.time(),
            'expected_execution_calls':len(selection['conditions'])*len(CELLS),
            'formation_cost':{'wall_second':time.perf_counter()-st,'cpu_second':time.process_time()-sc},
            'lineage':'Seen historical raw poses/receipts are reused for diagnosis, never counted as new scientific executions.'}
    w.write(root/'freeze.json',frozen);return frozen


def verify_freeze(root,value):
    root=Path(root)
    if value.get('check_contract')!=freeze_posebusters_dock():
        raise ValueError('frozen check contract missing or changed; old runs require separate retrospective audit')
    for collection in ['inputs','documents']:
        for rel,pin in value[collection].items():
            if w.pin(root/rel)!=pin:raise ValueError('changed frozen '+collection+': '+rel)
    for collection in ['sources','parent_pins']:
        for path,pin in value[collection].items():
            if w.pin(path)!=pin:raise ValueError('changed frozen '+collection+': '+path)
    for name,pin in value['modules'].items():
        module=importlib.import_module(name)
        if module.__file__!=pin['path'] or w.pin(module.__file__)!={k:pin[k] for k in ['sha256','bytes']}:
            raise ValueError('changed actual import: '+name)


def pair_diagnostics(mol,parameters,initial=None,angles=None):
    xyz=mol.GetConformer().GetPositions();heavy=[a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum()>1]
    pocket=parameters['pocket']['atoms'];protein=np.array([a['xyz'] for a in pocket]);table=Chem.GetPeriodicTable()
    radii=np.array([table.GetRvdw(mol.GetAtomWithIdx(i).GetAtomicNum()) for i in heavy])
    pr=np.array([table.GetRvdw(table.GetAtomicNumber(a['element'])) for a in pocket])
    dist=np.linalg.norm(xyz[heavy,None,:]-protein[None,:,:],axis=2)
    ratio=dist/(radii[:,None]+pr[None,:]);i,j=np.unravel_index(np.argmin(ratio),ratio.shape);atom=heavy[i]
    result={'minimum_vdw_ratio':float(ratio[i,j]),'ligand_atom_index':atom,'ligand_element':mol.GetAtomWithIdx(atom).GetSymbol(),
            'protein_key':pocket[j]['key'],'protein_element':pocket[j]['element'],'distance_A':float(dist[i,j]),
            'ligand_atom_fixed':atom in parameters['geometry']['hard']['fixed_indices'],
            'torsions_with_atom_in_moving_group':[d['id'] for d in parameters['geometry']['dofs'] if atom in d['moving_indices']],
            'pairs_below_ratio_075':int(np.sum(ratio<.75)),
            'scope':'Exact heavy pairs in frozen8A pocket using RDKit vdw; diagnostic only, not a replacement for PoseBusters'}
    if initial is not None and angles is not None:
        g=parameters['geometry'];q=np.deg2rad(angles);eps=1e-5;derivative=[]
        for k,dof in enumerate(g['dofs']):
            lo,hi=np.deg2rad(dof['bounds_degrees']);a=q.copy();b=q.copy();a[k]=min(hi,q[k]+eps);b[k]=max(lo,q[k]-eps)
            xa=w.torsion.rotate(initial,g['dofs'],a);xb=w.torsion.rotate(initial,g['dofs'],b)
            derivative.append(float(np.linalg.norm(xa[atom]-xb[atom])/(a[k]-b[k])))
        result['endpoint_atom_motion_A_per_radian_by_torsion']=derivative
    return result


def run_attempt(root,condition,cell,system):
    root=Path(root);run=root/'executions'/(condition['condition_id']+'--'+cell)
    request={'schema':'snd1-controlled-request/1','request_id':run.name,'attempt_id':'controlled-v1-attempt-1',
             'condition':condition,'cell':cell,'configuration':CELLS[cell],'freeze':w.pin(root/'freeze.json'),
             'input':w.pin(root/condition['input_xyz']),'parameters':w.pin(root/system['parameters']),
             'new_execution':True}
    if run.exists():return w.completed_receipt(run,request)
    run.mkdir(parents=True);w.write(run/'request.json',request)
    w.write(run/'lease.json',{'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().split()[21],'started_unix':time.time()})
    st,sc=time.perf_counter(),time.process_time();receipt={**request,'status':'failed','artifacts':{},'error':None}
    try:
        mol=w.load_base(root,system,condition['input_xyz']);initial=mol.GetConformer().GetPositions().copy()
        parameters=w.controlled_torsion_parameters(w.read(root/system['parameters']),**CELLS[cell])
        t,c=time.perf_counter(),time.process_time()
        out,details,trace,xyz=w.torsion.refine(mol,parameters,'torsion_distance',gradient_mode='cartesian_chain')
        solver={'wall_second':time.perf_counter()-t,'cpu_second':time.process_time()-c}
        if w.old.canonical_smiles(out)!=w.old.canonical_smiles(mol):raise ValueError('chemical identity changed')
        w.save_mol(run/'pose.sdf',out);np.save(run/'pose.npy',xyz);np.save(run/'trace.npy',trace)
        receipt.update(status='completed',graph_exact=True,details=details,solver_cost=solver,
                       diagnostics=pair_diagnostics(out,parameters,initial,details['torsion_delta_degrees']))
        receipt['artifacts']={name:w.pin(run/name) for name in ['pose.sdf','pose.npy','trace.npy']}
    except Exception as exc:receipt['error']={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
    receipt['actual_consumption']={'wall_second':time.perf_counter()-st,'cpu_second':time.process_time()-sc}
    w.write(run/'receipt.json',receipt);return receipt


def verifier(root):
    path=Path(root)/'source'/'pose_verifier.py'
    spec=importlib.util.spec_from_file_location('etalon.research.controlled_frozen_pose_verifier',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def evaluate(root,selection,systems):
    root=Path(root);results=[]
    for sid,system in systems.items():
        conditions=[c for c in selection['conditions'] if c['system_id']==sid]
        for cell in CELLS:
            run=root/'evaluation'/(sid+'--'+cell)
            attempts=[w.read(root/'executions'/(c['condition_id']+'--'+cell)/'receipt.json') for c in conditions]
            completed=[a for a in attempts if a['status']=='completed']
            request={'schema':'snd1-controlled-evaluation-request/1','request_id':run.name,'attempt_id':'independent-v1',
                     'freeze':w.pin(root/'freeze.json'),'input_receipts':{a['request_id']:w.pin(root/'executions'/a['request_id']/'receipt.json') for a in completed}}
            if run.exists():
                receipt=w.completed_receipt(run,request)
            else:
                run.mkdir(parents=True);w.write(run/'request.json',request);st,sc=time.perf_counter(),time.process_time()
                with Chem.SDWriter(str(run/'poses.sdf')) as writer:
                    for a in completed:writer.write(Chem.SDMolSupplier(str(root/'executions'/a['request_id']/'pose.sdf'),removeHs=False)[0])
                value=verifier(root).verify_poses(run/'poses.sdf',root/system['reference'],root/system['receptor_pdb'],threshold=2.)
                if len(value['rows'])!=len(completed):raise ValueError('evaluation row count mismatch')
                for check,a in zip(value['rows'],completed):check['request_id']=a['request_id']
                w.write(run/'verification.json',value)
                receipt={**request,'status':'completed','artifacts':{n:w.pin(run/n) for n in ['poses.sdf','verification.json']},
                         'actual_consumption':{'wall_second':time.perf_counter()-st,'cpu_second':time.process_time()-sc}}
                w.write(run/'receipt.json',receipt)
            if receipt.get('status')!='completed' or any(receipt.get(k)!=v for k,v in request.items()):
                raise ValueError('independent evaluation request/receipt binding mismatch')
            value=w.read(run/'verification.json')
            audit=audit_pose_verification(value,check_contract=w.read(root/'freeze.json')['check_contract'],
                pool=w.pin(run/'poses.sdf'),reference=w.pin(root/system['reference']),
                receptor=w.pin(root/system['receptor_pdb']),evaluation_request=request,
                expected_request_ids=[a['request_id'] for a in completed])
            if audit['status']!='complete':
                raise ValueError('independent evaluation check contract incomplete: '+','.join(audit['issues']))
            results.extend(value['rows'])
    return results


def summarize(root):
    root=Path(root);selection=w.read(root/'selection.json');prior={r['request_id']:r for r in w.read(root/'historical-selected.json')}
    checks={r['request_id']:r for r in evaluate(root,selection,{s['system_id']:s for s in w.read(root/'systems.json')})}
    rows=[]
    for c in selection['conditions']:
        for cell in CELLS:
            rid=c['condition_id']+'--'+cell;r=w.read(root/'executions'/rid/'receipt.json')
            row={**r,'verification':checks.get(rid),'historical_stratum':c['historical_stratum']}
            if cell=='narrow_on' and r['status']=='completed':
                old=prior[c['condition_id']+'--torsion_distance_cartesian_chain']
                old_pose=np.load(Path(old['receipt_path']).parent/'pose.npy');new_pose=np.load(root/'executions'/rid/'pose.npy')
                delta=float(np.max(np.linalg.norm(old_pose-new_pose,axis=1)))
                energy=abs(sum(r['details']['final_terms'].values())-old['details']['final_objective'])
                row['historical_reproduction']={'maximum_atom_delta_A':delta,'objective_delta_kcal_mol':energy,
                    'passed':delta<=.001 and energy<=.0001 and checks.get(rid,{}).get('success')==old['verification']['success']}
            rows.append(row)
    lookup={r['request_id']:r for r in rows};paired=[]
    for c in selection['conditions']:
        result={'condition_id':c['condition_id'],'system_id':c['system_id'],'historical_stratum':c['historical_stratum']}
        for cell in CELLS:
            row=lookup[c['condition_id']+'--'+cell]
            result[cell]={'status':row['status'],'success':row['verification']['success'] if row['verification'] else None,
                          'rmsd_A':row['verification']['rmsd_A'] if row['verification'] else None,
                          'solver_cpu_second':row.get('solver_cost',{}).get('cpu_second'),
                          'hard_valid':row.get('details',{}).get('hard_invariants',{}).get('valid'),
                          'diagnostics':row.get('diagnostics'),'active_bounds':row.get('details',{}).get('active_hard_bounds')}
        paired.append(result)
    fail=[p for p in paired if p['historical_stratum']=='common_failure'];control=[p for p in paired if p['historical_stratum']!='common_failure']
    rescues=sum(p['wide_on']['success'] is True and p['narrow_on']['success'] is False for p in fail)
    regressions=sum(p['narrow_on']['success'] is True and p['wide_on']['success'] is not True for p in control)
    baseline_cpu=sum(p['narrow_on']['solver_cpu_second'] or 0 for p in paired);candidate_cpu=sum(p['wide_on']['solver_cpu_second'] or 0 for p in paired)
    hard=sum(r.get('details',{}).get('hard_invariants',{}).get('valid') is not True for r in rows)
    reproduction=all(r['historical_reproduction']['passed'] for r in rows if 'historical_reproduction' in r)
    no_unknown=all(r['status']=='completed' and r['verification'] is not None and r['verification']['error'] is None for r in rows)
    passed=rescues>=3 and regressions==0 and hard==0 and candidate_cpu/baseline_cpu<=2 and reproduction and no_unknown
    effects=[]
    for a,b,label in [('narrow_off','wide_off','range_without_pocket'),('narrow_on','wide_on','range_with_pocket'),('narrow_off','narrow_on','pocket_at_narrow'),('wide_off','wide_on','pocket_at_wide')]:
        effects.append({'effect':label,'rescues':sum(p[a]['success'] is False and p[b]['success'] is True for p in paired),
                        'regressions':sum(p[a]['success'] is True and p[b]['success'] is False for p in paired),
                        'success_delta':sum(p[b]['success'] is True for p in paired)-sum(p[a]['success'] is True for p in paired)})
    summary={'schema':'snd1-controlled-summary/1','candidate_id':CANDIDATE,'scope':protocol()['scope'],
             'unique_conditions':len(paired),'unique_coordinate_hashes':selection['unique_coordinate_hashes'],'matched_slots':selection['matched_slots'],
             'strata':selection['unique_strata'],'execution_calls':len(rows),'completed':sum(r['status']=='completed' for r in rows),
             'success_counts':{cell:sum(p[cell]['success'] is True for p in paired) for cell in CELLS},
             'failure_stratum_success':{cell:sum(p[cell]['success'] is True for p in fail) for cell in CELLS},
             'paired_effects':effects,'gate':{'passed':passed,'rescues_of17':rescues,'control_regressions':regressions,'hard_violations_or_unavailable':hard,
             'solver_cpu_ratio':candidate_cpu/baseline_cpu,'narrow_on_reproduction':reproduction,'all_completed_and_evaluated':no_unknown},
             'recommendation':'eligible_for_seen_panel_scoped_repair_only' if passed else 'retain_existing_method',
             'adoption':'No automatic adoption or default change; no unseen or affinity evidence.',
             'cost':{'solver_cpu_second':{cell:sum(p[cell]['solver_cpu_second'] or 0 for p in paired) for cell in CELLS},
                     'attempt_cpu_second':sum(r['actual_consumption']['cpu_second'] for r in rows),
                     'evaluation_cpu_second':sum(w.read(p)['actual_consumption']['cpu_second'] for p in (root/'evaluation').glob('*/receipt.json')),
                     'note':'Attempt includes solver. Outer run costs include attempts/evaluation. Do not add overlapping costs.'}}
    metrics={'schema':'snd1-controlled-evaluation-metrics/1','candidate_id':CANDIDATE,
             'protocol_sha256':w.pin(root/'protocol.json')['sha256'],'matched_cases':len(paired),'failure_cases':len(fail),
             'recovered_failures':rescues,'control_regressions':regressions,'hard_invariant_failures':hard,
             'candidate_cpu_ratio':candidate_cpu/baseline_cpu,
             'denominator_complete':len(rows)==len(paired)*4 and no_unknown,
             'original_pins_verified':True,'required_checks_complete':no_unknown and reproduction,
             'conclusion':summary['recommendation'],'freeze':w.pin(root/'freeze.json')}
    w.write(root/'panel-results.json',rows);w.write(root/'paired-results.json',paired);w.write(root/'summary.json',summary)
    w.write(root/'evaluation-metrics.json',metrics);return summary


def run(root):
    root=Path(root);value=w.read(root/'freeze.json');verify_freeze(root,value)
    if sorted(os.sched_getaffinity(0))!=protocol()['resources']['cpu_affinity']:
        raise ValueError('execution must use the frozen single CPU affinity24')
    if os.environ.get('CUDA_VISIBLE_DEVICES')!='' or any(os.environ.get(k)!='1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']):
        raise ValueError('frozen GPU-disabled, single-thread environment required')
    st,sc=time.perf_counter(),time.process_time();selection=w.read(root/'selection.json');systems={s['system_id']:s for s in w.read(root/'systems.json')}
    receipt_path=root/'run-receipt.json'
    if receipt_path.exists():
        receipt=w.read(receipt_path)
        for name,pin in receipt['artifacts'].items():
            if w.pin(root/name)!=pin:raise ValueError('completed run changed')
        return receipt
    fd=os.open(root/'controller.lock',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.write(fd,json.dumps({'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().split()[21]}).encode());os.close(fd)
    w.write(root/'run-identity.json',{'pid':os.getpid(),'start_ticks':Path('/proc/self/stat').read_text().split()[21],
        'affinity':sorted(os.sched_getaffinity(0)),'started_unix':time.time(),'python':sys.executable,
        'threads':{k:os.environ.get(k) for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','CUDA_VISIBLE_DEVICES']},'freeze':w.pin(root/'freeze.json')})
    completed=0
    try:
        for condition in selection['conditions']:
            cells=list(CELLS) if condition['seed']%2 else list(reversed(CELLS))
            for cell in cells:
                run_attempt(root,condition,cell,systems[condition['system_id']]);completed+=1
                w.write(root/'progress.json',{'stage':'optimization','completed_slots':completed,'total_slots':value['expected_execution_calls'],'unix':time.time()})
                if time.process_time()-sc>protocol()['resources']['cpu_cap_seconds']:raise RuntimeError('frozenCPU cap exceeded')
        w.write(root/'progress.json',{'stage':'independent_evaluation','completed_slots':completed,'unix':time.time()})
        summary=summarize(root)
        receipt={'schema':'snd1-controlled-run-receipt/1','status':'completed','freeze':w.pin(root/'freeze.json'),
            'actual_consumption':{'wall_second':time.perf_counter()-st,'cpu_second':time.process_time()-sc},
            'artifacts':{n:w.pin(root/n) for n in ['summary.json','panel-results.json','paired-results.json','evaluation-metrics.json']},'finished_unix':time.time()}
        w.write(receipt_path,receipt);return receipt
    finally:(root/'controller.lock').unlink()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['freeze','run']);parser.add_argument('--root',required=True);parser.add_argument('--parent')
    args=parser.parse_args();result=freeze(args.root,args.parent) if args.command=='freeze' else run(args.root)
    print(json.dumps({k:v for k,v in result.items() if k not in ['inputs','documents','sources','modules','parent_pins']},sort_keys=True),flush=True)
