"""Explicit bridge from the original pose verifier to frozen check requirements.

The old verifier's observations are preserved. This adapter audits them; it does
not run PoseBusters or retroactively claim a historical configuration freeze.
"""
from __future__ import annotations

import copy
import importlib.metadata
from pathlib import Path

from etalon.active.schema import digest
from .scientific_input import identity
from .result_integrity import freeze_check_contract, validate_check_contract, validate_verifier


def freeze_posebusters_dock():
    from posebusters import PoseBusters
    import posebusters
    package = Path(posebusters.__file__).parent
    implementation = {str(p): identity(p) for p in sorted(package.rglob('*'))
                      if p.is_file() and p.suffix in ('.py', '.yml', '.yaml', '.json')}
    return freeze_check_contract(PoseBusters(config='dock').config,
        version=importlib.metadata.version('posebusters'), implementation=implementation)


def audit_pose_verification(value, *, check_contract, pool, reference, receptor,
                            evaluation_request, expected_request_ids):
    """Check old-format observations against independently supplied identities."""
    validate_check_contract(check_contract)
    expected_count = len(expected_request_ids)
    issues = []
    if not isinstance(value, dict):
        value = {}
    if value.get('schema') != 'etalon-pose-verification/1':
        issues.append('legacy_verification_schema_mismatch')
    if value.get('protocol') != 'fixed-receptor-frame-heavy-symmetry/1':
        issues.append('legacy_rmsd_protocol_mismatch')
    if type(value.get('threshold_A')) not in (int, float) or value.get('threshold_A') != 2.:
        issues.append('legacy_rmsd_threshold_mismatch')
    if type(value.get('requested_slots')) is not int or value.get('requested_slots') != expected_count:
        issues.append('legacy_requested_slots_mismatch')
    if value.get('inputs') != {'predicted': pool, 'reference': reference, 'receptor': receptor}:
        issues.append('legacy_input_identity_mismatch')
    rows = value.get('rows')
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        rows = []
        issues.append('legacy_rows_missing_or_malformed')
    if [r.get('request_id') for r in rows] != list(expected_request_ids):
        issues.append('legacy_row_request_order_or_coverage_mismatch')
    if len(expected_request_ids) != len(set(expected_request_ids)):
        issues.append('expected_request_ids_not_unique')
    binding = {'schema': 'etalon-pose-evaluation-bridge-binding/1',
        'evaluation_request_digest': digest(evaluation_request),
        'protocol': evaluation_request.get('freeze'), 'pool': pool, 'reference': reference, 'receptor': receptor,
        'rmsd_protocol': 'fixed-receptor-frame-heavy-symmetry/1', 'threshold_A': 2.,
        'check_contract_digest': digest(check_contract)}
    inputs = value.get('inputs') if isinstance(value.get('inputs'), dict) else {}
    normalized = {'schema': 'snd1-p2-pose-verification/2', 'binding': binding,
        'pool': inputs.get('predicted'),
        'reference': inputs.get('reference'), 'receptor': inputs.get('receptor'),
        'posebusters_version': value.get('posebusters_version'), 'check_contract_digest': digest(check_contract),
        'expected_pose_slots': value.get('requested_slots'), 'coverage_complete': True,
        'rows': [{**copy.deepcopy(r), 'valid_and_RMSD_le_2A': r.get('success')} for r in rows]}
    audit = validate_verifier(normalized, expected_pose_count=expected_count,
                              check_contract=check_contract, expected_binding=binding)
    if type(value.get('top1_success')) is not bool or value.get('top1_success') != bool(rows and rows[0].get('success') is True):
        issues.append('legacy_top1_success_mismatch')
    if type(value.get('any_success')) is not bool or value.get('any_success') != any(r.get('success') is True for r in rows):
        issues.append('legacy_any_success_mismatch')
    audit['issues'] = issues + audit['issues']
    if audit['issues']:
        audit.update(status='unknown', coverage_complete=None)
        if any('identity' in item for item in issues):
            audit['identity_complete'] = None
    audit['binding'] = binding
    audit['binding_mode'] = 'explicit_bridge_no_new_PoseBusters_execution'
    return audit
