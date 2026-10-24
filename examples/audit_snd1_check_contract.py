"""Read-only retrospective audit of a completed SND1 controlled evaluation.

Uses exact preserved inputs and the actual historical scientific interpreter.
No optimization, docking, CNN, or PoseBusters bust call is performed. Fixed-frame
RMSD is recomputed as separately measured reanalysis, using the frozen function.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import numpy as np
from rdkit import Chem

from etalon.active.schema import digest
from etalon.research.scientific_input import identity
from etalon.research.pose_check_bridge import freeze_posebusters_dock, audit_pose_verification


def read(p):
    return json.loads(Path(p).read_text('utf-8'))


def save(p, value):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    if p.exists():
        if p.read_bytes() != raw:
            raise ValueError('immutable audit artifact differs: ' + str(p))
    else:
        p.write_bytes(raw)


def molecular_signature(mol):
    atoms = [(a.GetAtomicNum(), a.GetFormalCharge(), a.GetIsotope(), int(a.GetChiralTag())) for a in mol.GetAtoms()]
    bonds = [(b.GetBeginAtomIdx(), b.GetEndAtomIdx(), str(b.GetBondType()), int(b.GetStereo())) for b in mol.GetBonds()]
    return atoms, bonds


def audit(root, out):
    root, out = Path(root).absolute(), Path(out).absolute()
    if not out.is_relative_to(root / 'check-contract-audit'):
        raise ValueError('audit output must stay in the separate check-contract-audit directory')
    started, cpu = time.perf_counter(), time.process_time()
    originals = {str(p): identity(p) for p in sorted(root.rglob('*'))
                 if p.is_file() and not p.is_relative_to(root / 'check-contract-audit') and '__pycache__' not in p.parts}
    frozen = read(root / 'freeze.json')
    frozen_pin = identity(root / 'freeze.json')
    for group in ('inputs', 'documents'):
        for rel, pin in frozen[group].items():
            if identity(root / rel) != pin:
                raise ValueError('frozen original changed: ' + rel)
    for name, pin in frozen['modules'].items():
        module = importlib.import_module(name)
        if module.__file__ != pin['path'] or identity(module.__file__) != {k: pin[k] for k in ('sha256', 'bytes')}:
            raise ValueError('actual historical scientific import differs: ' + name)
    for path, pin in frozen['sources'].items():
        if identity(path) != pin:
            raise ValueError('historical source changed: ' + path)
    contract = freeze_posebusters_dock()
    verifier_path = root / 'source/pose_verifier.py'
    spec = importlib.util.spec_from_file_location('etalon.research.check_audit_frozen_pose_verifier', verifier_path)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    selection = read(root / 'selection.json')
    systems = {s['system_id']: s for s in read(root / 'systems.json')}
    protocol = read(root / 'protocol.json')
    cells = protocol['cells']
    expected_groups = {sid + '--' + cell for sid in systems for cell in cells}
    present_groups = {p.parent.name for p in (root / 'evaluation').glob('*/verification.json')}
    if expected_groups != present_groups:
        raise ValueError('evaluation group inventory differs from frozen systems/cells')
    audit_freeze = {'schema': 'snd1-check-contract-audit-freeze/1', 'mode': 'retrospective_binding',
        'original_protocol_freeze': frozen_pin, 'original_inputs': originals, 'check_contract': contract,
        'old_verifier_source': {'path': str(verifier_path), **identity(verifier_path)},
        'actual_python': sys.executable, 'audit_source': {'path': __file__, **identity(__file__)},
        'bridge_source': {'path': sys.modules['etalon.research.pose_check_bridge'].__file__,
                          **identity(sys.modules['etalon.research.pose_check_bridge'].__file__)},
        'integrity_source': {'path': sys.modules['etalon.research.result_integrity'].__file__,
                            **identity(sys.modules['etalon.research.result_integrity'].__file__)},
        'native_execution': 'prohibited', 'posebusters_bust_calls': 'prohibited',
        'historical_config_predeclaration': 'not_established; full dock configuration frozen retrospectively here'}
    save(out / 'freeze.json', audit_freeze)
    groups, rows, problems = [], [], []
    success_counts = collections.Counter()
    rmsd_tick, rmsd_cpu = 0., 0.
    for sid, system in sorted(systems.items()):
        conditions = [c for c in selection['conditions'] if c['system_id'] == sid]
        for cell in cells:
            group = sid + '--' + cell
            run = root / 'evaluation' / group
            request, receipt = read(run / 'request.json'), read(run / 'receipt.json')
            expected_ids = [c['condition_id'] + '--' + cell for c in conditions]
            expected_request = {'schema': 'snd1-controlled-evaluation-request/1', 'request_id': group,
                'attempt_id': 'independent-v1', 'freeze': frozen_pin,
                'input_receipts': {rid: identity(root / 'executions' / rid / 'receipt.json') for rid in expected_ids}}
            if request != expected_request or receipt.get('status') != 'completed' or any(receipt.get(k) != v for k, v in request.items()):
                raise ValueError('evaluation request/receipt binding mismatch: ' + group)
            if set(receipt.get('artifacts', {})) != {'poses.sdf', 'verification.json'}:
                raise ValueError('evaluation artifact inventory mismatch: ' + group)
            for name, pin in receipt['artifacts'].items():
                if identity(run / name) != pin:
                    raise ValueError('evaluation artifact identity mismatch: ' + group + '/' + name)
            value = read(run / 'verification.json')
            report = audit_pose_verification(value, check_contract=contract, pool=identity(run / 'poses.sdf'),
                reference=identity(root / system['reference']), receptor=identity(root / system['receptor_pdb']),
                evaluation_request=request, expected_request_ids=expected_ids)
            if report['status'] != 'complete':
                problems.append({'group': group, 'issues': report['issues']})
            aggregate = list(Chem.SDMolSupplier(str(run / 'poses.sdf'), removeHs=False))
            reference = Chem.SDMolSupplier(str(root / system['reference']), removeHs=False)[0]
            if len(aggregate) != len(conditions) or reference is None:
                raise ValueError('actual evaluation molecule slot count mismatch: ' + group)
            for index, (condition, predicted) in enumerate(zip(conditions, aggregate)):
                rid = expected_ids[index]
                native = root / 'executions' / rid
                native_request, native_receipt = read(native / 'request.json'), read(native / 'receipt.json')
                expected_native = {'schema': 'snd1-controlled-request/1', 'request_id': rid,
                    'attempt_id': 'controlled-v1-attempt-1', 'condition': condition, 'cell': cell,
                    'configuration': cells[cell], 'freeze': frozen_pin,
                    'input': identity(root / condition['input_xyz']), 'parameters': identity(root / system['parameters']), 'new_execution': True}
                if (native_request != expected_native or native_receipt.get('status') != 'completed'
                        or native_receipt.get('error') is not None
                        or any(native_receipt.get(k) != v for k, v in native_request.items())):
                    raise ValueError('native request/receipt/input binding mismatch: ' + rid)
                if set(native_receipt.get('artifacts', {})) != {'pose.sdf', 'pose.npy', 'trace.npy'}:
                    raise ValueError('native artifact coverage mismatch: ' + rid)
                for name, pin in native_receipt['artifacts'].items():
                    if identity(native / name) != pin:
                        raise ValueError('native output identity mismatch: ' + rid + '/' + name)
                native_pose = Chem.SDMolSupplier(str(native / 'pose.sdf'), removeHs=False)[0]
                initial = np.load(root / condition['input_xyz'])
                if hashlib.sha256(initial.astype('<f8').tobytes()).hexdigest() != condition['coordinate_sha256']:
                    raise ValueError('initial coordinate identity mismatch: ' + rid)
                if predicted is None or native_pose is None or molecular_signature(predicted) != molecular_signature(native_pose):
                    raise ValueError('evaluation row molecular identity/order differs from native output: ' + rid)
                coordinate_delta = float(np.abs(predicted.GetConformer().GetPositions() - native_pose.GetConformer().GetPositions()).max())
                if coordinate_delta > 1.e-12:
                    raise ValueError('evaluation pool coordinates differ from native output: ' + rid)
                t, c = time.perf_counter(), time.process_time()
                recomputed = verifier.symmetry_rmsd(predicted, reference)
                rmsd_tick += time.perf_counter() - t
                rmsd_cpu += time.process_time() - c
                observation = value['rows'][index]
                delta = abs(recomputed - observation['rmsd_A'])
                expected_success = observation['physical_valid'] and recomputed <= 2.
                good = delta <= 1.e-10 and type(observation.get('success')) is bool and observation['success'] == expected_success
                if not good:
                    problems.append({'request_id': rid, 'issues': ['RMSD_or_success_recalculation_mismatch']})
                if good and observation['success']:
                    success_counts[cell] += 1
                rows.append({'request_id': rid, 'group': group, 'pose_index': index,
                    'required_check_names_exact': set(observation.get('checks', {})) == set(contract['required_checks']),
                    'all_checks_strict_boolean': all(type(v) is bool for v in observation.get('checks', {}).values()),
                    'physical_valid': observation['physical_valid'], 'recorded_rmsd_A': observation['rmsd_A'],
                    'recomputed_rmsd_A': recomputed, 'rmsd_delta_A': delta, 'success': observation['success'],
                    'RMSD_success_verified': good, 'native_input_binding_verified': True,
                    'evaluation_pose_native_coordinate_delta_A': coordinate_delta,
                    'evaluation_input_receipt': request['input_receipts'][rid]})
            groups.append({'group': group, 'verification': {'path': str(run / 'verification.json'), **identity(run / 'verification.json')},
                           'request': identity(run / 'request.json'), 'receipt': identity(run / 'receipt.json'), 'integrity': report})
    changed = [path for path, pin in originals.items() if identity(path) != pin]
    if changed:
        raise ValueError('original bytes changed during audit: ' + ','.join(changed))
    report = {'schema': 'snd1-controlled-check-contract-audit/1', 'status': 'complete' if not problems else 'unknown',
        'mode': 'retrospective_binding_and_RMSD_reanalysis', 'evaluation_files': len(groups), 'rows': len(rows),
        'required_check_count': len(contract['required_checks']), 'unknown_groups': len(problems), 'problems': problems,
        'strict_required_sets_complete': all(r['required_check_names_exact'] for r in rows),
        'strict_boolean_checks_complete': all(r['all_checks_strict_boolean'] for r in rows),
        'RMSD_and_success_verified': all(r['RMSD_success_verified'] for r in rows),
        'native_input_receipt_binding_verified': True, 'changed_original_files': changed,
        'success_counts': {cell: success_counts[cell] for cell in cells}, 'new_native_executions': 0,
        'new_PoseBusters_bust_calls': 0, 'new_optimization_calls': 0,
        'RMSD_recalculation_count': len(rows), 'RMSD_reanalysis_cost': {'wall_second': rmsd_tick, 'cpu_second': rmsd_cpu},
        'total_audit_cost': {'wall_second': time.perf_counter() - started, 'cpu_second': time.process_time() - cpu},
        'cost_note': 'RMSD subintervals are included in total audit cost; do not add twice',
        'old_config_predeclared': False, 'historical_source_pins_verified': True,
        'old_metrics_required_checks_complete_trusted': False,
        'scope': 'exact 136 seen diagnostic observations only; no new PB observations, affinity, independent transfer or prospective freeze claim'}
    save(out / 'groups.json', groups)
    save(out / 'rows.json', rows)
    save(out / 'summary.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.root, args.out), indent=2))
