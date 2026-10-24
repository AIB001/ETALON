"""Versioned retrospective reassessment of an immutable completed SND1 inventory.

Reads copies selected by freeze_completed.py; never resumes the active batch.
No docking/CNN executor is called. Optional checker smoke is a separately charged
PoseBusters calculation and never replaces the original measured verifier rows.
"""
from __future__ import annotations

import argparse
import collections
import copy
import importlib.util
import json
import sys
import time
from pathlib import Path

from etalon.active.schema import digest
from etalon.research.scientific_input import identity
from etalon.research.result_integrity import (confirmation_eligibility, freeze_check_contract,
    result_binding, validate_native_receipt, validate_verifier)

spec = importlib.util.spec_from_file_location('snd1_scale_screen', Path(__file__).with_name('snd1_scale_screen.py'))
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


class FrozenOriginals:
    def __init__(self, manifest):
        self.manifest = read(manifest)
        self.files = {r['original']: r for r in self.manifest['files']}

    def path(self, original):
        row = self.files[str(original)]
        path = Path(row['snapshot'])
        if identity(path) != {k: row[k] for k in ('sha256', 'bytes')}:
            raise ValueError('frozen original changed: ' + str(original))
        return path

    def pin(self, original):
        return identity(self.path(original))

    def read(self, original):
        return read(self.path(original))


