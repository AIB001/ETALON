"""Identity and completeness checks for already produced scientific results.

This module neither executes a scientific tool nor certifies scientific truth.
Unknown/malformed evidence stays unknown; a measured rejection requires a full
set of typed observations under the exact frozen checker configuration.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

from etalon.active.schema import digest
from .scientific_input import identity

CHECK_CONTRACT = 'etalon-pose-check-contract/1'


def required_check_names(config):
    """Derive PoseBusters' public column names, without observing result rows."""
    modules = config.get('modules') if isinstance(config, dict) else None
    if not isinstance(modules, list) or not modules:
        raise ValueError('explicit checker modules required')
    # PoseBusters 0.6.x builds these lookups across modules, then normalizes.
    names = {(m['name'], k): v for m in modules for k, v in m.get('rename_outputs', {}).items()}
    suffixes = {m['name']: m['rename_suffix'] for m in modules if 'rename_suffix' in m}
    output = []
    for module in modules:
        chosen = module.get('chosen_binary_test_output')
        if not isinstance(chosen, list) or not chosen or any(not isinstance(n, str) or not n for n in chosen):
            raise ValueError('explicit binary outputs required for every checker module')
        for name in chosen:
            public = names.get((module['name'], name), name + suffixes.get(module['name'], ''))
            output.append(public.lower().replace(' ', '_'))
    if len(output) != len(set(output)):
        raise ValueError('duplicate checker output after public-name normalization')
    return sorted(output)


def freeze_check_contract(config, *, version, implementation):
    if not isinstance(version, str) or not version or not isinstance(implementation, dict) or not implementation:
        raise ValueError('checker version and actual implementation identities required')
    return {'schema': CHECK_CONTRACT, 'checker': 'PoseBusters', 'version': version,
            'configuration': copy.deepcopy(config), 'configuration_digest': digest(config),
            'required_checks': required_check_names(config), 'implementation': copy.deepcopy(implementation)}


def validate_check_contract(contract):
    if not isinstance(contract, dict) or contract.get('schema') != CHECK_CONTRACT:
        raise ValueError('frozen checker contract required')
    expected = freeze_check_contract(contract['configuration'], version=contract['version'], implementation=contract['implementation'])
    if contract != expected:
        raise ValueError('checker contract differs from configuration-derived requirements')
    return expected


def result_binding(*, compiled, protocol, pool, receptor, reference, check_contract):
    validate_check_contract(check_contract)
    return {'schema': 'etalon-pose-result-binding/1', 'case_id': compiled['case_id'],
            'input_digest': compiled['consumption']['input_sha256'], 'compiled_digest': digest(compiled),
            'method': compiled['method'], 'protocol': protocol,
            'ligand': compiled['files']['ligand'], 'pool': pool, 'receptor': receptor, 'reference': reference,
            'check_contract_digest': digest(check_contract)}


