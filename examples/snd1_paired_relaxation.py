"""Parameterized paired relaxation/readout for an already mapped SND1 ligand.

This is a short, conditional local basin probe. No contact term is added to the
Hamiltonian. It does not predict affinity or claim equilibrium persistence.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np


def pin(path):
    path = Path(path); digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return {'path': str(path), 'sha256': digest.hexdigest(), 'bytes': path.stat().st_size}


def chemical_identity(molecule):
    from rdkit import Chem
    mol = Chem.RemoveHs(Chem.Mol(molecule))
    return dict(canonical_isomeric_smiles=Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True),
                formal_charge=Chem.GetFormalCharge(mol), heavy_atoms=mol.GetNumAtoms(),
                stereochemistry='retained; no tautomer, protonation or charge normalization',
                canonicalization='RDKit RemoveHs then MolToSmiles(canonical=True,isomericSmiles=True)')


def minimum_image(vectors, box):
    return (np.asarray(vectors) @ np.linalg.inv(box) - np.rint(np.asarray(vectors) @ np.linalg.inv(box))) @ box


def place_candidate(full_solute, reference_solute, candidate, ligand_indices, box, tolerance_A=.025):
    """Require an exact periodic frame match before inserting the new pose."""
    full_solute, reference_solute = np.asarray(full_solute), np.asarray(reference_solute)
    error = np.linalg.norm(minimum_image(full_solute-reference_solute, box), axis=1)
    if np.max(error) > tolerance_A: raise ValueError('Full-system frame does not match candidate producer frame')
    ids = np.asarray(ligand_indices)
    lattice = np.rint((full_solute[ids].mean(0)-reference_solute[ids].mean(0)) @ np.linalg.inv(box)) @ box
    placed = np.asarray(candidate)+lattice
    return placed, dict(maximum_periodic_solute_error_A=float(np.max(error)), lattice_translation_A=lattice.tolist())


def replace_gro_coordinates(template, replacements_nm):
    """Keep native atom/residue identifiers; never round-trip through a renamer."""
    lines=template.splitlines();count=int(lines[1]);output=lines[:2]
    if len(lines)!=count+3:raise ValueError('Unexpected GRO record count')
    for index,line in enumerate(lines[2:-1]):
        dots=[i for i,c in enumerate(line[20:],start=20) if c=='.']
        width=dots[1]-dots[0]
        xyz=[float(line[20+k*width:20+(k+1)*width]) for k in range(3)]
        if index in replacements_nm:xyz=replacements_nm[index]
        output.append(line[:20]+''.join(f'{float(x):11.6f}' for x in xyz))
    return '\n'.join(output+[lines[-1]])+'\n'


def relaxation_mdp(*, mode, steps, seed=20261026, temperature_K=300, dt_ps=.002):
    if mode not in ('minimize', 'dynamics') or steps <= 0: raise ValueError('Invalid relaxation mode/budget')
    common = dict(define='-DPOSRES', **{'cutoff-scheme':'Verlet', 'nstlist':20, 'rlist':1.0,
        'coulombtype':'PME', 'rcoulomb':1.0, 'vdwtype':'Cut-off', 'rvdw':1.0,
        'vdw-modifier':'Potential-shift', 'DispCorr':'EnerPres', 'fourierspacing':.12,
        'pme_order':4, 'ewald_rtol':1e-5, 'pbc':'xyz', 'constraints':'h-bonds',
        'constraint_algorithm':'lincs', 'lincs_order':4, 'nsteps':int(steps), 'nstenergy':100,
        'nstlog':500, 'nstxout':0, 'nstvout':0, 'nstfout':0})
    if mode == 'minimize': common.update(integrator='steep', emtol=1000, emstep=.005, **{'nstxout-compressed':0})
    else:
        common.update(integrator='md', dt=dt_ps, continuation='no', tcoupl='V-rescale',
            **{'tc-grps':'System', 'tau-t':.1, 'ref-t':temperature_K, 'pcoupl':'no',
               'gen-vel':'yes', 'gen-temp':temperature_K, 'gen-seed':int(seed),
               'nstxout-compressed':500, 'nstcalcenergy':100, 'comm-mode':'Linear', 'nstcomm':100})
    return '\n'.join(f'{key} = {value}' for key,value in common.items())+'\n'


def scale_protein_restraints(text, coefficient):
    if coefficient <= 0: raise ValueError('Positive restraint strength required')
    result=[]; count=0
    for line in text.splitlines():
        bits=line.split(';',1)[0].split()
        if len(bits)==5 and bits[0].isdigit():
            line=f'{bits[0]} {bits[1]} {coefficient:g} {coefficient:g} {coefficient:g}'; count+=1
        result.append(line)
    if not count: raise ValueError('No protein restraint records')
    return '\n'.join(result)+'\n', count


def contact_series(xyz_A, boxes_A, triples, residues, *, distance_A=3.0, angle_deg=150):
    xyz=np.asarray(xyz_A); triples=np.asarray(triples); labels=np.asarray(residues); rows=[]
    for points,box in zip(xyz,boxes_A):
        v=minimum_image(points[triples[:,0]]-points[triples[:,1]],box)
        w=minimum_image(points[triples[:,2]]-points[triples[:,1]],box)
        da=np.linalg.norm(minimum_image(points[triples[:,0]]-points[triples[:,2]],box),axis=1)
        angle=np.degrees(np.arccos(np.clip((v*w).sum(1)/np.linalg.norm(v,axis=1)/np.linalg.norm(w,axis=1),-1,1)))
        primary=(da<=distance_A)&(angle>=angle_deg); sensitive=(da<=3.5)&(angle>=angle_deg)
        row={}
        for name,selected in [('SER254',['SER254']),('ASN281',['ASN281']),('ARG255',['ARG255']),('ASN281_or_ARG255',['ASN281','ARG255'])]:
            mask=np.isin(labels,selected)
            loss=np.maximum(da/3-1,0)**2+np.maximum((150-angle)/30,0)**2
            best=int(np.argmin(np.where(mask,loss,np.inf)))
            row[name]=dict(primary=bool(primary[mask].any()),sensitivity=bool(sensitive[mask].any()),
                best_DA_A=float(da[best]), best_DHA_deg=float(angle[best]), residue=str(labels[best]), triple=triples[best].tolist())
        rows.append(row)
    return rows


def persistence_summary(times_ps, contacts, *, last_window_ps=50):
    times=np.asarray(times_ps); late=times>=times[-1]-last_window_ps
    targets={}
    for name in ('SER254','ASN281','ARG255','ASN281_or_ARG255'):
        values=np.asarray([row[name]['primary'] for row in contacts]); sensitive=np.asarray([row[name]['sensitivity'] for row in contacts])
        targets[name]=dict(whole_fraction=float(values.mean()),late_fraction=float(values[late].mean()),
            late_sensitivity_fraction=float(sensitive[late].mean()), final=contacts[-1][name],
            first_absent_ps=float(times[np.flatnonzero(~values)[0]]) if (~values).any() else None,
            longest_consecutive_samples=max((len(x) for x in ''.join('1' if b else '0' for b in values).split('0')),default=0))
    targets['dual_ASN_ARG_fraction']=float(np.mean([x['ASN281']['primary'] and x['ARG255']['primary'] for x in contacts]))
    targets['SER_plus_ASN_ARG_fraction']=float(np.mean([x['SER254']['primary'] and x['ASN281_or_ARG255']['primary'] for x in contacts]))
    return dict(frames=len(times), time_ps=[float(times[0]),float(times[-1])], late_frames=int(late.sum()), targets=targets)


def choose_successor(rows):
    """Select one weaker-receptor-restraint pair from real paired feedback."""
    paired=[]
    for run in sorted({row['run_id'] for row in rows}):
        arms={row['arm']:row for row in rows if row['run_id']==run}
        if set(arms)!= {'input','candidate'} or any(r.get('status')!='succeeded' for r in arms.values()): continue
        c=arms['candidate']['readout']['targets']['ASN281_or_ARG255']['late_fraction']
        i=arms['input']['readout']['targets']['ASN281_or_ARG255']['late_fraction']
        paired.append((c-i,c,-arms['candidate']['initial_core_movement_A'],run))
    if not paired: return dict(choice='stop_diagnose_native_failure', reason='No completed comparable pair', new_jobs=0)
    best=max(paired)
    return dict(choice='weaker_protein_restraint_pair', run_id=best[3], protein_restraint_kJ_mol_nm2=100,
        dynamics_ps=100, new_jobs=2, reason='Test whether the best actual paired survival advantage depends on the strong receptor restraint',
        primary_late_occupancy_advantage=best[0], primary_candidate_late_occupancy=best[1],
        tie_break='candidate occupancy, then smaller original core movement; deterministic run id')
