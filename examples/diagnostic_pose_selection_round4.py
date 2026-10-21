"""Consume released R3b distances and PRISM's fixed-pool audit, without rescoring."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time

from etalon.active.schema import canonical
from etalon.boundary.prism_v1 import file_identity
from etalon.research.pose_selection import selection_diagnostic


def analyze(producer_report, released_result, release, structure_dir, output):
    wall, cpu = time.perf_counter(), time.process_time()
    production = json.loads(producer_report.read_text()); independent = json.loads(released_result.read_text())
    permission = json.loads(release.read_text())
    if file_identity(released_result)['sha256'] != permission['result_sha256']:
        raise ValueError('released result identity mismatch')
    groups, refs = {}, []
    for group, old in independent['auxiliary_structure']['groups'].items():
        pred_path, result_path = (structure_dir/(group+suffix) for suffix in ('.predictions.json', '.result.json'))
        original = json.loads(pred_path.read_text()); distances = json.loads(result_path.read_text())
        if distances['manifest_sha256'] != file_identity(pred_path)['sha256']:
            raise ValueError('independent distance manifest mismatch')
        refs.extend({'path': str(p.absolute()), **file_identity(p)} for p in (pred_path, result_path))
        by_path = {r['path']: r for r in distances['predictions']}
        candidate, policy = group.rsplit('-', 1)
        raw = [r for r in production['rows'] if r['candidate'] == candidate and r['policy'] == policy]
        admitted = {r['global_slot']: r for r in raw if r['pose_admitted']}
        if set(admitted) != {r['global_slot'] for r in original}:
            raise ValueError('producer and independent admitted slots differ')
        rows = []
        for r in original:
            p = Path(r['path']); measured = by_path[r['path']]; producer = admitted[r['global_slot']]
            if file_identity(p)['sha256'] != r['sha256'] or r['sha256'] != producer['raw']['sha256']:
                raise ValueError('pose raw identity mismatch')
            modes = {str(m['residue'])+'/'+m['altloc']: m['heavy_rmsd_angstrom'] for m in measured['modes']}
            producer_modes = {str(m['residue'])+'/'+m['altloc']: m['rmsd_A'] for m in producer['modes']}
            if set(modes) != set(producer_modes) or any(abs(v-producer_modes[k]) > 1e-10 for k,v in modes.items()):
                raise ValueError('independent and producer mode distances disagree')
            score = producer['components_kcal_mol']['total']
            if r['e1'] != score or r['e0'] != producer['e0']: raise ValueError('score/slot association changed')
            rows.append({'id': producer['id'], 'slot': r['global_slot'], 'admitted': True,
                'mode_rmsd_A': modes, 'score': score, 'e0': r['e0']})
        rows.extend({'id': r['id'], 'slot': r['global_slot'], 'admitted': False} for r in raw if not r['pose_admitted'])
        result = selection_diagnostic(rows)
        selected = next(r for r in original if r['global_slot'] == result['selected_slot'])
        if selected['path'] != old['proxy_selected_pose']['e1']['path']:
            raise ValueError('original frozen selection/index differs')
        if abs(result['ensemble_worst_mode_coverage_A']-old['J_structure_A']) > 1e-10:
            raise ValueError('ensemble coverage changed')
        result['e0_comparison'] = selection_diagnostic(rows, score_key='e0', minimize=False)
        best_ids = result['single_pose_objectives']['nearest_mode']['oracle_pose_ids']
        selected_raw = next(r for r in raw if r['id'] == result['selected_pose_id'])
        result['selected_minus_nearest_oracle_components_kcal_mol'] = {
            pid: {name: value-next(r for r in raw if r['id']==pid)['components_kcal_mol'][name]
                  for name,value in selected_raw['components_kcal_mol'].items()} for pid in best_ids}
        result['geometry_source'] = 'producer report; distances independently cross-checked; no new structure alignment'
        groups[group] = result
    report = {'schema': 'etalon-same-loss-pose-diagnostic/1', 'groups': groups,
        'scope': 'released seen-feedback reanalysis; no new scoring, reference alignment, or scientific execution',
        'sources': [{'path': str(p.absolute()), **file_identity(p)} for p in (producer_report,released_result,release)],
        'independent_distance_sources': refs, 'python': sys.executable,
        'source': {'path': str(Path(__file__).absolute()), **file_identity(__file__)},
        'module_source': file_identity(sys.modules[selection_diagnostic.__module__].__file__),
        'processing': {'wall_second': time.perf_counter()-wall, 'cpu_second': time.process_time()-cpu},
        'new_producer_executions': 0, 'mechanism_posterior': None,
        'next_intervention': 'fixed identical pose pool, receptor/state and Meeko preparation; Vina versus Vinardo score-only',
        'limitation': 'score-family change tests scoring sensitivity, not uniquely solvation or receptor causality'}
    with output.open('x', encoding='utf-8') as f: f.write(canonical(report)+'\n')
    return report


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('producer-report','released-result','release','structure-dir','output'):
        p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args(); result=analyze(args.producer_report,args.released_result,args.release,args.structure_dir,args.output)
    print(json.dumps({k:{name:metric['same_loss_regret_A'] for name,metric in v['single_pose_objectives'].items()}
                      for k,v in result['groups'].items()},indent=2))