def validate_verifier(verifier, *, expected_pose_count, check_contract, expected_binding):
    """Return evidence status, never impute missing checks as false observations."""
    validate_check_contract(check_contract)
    issues = []
    if not isinstance(verifier, dict):
        verifier = {}
    if verifier.get('error'):
        issues.append('verifier_error')
    if verifier.get('binding') != expected_binding or not isinstance(expected_binding, dict):
        issues.append('binding_mismatch_or_missing')
    for field in ('pool', 'receptor', 'reference'):
        if verifier.get(field) != (expected_binding or {}).get(field):
            issues.append(field + '_identity_mismatch')
    if verifier.get('posebusters_version') != check_contract['version']:
        issues.append('checker_version_mismatch')
    if verifier.get('check_contract_digest') != digest(check_contract):
        issues.append('checker_contract_mismatch_or_missing')
    if type(expected_pose_count) is not int or expected_pose_count < 1:
        issues.append('invalid_expected_pose_count')
    if verifier.get('expected_pose_slots') != expected_pose_count or type(verifier.get('expected_pose_slots')) is not int:
        issues.append('pose_count_mismatch_or_missing')
    if verifier.get('coverage_complete') is not True:
        issues.append('coverage_not_complete')
    rows = verifier.get('rows')
    good_rows = isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
    indices = [r.get('pose_index') for r in rows] if good_rows else []
    if (not good_rows or type(expected_pose_count) is not int or len(indices) != expected_pose_count
            or any(type(i) is not int for i in indices) or set(indices) != set(range(max(expected_pose_count, 0)))):
        issues.append('row_coverage_mismatch')
    required = set(check_contract['required_checks'])
    for row in rows if good_rows else []:
        index = row.get('pose_index')
        checks = row.get('checks')
        if row.get('error') or row.get('status', 'checked') != 'checked':
            issues.append(f'row_{index}_error_or_unchecked')
        if not isinstance(checks, dict) or set(checks) != required:
            issues.append(f'row_{index}_check_set_mismatch')
            continue
        if any(type(v) is not bool for v in checks.values()):
            issues.append(f'row_{index}_nonboolean_check')
            continue
        valid = row.get('physical_valid')
        if type(valid) is not bool or valid != all(checks.values()):
            issues.append(f'row_{index}_inconsistent_validity')
        rmsd = row.get('rmsd_A')
        joint = row.get('valid_and_RMSD_le_2A')
        if (expected_binding or {}).get('reference') is None:
            if rmsd is not None or joint is not None:
                issues.append(f'row_{index}_unexpected_reference_observation')
        elif (type(rmsd) not in (int, float) or not math.isfinite(rmsd) or rmsd < 0
              or type(joint) is not bool or joint != (valid and rmsd <= 2.)):
            issues.append(f'row_{index}_invalid_rmsd_observation')
    return {'schema': 'etalon-result-integrity/1', 'status': 'complete' if not issues else 'unknown',
            'coverage_complete': True if not issues else None,
            'identity_complete': None if any('identity' in i or 'binding' in i or 'contract' in i or 'version' in i for i in issues) else True,
            'expected_pose_count': expected_pose_count, 'required_check_count': len(required), 'issues': issues,
            'scientific_truth': 'not_certified'}


def top1_observation(verifier, rank, expected_pose_count, *, check_contract, expected_binding):
    indices = rank.get('ordered_pose_indices', []) if isinstance(rank, dict) else []
    index = indices[0] if indices else None
    out = {'pose_index': index, 'physical_valid': None, 'rmsd_A': None, 'valid_and_RMSD_le_2A': None}
    audit = validate_verifier(verifier, expected_pose_count=expected_pose_count,
                              check_contract=check_contract, expected_binding=expected_binding)
    if audit['status'] != 'complete' or type(index) is not int:
        return out
    row = next((r for r in verifier['rows'] if r['pose_index'] == index), None)
    if row:
        out.update({k: row.get(k) for k in ('physical_valid', 'rmsd_A', 'valid_and_RMSD_le_2A')})
    return out


