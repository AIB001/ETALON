"""Task-local, chemistry-typed directional readout of existing SND1 MD.

Scientific analysis and a small replaceable sampling method, not a harness
stage. The caller supplies frozen inputs and stores outputs with the existing
research publication interfaces. Never starts MD or modifies input data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

import numpy as np

P4 = Path('/home/zx/etalon/runs/snd1-scale-20261024/p4')
ANALYSIS = Path('/home/zx/etalon/runs/etalon-research-closeout-20261025/md/analysis-v2/runs')
EXTENSION = Path('/home/zx/etalon/runs/etalon-linux-continuity-20261026/science/successor-contact-topology')
DEVELOPMENT = '7KNX-QOV-seed-202610242'
EVALUATION = ['7KNX-QOV-seed-202610241', '7KNX-QOV-seed-202610243',
              '7KNW-QOY-seed-202610241', '7KNW-QOY-seed-202610242', '7KNW-QOY-seed-202610243']
SCHEMA = 'snd1-directional-task-learning/1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def pin(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return dict(path=str(path), sha256=digest.hexdigest(), bytes=path.stat().st_size)


def topology_sections(path):
    """Read explicit local atoms/bonds; do not infer donor-H bonds by distance."""
    atoms, bonds, section = [], [], ''
    for line in Path(path).read_text('utf-8').splitlines():
        line = line.split(';', 1)[0].strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('['):
            section = line.strip('[] ').strip()
            continue
        fields = line.split()
        if section == 'atoms':
            atoms.append(dict(index=int(fields[0]) - 1, type=fields[1], resid=int(fields[2]),
                              residue=fields[3], name=fields[4], charge=float(fields[6]), mass=float(fields[7])))
        elif section == 'bonds':
            bonds.append((int(fields[0]) - 1, int(fields[1]) - 1))
    if [a['index'] for a in atoms] != list(range(len(atoms))):
        raise ValueError('Atom topology must be a single consecutive molecule')
    return atoms, bonds


def chemistry(system, mapping, topology, *, root=None, ligand_path=None, coordinates_path=None):
    from rdkit import Chem, RDConfig
    from rdkit.Chem import ChemicalFeatures
    root = Path(root) if root is not None else P4 / 'controls' / system
    protein, protein_bonds = topology_sections(root / 'topol.top')
    ligand, ligand_bonds = topology_sections(root / 'ligand.itp')
    offset = len(protein)
    if list(mapping['ligand_indices0']) != list(range(offset, offset + len(ligand))):
        raise ValueError('Ligand index map differs from explicit native topology')
    ligand_path = ligand_path or P4 / 'inputs' / system.split('-')[1] / 'ligand.sdf'
    molecule = Chem.SDMolSupplier(str(ligand_path), removeHs=False)[0]
    if molecule is None or molecule.GetNumAtoms() != len(ligand):
        raise ValueError('Missing explicit-H ligand graph')
    gro_lines = Path(coordinates_path or root / 'complex.gro').read_text('utf-8').splitlines()
    raw_names = [line[10:15].strip() for line in gro_lines[2:-1]]
    # MDTraj normalizes e.g. ILE CD -> CD1 and H1 -> H. Native GRO and ITP
    # names, element identities and indexed positions establish the binding.
    for index, atom in enumerate([*protein, *ligand]):
        if raw_names[index] != atom['name']:
            raise ValueError('Native GRO and ITP atom-name ordering differs')
    if [a.GetSymbol() for a in molecule.GetAtoms()] != mapping['ligand_elements']:
        raise ValueError('Ligand element identity differs from original indexed mapping')
    if {tuple(sorted((b.GetBeginAtomIdx(), b.GetEndAtomIdx()))) for b in molecule.GetBonds()} != {tuple(sorted(b)) for b in ligand_bonds}:
        raise ValueError('Ligand SDF and native topology bonds differ')
    fdef = Path(RDConfig.RDDataDir) / 'BaseFeatures.fdef'
    factory = ChemicalFeatures.BuildFeatureFactory(str(fdef))
    features = factory.GetFeaturesForMol(molecule)
    ligand_acceptors = sorted({offset + i for f in features if f.GetFamily() == 'Acceptor'
                              for i in f.GetAtomIds() if molecule.GetAtomWithIdx(i).GetSymbol() in {'N', 'O'}})
    ligand_donor_atoms = {i for f in features if f.GetFamily() == 'Donor' for i in f.GetAtomIds()
                          if molecule.GetAtomWithIdx(i).GetSymbol() in {'N', 'O'}}
    ligand_donors = []
    for a, b in ligand_bonds:
        for heavy, hydrogen in ((a, b), (b, a)):
            if heavy in ligand_donor_atoms and molecule.GetAtomWithIdx(hydrogen).GetAtomicNum() == 1:
                ligand_donors.append((heavy + offset, hydrogen + offset))
    adjacency = {i: set() for i in range(len(protein))}
    for a, b in protein_bonds:
        adjacency[a].add(b)
        adjacency[b].add(a)
    protein_donors, protein_acceptors, unknowns = [], [], []
    supported = {'ALA', 'ARG', 'ASN', 'ASP', 'CYS', 'GLN', 'GLU', 'GLY', 'HIS', 'HID', 'HIE', 'HIP',
                 'ILE', 'LEU', 'LYS', 'LYN', 'MET', 'PHE', 'PRO', 'SER', 'THR', 'TRP', 'TYR', 'VAL'}
    side_oxygen = {'ASP': {'OD1', 'OD2'}, 'GLU': {'OE1', 'OE2'}, 'ASN': {'OD1'}, 'GLN': {'OE1'},
                   'SER': {'OG'}, 'THR': {'OG1'}, 'TYR': {'OH'}}
    for atom in protein:
        i, name, residue = atom['index'], atom['name'], atom['residue']
        if residue not in supported:
            unknowns.append(dict(atom=i, residue=residue, reason='unsupported protein residue chemistry'))
            continue
        element = topology.atom(i).element.symbol
        hydrogens = [j for j in adjacency[i] if protein[j]['mass'] < 2]
        if element in {'N', 'O'}:
            protein_donors.extend((i, j) for j in hydrogens)
        if name in {'O', 'OXT', 'OC1', 'OC2', 'OT1', 'OT2'} or name in side_oxygen.get(residue, set()):
            protein_acceptors.append(i)
        elif residue in {'HIS', 'HID', 'HIE', 'HIP'} and name in {'ND1', 'NE2'} and not hydrogens:
            protein_acceptors.append(i)
    triples, residue_ids, descriptions = [], [], []
    for donors, acceptors, protein_is_donor in ((protein_donors, ligand_acceptors, True),
                                               (ligand_donors, protein_acceptors, False)):
        for donor, hydrogen in donors:
            for acceptor in acceptors:
                atom = topology.atom(donor if protein_is_donor else acceptor)
                label = f'{atom.residue.name}{atom.residue.resSeq}'
                triples.append((donor, hydrogen, acceptor))
                residue_ids.append(label)
                descriptions.append(dict(donor=donor, hydrogen=hydrogen, acceptor=acceptor,
                                         protein_residue=label, ligand_atom=topology.atom(acceptor if protein_is_donor else donor).name))
    if not triples:
        raise ValueError('No chemically qualified donor-H-acceptor triples')
    return np.asarray(triples, dtype=np.int32), residue_ids, dict(
        triples=descriptions, ligand_acceptors=[topology.atom(i).name for i in ligand_acceptors],
        ligand_donors=[dict(donor=topology.atom(d).name, hydrogen=topology.atom(h).name) for d, h in ligand_donors],
        protein_donor_pairs=len(protein_donors), protein_acceptors=len(protein_acceptors),
        unknowns=unknowns, feature_definition=pin(fdef),
        limitations=['Fixed native protonation state; no proton-transfer inference.',
                     'N/O hydrogen bonds only; sulfur and water-mediated interactions not evaluated.'])


def state_labels(contact_fraction, rmsd_A):
    contact = np.where(np.asarray(contact_fraction) <= 0, 0, np.where(np.asarray(contact_fraction) <= .3, 1, 2))
    return contact * 2 + (np.asarray(rmsd_A) > 4).astype(int)


def allocate(counts, total, variances=None):
    counts = np.asarray(counts, dtype=int)
    if total > counts.sum() or total < np.count_nonzero(counts):
        raise ValueError('Invalid bounded sample budget')
    if variances is None:
        scores = (counts > 0).astype(float)
    else:
        scores = counts * np.sqrt(np.maximum(np.asarray(variances, dtype=float), .01))
    allocation = np.minimum(counts, np.where(counts > 0, 4, 0))
    while allocation.sum() > total:
        allocation[np.argmax(allocation)] -= 1
    for _ in range(total - int(allocation.sum())):
        available = counts > allocation
        desired = scores / scores.sum() * total
        delta = np.where(available, desired - allocation, -np.inf)
        allocation[np.argmax(delta)] += 1
    return allocation


def select_frames(states, total=96, variances=None, uniform=False):
    states = np.asarray(states, dtype=int)
    if uniform:
        selected = np.rint(np.linspace(0, len(states) - 1, total)).astype(int)
        return selected, np.full(total, 1 / total), dict(method='uniform-time', count=total)
    counts = np.bincount(states, minlength=6)
    allocations = allocate(counts, total, variances)
    selected, weights = [], []
    for state, size in enumerate(allocations):
        members = np.flatnonzero(states == state)
        if not size:
            continue
        indices = members[np.rint(np.linspace(0, len(members) - 1, size)).astype(int)]
        selected.extend(indices.tolist())
        weights.extend([len(members) / len(states) / size] * size)
    order = np.argsort(selected)
    return np.asarray(selected)[order], np.asarray(weights)[order], dict(
        method='population-weighted-state-stratification', counts=counts.tolist(), allocation=allocations.tolist(), count=len(selected))


def load_case(run_id):
    import mdtraj as md
    start = time.perf_counter()
    system = run_id.split('-seed-')[0]
    old = ANALYSIS / run_id / 'attempt-001'
    result = read(old / 'result.json')
    reference = md.load(str(P4 / 'controls' / system / 'complex.gro'))
    traj = md.load(str(old / 'solute-whole.xtc'), top=reference.topology)
    cheap_begin = time.perf_counter()
    frames = np.load(old / 'frame-data.npz', allow_pickle=False)
    if not np.array_equal(frames['time_ps'], traj.time):
        raise ValueError('Cached minimum distances and original trajectory frame times differ')
    mapping = result['mapping']
    labels = list(frames['residue_labels'])
    minimum_distances = np.asarray(frames['minimum_heavy_distances_nm'])
    contact = (minimum_distances[:, [i for i, label in enumerate(labels) if label in result['reference_contacts']]] < .4).mean(1)
    heavy = np.asarray(mapping['ligand_heavy_indices0']) - mapping['ligand_indices0'][0]
    ligand_reference = reference.xyz[0, mapping['ligand_heavy_indices0']]
    rmsd = np.sqrt(((frames['ligand_aligned_nm'][:, heavy] - ligand_reference) ** 2).sum(2).mean(1)) * 10
    cheap_feature_seconds = time.perf_counter() - cheap_begin
    originals = [old / 'result.json', old / 'solute-whole.xtc', old / 'frame-data.npz',
                 P4 / 'controls' / system / 'complex.gro', P4 / 'controls' / system / 'topol.top',
                 P4 / 'controls' / system / 'ligand.itp', P4 / 'inputs' / system.split('-')[1] / 'ligand.sdf']
    if run_id == DEVELOPMENT:
        extension = md.load(str(EXTENSION / 'solute-20-25ns-whole.xtc'), top=reference.topology)[1:]
        other = np.load(EXTENSION / 'frame-data.npz', allow_pickle=False)
        if not np.array_equal(other['time_ps'][1:], extension.time):
            raise ValueError('Extension cache and trajectory frame times differ')
        traj = traj.join(extension, check_topology=True)
        contact = np.r_[contact, other['reference_contact_retention_4A'][1:]]
        rmsd = np.r_[rmsd, other['ligand_RMSD_A_protein_fit'][1:]]
        if list(other['residue_labels']) != labels:
            raise ValueError('Extension residue distance cache identity differs')
        minimum_distances = np.r_[minimum_distances, other['minimum_heavy_distances_nm'][1:]]
        originals.extend([EXTENSION / 'solute-20-25ns-whole.xtc', EXTENSION / 'frame-data.npz'])
    triples, triple_residues, typing = chemistry(system, mapping, reference.topology)
    universe = sorted(set(triple_residues))
    residue_index = np.asarray([universe.index(r) for r in triple_residues], dtype=int)
    return dict(run_id=run_id, system=system, trajectory=traj, reference=reference, triples=triples,
                triple_residues=triple_residues, residue_index=residue_index, residues=universe,
                typing=typing, states=state_labels(contact, rmsd), contact=contact, rmsd=rmsd,
                minimum_distances_nm=minimum_distances, minimum_distance_labels=labels,
                reference_contacts=result['reference_contacts'], load_wall_seconds=time.perf_counter()-start,
                cheap_feature_load_seconds=cheap_feature_seconds,
                originals=[pin(p) for p in originals])


def directional(case, indices):
    import mdtraj as md
    started, cpu = time.perf_counter(), time.process_time()
    traj = case['trajectory'][indices]
    triples = case['triples']
    distances = md.compute_distances(traj, triples[:, [0, 2]], periodic=True) if len(triples) else np.empty((len(indices), 0))
    angles = md.compute_angles(traj, triples, periodic=True) if len(triples) else np.empty((len(indices), 0))
    matrices = {}
    for cutoff in (.30, .35):
        active = (distances <= cutoff) & (angles >= math.radians(150))
        matrix = np.zeros((len(indices), len(case['residues'])), dtype=bool)
        for j in range(len(case['residues'])):
            matrix[:, j] = active[:, case['residue_index'] == j].any(axis=1)
        matrices[str(cutoff)] = matrix
    return matrices, dict(wall_seconds=time.perf_counter()-started, cpu_seconds=time.process_time()-cpu,
                          frames=len(indices), donor_hydrogen_acceptor_triples=len(triples),
                          triple_frame_evaluations=len(indices) * len(triples))


def exact_candidate_mask(triple_residues, labels, minimum_distances_nm, cutoff_nm=.35, tolerance_nm=.0001):
    """A residue-heavy minimum is a lower bound for every qualified D-A pair.

    Missing cache labels are retained rather than silently discarded. This
    candidate requires an original-identity-matched cache from the same frames.
    """
    distances = np.asarray(minimum_distances_nm)
    valid = np.isfinite(distances).all(axis=0) & (distances >= 0).all(axis=0)
    lower = np.where(valid, np.min(distances, axis=0), np.nan)
    by_label = dict(zip(labels, lower))
    return np.asarray([label not in by_label or not np.isfinite(by_label[label]) or by_label[label] < 0
                       or by_label[label] <= cutoff_nm+tolerance_nm
                       for label in triple_residues], dtype=bool)


def aggregate(case, matrices, weights):
    pocket = np.asarray([r in case['reference_contacts'] for r in case['residues']])
    return {key: dict(residue_occupancy=(weights @ matrix).tolist(),
                       any_occupancy=float(weights @ matrix.any(1)),
                       prepared_pocket_occupancy=float(weights @ matrix[:, pocket].any(1)))
            for key, matrix in matrices.items()}


def comparison(reference, estimate):
    truth, pred = np.asarray(reference['residue_occupancy']), np.asarray(estimate['residue_occupancy'])
    eligible = truth > 0
    persistent = truth >= .1
    return dict(mean_absolute_residue_occupancy_error=float(np.mean(np.abs(truth[eligible]-pred[eligible]))) if eligible.any() else 0.0,
                eligible_residue_count=int(eligible.sum()),
                maximum_residue_error=float(np.max(np.abs(truth-pred))),
                any_occupancy_absolute_error=abs(reference['any_occupancy']-estimate['any_occupancy']),
                prepared_pocket_occupancy_absolute_error=abs(reference['prepared_pocket_occupancy']-estimate['prepared_pocket_occupancy']),
                persistent_motif_recall=float(np.mean(pred[persistent] > 0)) if persistent.any() else None,
                persistent_motifs=int(persistent.sum()))


def run_case(run_id, output, rule=None):
    started, cpu = time.perf_counter(), time.process_time()
    output.mkdir(parents=True, exist_ok=False)
    case = load_case(run_id)
    save(output / 'input-identities.json', case['originals'])
    save(output / 'chemistry.json', case['typing'])
    if case['typing']['unknowns']:
        raise ValueError('Unsupported chemistry must be resolved explicitly before this comparison')
    lanes, lane_arrays = {}, {}
    # Alternate order by seed. Full-frame reference is computed only afterwards.
    order = ['baseline', 'candidate'] if run_id.endswith('1') or run_id == DEVELOPMENT else ['candidate', 'baseline']
    for lane in order:
        begin, cpu_begin = time.perf_counter(), time.process_time()
        indices, weights, selection = select_frames(case['states'], uniform=lane == 'baseline',
                                                    variances=None if rule is None else rule['stratum_variance'])
        selection_seconds = time.perf_counter()-begin
        matrices, geometry_cost = directional(case, indices)
        lane_arrays[lane] = matrices
        aggregate_begin = time.perf_counter()
        estimates = aggregate(case, matrices, weights)
        aggregate_seconds = time.perf_counter()-aggregate_begin
        lanes[lane] = dict(selection=selection, selected_indices=indices.tolist(), selected_time_ps=case['trajectory'].time[indices].tolist(),
                           population_weights=weights.tolist(), estimate=estimates,
                           geometry_cost=geometry_cost, selection_and_execution_wall_seconds=time.perf_counter()-begin,
                           selection_and_execution_CPU_seconds=time.process_time()-cpu_begin,
                           selection_wall_seconds=selection_seconds, aggregate_wall_seconds=aggregate_seconds,
                           shared_load_seconds=case['load_wall_seconds'], cheap_feature_load_seconds=case['cheap_feature_load_seconds'])
        if lanes[lane]['selection_and_execution_wall_seconds'] + case['load_wall_seconds'] > 180:
            raise TimeoutError('Frozen per-lane wall budget exceeded')
    reference, reference_cost = directional(case, np.arange(len(case['states'])))
    truth = aggregate(case, reference, np.full(len(case['states']), 1/len(case['states'])))
    for lane in lanes:
        lanes[lane]['quality'] = {key: comparison(truth[key], lanes[lane]['estimate'][key]) for key in truth}
    np.savez_compressed(output / 'directional-frames.npz', time_ps=case['trajectory'].time,
                        residues=np.asarray(case['residues']), primary=reference['0.3'], sensitivity=reference['0.35'],
                        state=case['states'], reference_contact_fraction=case['contact'], ligand_RMSD_A=case['rmsd'])
    summaries = []
    for begin in range(0, int(case['trajectory'].time[-1]), 5000):
        end = begin + 5000
        mask = (case['trajectory'].time >= begin) & (case['trajectory'].time < end if end < case['trajectory'].time[-1] else case['trajectory'].time <= end)
        view = aggregate(case, {key: value[mask] for key, value in reference.items()}, np.full(int(mask.sum()), 1/int(mask.sum())))
        summaries.append(dict(window_ns=[begin/1000, end/1000], frames=int(mask.sum()), directional=view,
                              mean_reference_contact_fraction=float(case['contact'][mask].mean()), mean_ligand_RMSD_A=float(case['rmsd'][mask].mean())))
    result = dict(schema=SCHEMA, run_id=run_id, phase='development' if run_id == DEVELOPMENT else 'frozen_evaluation',
                  frames=len(case['states']), residues=case['residues'], reference_contacts=case['reference_contacts'],
                  shared_full_frame_reference=truth, shared_reference_cost=reference_cost, windows=summaries, lanes=lanes,
                  typing_scope=case['typing']['limitations'], unknowns=case['typing']['unknowns'],
                  cost=dict(wall_seconds=time.perf_counter()-started, CPU_seconds=time.process_time()-cpu,
                            shared_load_seconds=case['load_wall_seconds'], new_MD=0, GPU=0))
    if run_id == DEVELOPMENT:
        indices = np.asarray(lanes['candidate']['selected_indices'])
        data = lane_arrays['candidate']['0.3']
        variances = []
        for state in range(6):
            rows = data[case['states'][indices] == state]
            # Learn from the same 96 evaluated development frames, not the full reference.
            variances.append(float(np.mean(np.var(rows.astype(float), axis=0))) if len(rows) >= 2 else .25)
        result['learned_rule'] = dict(schema='snd1-state-allocation-rule/1', version=2, stratum_variance=variances,
                                      source_run=run_id, source_selected_indices=indices.tolist(),
                                      fit_uses_full_reference=False, scope='QOV prepared monomer, fixed directional endpoint; QOY transfer exploratory')
    save(output / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['develop', 'revise', 'evaluate', 'exact'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if hasattr(os, 'sched_getaffinity'):
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    args.output.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    protocol = read(args.output / 'PROTOCOL.json')
    phase_result = args.output / f'{args.phase}-result.json'
    if phase_result.exists():
        print(json.dumps(dict(reused=True, result=str(phase_result), sha256=pin(phase_result)['sha256'])))
        return
    if args.phase == 'develop':
        result = run_case(DEVELOPMENT, args.output / 'development')
        save(args.output / 'rule-v1.json', dict(version=1, method='equal allocation to populated states'))
        save(args.output / 'rule-v2.json', result['learned_rule'])
        save(args.output / 'RULE-FREEZE.json', dict(protocol=pin(args.output / 'PROTOCOL.json'),
             implementation=pin(__file__), rule=pin(args.output / 'rule-v2.json'),
             created_unix=time.time(), evaluation_endpoints_read=False))
        save(phase_result, dict(status='complete', result=str(args.output / 'development/result.json'),
                               next_action='Use frozen v2 allocation for new directional endpoints on QOV241/243; evaluate QOY transfer separately',
                               wall_seconds=time.perf_counter()-start))
    elif args.phase == 'revise':
        previous = read(args.output / 'development/result.json')
        index = np.asarray(previous['lanes']['candidate']['selected_indices'])
        frames = np.load(args.output / 'development/directional-frames.npz', allow_pickle=False)
        sampled = frames['primary'][index]
        active = sampled.any(0)
        variances = []
        for state in range(6):
            rows = sampled[frames['state'][index] == state][:, active]
            variances.append(float(np.mean(np.var(rows.astype(float), axis=0))) if len(rows) >= 2 and active.any() else .25)
        rule = dict(previous['learned_rule'], version=3, stratum_variance=variances,
                    variance_dimension='residues active in the same 96 development samples',
                    development_active_residues=frames['residues'][active].tolist(),
                    reason='Equal allocation lost to uniform; v2 variance over all protein residues was dominated by never-active dimensions. Preserve population weighting and fit variances only over observed active motifs.',
                    previous_rule=pin(args.output / 'rule-v2.json'))
        save(args.output / 'rule-v3.json', rule)
        prior_freeze = read(args.output / 'RULE-FREEZE.json')
        save(args.output / 'RULE-FREEZE-v2.json', prior_freeze)
        save(args.output / 'RULE-FREEZE.json', dict(protocol=pin(args.output / 'PROTOCOL.json'),
             implementation=pin(__file__), rule=pin(args.output / 'rule-v3.json'),
             created_unix=time.time(), evaluation_endpoints_read=False))
        save(phase_result, dict(status='complete', new_geometry_evaluations=0,
                               learned_from_same_96_development_frames=True, active_residues=rule['development_active_residues'],
                               stratum_variance=variances, wall_seconds=time.perf_counter()-start))
    elif args.phase == 'exact':
        freeze = read(args.output / 'EXACT-FREEZE.json')
        if pin(__file__) != freeze['implementation']:
            raise ValueError('Exact algorithm implementation changed after freeze')
        rows = []
        for run_id in [DEVELOPMENT, *EVALUATION]:
            begin, cpu = time.perf_counter(), time.process_time()
            folder = args.output / ('development' if run_id == DEVELOPMENT else run_id)
            original = read(folder / 'result.json')
            reference = np.load(folder / 'directional-frames.npz', allow_pickle=False)
            case = load_case(run_id)
            if case['originals'] != read(folder / 'input-identities.json'):
                raise ValueError('Original case inputs changed since the full-frame endpoint')
            select_begin = time.perf_counter()
            keep = exact_candidate_mask(case['triple_residues'], case['minimum_distance_labels'], case['minimum_distances_nm'])
            selection_seconds = time.perf_counter()-select_begin
            reduced = dict(case, triples=case['triples'][keep], residue_index=case['residue_index'][keep])
            matrices, geometry_cost = directional(reduced, np.arange(len(case['states'])))
            same = bool(np.array_equal(matrices['0.3'], reference['primary']) and
                        np.array_equal(matrices['0.35'], reference['sensitivity']))
            if not same:
                raise ValueError('Exact pruning changed the directional endpoint')
            rows.append(dict(run_id=run_id, frames=len(case['states']),
                             original_triples=len(keep), retained_triples=int(keep.sum()),
                             exact_primary_and_sensitivity_arrays=same,
                             zero_sampling_error=True, geometry_cost=geometry_cost,
                             selection_seconds=selection_seconds,
                             preparation_seconds=case['load_wall_seconds'],
                             full_reference_geometry_seconds=original['shared_reference_cost']['wall_seconds'],
                             full_reference_preparation_seconds=original['cost']['shared_load_seconds'],
                             exact_wall_seconds=time.perf_counter()-begin, exact_CPU_seconds=time.process_time()-cpu,
                             reference_artifacts=[pin(folder / 'result.json'), pin(folder / 'directional-frames.npz')]))
        save(phase_result, dict(status='complete', candidate=freeze, cases=rows,
                               wall_seconds=time.perf_counter()-start, new_MD=0,
                               disposition='adopt_for_identity_matched_existing_distance_caches',
                               limitations=['Same seen trajectories and geometric oracle; exact computational equivalence, not new independent chemistry.',
                                            'No claim that generating a missing cache would be cheaper. Use ordinary full-frame analysis if cache is absent.']))
    else:
        freeze = read(args.output / 'RULE-FREEZE.json')
        rule_path = Path(freeze['rule']['path'])
        if pin(__file__) != freeze['implementation'] or pin(rule_path) != freeze['rule']:
            raise ValueError('Implementation or candidate changed after development freeze')
        rule = read(rule_path)
        rows = []
        for run_id in EVALUATION:
            rows.append(run_case(run_id, args.output / run_id, rule))
            if time.perf_counter()-start > protocol['budget']['new_CPU_analysis_total_wall_seconds_max']:
                raise TimeoutError('Scientific study analysis wall budget exceeded')
        summary = []
        for row in rows:
            a, b = row['lanes']['baseline']['quality']['0.3'], row['lanes']['candidate']['quality']['0.3']
            summary.append(dict(run_id=row['run_id'], baseline=a, candidate=b,
                                error_improvement=a['mean_absolute_residue_occupancy_error']-b['mean_absolute_residue_occupancy_error']))
        save(phase_result, dict(status='complete', summary=summary, wall_seconds=time.perf_counter()-start,
                               candidate_freeze=freeze, new_MD=0))
    print(json.dumps(dict(status='complete', phase=args.phase, result=str(phase_result), wall_seconds=time.perf_counter()-start)))


if __name__ == '__main__':
    main()
