"""Feedback-driven local SND1 search with explicit, equal proposal budgets.

This method changes coupled linker/pose generation, not the physical endpoint.
All coordinates are Angstrom in the caller's native frame. Native relaxation
and dynamics remain component operations; this module does no execution.
"""
from __future__ import annotations

import numpy as np
import snd1_anchor_intervention as anchor
import snd1_contact_program as contact


def proposals(ctx, *, budget=96, mode='coupled', objective='ASN_ARG'):
    if mode == 'rigid':
        return contact.paired_proposals(ctx, budget=budget, objective=objective, guided=True)
    if mode != 'coupled' or budget < 16:
        raise ValueError('Select rigid/coupled and at least sixteen evaluations')
    # A rigid pair certificate is valid only for its own internal geometry.
    # Rebuild the pool after changing the two sulfonamide linker freedoms.
    chemical = ctx['chemical']
    rotors = chemical['rotors']
    base = np.array([anchor.dihedral(ctx['ligand'], r['dihedral']) for r in rotors])
    offsets = [(0, 0), (-30, 0), (30, 0), (0, -60), (0, 60),
               (-30, -60), (30, 60), (-30, 60)]
    poses, metadata = [], []
    each, extra = divmod(budget, len(offsets))
    for i, (a, b) in enumerate(offsets):
        targets = base.copy(); targets[:2] += [a, b]
        xyz = anchor.set_torsions(ctx['ligand'], rotors, targets)
        local = {**ctx, 'ligand': xyz}
        part, info = contact.paired_proposals(local, budget=each + (i < extra),
                                             objective=objective, guided=True)
        poses.extend(part)
        metadata.extend(dict(row, torsion_offsets_deg=[a, b],
                             certificate_geometry='this internal conformation')
                        for row in info['candidate_metadata'])
    return poses, dict(candidate_metadata=metadata, mode=mode, evaluations=budget,
        objective=objective, offsets_deg=offsets,
        scope='Specified linker torsions plus rigid pose; no chemistry or protein edit')


def shortlist(poses, ctx, *, limit=4, objective='ASN_ARG', diversity_A=.20):
    """Select before native results; keep unsuccessful candidates in the table."""
    rows = []
    for index, pose in enumerate(poses):
        r = contact.combined_readout(pose, ctx, anchor)
        rows.append(dict(index=index, **r))
    key = lambda r: (not r['valid'], not r[objective], r['core_movement_A'],
                     r['strain_change_kcal_mol'], r['index'])
    ordered = sorted(rows, key=key)
    chosen = []
    heavy = ctx['chemical']['heavy']
    for row in ordered:
        if not row['valid']:
            continue
        xyz = poses[row['index']][heavy]
        if all(np.sqrt(np.mean(np.sum((xyz - poses[j][heavy]) ** 2, axis=1)))
               >= diversity_A for j in chosen):
            chosen.append(row['index'])
        if len(chosen) == limit:
            break
    return chosen, rows


def select_relaxed(rows, *, objective='ASN_ARG', exclude_input_identities=()):
    """One scoped physical ranking; never turn a failed endpoint into a hit.

    Full-system potential is a local screening quantity under one Hamiltonian,
    not affinity/free energy. A no-hit batch can still inform the next question.
    """
    eligible = [r for r in rows if r.get('converged') and r.get('quality', {}).get('acceptable')
                and r.get('input_coordinate_identity') not in exclude_input_identities]
    if not eligible:
        return dict(selected=None, reason='No converged, chemically acceptable candidate')
    best = min(eligible, key=lambda r: (not r['contacts'][objective],
                r['potential_kJ_mol'], r['core_movement_A'], r['index']))
    return dict(selected=best['index'], reason='Preserved endpoint, then full native potential',
        endpoint_met=best['contacts'][objective], potential_kJ_mol=best['potential_kJ_mol'],
        excluded_known_inputs=list(exclude_input_identities))


def followup(rows):
    """Interpret normal paired feedback into a next *research* action.

    No scientific run is submitted here. The caller compiles the selected
    operation using its existing authorization and remaining budget.
    """
    successful = [r for r in rows if r.get('status') == 'succeeded']
    if len(successful) != len(rows) or not successful:
        return dict(outcome='unresolved', action='inspect_saved_native_handles',
                    reason='Incomplete normal feedback; do not repeat completed trajectories')
    groups = {mode: [r for r in successful if r['mode'] == mode] for mode in ('rigid', 'coupled')}
    paired = set(r['seed'] for r in groups['rigid']) == set(r['seed'] for r in groups['coupled'])
    if not paired or not all(groups.values()):
        return dict(outcome='unresolved', action='interpret_available_results',
                    reason='Missing a comparable method/seed cell')
    wins = {mode: sum(r['dual_retained'] for r in data) for mode, data in groups.items()}
    if wins['coupled'] > wins['rigid']:
        return dict(outcome='retain', action='test_local_candidate_in_additional_state', wins=wins,
                    reason='Promising conditional gain; one starting state does not establish adoption')
    return dict(outcome='retain', action='analyze_competing_anchor_states', wins=wins,
        reason='No strict dynamic gain; reuse normal trajectories to select a different anchor hypothesis')