def validate_endpoint_verifier(verifier, *, check_contract, expected_inputs,
                              expected_pose_count, qualification_refs, workspace):
    """Validate the opt-in endpoint report without rerunning physical calculations.

    Original qualification claims are independently re-read. Report integrity and
    endpoint availability are separate: a valid report can explicitly retain an
    unknown RMSD and joint endpoint. This never promotes a historical result.
    """
    from .scientific_handoff import qualification_for_pose
    validate_check_contract(check_contract)
    issues, observations = [], {name: {"observed": 0, "unknown": 0} for name in ("physical", "rmsd", "joint")}
    if not isinstance(verifier, dict):
        verifier = {}
    if verifier.get("schema") != "etalon-pose-endpoint-verification/1":
        issues.append("endpoint_schema_mismatch")
    for key, expected in (("inputs", expected_inputs), ("expected_pose_count", expected_pose_count),
            ("qualification_refs", qualification_refs), ("check_contract_digest", digest(check_contract)),
            ("posebusters_version", check_contract["version"]), ("threshold_A", 2.0),
            ("historical_results_modified", False)):
        if verifier.get(key) != expected:
            issues.append(key + "_mismatch")
    actual = verifier.get("actual_pose_count")
    if (type(expected_pose_count) is not int or not 1 <= expected_pose_count <= 100
            or type(actual) is not int or not 0 <= actual <= 100):
        issues.append("invalid_pose_counts")
        actual = 0
    if actual != expected_pose_count or verifier.get("slot_coverage_complete") is not True:
        issues.append("slot_coverage_incomplete")
    rows = verifier.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        rows = []
        issues.append("malformed_rows")
    count = max(expected_pose_count, actual) if type(expected_pose_count) is int else actual
    indices = [row.get("pose_index") for row in rows]
    if any(type(i) is not int for i in indices) or len(indices) != count or set(indices) != set(range(count)):
        issues.append("row_coverage_mismatch")
    required = set(check_contract["required_checks"])
    for row in rows:
        index = row.get("pose_index")
        prefix = "row_" + str(index) + "_"
        if type(index) is not int:
            continue
        if row.get("slot_present") is not (index < actual):
            issues.append(prefix + "slot_presence_mismatch")
        qualification, qualified = row.get("qualification"), False
        ref = qualification_refs.get(str(index))
        if qualification is not None:
            try:
                if ref is None:
                    raise ValueError("qualification has no declared exact ref")
                checked = qualification_for_pose(ref, workspace=workspace,
                    predicted_identity=expected_inputs["predicted"], reference_identity=expected_inputs["reference"], pose_index=index)
                if qualification != checked:
                    raise ValueError("qualification claim mismatch")
                qualified = checked["eligibility"]["identity_comparable"] is True
            except Exception:
                issues.append(prefix + "qualification_unverified")
        endpoints = {}
        for name in observations:
            endpoint = row.get(name)
            if not isinstance(endpoint, dict) or endpoint.get("status") not in {"observed", "unknown"}:
                issues.append(prefix + name + "_malformed")
                continue
            endpoints[name] = endpoint
            observations[name][endpoint["status"]] += 1
            value = endpoint.get("value")
            if not endpoint.get("reason"):
                issues.append(prefix + name + "_reason_missing")
            if endpoint["status"] == "unknown":
                if value is not None:
                    issues.append(prefix + name + "_unknown_has_value")
            elif not row.get("slot_present"):
                issues.append(prefix + name + "_missing_slot_observation")
            elif name == "rmsd":
                if not qualified or type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    issues.append(prefix + "rmsd_ineligible_or_invalid")
            elif type(value) is not bool:
                issues.append(prefix + name + "_nonboolean")
        physical, rmsd, joint = (endpoints.get(name, {}) for name in ("physical", "rmsd", "joint"))
        if physical.get("status") == "observed":
            checks = row.get("checks")
            if (not isinstance(checks, dict) or set(checks) != required
                    or any(type(v) is not bool for v in checks.values())
                    or physical.get("value") != all(checks.values())):
                issues.append(prefix + "physical_check_coverage_or_value_mismatch")
        available = physical.get("status") == rmsd.get("status") == "observed"
        if available:
            value = rmsd.get("value")
            if type(value) in (int, float) and (joint.get("status") != "observed"
                    or joint.get("value") is not (physical.get("value") is True and value <= 2.0)):
                issues.append(prefix + "joint_inconsistent")
        elif joint.get("status") != "unknown" or joint.get("value") is not None:
            issues.append(prefix + "joint_requires_all_endpoints")
    return {"schema": "etalon-pose-endpoint-integrity/1", "status": "complete" if not issues else "unknown",
            "issues": issues, "endpoint_availability": observations,
            "scientific_truth": "not_certified", "historical_results_modified": False}


def validate_native_receipt(receipt, request, *, compiled, stage, protocol, pool, output, identity_reader=identity):
    """Reuse existing native artifact hashes and verify their request binding."""
    key = digest({'compiled': compiled, 'stage': stage, 'freeze': protocol})
    if (request.get('request_id') != key or request.get('compiled') != compiled or request.get('stage') != stage
            or request.get('freeze') != protocol or request.get('pool') != pool
            or receipt.get('request_id') != key or receipt.get('case_id') != compiled['case_id']
            or receipt.get('stage') != stage or receipt.get('attempt_id') != request.get('attempt_id')):
        raise ValueError('native receipt/request identity mismatch')
    artifacts = receipt.get('artifacts')
    if not isinstance(artifacts, dict):
        raise ValueError('native artifact identities missing')
    if receipt.get('status') not in ('completed', 'failed', 'censored'):
        raise ValueError('native receipt has unknown execution state')
    if receipt.get('status') == 'completed':
        resource = receipt.get('native_resource')
        if (receipt.get('error') is not None or receipt.get('execution_mode') != 'new_execution'
                or not isinstance(resource, dict) or type(resource.get('exit_status')) is not int
                or resource['exit_status'] != 0):
            raise ValueError('completed native execution evidence missing or inconsistent')
    for path, pin in artifacts.items():
        if identity_reader(path) != pin:
            raise ValueError('saved native artifact changed: ' + path)
    if receipt.get('status') == 'completed' and (str(output) not in artifacts or identity_reader(output) != artifacts[str(output)]):
        raise ValueError('completed receipt does not bind expected output')
    return receipt


