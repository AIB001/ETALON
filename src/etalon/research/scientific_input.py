"""A small, versioned evidence-to-parameter envelope, stored as ordinary Artifacts.

This is not a new journal or scientific inference engine. Domain adapters ground
claims into values; this module checks provenance, visibility and consumption.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

SCHEMA = 'etalon-scientific-input/1'
ROLES = {'conditioning', 'evaluation_only', 'context'}


def identity(path):
    data = Path(path).read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def validate_input(bundle, *, verify_files=True):
    """Validate layered lineage, including indirect evaluation-label leakage."""
    if bundle.get('schema') != SCHEMA:
        raise ValueError('unsupported scientific input schema')
    json.dumps(bundle, allow_nan=False)
    for key in ('task_id', 'scope', 'subject'):
        if not bundle.get(key):
            raise ValueError('missing '+key)
    records = {}
    for layer in ('sources', 'observations', 'interpretations'):
        for row in bundle[layer]:
            rid = row['id']
            if not isinstance(rid, str) or not rid or rid in records:
                raise ValueError('duplicate or empty evidence id')
            if row.get('role') not in ROLES or not row.get('scope'):
                raise ValueError('explicit visibility and scientific scope required')
            records[rid] = (layer, row)
            if layer == 'sources':
                if not Path(row['path']).is_absolute() or not row.get('locator'):
                    raise ValueError('source needs absolute path and locator')
                if verify_files and identity(row['path']) != {k: row[k] for k in ('sha256', 'bytes')}:
                    raise ValueError('source bytes changed: '+rid)
            else:
                if not row.get('basis_ids'):
                    raise ValueError('derived evidence requires source lineage')
                if layer == 'interpretations' and not row.get('assumption'):
                    raise ValueError('interpretations require explicit assumption')
                if row.get('kind') == 'activity':
                    data = row['data']
                    if not all(data.get(k) for k in ('endpoint', 'assay', 'molecule_id', 'target', 'relation', 'unit', 'status')):
                        raise ValueError('activity requires endpoint, assay, identity, unit and status')
                    value = data.get('value')
                    if data['status'] == 'quantified' and (type(value) not in (float, int) or not math.isfinite(value) or value <= 0):
                        raise ValueError('quantified activity requires a positive finite measurement')
                    if data['status'] != 'quantified' and value is not None:
                        raise ValueError('unresolved activity must not become a numeric label')

    def ancestors(rid, trail=()):
        if rid not in records or rid in trail:
            raise ValueError('missing or cyclic evidence lineage')
        layer, row = records[rid]
        result = {rid}
        if layer != 'sources':
            for parent in row['basis_ids']:
                result |= ancestors(parent, (*trail, rid))
        return result

    for rid in records:
        ancestors(rid)
    binding_ids, targets = set(), set()
    for binding in bundle['bindings']:
        if binding['id'] in binding_ids or binding['target'] in targets:
            raise ValueError('duplicate binding or parameter target')
        binding_ids.add(binding['id']); targets.add(binding['target'])
        if binding['use'] not in {'constraint', 'objective', 'selection', 'evaluation', 'context'}:
            raise ValueError('unknown evidence use')
        if not binding.get('basis_ids') or not binding.get('rationale'):
            raise ValueError('binding requires lineage and scientific rationale')
        lineage = set().union(*(ancestors(rid) for rid in binding['basis_ids']))
        forbidden = {'evaluation_only', 'context'} if binding['use'] in {'constraint', 'objective', 'selection'} else set()
        if any(records[rid][1]['role'] in forbidden for rid in lineage):
            raise ValueError('non-conditioning evidence used for execution: '+binding['id'])
    return bundle


def compile_input(bundle, *, allowed_targets, verify_files=True):
    """Fail on unsupported bindings; never silently pretend a tool consumed them."""
    validate_input(bundle, verify_files=verify_files)
    uses = allowed_targets if isinstance(allowed_targets, dict) else None
    allowed_targets = set(allowed_targets)
    parameters, manifest = {}, []
    for binding in bundle['bindings']:
        if binding['target'] not in allowed_targets:
            raise ValueError('unsupported parameter: '+binding['target'])
        if uses is not None and binding['use'] != uses[binding['target']]:
            raise ValueError('parameter use differs from consumer contract: '+binding['target'])
        parameters[binding['target']] = copy.deepcopy(binding['value'])
        manifest.append({k: copy.deepcopy(binding[k]) for k in ('id', 'target', 'use', 'basis_ids', 'rationale')})
    return {'schema': 'etalon-scientific-binding/1',
            'input_sha256': hashlib.sha256(json.dumps(bundle, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
            'parameters': parameters, 'bindings': manifest}


def consumption_receipt(compiled, *, consumed_targets, deferred=None):
    """Require every supplied parameter to be applied or explicitly deferred."""
    consumed, deferred = set(consumed_targets), dict(deferred or {})
    expected = set(compiled['parameters'])
    if consumed & deferred.keys() or consumed | deferred.keys() != expected or any(not v for v in deferred.values()):
        raise ValueError('incomplete, duplicate or unexplained parameter consumption')
    return {'schema': 'etalon-scientific-consumption/1', 'input_sha256': compiled['input_sha256'],
            'consumed_targets': sorted(consumed), 'deferred': deferred,
            'bindings': compiled['bindings']}
