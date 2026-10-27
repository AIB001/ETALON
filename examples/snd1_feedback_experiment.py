"""Run a frozen local QOV comparison; resume native handles without new work.

Usage: python snd1_feedback_experiment.py CONFIG.json [--launch-only]
Config supplies explicit source case, runtime and output paths. Native results,
including failures, are retained. No shared environment or default entry changes.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import numpy as np

import snd1_anchor_intervention as anchor
import snd1_contact_program as contact
import snd1_feedback_search as search
import snd1_paired_relaxation as relaxation
import snd1_task_learning as chemistry


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(body, indent=2, allow_nan=False) + '\n'
    if path.exists():
        if read(path) != body:
            raise ValueError('Immutable result already exists: ' + str(path))
        return
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(data, encoding='utf-8'); temp.replace(path)


def capture(folder, name, argv, *, stdin=None, timeout=600):
    """A completed exact command is reused; uncertain command outcomes are inspected."""
    folder.mkdir(parents=True, exist_ok=True)
    receipt = folder / (name + '.receipt.json')
    intent = folder / (name + '.intent.json')
    if receipt.exists():
        saved = read(receipt)
        if saved['argv'] != argv or saved['stdin'] != stdin:
            raise ValueError('Command identity changed')
        if saved['returncode']:
            raise RuntimeError('Retained failed command: ' + str(receipt))
        return saved
    if intent.exists():
        raise RuntimeError('Inspect incomplete command before retry: ' + str(intent))
    save(intent, dict(argv=argv, stdin=stdin))
    start = time.perf_counter()
    with (folder / (name + '.stdout')).open('w') as out, (folder / (name + '.stderr')).open('w') as err:
        env = os.environ.copy()
        if '-ntomp' in argv:
            env['OMP_NUM_THREADS'] = argv[argv.index('-ntomp') + 1]
        result = subprocess.run(argv, cwd=folder, input=stdin, text=True, stdout=out,
                                stderr=err, timeout=timeout, env=env)
    saved = dict(argv=argv, stdin=stdin, returncode=result.returncode,
                 wall_seconds=time.perf_counter()-start)
    save(receipt, saved)
    if result.returncode:
        raise RuntimeError('Native command failed: ' + str(receipt))
    return saved


def context(config):
    import mdtraj as md
    from rdkit import Chem
    root = Path(config['case_root'])
    pairpath = next((root / 'pairs').glob('*/pair.json'))
    pair = read(pairpath); pairdir = pairpath.parent
    native = root / 'primary' / pair['run_id'] / 'input'
    frame = md.load(str(pairdir / 'frame.gro'))
    offset, end = pair['ligand_offset'], pair['ligand_offset'] + pair['ligand_atoms']
    solute = frame.atom_slice(np.arange(end))
    mol = Chem.SDMolSupplier(str(pairdir / 'exact-input.sdf'), removeHs=False)[0]
    mapping = dict(ligand_indices0=list(range(offset, end)),
                   ligand_elements=[a.GetSymbol() for a in mol.GetAtoms()])
    triples, labels, typing = chemistry.chemistry('7KNX-QOV', mapping, solute.topology,
        root=native, ligand_path=pairdir/'exact-input.sdf', coordinates_path=pairdir/'frame.gro')
    atoms, bonds = chemistry.topology_sections(native/'ligand.itp')
    chemical = anchor.molecular_model(mol, atoms, bonds)
    case = dict(trajectory=solute, triples=triples, triple_residues=labels,
                reference_contacts=['ASN281','ARG255'], residues=sorted(set(labels)))
    return anchor.frame_context(case, chemical, 0), pair, pairdir, native, frame


def quality(xyz, ctx):
    chemical = ctx['chemical']
    _, _, stereo = anchor.shape_check(xyz, ctx['ligand'], chemical)
    strain = float(chemical['forcefield'].CalcEnergy(xyz.ravel().tolist())-ctx['input_energy'])
    ratio = anchor.clash_ratio(xyz, ctx)
    return dict(acceptable=bool(stereo and strain<=10 and ratio>=.65),
                stereo_preserved=bool(stereo), strain_change_kcal_mol=strain,
                min_protein_vdw_ratio=ratio)


def prepare(config, root):
    import mdtraj as md
    if (root/'SCREEN.json').exists():
        frozen=read(root/'FREEZE.json')
        if frozen['config']!=config:
            raise ValueError('Frozen scientific configuration changed')
        for expected in frozen['sources']+frozen['inputs']:
            if relaxation.pin(expected['path'])!=expected:
                raise ValueError('Frozen input or producer changed')
        return read(root/'SCREEN.json')
    ctx, pair, pairdir, native, frame = context(config)
    freeze = dict(config=config, python=sys.executable,
        sources=[relaxation.pin(p) for p in [__file__, anchor.__file__, contact.__file__,
                  search.__file__, relaxation.__file__, chemistry.__file__]],
        inputs=[relaxation.pin(p) for p in [pairdir/'frame.gro', pairdir/'exact-input.sdf',
                 pairdir/'pair.json', *[native/n for n in ['topol.top','ligand.itp',
                       'ligand-atomtypes.itp','posre.itp','reference.gro']]]])
    save(root/'FREEZE.json', freeze)
    if (root/'SCREEN.json').exists():
        return read(root/'SCREEN.json')
    start = time.perf_counter(); table = []
    for mode in ('rigid','coupled'):
        mode_dir = root/mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        poses, info = search.proposals(ctx, budget=config['proposal_budget'], mode=mode)
        indices, scores = search.shortlist(poses, ctx, limit=config['native_shortlist'])
        save(mode_dir/'GENERATION.json', dict(info=info, scores=scores, shortlist=indices))
        rows = []
        for index in indices:
            folder = mode_dir/str(index); folder.mkdir(exist_ok=True)
            if (folder/'RESULT.json').exists():
                rows.append(read(folder/'RESULT.json')); continue
            for name in ('topol.top','ligand.itp','ligand-atomtypes.itp','posre.itp','reference.gro'):
                shutil.copyfile(native/name, folder/name)
            replacements = dict(zip(range(ctx['offset'],ctx['offset']+len(poses[index])), poses[index]/10))
            (folder/'start.gro').write_text(relaxation.replace_gro_coordinates(
                (pairdir/'frame.gro').read_text(), replacements))
            (folder/'em.mdp').write_text(relaxation.relaxation_mdp(mode='minimize',steps=config['em_steps']))
            gmx = config['gmx']
            from etalon.research.method_reuse import request_identity,execute_or_reuse
            from etalon.research.evidence_refs import file_identity
            # This compiled, preprocessed topology includes the actual force-field
            # dependencies; compare it along with coordinates and effective MDP.
            capture(folder,'compile',[gmx,'grompp','-f','em.mdp','-c','start.gro','-r',
                    'reference.gro','-p','topol.top','-o','em.tpr','-po','effective.mdp','-pp','processed.top'])
            def normalized(path):
                return '\n'.join(line.split(';',1)[0].strip() for line in path.read_text().splitlines()
                    if line.split(';',1)[0].strip())
            request=request_identity(operation='native-local-minimization',
                implementation={'driver':file_identity(__file__), 'gmx':file_identity(gmx)},
                environment={'python':sys.executable,'GMXLIB':config['gmx_library']},
                inputs={'coordinates':file_identity(folder/'start.gro'),
                        'reference':file_identity(folder/'reference.gro'),
                        'topology':normalized(folder/'processed.top'),
                        'effective_parameters':normalized(folder/'effective.mdp')},
                parameters={'threads':config['em_threads'],'nb':'cpu','pme':'cpu'})
            def minimize():
                capture(folder,'minimize',[gmx,'mdrun','-s','em.tpr','-deffnm','em','-ntmpi','1',
                    '-ntomp',str(config['em_threads']),'-nb','cpu','-pme','cpu','-pin','off'])
                artifacts=[folder/n for n in ['em.gro','em.edr','em.log','minimize.stderr','minimize.receipt.json']]
                return {'result':{'directory':str(folder)},'artifacts':artifacts}
            reused=execute_or_reuse(root/'method-cache',request,minimize)
            if reused['receipt']['status']!='complete':
                raise RuntimeError('Native minimization failed; inspect retained receipt')
            if reused['reused']:
                for original in reused['receipt']['artifacts']:
                    shutil.copyfile(original['path'],folder/Path(original['path']).name)
            save(folder/'REUSE.json',reused)
            log = (folder/'minimize.stderr').read_text()
            match = re.search(r'Maximum force\s*=\s*([0-9.eE+-]+)',log)
            force = float(match[1]) if match else None
            capture(folder,'potential',[gmx,'energy','-f','em.edr','-o','potential.xvg'],stdin='Potential\n0\n')
            energies = np.array([[float(x) for x in line.split()] for line in
                (folder/'potential.xvg').read_text().splitlines() if line and line[0] not in '#@'])
            relaxed = md.load(str(folder/'em.gro')).atom_slice(np.arange(ctx['offset']+len(poses[index])))
            rc = anchor.frame_context({**ctx['case'],'trajectory':relaxed},ctx['chemical'],0)
            q = quality(rc['ligand'], {**rc,'ligand':ctx['ligand'],'input_energy':ctx['input_energy']})
            contacts = relaxation.contact_series(relaxed.xyz.astype(float)*10,
                relaxed.unitcell_vectors.astype(float)*10, ctx['case']['triples'],ctx['case']['triple_residues'])[0]
            flags = {k:contacts[k]['primary'] for k in ['ASN281','ARG255','SER254']}
            flags['ASN_ARG'] = flags['ASN281'] and flags['ARG255']
            row = dict(mode=mode,index=index,Fmax_kJ_mol_nm=force,converged=force is not None and force<1000,
                input_coordinate_identity=relaxation.pin(folder/'start.gro')['sha256'],
                potential_kJ_mol=float(energies[-1,1]),quality=q,contacts=flags,
                core_movement_A=float(np.sqrt(np.mean(np.sum((rc['ligand'][ctx['chemical']['core']]-
                    ctx['ligand'][ctx['chemical']['core']])**2,axis=1)))),
                em_gro=relaxation.pin(folder/'em.gro'), initial=scores[index])
            save(folder/'RESULT.json',row); rows.append(row)
        table.append(dict(mode=mode, generated=len(poses), rows=rows,
                          selection=search.select_relaxed(rows)))
    result = dict(arms=table, preparation_wall_seconds=time.perf_counter()-start,
        proposal_budget_per_arm=config['proposal_budget'],max_native_budget_per_arm=config['native_shortlist'],
        selection_frozen_before_MD=True)
    save(root/'SCREEN.json',result)
    return result


def advance(config, root, *, launch_only=False, recover_unstarted=False, selection_overrides=None):
    import mdtraj as md
    from prism.workflow.checkpoint import GromacsJob
    screen = prepare(config,root)
    overrides=selection_overrides or {}
    if overrides:
        import hashlib
        key=hashlib.sha256(json.dumps(overrides,sort_keys=True).encode()).hexdigest()[:12]
        save(root/('SELECTION-'+key+'.json'),dict(overrides=overrides,
             source=relaxation.pin(__file__),screen=relaxation.pin(root/'SCREEN.json'),
             reason='Explicit new candidate choice; existing completed controls are reused'))
    else:key=None
    ctx, pair, _, _, _ = context(config)
    jobs=[]
    for arm in screen['arms']:
        selected=overrides.get(arm['mode'],arm['selection']['selected'])
        if selected is None:
            continue
        if not any(r['index']==selected and r['converged'] and r['quality']['acceptable'] for r in arm['rows']):
            raise ValueError('Selected candidate has no eligible native preparation')
        source=root/arm['mode']/str(selected)
        selected_row=next(r for r in arm['rows'] if r['index']==selected)
        if relaxation.pin(source/'em.gro')!=selected_row['em_gro']:
            raise ValueError('Selected native prepared coordinates changed')
        for seed in config['velocity_seeds']:
            changed=arm['mode'] in overrides
            recover_this=recover_unstarted and not changed
            folder=root/('dynamics-selection-'+key if changed else 'dynamics')/arm['mode']/str(seed)
            folder.mkdir(parents=True,exist_ok=True)
            jobroot=folder/('mdjob-runtime02' if recover_this else 'mdjob')
            if recover_this and not jobroot.exists():
                old=GromacsJob(folder/'mdjob')
                old_state=old.inspect()
                stderr=(old.root/'attempt-001/supervisor.stderr').read_text()
                if old_state['status']!='interrupted' or list(old.root.glob('md.*')):
                    raise ValueError('Recovery is only for an unstarted native calculation')
                if 'ModuleNotFoundError' not in stderr or 'prism' not in stderr:
                    raise ValueError('Unexpected launcher failure; inspect original handle')
                save(folder/'RUNTIME_RECOVERY.json',dict(previous_handle=str(old.root),
                    previous_state=old_state,stderr=relaxation.pin(old.root/'attempt-001/supervisor.stderr'),
                    native_outputs_absent=True,correction='Propagate explicit PRISM source to subprocess PYTHONPATH'))
            if not (jobroot/'contract.json').exists():
                for name in ['topol.top','ligand.itp','ligand-atomtypes.itp','posre.itp','reference.gro','em.gro']:
                    shutil.copyfile(source/name,folder/name)
                (folder/'md.mdp').write_text(relaxation.relaxation_mdp(mode='dynamics',
                    steps=config['md_steps'],seed=seed))
                capture(folder,'compile',[config['gmx'],'grompp','-f','md.mdp','-c','em.gro',
                    '-r','reference.gro','-p','topol.top','-o','md.tpr','-po','effective.mdp'])
                job=GromacsJob.create(jobroot,folder/'md.tpr',threads=config['md_threads'],
                    provenance=dict(freeze=relaxation.pin(root/'FREEZE.json'),
                                    selection={'selected':selected,'method_selection':arm['selection'],
                                               'explicit_override':changed},
                                    contacts_forced=False,mode=arm['mode'],seed=seed))
            else:
                job=GromacsJob(jobroot)
            jobs.append((arm['mode'],seed,folder,job))
    handle_file=root/('HANDLES-'+key+'.json' if key else
                     'HANDLES-runtime02.json' if recover_unstarted else 'HANDLES.json')
    save(handle_file,[dict(mode=m,seed=s,root=str(j.root)) for m,s,f,j in jobs])
    # Fixed task concurrency. Existing native handles own execution and recovery.
    while True:
        states=[j.inspect() for _,_,_,j in jobs]
        active=sum(s['status'] in ('running','launching') for s in states)
        for (_,_,_,job),state in zip(jobs,states):
            if active>=config['concurrent_jobs']:break
            if state['status']=='ready':
                job.start();active+=1
            elif state['status']=='paused':
                job.start(resume=True);active+=1
        if launch_only:
            return dict(status='native_handles_persisted',handles=str(handle_file))
        if not active:break
        time.sleep(3)
    results=[]
    for mode,seed,folder,job in jobs:
        if (folder/'RESULT.json').exists():
            results.append(read(folder/'RESULT.json'));continue
        state=job.inspect()
        if state['status']!='succeeded':
            results.append(dict(mode=mode,seed=seed,status=state['status'],handle=str(job.root)));continue
        traj=md.load(str(job.root/'md.xtc'),top=str(folder/'em.gro')).atom_slice(np.arange(ctx['offset']+len(ctx['ligand'])))
        contacts=relaxation.contact_series(traj.xyz.astype(float)*10,traj.unitcell_vectors.astype(float)*10,
                                           ctx['case']['triples'],ctx['case']['triple_residues'])
        late=np.asarray(traj.time)>=float(traj.time[-1])-50
        dual=np.array([c['ASN281']['primary'] and c['ARG255']['primary'] for c in contacts])
        rc=anchor.frame_context({**ctx['case'],'trajectory':traj},ctx['chemical'],len(traj)-1)
        q=quality(rc['ligand'],{**rc,'ligand':ctx['ligand'],'input_energy':ctx['input_energy']})
        counts={k:int(sum(c[k]['primary'] for c,l in zip(contacts,late) if l))
                for k in ['ASN281','ARG255','SER254','ASN281_or_ARG255']}
        row=dict(mode=mode,seed=seed,status='succeeded',late_count=int(dual[late].sum()),
            late_samples=int(late.sum()),dual_retained=bool(dual[late].mean()>=.5 and dual[-1] and q['acceptable']),
            final_dual=bool(dual[-1]),quality=q,late_anchor_counts=counts,handle=str(job.root),
            trajectory=relaxation.pin(job.root/'md.xtc'))
        save(folder/'CONTACTS.json',contacts);save(folder/'RESULT.json',row);results.append(row)
    expected=2*len(config['velocity_seeds'])
    decision=search.followup(results) if len(results)==expected else dict(outcome='unresolved',
        action='interpret_available_results',reason='One arm did not produce eligible native candidates')
    result=dict(schema='snd1-feedback-comparison/1',rows=results,screen=screen,decision=decision,
        scope='One seen molecular starting state; fresh velocity repeats, not independent molecules',
        primary_endpoint='late 50 ps dual occupancy >= 0.5, final dual and original physical quality checks',
        synthesis='Chemical graph unchanged; original route identity retained, no new synthesis claim',
        full_host_tokens=None)
    if key:result['selection_override']=dict(overrides=overrides,key=key,
        scope='Exploratory changed candidate versus reused controls; original selection outcome retained')
    save(root/('RESULT-'+key+'.json' if key else 'RESULT-02.json' if recover_unstarted else 'RESULT.json'),result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path);parser.add_argument('--launch-only',action='store_true')
    parser.add_argument('--recover-unstarted',action='store_true',help='Recover the observed pre-native import failure')
    parser.add_argument('--selection-overrides',type=Path,help='Explicit new choice among saved native candidates')
    args=parser.parse_args(); config=read(args.config)
    os.environ['PATH']=str(Path(config['gmx']).parent)+os.pathsep+os.environ['PATH']
    os.environ['GMXLIB']=config['gmx_library']
    os.environ['OMP_NUM_THREADS']=str(config['md_threads'])
    os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['CUDA_VISIBLE_DEVICES']=''
    os.environ['PYTHONPATH']=config['prism_source']+os.pathsep+os.environ.get('PYTHONPATH','')
    sys.path.insert(0,config['prism_source'])
    root=Path(config['output']);root.mkdir(parents=True,exist_ok=True)
    result=advance(config,root,launch_only=args.launch_only,recover_unstarted=args.recover_unstarted,
                   selection_overrides=read(args.selection_overrides) if args.selection_overrides else None)
    print(json.dumps({k:result[k] for k in ['status','handles','decision','rows'] if k in result}))


if __name__=='__main__':
    main()