def integrity_required_checks(input_name='integrity'):
    """Directly composable with evaluation_contract.build_evaluation_contract."""
    return [{'check_id': 'result_integrity_' + key, 'selector': {'input': input_name, 'pointer': '/' + key}, 'expected': True}
            for key in ('coverage_complete', 'identity_complete')]


def confirmation_eligibility(parent, *, candidate_freeze, overlay, panel_identity):
    """Candidate-relative role; never mutate the original chemical split."""
    reasons = []
    if parent.get('split') != 'confirmation':
        reasons.append('original_split_not_confirmation')
    if not isinstance(candidate_freeze, dict) or candidate_freeze.get('schema') != 'etalon-candidate-confirmation-freeze/1':
        return {'eligible': False, 'role': 'unestablished', 'reasons': reasons + ['candidate_relative_freeze_missing']}
    if candidate_freeze.get('panel') != panel_identity or not candidate_freeze.get('candidate_digest'):
        reasons.append('candidate_or_panel_binding_missing')
    if (type(candidate_freeze.get('frozen_at_ns')) is not int or candidate_freeze.get('decisions_frozen_before_execution') is not True
            or candidate_freeze.get('outcomes_visible_at_freeze') is not False):
        reasons.append('candidate_freeze_chronology_unestablished')
    if parent.get('id') not in candidate_freeze.get('parent_ids', []):
        reasons.append('parent_not_in_candidate_freeze')
    # The historical immutable declaration, not a newly asserted boolean or a
    # command-line flag, must bind this candidate and its pre-exposure time.
    evidence = candidate_freeze.get('freeze_evidence', {})
    evidence_valid = False
    try:
        import json
        path = evidence['path']
        if identity(path) != {k: evidence[k] for k in ('sha256', 'bytes')}:
            raise ValueError('freeze evidence bytes changed')
        declaration = json.loads(Path(path).read_text('utf-8'))
        declared_candidate = declaration.get('candidate_digest') or digest(declaration)
        if (declared_candidate != candidate_freeze['candidate_digest']
                or declaration.get('frozen_at_ns') != candidate_freeze.get('frozen_at_ns')
                or declaration.get('panel') != panel_identity
                or declaration.get('seen_series_ids', []) != candidate_freeze.get('seen_series_ids', [])):
            raise ValueError('freeze declaration differs')
        evidence_valid = True
    except (KeyError, TypeError, ValueError, OSError):
        reasons.append('candidate_freeze_evidence_unverified')
    seen = set(candidate_freeze.get('seen_series_ids', []))
    if not isinstance(overlay, dict) or overlay.get('schema') != 'snd1-seen-series-visibility-overlay/1':
        reasons.append('seen_series_overlay_missing')
    else:
        source = {k: overlay.get('source_panel', {}).get(k) for k in ('sha256', 'bytes')}
        if source != panel_identity or overlay.get('original_split_rewritten') is not False:
            reasons.append('overlay_panel_binding_mismatch')
        if overlay.get('status') != 'activated_after_audit' or type(overlay.get('activated_at_ns')) is not int:
            reasons.append('overlay_visibility_unestablished')
        historical = (evidence_valid and type(overlay.get('activated_at_ns')) is int
                      and candidate_freeze['frozen_at_ns'] < overlay['activated_at_ns'])
        if not historical:
            seen.update(r['series_id'] for r in overlay.get('rows', []))
            if parent.get('id') not in overlay.get('remaining_parent_ids', []):
                reasons.append('parent_not_in_remaining_unseen_inventory')
    if parent.get('series_id') in seen:
        reasons.append('series_feedback_seen')
    return {'eligible': not reasons, 'role': 'candidate_relative_confirmation' if not reasons else 'feedback_seen_or_unestablished',
            'original_split': parent.get('split'), 'reasons': reasons,
            'chronology_scope': 'verified declaration bytes and supplied historical timestamps; not independent timestamp attestation'}