def reassess(root, *, overlay_path, checker_smoke=False, existing_smoke_path=None):
    root = Path(root)
    originals = FrozenOriginals(root / 'completed-manifest.json')
    source = Path(originals.manifest['source_root'])
    protocol = originals.read(source / 'freeze.json')
    protocol_pin = originals.pin(source / 'freeze.json')
    panel = originals.read(source / 'panel.json')
    panel_pin = originals.pin(source / 'panel.json')
    if protocol['panel'] != panel_pin:
        raise ValueError('original panel/protocol mismatch')
    old_source = source / 'source/snd1_scale_screen.py'
    if originals.pin(old_source) != protocol['runtime']['modules'][str(old_source)]:
        raise ValueError('original screen source does not match frozen runtime')
    for path, pin in protocol['runtime']['modules'].items():
        actual = originals.pin(path) if path in originals.files else identity(path)
        if actual != pin:
            raise ValueError('historical checker/runtime module changed: ' + path)
    runtime = read(root / 'runtime-at-freeze.json')
    config = read(root / 'posebusters-dock-config.json')
    contract = freeze_check_contract(config, version=runtime['packages']['posebusters'],
                                     implementation=runtime['posebusters_implementation'])
    if screen.pose_check_contract() != contract:
        raise ValueError('actual checker differs from reassessment checker freeze')
    overlay = read(overlay_path)
    parents = {r['id']: r for r in panel['parents']}
    receptors = {r['id']: r for r in panel['receptors']}
    requests = {r['case_id']: r for r in originals.read(source / 'requests-manifest.json')['rows']}
    # This declaration derives from the historical policy freeze. It is not a
    # new candidate, nor a claim that a new post-feedback candidate is unseen.
    candidate = {'schema': 'etalon-candidate-confirmation-freeze/1',
        'candidate_digest': digest(protocol), 'panel': panel_pin,
        'frozen_at_ns': protocol['frozen_at_ns'], 'decisions_frozen_before_execution': True,
        'outcomes_visible_at_freeze': False, 'seen_series_ids': [],
        'parent_ids': [p['id'] for p in panel['parents'] if p['split'] == 'confirmation'],
        'freeze_evidence': {'path': str(originals.path(source / 'freeze.json')), **protocol_pin}}
    implementation = {'entry': {'path': str(Path(__file__).absolute()), **identity(__file__)},
        'screen': {'path': screen.__file__, **identity(screen.__file__)},
        'result_integrity': {'path': sys.modules['etalon.research.result_integrity'].__file__,
            **identity(sys.modules['etalon.research.result_integrity'].__file__)},
        'python_executable': sys.executable,
        'pose_task': {'path': sys.modules['etalon.research.pose_task'].__file__, **identity(sys.modules['etalon.research.pose_task'].__file__)}}
    screen.save(root / 'reassessment-freeze.json', {'schema': 'snd1-reassessment-freeze/1',
        'originals_manifest': identity(root / 'completed-manifest.json'), 'old_protocol': protocol_pin,
        'old_source': originals.pin(old_source), 'new_implementation': implementation,
        'check_contract': contract, 'overlay': {'path': str(overlay_path), **identity(overlay_path)},
        'candidate_relative_freeze': candidate, 'mode': 'retrospective_reanalysis_not_independent_replication',
        'native_execution': 'prohibited', 'old_results_overwritten': False})
    all_results = []
    start, cpu = time.perf_counter(), time.process_time()
    if checker_smoke and existing_smoke_path is not None:
        raise ValueError('choose one new checker smoke or an exact existing replay')
    smoke = read(existing_smoke_path) if existing_smoke_path is not None else None
    if smoke is not None and (smoke.get('new_native_executions') != 0 or smoke.get('new_verifier_executions') != 1):
        raise ValueError('existing verifier replay accounting is not usable')
    for number, result_path in enumerate(originals.manifest['completed_result_paths']):
        old = originals.read(result_path)
        folder = Path(result_path).parent
        case_id = folder.name
        destination = root / 'results' / case_id
        if (destination / 'result.json').exists():
            all_results.append(read(destination / 'result.json'))
            continue
        try:
            row, rec = parents[old['parent_id']], receptors[old['receptor']]
            seed = old['seed']
            compiled = screen.compile_task(screen.make_task(source, row, rec, seed))
            if compiled['case_id'] != case_id:
                raise ValueError('cached result case metadata mismatch')
            expected_metadata = {'parent_key': row['parent_key'], 'series_id': row['series_id'],
                'split': row['split'], 'evidence_class': row['evidence_class'], 'case_id': case_id}
            if any(old.get(k) != v for k, v in expected_metadata.items()):
                raise ValueError('cached result parent/split metadata mismatch')
            pool_pin = originals.pin(folder / 'empirical.sdf')
            reference = rec['reference'] if row['full_inchikey'] == rec['reference_inchikey'] else None
            binding = result_binding(compiled=compiled, protocol=protocol_pin, pool=pool_pin,
                receptor=originals.pin(rec['receptor']), reference=originals.pin(reference) if reference else None,
                check_contract=contract)
            native_receipts = {}
            for stage, state in old['stages'].items():
                key = screen.execution_key(compiled, stage, protocol_pin)
                if requests[case_id][stage + '_request_id'] != key:
                    raise ValueError('manifest method/protocol/request identity mismatch')
                receipt_path = source / 'executions' / key / 'receipt.json'
                request_path = receipt_path.with_name('request.json')
                receipt, request = originals.read(receipt_path), originals.read(request_path)
                validate_native_receipt(receipt, request, compiled=compiled, stage=stage,
                    protocol=protocol_pin, pool=pool_pin if stage == 'cnn' else None,
                    output=folder / (stage + '.sdf'), identity_reader=originals.pin)
                if state != receipt['status']:
                    raise ValueError('result stage status differs from original native receipt')
                native_receipts[stage] = {'path': str(receipt_path), **originals.pin(receipt_path)}
            if compiled['files']['receptor'] != binding['receptor'] or compiled['files']['ligand'] != originals.pin(row['ligand']):
                raise ValueError('compiled scientific input file identity differs')
            verifier_path = folder / 'verifier.json'
            verifier = originals.read(verifier_path)
            if old.get('verifier') != {'path': str(verifier_path), **originals.pin(verifier_path)}:
                raise ValueError('cached result verifier identity mismatch')
            for field in ('pool', 'receptor', 'reference'):
                if verifier.get(field) != binding[field]:
                    raise ValueError('legacy verifier ' + field + ' identity mismatch')
            count = len(screen.pool_identity(originals.path(folder / 'empirical.sdf')))
            ranks = {'empirical': screen.ranking(originals.path(folder / 'empirical.sdf'), 'minimizedAffinity')}
            if old['stages'].get('cnn') == 'completed':
                screen.same_pool(originals.path(folder / 'empirical.sdf'), originals.path(folder / 'cnn.sdf'))
                ranks['cnn'] = screen.ranking(originals.path(folder / 'cnn.sdf'), 'CNNscore', True)
            if old['rankings'] != ranks:
                raise ValueError('cached result rankings differ from original pools')
            versioned = copy.deepcopy(verifier)
            versioned.update(schema='snd1-p2-pose-verification/2', binding=binding,
                check_contract_digest=digest(contract), expected_pose_slots=count,
                coverage_complete=verifier.get('coverage_complete', True),
                reassessment_provenance={'mode': 'legacy_observations_reassessed_no_checker_execution',
                    'original_verifier': {'path': str(verifier_path), **originals.pin(verifier_path)},
                    'old_runtime': protocol['runtime'], 'native_receipts': native_receipts})
            audit = validate_verifier(versioned, expected_pose_count=count, check_contract=contract, expected_binding=binding)
            versioned['coverage_complete'] = audit['coverage_complete']
            # Preserve the first audit's detailed reason rather than erasing it
            # behind a derived incomplete flag on subsequent reads.
            versioned['reassessment_integrity'] = audit
            screen.save(destination / 'verifier.json', versioned)
            top = {stage: screen.top1_verification(versioned, rank, count,
                check_contract=contract, binding=binding) for stage, rank in ranks.items()}
            eligibility = confirmation_eligibility(row, candidate_freeze=candidate, overlay=overlay, panel_identity=panel_pin)
            result = {'schema': 'snd1-versioned-result-reassessment/1', 'case_id': case_id,
                **expected_metadata, 'receptor': rec['id'], 'seed': seed,
                'original_result': {'path': str(result_path), **originals.pin(result_path)},
                'binding': binding, 'stages': old['stages'], 'rankings': ranks, 'top1': top,
                'old_top1': old.get('top1'), 'top1_changed': top != old.get('top1'), 'integrity': audit,
                'evaluation_role': 'retrospective_reanalysis_of_original_' + row['split'],
                'candidate_relative_eligibility': eligibility, 'correctness_unknown': reference is None,
                'native_receipts': native_receipts, 'new_native_executions': 0, 'new_verifier_executions': 0}
            if checker_smoke and smoke is None and audit['status'] == 'complete':
                tick, cputick = time.perf_counter(), time.process_time()
                try:
                    observed = screen.physical_check(originals.path(folder / 'empirical.sdf'),
                        originals.path(rec['receptor']), originals.path(reference) if reference else None,
                        check_contract=contract, binding=binding)
                    smoke = {'status': 'completed', 'case_id': case_id, 'verifier': observed,
                        'equal_original_checks': [r['checks'] for r in observed['rows']] == [r['checks'] for r in verifier['rows']],
                        'scope': 'one actual PoseBusters replay; same cached poses, not independent replication'}
                except Exception as exc:
                    smoke = {'status': 'failed', 'case_id': case_id, 'error': type(exc).__name__ + ': ' + str(exc)}
                smoke.update(wall_second=time.perf_counter() - tick, cpu_second=time.process_time() - cputick,
                    new_native_executions=0, new_verifier_executions=1)
                screen.save(root / 'actual-verifier-smoke.json', smoke)
        except Exception as exc:
            result = {'schema': 'snd1-versioned-result-reassessment/1', 'case_id': case_id,
                'original_result': {'path': str(result_path), **originals.pin(result_path)},
                'integrity': {'status': 'unknown', 'coverage_complete': None, 'identity_complete': None,
                              'issues': [type(exc).__name__ + ': ' + str(exc)]},
                'top1': {s: {'pose_index': None, 'physical_valid': None, 'rmsd_A': None,
                            'valid_and_RMSD_le_2A': None} for s in old.get('rankings', {})},
                'old_top1': old.get('top1'), 'new_native_executions': 0, 'new_verifier_executions': 0}
        screen.save(destination / 'result.json', result)
        all_results.append(result)
        if (number + 1) % 100 == 0:
            print(json.dumps({'reassessed': number + 1, 'total': len(originals.manifest['completed_result_paths'])}), flush=True)
    counters = collections.Counter(r['integrity']['status'] for r in all_results)
    measured = {stage: dict(collections.Counter('unknown' if r['top1'][stage]['physical_valid'] is None
        else 'measured_valid' if r['top1'][stage]['physical_valid'] else 'measured_rejected'
        for r in all_results if stage in r['top1'])) for stage in ('empirical', 'cnn')}
    old_receipts = [originals.read(p) for p in originals.manifest['receipt_paths']]
    old_verifiers = [originals.read(Path(p).with_name('verifier.json')) for p in originals.manifest['completed_result_paths']]
    summary = {'schema': 'snd1-reassessment-summary/1', 'mode': 'versioned_retrospective_reanalysis',
        'frozen_completed_results': len(all_results), 'integrity_status': dict(counters), 'top1': measured,
        'top1_changed_cases': sum(r.get('top1_changed', False) for r in all_results),
        'failures_retained': [r['case_id'] for r in all_results if r['integrity']['status'] != 'complete'],
        'original_receipts_frozen': len(originals.manifest['receipt_paths']),
        'original_unresolved_started_preserved': originals.manifest['unresolved_started_at_freeze'],
        'original_artifacts_overwritten': 0, 'new_native_executions': 0,
        'new_docking_executions': 0, 'new_cnn_executions': 0,
        'additional_verifier': {k: smoke[k] for k in ('status', 'wall_second', 'cpu_second', 'new_verifier_executions')} if smoke else {'new_verifier_executions': 0, 'wall_second': 0., 'cpu_second': 0.},
        'verifier_replay_reuse': {'path': str(existing_smoke_path), **identity(existing_smoke_path)} if existing_smoke_path is not None else None,
        'new_verifier_executions_this_reassessment': 1 if checker_smoke and smoke else 0,
        'old_costs_at_frozen_inventory': {'native_statuses': dict(collections.Counter(r['status'] for r in old_receipts)),
            'native_wall_second': sum(r['wall_seconds'] for r in old_receipts),
            'native_cpu_second': sum(r.get('cpu_second') or 0. for r in old_receipts),
            'native_cpu_missing_count': sum(r.get('cpu_second') is None for r in old_receipts),
            'cpu_budget_charge_second': sum(r['cpu_budget_charge_second'] for r in old_receipts),
            'gpu_device_second': sum(r['gpu_device_second'] for r in old_receipts),
            'completed_result_verifier_wall_second': sum(v.get('wall_seconds', 0.) for v in old_verifiers),
            'scope': 'all frozen native receipts including completed acquisition without a result; actual CPU and GPU wall attribution distinct'},
        'reassessment_processing_including_smoke': {'wall_second': time.perf_counter() - start, 'cpu_second': time.process_time() - cpu},
        'scope': 'frozen completed inventory only; U validity is not pose correctness, affinity, or activity; seed repeats correlated',
        'old_runtime': protocol['runtime'], 'new_runtime': implementation,
        'old_source_frozen_check_contract': False, 'new_configuration_required_check_count': len(contract['required_checks']),
        'checker_configuration_applicability': 'actual PoseBusters 0.6.5 dock configuration and frozen old checker source identities; retrospective explicit binding reconstruction',
        'candidate_relative_eligibility_counts': dict(collections.Counter(r.get('candidate_relative_eligibility', {}).get('role', 'unestablished') for r in all_results)),
        'completed_result_splits': dict(collections.Counter(r.get('split', 'unknown') for r in all_results)),
        'evaluation_contract_input': {'coverage_complete': True if counters.get('unknown', 0) == 0 else None,
            'identity_complete': True if all(r['integrity'].get('identity_complete') is True for r in all_results) else None,
            'count': len(all_results), 'unknown_count': counters.get('unknown', 0)},
        'confirmation_inventory': {'original_count': sum(p['split']=='confirmation' for p in panel['parents']),
            'historical_frozen_policy_candidate_relative_eligible_count': sum(confirmation_eligibility(p,
                candidate_freeze=candidate, overlay=overlay, panel_identity=panel_pin)['eligible']
                for p in panel['parents'] if p['split']=='confirmation'),
            'seen_series_parent_count_for_post_feedback_candidates': overlay['affected_series_parents'],
            'untouched_parent_count_for_post_feedback_candidates': overlay['remaining_unseen_series_parents'],
            'note': 'untouched inventory alone does not establish a new candidate freeze or a new execution'},
        'new_candidate_after_P3': 'overlay exposed 67 parents in 12 series; 61 parents in 26 series remain untouched; 12 exact tasks actually ran',
        'old_split_rewritten': False, 'scientific_truth': 'not_certified', 'independent_replication': False}
    screen.save(root / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--overlay', type=Path, required=True)
    parser.add_argument('--checker-smoke', action='store_true')
    parser.add_argument('--existing-checker-smoke', type=Path)
    args = parser.parse_args()
    print(json.dumps(reassess(args.root, overlay_path=args.overlay, checker_smoke=args.checker_smoke,
                             existing_smoke_path=args.existing_checker_smoke), indent=2))
