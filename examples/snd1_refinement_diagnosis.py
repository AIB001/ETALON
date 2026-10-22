"""Bounded SND1 optimizer/constraint diagnosis using the existing objective.

Generation has no reference-pose argument. Coordinates for every accepted
iteration are saved; an independent command adds retrospective quality readouts.
These interventions diagnose a failed method, and do not promote a replacement.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import time
import numpy as np
import scipy
from scipy.optimize import minimize
from rdkit import Chem
from rdkit.Chem import AllChem
import snd1_evidence_workflow as science
from snd1_evidence_evaluation import receptor_fit
from etalon.research.scientific_input import compile_input

read, write, pin = science.read, science.write, science.identity


def intervention(parameters, arm, mol):
    p = copy.deepcopy(parameters)
    p['optimizer']['maxiter'] = 5000
    if arm == 'ligand_only':
        p['pocket']['force_constant'] = 0.
        for c in p['contacts']: c['force_constant'] = 0.
        p['mobility']['force_constant'] = 0.
    elif arm == 'tight_tether':
        p['mobility']['max_displacement_A'] = .25
    elif arm == 'no_contacts':
        for c in p['contacts']: c['force_constant'] = 0.
    elif arm == 'no_position':
        p['mobility']['force_constant'] = 0.
    elif arm == 'hydrogens_only':
        p['mobility']['fixed_indices'] = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() > 1]
    elif arm != 'full_long':
        raise ValueError('unknown diagnostic arm')
    return p


def objective_parts(mol, p):
    props = AllChem.MMFFGetMoleculeProperties(mol, mmffVariant='MMFF94s')
    if props is None: raise ValueError('MMFF unavailable')
    ff = AllChem.MMFFGetMoleculeForceField(mol, props)
    ff.Initialize()
    numbers = [a.GetAtomicNum() for a in mol.GetAtoms()]
    def parts(xyz):
        flat = tuple(xyz.ravel())
        mmff = ff.CalcEnergy(flat)
        g = np.array(ff.CalcGrad(flat)).reshape(-1, 3)
        extra, external_g = science.external_terms(xyz, numbers, p)
        return {'mmff': float(mmff), **extra}, g, external_g
    return parts


def run(bundle, baseline, output, protocol):
    output = Path(output)
    request = {'bundle': pin(bundle), 'baseline': pin(Path(baseline)/'result.json'),
        'protocol': pin(protocol), 'diagnostic_source': pin(__file__),
        'objective_source': {'path': science.__file__, **pin(science.__file__)},
        'runtime': science.old.runtime(),
        'scipy': {'version': scipy.__version__, 'path': scipy.__file__, **pin(scipy.__file__)}}
    if output.exists():
        if read(output/'request.json') != request: raise ValueError('changed request')
        prior = read(output/'result.json')
        for name, identity in prior['artifacts'].items():
            if pin(output/name) != identity: raise ValueError('changed completed artifact')
        return {'reused': True, 'optimization_calls': 0}
    output.mkdir(parents=True)
    write(output/'request.json', request)
    parameters = compile_input(read(bundle), allowed_targets=science.TARGETS)['parameters']
    base = read(Path(baseline)/'result.json')
    for name, identity in base['artifacts'].items():
        if pin(Path(baseline)/name) != identity: raise ValueError('changed baseline')
    panel = [(arm, slot) for arm in ('full_long', 'ligand_only', 'tight_tether') for slot in (1, 6, 12)]
    panel += [(arm, 1) for arm in ('no_contacts', 'no_position', 'hydrogens_only')]
    rows = []
    started, cpu = time.perf_counter(), time.process_time()
    for arm, slot in panel:
        t, c = time.perf_counter(), time.process_time()
        name = f'{arm}-{slot:03d}'
        src = next(r for r in base['rows'] if r['slot'] == slot)
        mol = Chem.SDMolSupplier(str(Path(baseline)/src['pose']), removeHs=False)[0]
        initial = mol.GetConformer().GetPositions().copy()
        p = intervention(parameters, arm, mol)
        fixed = p['mobility']['fixed_indices']
        free = [i for i in range(mol.GetNumAtoms()) if i not in fixed]
        parts = objective_parts(mol, p)
        trace = [initial.copy()]
        def unpack(x):
            xyz = initial.copy(); xyz[free] = x.reshape(-1, 3)
            return xyz
        def fun(x):
            e, gm, ge = parts(unpack(x))
            return sum(e.values()), (gm+ge)[free].ravel()
        # Callback saves only accepted coordinates; no extra force-field calls.
        def callback(xk): trace.append(unpack(xk))
        result = minimize(fun, initial[free].ravel(), method='L-BFGS-B', jac=True,
                          callback=callback, options=p['optimizer'])
        xyz = unpack(result.x)
        assert len(trace) == result.nit+1 and np.array_equal(trace[-1], xyz)
        assert np.array_equal(xyz[fixed], initial[fixed])
        trace_name = name+'.npz'
        np.savez_compressed(output/trace_name, xyz=np.array(trace))
        for i, pos in enumerate(xyz): mol.GetConformer().SetAtomPosition(i, pos)
        pose_name = name+'.sdf'
        with Chem.SDWriter(str(output/pose_name)) as writer: writer.write(mol)
        e, gm, ge = parts(xyz)
        row = {'name': name, 'arm': arm, 'slot': slot, 'seed': src['seed'],
            'initial_pose': str(Path(baseline)/src['pose']), 'initial_identity': pin(Path(baseline)/src['pose']),
            'parameters': p, 'pose': pose_name, 'trace': trace_name,
            'success': bool(result.success), 'status': int(result.status), 'message': str(result.message),
            'iterations': int(result.nit), 'nfev': int(result.nfev), 'njev': int(result.njev),
            'objective': float(result.fun), 'terms': e,
            'free_gradient_inf': float(np.abs((gm+ge)[free]).max()),
            'gtol_satisfied': bool(np.abs((gm+ge)[free]).max() <= p['optimizer']['gtol']),
            'cost': {'wall_seconds': time.perf_counter()-t, 'cpu_seconds': time.process_time()-c}}
        rows.append(row)
        write(output/'progress.json', {'rows': rows})
        print(json.dumps({k: row[k] for k in ('name', 'success', 'iterations', 'objective', 'free_gradient_inf')}), flush=True)
    result = {'schema': 'snd1-refinement-diagnosis-execution/1', 'request': request, 'rows': rows,
        'cost': {'wall_seconds': time.perf_counter()-started, 'cpu_seconds': time.process_time()-cpu},
        'artifacts': {p.name: pin(p) for p in sorted(output.iterdir()) if p.suffix in ('.sdf', '.npz')},
        'scope': 'same original starts, new diagnostic executions; no optimizer restart or target-pose conditioning'}
    write(output/'result.json', result)
    return {'reused': False, 'optimization_calls': len(rows), 'cost': result['cost']}


def analyze(bundle, input_dir, output, prior_output, reference, report):
    report = Path(report); report.mkdir(parents=True, exist_ok=False)
    output = Path(output); raw = read(output/'result.json')
    for name, identity in raw['artifacts'].items():
        if pin(output/name) != identity: raise ValueError('changed raw output')
    # Seal all output identities before opening experimental answer coordinates.
    write(report/'output-freeze.json', {'execution': pin(output/'result.json'), 'artifacts': raw['artifacts']})
    parameters = compile_input(read(bundle), allowed_targets=science.TARGETS)['parameters']
    inp = read(Path(input_dir)/'input.json'); receptor = read(Path(input_dir)/'receptor.json')
    fixed_ca = {tuple(a['key'][2:]): a['xyz'] for a in receptor['atoms'] if a['key'][-1] == 'CA'}
    mobile_ca = {tuple(a['key'][2:]): a['xyz'] for a in science.old.pdb_atoms(reference)
                 if a['protein'] and a['key'][1] == 'A' and a['key'][-1] == 'CA'}
    keys = sorted(set(fixed_ca)&set(mobile_ca))
    fit, transform = receptor_fit([fixed_ca[k] for k in keys], [mobile_ca[k] for k in keys])
    if len(keys) < 50 or fit > 2: raise ValueError('bad protein fit')
    answer, extraction = science.old.ligand(reference, 'QOV', inp['target_smiles'])
    truth = answer.GetConformer().GetPositions()@transform[:3, :3].T+transform[:3, 3]
    summary = []; series = {}; atoms = {}; old_raw = read(Path(prior_output)/'result.json')
    for row in raw['rows']:
        mol = Chem.SDMolSupplier(row['initial_pose'], removeHs=False)[0]
        heavy = Chem.RemoveHs(mol); nh = heavy.GetNumAtoms()
        assert all(a.GetAtomicNum() > 1 for a in list(mol.GetAtoms())[:nh])
        matches = answer.GetSubstructMatches(heavy, uniquify=False, maxMatches=2048, useChirality=True)
        if not matches or len(matches) == 2048: raise ValueError('incomplete mapping')
        xyz = np.load(output/row['trace'])['xyz']
        targets = np.array([truth[list(m)] for m in matches])
        differences = xyz[:, None, :nh, :] - targets[None, :, :, :]
        best = np.argmin(np.sum(differences**2, axis=(2, 3)), axis=1)
        delta = differences[np.arange(len(xyz)), best]
        nonanchor = [i for i in range(nh) if i not in parameters['mobility']['fixed_indices']]
        full = np.sqrt(np.mean(np.sum(delta**2, axis=2), axis=1))
        region = np.sqrt(np.mean(np.sum(delta[:, nonanchor]**2, axis=2), axis=1))
        p = row['parameters']; parts = objective_parts(mol, p)
        free = [i for i in range(mol.GetNumAtoms()) if i not in p['mobility']['fixed_indices']]
        indices = sorted(set([0, 1, 10, 50, 100, 250, 500, 1000, 2000, len(xyz)-1] + list(range(0, len(xyz), 10))))
        samples = []
        for i in indices:
            if i >= len(xyz): continue
            e, gm, ge = parts(xyz[i]); g = gm+ge
            samples.append({'iteration': i, 'objective': sum(e.values()), **e,
                'free_gradient_inf': float(np.abs(g[free]).max()),
                'mmff_gradient_norm': float(np.linalg.norm(gm[free])),
                'external_gradient_norm': float(np.linalg.norm(ge[free])),
                'full_rmsd_A': float(full[i]), 'nonanchor_rmsd_A': float(region[i])})
        start, end = samples[0], samples[-1]
        result = {k: row[k] for k in ('name', 'arm', 'slot', 'success', 'message', 'iterations', 'nfev', 'gtol_satisfied', 'cost')}
        result.update(start=start, final=end, min_observed_nonanchor_rmsd_A=float(region.min()),
            min_quality_iteration=int(np.argmin(region)),
            sampled_penalty_active_fraction={k: float(np.mean([s[k] > 1e-8 for s in samples]))
                for k in ('protein_exclusion', 'contact_distance', 'position_restraint')})
        if row['arm'] == 'full_long':
            oldrow = next(r for r in old_raw['rows'] if r['slot'] == row['slot'])
            prior = Chem.SDMolSupplier(str(Path(prior_output)/oldrow['pose']), removeHs=False)[0]
            difference = float(np.abs(xyz[500]-prior.GetConformer().GetPositions()).max())
            checkpoint = next(s for s in samples if s['iteration'] == 500)
            error = abs(checkpoint['objective']-oldrow['final_objective'])
            assert difference <= .000051 and error < 1e-7, (difference, error)
            result.update(iteration500=checkpoint, old_pose_max_component_difference_A=difference,
                old_objective_error=error, reproducible_prefix=True)
        final_mol = Chem.SDMolSupplier(str(output/row['pose']), removeHs=False)[0]
        assert science.old.canonical_smiles(final_mol) == science.old.canonical_smiles(mol)
        assert np.max(np.abs(xyz[-1]-final_mol.GetConformer().GetPositions())) <= .000051
        result.update(final_contacts=science.metrics(final_mol, parameters))
        rows = []
        for i in range(nh):
            a = answer.GetAtomWithIdx(matches[best[-1]][i])
            rows.append({'index': i, 'element': mol.GetAtomWithIdx(i).GetSymbol(),
                'reference_name': a.GetPDBResidueInfo().GetName().strip(),
                'fixed_anchor': i in parameters['mobility']['fixed_indices'],
                'initial_error_A': float(np.linalg.norm(delta[0, i])),
                'final_error_A': float(np.linalg.norm(delta[-1, i])),
                'movement_A': float(np.linalg.norm(xyz[-1, i]-xyz[0, i]))})
        atoms[row['name']] = sorted(rows, key=lambda a: a['final_error_A']**2-a['initial_error_A']**2, reverse=True)
        series[row['name']] = samples; summary.append(result)
    write(report/'trace-metrics.json', series); write(report/'atom-movements.json', atoms)
    groups = {}
    for arm in sorted({r['arm'] for r in summary}):
        rows = [r for r in summary if r['arm'] == arm]
        groups[arm] = {'n': len(rows), 'optimizer_successes': sum(r['success'] for r in rows),
            'gtol_successes': sum(r['gtol_satisfied'] for r in rows),
            'median_full_rmsd_A': float(np.median([r['final']['full_rmsd_A'] for r in rows])),
            'median_nonanchor_rmsd_A': float(np.median([r['final']['nonanchor_rmsd_A'] for r in rows])),
            'median_objective': float(np.median([r['final']['objective'] for r in rows]))}
    result = {'schema': 'snd1-refinement-diagnosis-analysis/1', 'reference': extraction,
        'protein_fit_rmsd_A': fit, 'matched_ca': len(keys), 'rows': summary, 'groups': groups,
        'no_affinity_or_independent_transfer_claim': True,
        'scope': 'posthoc diagnosis on previously seen structures; no replacement promoted; seed slots correlated'}
    write(report/'summary.json', result)
    print(json.dumps(groups, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); commands = p.add_subparsers(dest='command', required=True)
    g = commands.add_parser('run')
    for key in ('bundle', 'baseline', 'output', 'protocol'): g.add_argument('--'+key, required=True)
    a = commands.add_parser('analyze')
    for key in ('bundle', 'input-dir', 'output', 'prior-output', 'reference', 'report'): a.add_argument('--'+key, required=True)
    args = vars(p.parse_args()); command = args.pop('command')
    result = (run if command == 'run' else analyze)(**args)
    if result is not None: print(json.dumps(result))
