"""Same-objective pose selection diagnostics from admitted, already measured rows."""
from __future__ import annotations

from etalon.active.schema import finite


def selection_diagnostic(rows: list[dict], *, score_key='score', minimize=True,
                         tie_tolerance=1e-6, loss_cap=6.) -> dict:
    finite(tie_tolerance, 'score tie tolerance', minimum=0)
    finite(loss_cap, 'loss cap', minimum=0)
    if len({r['id'] for r in rows}) != len(rows) or len({r['slot'] for r in rows}) != len(rows):
        raise ValueError('unique pose identity and slot index required')
    admitted = [r for r in rows if r['admitted']]
    if not admitted: return {'status': 'no_admitted_pose', 'attempted': len(rows)}
    mode_sets = [set(r['mode_rmsd_A']) for r in admitted]
    if not mode_sets[0] or any(s != mode_sets[0] for s in mode_sets):
        raise ValueError('identical nonempty reference mode set required for every pose')
    for row in admitted:
        for value in row['mode_rmsd_A'].values(): finite(value, 'RMSD', minimum=0)
        if row.get(score_key) is not None: finite(row[score_key], 'score')
    scored = [r for r in admitted if r.get(score_key) is not None]
    if not scored: return {'status': 'no_scored_pose', 'attempted': len(rows), 'admitted': len(admitted)}
    optimum = (min if minimize else max)(r[score_key] for r in scored)
    ties = sorted((r for r in scored if abs(r[score_key] - optimum) <= tie_tolerance), key=lambda r: r['slot'])
    selected = ties[0]
    metrics = {}
    for name, reduction in [('nearest_mode', min), ('worst_mode', max)]:
        losses = {r['id']: min(loss_cap, reduction(r['mode_rmsd_A'].values())) for r in admitted}
        oracle = min(losses.values()); scored_oracle = min(losses[r['id']] for r in scored)
        metrics[name] = {'selected_loss_A': losses[selected['id']], 'pool_optimal_loss_A': oracle,
            'scored_pool_optimal_loss_A': scored_oracle,
            'same_loss_regret_A': losses[selected['id']] - oracle,
            'scored_pool_regret_A': losses[selected['id']] - scored_oracle,
            'oracle_pose_ids': sorted(k for k, v in losses.items() if abs(v-oracle) <= 1e-10),
            'tie_loss_range_A': [min(losses[r['id']] for r in ties), max(losses[r['id']] for r in ties)],
            'all_pose_losses_A': losses}
    coverage = min(loss_cap, max(min(r['mode_rmsd_A'][mode] for r in admitted) for mode in mode_sets[0]))
    return {'status': 'measured', 'attempted': len(rows), 'admitted': len(admitted), 'scored': len(scored),
        'unscored_admitted_ids': [r['id'] for r in admitted if r.get(score_key) is None],
        'score_direction': 'min' if minimize else 'max', 'selected_pose_id': selected['id'],
        'selected_slot': selected['slot'], 'selected_score': selected[score_key],
        'tie_tolerance': tie_tolerance, 'tie_pose_ids': [r['id'] for r in ties],
        'tie_resolution': 'minimum global slot among scores within declared tolerance',
        'loss_cap_A': loss_cap, 'single_pose_objectives': metrics,
        'ensemble_worst_mode_coverage_A': coverage,
        'scope': 'same single-pose losses; structural diagnostics do not establish affinity or mechanism'}
